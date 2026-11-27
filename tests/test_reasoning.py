# -*- coding: utf-8 -*-
"""推理能力测试：8 道验证题必须全部通过，规则边界必须可复现。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.reasoner.engine import get_engine  # noqa: E402
from app.reasoner.owl import owl_self_check  # noqa: E402
from app.reasoner.rules import RULES  # noqa: E402
from app.reasoner.verify import QUESTIONS, run_all  # noqa: E402


@pytest.fixture(scope="module")
def engine():
    return get_engine(refresh=True)


def test_rule_catalog_has_twelve_rules() -> None:
    assert len(RULES) == 12
    ids = [r["id"] for r in RULES]
    assert ids == sorted(ids, key=lambda x: int(x[1:]))
    for rule in RULES:
        assert rule["name"] and rule["description"] and rule["output"]
        assert rule["axioms"], f"{rule['id']} 未关联任何公理"


def test_all_verification_questions_pass(engine) -> None:
    report = run_all(engine)
    failed = [f"{r['id']}: {r['actual']}" for r in report["results"] if not r["passed"]]
    assert report["passed"] == report["total"], "未通过：" + " | ".join(failed)
    assert len(QUESTIONS) >= 5


def test_s1_open_causes_exactly_1200_households(engine) -> None:
    r = engine.outage("S1", "open")
    assert r["households"] == 1200
    assert sorted(d["id"] for d in r["affected_districts"]) == ["TA", "TB", "TC"]
    # 上游台区不受影响：这是「传播」而非「全停」的关键证据
    assert "TD" not in {d["id"] for d in r["affected_districts"]}


def test_closing_tie_switch_restores_power(engine) -> None:
    plans = engine.transfer_plans("S1", "open")
    assert plans["feasible_count"] == 1
    p = plans["plans"][0]
    assert p["via_switch"] == "L1"
    assert p["transferred_kw"] == 1000
    assert p["restored_households"] == 1200
    assert p["recheck"]["remaining_households"] == 0
    assert p["target_rate_after"] == pytest.approx(0.45)


def test_transfer_rejected_when_target_feeder_is_heavy(engine) -> None:
    plans = engine.transfer_plans("S1", "open", feeder_load_override={"FY": 3400})
    p = plans["plans"][0]
    assert p["feasible"] is False
    assert p["gap_kw"] == 400
    assert "缺口" in p["reason"]


def test_breaker_trip_kills_whole_feeder(engine) -> None:
    r = engine.outage("BR1", "open")
    assert r["households"] == 1830
    assert r["affected_feeders"] == ["FX"]


def test_overload_classification(engine) -> None:
    report = engine.load_report()
    assert [x["id"] for x in report["overloaded"]] == ["TR_B"]
    assert [x["id"] for x in report["heavy"]] == ["TR_F"]
    assert round(report["overloaded"][0]["rate"], 2) == 1.05


def test_maintenance_order_scope(engine) -> None:
    r = engine.maintenance_scope("GD_2026_018")
    assert r["switch"] == "S2"
    assert r["outage"]["households"] == 300
    assert r["transfer"]["feasible_count"] == 0


def test_transformer_relief_has_no_transfer_path(engine) -> None:
    r = engine.transformer_relief("TR_B")
    assert r["can_transfer"] is False
    assert r["excess_kw"] == 20
    assert r["options"][0]["action"] == "切负荷"


def test_fault_chain_links_trip_switch_and_backfeed(engine) -> None:
    r = engine.fault_chain("F1")
    assert r["trip_switch"] == "S1"
    assert r["outage"]["households"] == 1200
    assert "L1" in r["backfeed_suggestion"]


def test_consistency_check_passes(engine) -> None:
    r = engine.consistency_check()
    assert r["passed"] is True
    assert r["issues"] == []


def test_owl_self_check_detects_no_conflict(engine) -> None:
    r = engine.outage("S1", "open")
    load = engine.load_report()
    result = owl_self_check(
        sorted(r["_energized"]),
        [d["id"] for d in r["de_energized"]],
        [x["id"] for x in load["overloaded"]],
        [x["id"] for x in load["heavy"]],
    )
    assert result["passed"] is True
    assert result["conflicts"] == []
    assert result["inferred_triples"] > 0
    assert result["spot_check"]["inherited_power_equipment"] is True


def test_unknown_switch_raises(engine) -> None:
    with pytest.raises(ValueError):
        engine.outage("S99", "open")


def test_illegal_state_raises(engine) -> None:
    with pytest.raises(ValueError):
        engine.outage("S1", "half-open")


def test_state_override_applies_multiple_switches(engine) -> None:
    r = engine.outage(state_overrides={"S1": "open", "S2": "open"})
    assert r["households"] == 1200
    r2 = engine.outage(state_overrides={"S2": "open"})
    assert r2["households"] == 300


def test_energized_path_explains_supply(engine) -> None:
    path = engine.energized_path("TR_A")
    assert path, "应能给出 TR_A 的供电路径"
    assert any(step["via_switch"] == "S1" for step in path)

def test_transfer_rejects_negative_feeder_load(engine) -> None:
    """负的模拟负荷会让容量校验失去意义，必须在引擎层就拒绝（不只靠接口层 schema）。"""
    with pytest.raises(ValueError, match="不能为负"):
        engine.transfer_plans("S1", "open", feeder_load_override={"FY": -100.0})


def test_transfer_rejects_unknown_feeder_in_override(engine) -> None:
    with pytest.raises(ValueError, match="不是有效馈线"):
        engine.transfer_plans("S1", "open", feeder_load_override={"NOT_A_FEEDER": 100.0})
