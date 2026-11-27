# -*- coding: utf-8 -*-
"""
数据接入与治理测试。

这组测试盯的是「甲方换一张表还能不能用」：
列名别名、编码、缺字段、重编号、断关系、类型不匹配，每一种都要给出明确结论，
而且不能把好数据判成坏数据（误杀比漏判更容易让人放弃使用）。
"""
from __future__ import annotations

import base64
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ingest import rdfgen, topology as topo_mod, validator  # noqa: E402
from app.ingest.mapping import resolve_columns, resolve_type  # noqa: E402
from app.reasoner.engine import ReasoningEngine  # noqa: E402
from app.reasoner.graph import ONTO_TTL, GridModel  # noqa: E402

TEMPLATE_DIR = ROOT / "data" / "templates"
LEDGER = TEMPLATE_DIR / "设备台账模板.csv"
TOPO = TEMPLATE_DIR / "拓扑关系模板.csv"


def _read(path: Path):
    return validator.decode_bytes(base64.b64encode(path.read_bytes()).decode())


# ---------------------------------------------------------------------------
# 字段映射
# ---------------------------------------------------------------------------
def test_column_aliases_resolve() -> None:
    headers = ["编号", "类型", "名称", "馈线编号", "所属线路段", "容量kVA", "负荷", "台区户数"]
    cols = resolve_columns(headers)
    mapping = cols["mapping"]
    assert mapping["code"] == 0
    assert mapping["type"] == 1
    assert mapping["name"] == 2
    assert mapping["feeder"] == 3
    assert mapping["parent"] == 4
    assert mapping["rated_kw"] == 5
    assert mapping["load_kw"] == 6
    assert mapping["households"] == 7
    assert cols["missing"] == []


def test_unknown_columns_reported_not_silently_dropped() -> None:
    cols = resolve_columns(["设备编号", "设备类型", "投运日期", "资产编号"])
    assert "投运日期" in cols["unmapped"]
    assert "资产编号" in cols["unmapped"]


def test_missing_required_columns_detected() -> None:
    cols = resolve_columns(["设备名称", "所属馈线"])
    assert "设备编号" in cols["missing"]
    assert "设备类型" in cols["missing"]


@pytest.mark.parametrize("raw,expected", [
    ("配变", "DistributionTransformer"),
    ("配电变压器", "DistributionTransformer"),
    ("公用变压器", "DistributionTransformer"),
    ("变压器", "DistributionTransformer"),
    ("联络开关", "TieSwitch"),
    (" 台区 ", "TransformerDistrict"),
])
def test_type_aliases(raw: str, expected: str) -> None:
    assert resolve_type(raw) == expected


def test_unknown_type_returns_none() -> None:
    assert resolve_type("量子变压器") is None


# ---------------------------------------------------------------------------
# 编码
# ---------------------------------------------------------------------------
def test_gbk_file_decoded() -> None:
    text = "设备编号,设备类型\nTR_A,配变\n"
    raw = text.encode("gbk")
    decoded, enc = validator.decode_bytes(base64.b64encode(raw).decode())
    assert "配变" in decoded
    assert enc.lower() in ("gb18030", "gbk")


def test_utf8_bom_stripped() -> None:
    text = "\ufeff设备编号,设备类型\nTR_A,配变\n"
    headers, body = validator.parse_csv(text)
    assert headers[0] == "设备编号"
    assert len(body) == 1


def test_semicolon_delimiter_supported() -> None:
    text = "设备编号;设备类型\nTR_A;配变\n"
    headers, body = validator.parse_csv(text)
    assert headers == ["设备编号", "设备类型"]
    assert body[0] == ["TR_A", "配变"]


# ---------------------------------------------------------------------------
# 校验规则
# ---------------------------------------------------------------------------
def test_complete_template_passes() -> None:
    text, enc = _read(LEDGER)
    result = validator.validate(text, "ledger.csv", enc)
    assert result["passed"], result["summary"]
    assert result["rows_total"] == 37
    assert result["rows_valid"] == 37
    assert not result["issues"]


def test_duplicate_code_detected() -> None:
    text, _ = _read(LEDGER)
    text = text + "TR_A,配变,重复的配变A,FX,LS2,630,380,,,,,10,\n"
    result = validator.validate(text, "dup.csv")
    codes = [i["code"] for i in result["issues"]]
    assert "V2" in codes
    assert result["rows_valid"] == result["rows_total"] - 1


def test_missing_required_field_detected() -> None:
    text = "设备编号,设备类型\n,配变\n"
    result = validator.validate(text, "x.csv")
    assert any(i["code"] == "V1" for i in result["issues"])
    assert not result["passed"]


def test_unknown_type_blocks_row() -> None:
    text = "设备编号,设备类型\nTR_X,量子变压器\n"
    result = validator.validate(text, "x.csv")
    assert any(i["code"] == "V3" for i in result["issues"])


def test_dangling_reference_detected() -> None:
    text = ("设备编号,设备类型,所属馈线,上级设备\n"
            "FX,馈线,,ST1\n"      # ST1 不存在
            "LS1,线路段,FX,LS9\n")  # LS9 不存在
    result = validator.validate(text, "x.csv")
    v5 = [i for i in result["issues"] if i["code"] == "V5"]
    assert len(v5) >= 2


def test_wrong_parent_type_detected() -> None:
    """台区的上级必须是配变，填成线路段要报错。"""
    text = ("设备编号,设备类型,所属馈线,上级设备\n"
            "ST1,变电站,,,,\n"
            "FX,馈线,,ST1\n"
            "LS1,线路段,FX,FX\n"
            "TA,台区,,LS1\n")
    result = validator.validate(text, "x.csv")
    assert any(i["code"] == "V6" for i in result["issues"])


def test_negative_load_detected() -> None:
    text = ("设备编号,设备类型,所属馈线,上级设备,当前负荷(kW)\n"
            "ST1,变电站,,,,\n"
            "FX,馈线,,ST1,-100\n")
    result = validator.validate(text, "x.csv")
    assert any(i["code"] == "V4" for i in result["issues"])


def test_missing_households_on_district_detected() -> None:
    text = ("设备编号,设备类型,上级设备\n"
            "TR_A,配变,LS2\n"
            "TA,台区,TR_A\n")
    result = validator.validate(text, "x.csv")
    assert any(i["code"] == "V8" and "户数" in i["message"] for i in result["issues"])


def test_unreachable_device_warned() -> None:
    text = ("设备编号,设备类型,所属馈线,上级设备,额定容量(kW),当前负荷(kW)\n"
            "ST1,变电站,,,,\n"
            "FX,馈线,,ST1,4000,1700\n"
            "TR_X,配变,FX,LS_NOT_EXIST,630,100\n")
    result = validator.validate(text, "x.csv")
    assert any(i["code"] in ("V5", "V7") for i in result["issues"])


def test_real_template_has_no_warnings() -> None:
    """示例模板必须干净：它是给甲方照抄的样板。"""
    text, enc = _read(LEDGER)
    result = validator.validate(text, "ledger.csv", enc)
    warnings = [i for i in result["issues"] if i["level"] == "warning"]
    assert not warnings, f"模板出现告警：{warnings[:3]}"


# ---------------------------------------------------------------------------
# 拓扑表
# ---------------------------------------------------------------------------
def _ledger_devices():
    text, enc = _read(LEDGER)
    return validator.validate(text, "ledger.csv", enc)["devices_detail"]


def test_topology_template_passes() -> None:
    text, _ = _read(TOPO)
    result = topo_mod.validate(text, _ledger_devices(), "topo.csv")
    assert result["passed"], result["issues"]
    assert result["valid_edges"] == 15


def test_topology_self_loop_detected() -> None:
    text = "起点设备,终点设备,连接开关\nLS1,LS1,S1\n"
    result = topo_mod.validate(text, _ledger_devices(), "t.csv")
    assert any(i["code"] == "T4" for i in result["issues"])


def test_topology_duplicate_edge_detected() -> None:
    text = "起点设备,终点设备,连接开关\nLS1,LS2,S1\nLS1,LS2,S2\n"
    result = topo_mod.validate(text, _ledger_devices(), "t.csv")
    assert any(i["code"] == "T2" for i in result["issues"])


def test_topology_unknown_endpoint_detected() -> None:
    text = "起点设备,终点设备,连接开关\nLS1,NOPE,S1\n"
    result = topo_mod.validate(text, _ledger_devices(), "t.csv")
    assert any(i["code"] == "T1" for i in result["issues"])


def test_topology_non_switch_as_switch_detected() -> None:
    text = "起点设备,终点设备,连接开关\nLS1,LS2,TR_A\n"
    result = topo_mod.validate(text, _ledger_devices(), "t.csv")
    assert any(i["code"] == "T3" for i in result["issues"])


def test_topology_orphan_warned() -> None:
    text = "起点设备,终点设备,连接开关\nST1,FX,\n"
    result = topo_mod.validate(text, _ledger_devices(), "t.csv")
    assert any(i["code"] == "T5" for i in result["issues"])


# ---------------------------------------------------------------------------
# 三元组生成与闭环
# ---------------------------------------------------------------------------
def test_turtle_is_parseable() -> None:
    from rdflib import Graph

    ltext, enc = _read(LEDGER)
    ledger = validator.validate(ltext, "l.csv", enc)
    ttext, _ = _read(TOPO)
    topo = topo_mod.validate(ttext, ledger["devices_detail"], "t.csv")
    ttl = rdfgen.to_turtle(ledger["devices_detail"], topo["edges"])

    g = Graph()
    g.parse(data=ttl, format="turtle")
    assert len(g) > 100
    # 关键属性必须落在图上
    from rdflib import Namespace
    grid = Namespace("http://grid.ontology.local/onto#")
    assert (None, grid.ratedCapacityKW, None) in g
    assert (None, grid.householdCount, None) in g


def test_generated_graph_reproduces_reasoning_result() -> None:
    """
    闭环验证：从 CSV 生成的三元组，必须能推出与原文实例完全一致的结论。

    这是「数据接入」这件事是否真的成立的唯一硬标准——
    不是能导进去就行，而是导进去以后推理结果不变。
    """
    ltext, enc = _read(LEDGER)
    ledger = validator.validate(ltext, "l.csv", enc)
    ttext, _ = _read(TOPO)
    topo = topo_mod.validate(ttext, ledger["devices_detail"], "t.csv")
    ttl = rdfgen.to_turtle(ledger["devices_detail"], topo["edges"])

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "generated.ttl"
        path.write_text(ttl, encoding="utf-8")
        model = GridModel.load(ONTO_TTL, path)
        engine = ReasoningEngine(model)

        assert model.census()["households"] == 2440
        outage = engine.outage("S1", "open")
        assert outage["households"] == 1200
        assert [d["id"] for d in outage["affected_districts"]] == ["TA", "TB", "TC"]
        assert len(outage["critical_customers"]) == 2

        plans = engine.transfer_plans("S1", "open")
        assert plans["feasible_count"] == 1
        assert plans["plans"][0]["transferred_kw"] == 1000


def test_bad_rows_excluded_from_turtle() -> None:
    ltext, enc = _read(LEDGER)
    ledger = validator.validate(ltext + "BAD_ROW,量子设备,,,,,,,,,,,\n", "l.csv", enc)
    ttl = rdfgen.to_turtle(ledger["devices_detail"], [])
    assert "data:BAD_ROW" not in ttl


def test_stats_reports_skipped() -> None:
    ltext, enc = _read(LEDGER)
    ledger = validator.validate(ltext + "BAD_ROW,量子设备,,,,,,,,,,,\n", "l.csv", enc)
    st = rdfgen.stats(ledger["devices_detail"], [])
    assert st["skipped"] >= 1
    assert "BAD_ROW" in st["skipped_codes"]
