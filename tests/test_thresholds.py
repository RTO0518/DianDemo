# -*- coding: utf-8 -*-
"""
阈值配置测试。

重点不是「默认值是不是 80%」，而是三条工程约束：
    1. 改阈值必须真的改变结论（否则配置是个摆设）；
    2. 非法值必须被拒绝，不能悄悄生效；
    3. 基准验证题不受运行时阈值影响（否则演示时调一下滑杆，验证页就全红了）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.reasoner.rules import classify_load  # noqa: E402
from app.reasoner.thresholds import META, get_thresholds  # noqa: E402
from app.reasoner.verify import run_all  # noqa: E402


@pytest.fixture()
def th():
    engine_th = get_thresholds()
    snapshot = engine_th.current()
    yield engine_th
    engine_th.restore(snapshot)


def test_baseline_values_loaded_from_file(th) -> None:
    base = th.current(baseline=True)
    assert base["overload_rate"] == 1.00
    assert base["heavy_rate"] == 0.80
    assert 0 < base["heavy_rate"] < base["overload_rate"]


def test_meta_covers_every_threshold(th) -> None:
    keys = {m["key"] for m in META}
    assert keys == set(th.current().keys())
    for m in META:
        assert m["label"] and m["description"] and m["rules"]
        assert m["min"] < m["max"]


def test_update_takes_effect_immediately(th) -> None:
    values, errors = th.update({"overload_rate": 0.9})
    assert errors == []
    assert values["overload_rate"] == 0.9
    assert classify_load(0.95) == "overloaded"   # 95% 在 90% 阈值下是过载
    th.update({"overload_rate": 1.0})
    assert classify_load(0.95) == "heavy"        # 回到 100% 阈值后是重载


def test_out_of_range_rejected(th) -> None:
    before = th.current()
    for bad in ({"overload_rate": 9.9}, {"heavy_rate": 0.01}, {"overload_rate": -1}):
        values, errors = th.update(bad)
        assert errors, f"{bad} 应当被拒绝"
        assert values == before, "被拒绝的修改不应产生任何部分生效"


def test_unknown_key_rejected(th) -> None:
    _, errors = th.update({"not_a_threshold": 0.5})
    assert errors and "未知" in errors[0]


def test_non_numeric_rejected(th) -> None:
    _, errors = th.update({"overload_rate": "abc"})
    assert errors


def test_heavy_must_be_below_overload(th) -> None:
    _, errors = th.update({"heavy_rate": 1.2, "overload_rate": 1.0})
    assert errors and "低于" in errors[0]


def test_reset_returns_to_file_baseline(th) -> None:
    th.update({"overload_rate": 1.5})
    assert th.get("overload_rate") == 1.5
    th.reset()
    assert th.get("overload_rate") == th.current(baseline=True)["overload_rate"]


def test_verification_uses_baseline_not_runtime(th) -> None:
    """把阈值调到 90% 后，8 道基准题仍应按基线口径全部通过。"""
    th.update({"overload_rate": 0.9, "heavy_rate": 0.85})
    report = run_all()
    assert report["passed"] == report["total"], report["summary"]
    # 跑完必须把现场阈值放回去
    assert th.get("overload_rate") == 0.9


def test_transfer_reserve_reduces_spare_capacity(th) -> None:
    """预留容量比例提高后，可用备用必须变小，且与手算一致。"""
    from app.reasoner.engine import get_engine

    engine = get_engine()
    th.update({"capacity_reserve_rate": 0.0})
    plan0 = engine.transfer_plans("S1", "open")["plans"][0]

    th.update({"capacity_reserve_rate": 0.10})
    plan1 = engine.transfer_plans("S1", "open")["plans"][0]

    rated = engine.model.num(engine.model.devices[plan1["to_feeder"]], "ratedCapacityKW")
    load_before = plan1["target_load_before_kw"]
    assert plan1["spare_kw"] < plan0["spare_kw"]
    assert plan1["spare_kw"] == pytest.approx(rated - load_before - rated * 0.10, abs=0.01)
    assert plan0["spare_kw"] == pytest.approx(rated - load_before, abs=0.01)
