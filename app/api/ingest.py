# -*- coding: utf-8 -*-
"""
数据接入与治理接口。

流程与甲方现场的实际做法一致：
    上传台账 CSV → 字段映射 → 逐行校验 → （可选）拓扑表校验 → 生成 TTL 预览

设计取舍：只做校验与预览，不自动替换运行数据。
演示环境的实例是精心构造的验证用例（8 道题、25 项测试都依赖它），
被一份尚在核对中的台账覆盖，整个演示会连锁失效。
正式实施时由实施人员确认后替换 data/grid-instances.ttl 并重启，这条路径写在返回说明里。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import audit
from ..ingest import rdfgen, topology as topo_mod, validator
from ..ingest.mapping import FIELDS, type_options

router = APIRouter()

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = ROOT / "data" / "templates"

TEMPLATES = [
    {"name": "设备台账模板", "file": "设备台账模板.csv",
     "description": "变电站、馈线、线路段、开关、配变、台区，一张表按上级关系串起来"},
    {"name": "拓扑关系模板", "file": "拓扑关系模板.csv",
     "description": "起点设备—开关—终点设备，描述电气连接；开关为空表示固定连接"},
]


class CsvPayload(BaseModel):
    filename: str = Field("", description="原始文件名，仅用于回显")
    content_base64: str = Field("", description="CSV 原始字节的 base64（推荐，能自动识别 GBK/UTF-8）")
    content_text: str = Field("", description="纯文本内容，与 content_base64 二选一")
    encoding: str = Field("utf-8", description="content_text 的编码，仅用于回显")


class IngestPayload(BaseModel):
    ledger: CsvPayload
    topology: Optional[CsvPayload] = Field(None, description="可选，不传则只校验台账")


def _read(payload: CsvPayload):
    if payload.content_base64:
        return validator.decode_bytes(payload.content_base64)
    if payload.content_text:
        return payload.content_text, payload.encoding
    raise HTTPException(400, "请提供 content_base64 或 content_text")


@router.get("/schema")
def schema() -> dict:
    """告诉甲方「我们认哪些列」，以及每条校验规则的含义。"""
    return {
        "fields": FIELDS,
        "type_options": type_options(),
        "checks": validator.CHECKS,
        "topology_checks": topo_mod.TOPOLOGY_CHECKS,
        "topology_columns": [
            {"column": "起点设备", "description": "边的上游端点，填设备台账里的设备编号"},
            {"column": "终点设备", "description": "边的下游端点"},
            {"column": "连接开关", "description": "该边上的开关编号；固定连接（如配变挂接）留空"},
        ],
        "encodings": validator.ENCODINGS,
        "note": "列名支持别名：如「配变/配电变压器/公用变压器」都识别为配变，"
                "「所属馈线/馈线编号/线路编号」都识别为所属馈线。",
    }


@router.get("/templates")
def templates() -> dict:
    out = []
    for t in TEMPLATES:
        path = TEMPLATE_DIR / t["file"]
        out.append({**t, "exists": path.exists(), "size": path.stat().st_size if path.exists() else 0})
    return {"count": len(out), "templates": out}


@router.get("/templates/{name}")
def template_content(name: str) -> dict:
    match = next((t for t in TEMPLATES if t["name"] == name or t["file"] == name), None)
    if not match:
        raise HTTPException(404, f"模板 {name} 不存在")
    path = TEMPLATE_DIR / match["file"]
    if not path.exists():
        raise HTTPException(404, f"模板文件缺失：{match['file']}")
    return {"name": match["name"], "file": match["file"],
            "content": path.read_text(encoding="utf-8-sig")}


@router.post("/validate")
def validate(payload: CsvPayload) -> dict:
    """只校验台账，不生成 TTL。前端分步展示时用。"""
    text, enc = _read(payload)
    result = validator.validate(text, payload.filename, enc)
    audit.record("ingest", "台账校验", result["summary"], ok=result["passed"],
                 detail={"file": payload.filename, "rows": result["rows_total"]})
    return result


@router.post("/preview")
def preview(payload: IngestPayload) -> dict:
    """完整链路：台账校验 + 拓扑校验 + TTL 生成。"""
    ledger_text, ledger_enc = _read(payload.ledger)
    ledger = validator.validate(ledger_text, payload.ledger.filename, ledger_enc)

    topo = None
    if payload.topology is not None:
        topo_text, _ = _read(payload.topology)
        topo = topo_mod.validate(topo_text, ledger["devices_detail"], payload.topology.filename)

    edges = (topo or {}).get("edges") or []
    ttl = rdfgen.to_turtle(ledger["devices_detail"], edges)
    gen_stats = rdfgen.stats(ledger["devices_detail"], edges)

    ready = ledger["passed"] and (topo is None or topo["passed"])
    blocked = []
    if ledger.get("parse_error"):
        blocked.append(f"台账无法解析：{ledger['parse_error']}")
    elif not ledger["passed"]:
        blocked.append(f"台账存在 {len([i for i in ledger['issues'] if i['level'] == 'error'])} 处阻断性问题")
    if topo is not None:
        if topo.get("parse_error"):
            blocked.append(f"拓扑表无法解析：{topo['parse_error']}")
        elif not topo["passed"]:
            blocked.append(f"拓扑表存在 {len([i for i in topo['issues'] if i['level'] == 'error'])} 处阻断性问题")

    audit.record(
        "ingest", "数据接入预览",
        f"台账 {ledger['rows_total']} 行 / 设备 {ledger['devices']} 台 → 三元组约 {gen_stats['estimated_triples']} 条",
        ok=ready,
        detail={"ledger": payload.ledger.filename, "topology": payload.topology.filename if payload.topology else None},
    )

    return {
        "ledger": ledger,
        "topology": topo,
        "turtle": ttl,
        "stats": gen_stats,
        "ready": ready,
        "blocked": blocked,
        "next_step": (
            "校验通过。可由实施人员用生成的 TTL 替换 data/grid-instances.ttl 后重启服务，"
            "推理引擎会自动在新数据上工作；演示环境当前数据不会被改动。"
            if ready else
            "存在阻断性问题，请按问题清单修正台账后重新校验。错误行不会进入 TTL。"
        ),
    }
