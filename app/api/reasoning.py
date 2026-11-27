# -*- coding: utf-8 -*-
"""推理相关接口：失电、转供、过载、检修、故障、一致性、验证与 OWL 自检。"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException

from .. import audit
from ..reasoner.engine import get_engine
from ..reasoner.owl import owl_self_check
from ..reasoner.rules import RULES
from ..reasoner.thresholds import get_thresholds
from ..reasoner.verify import run_all
from .schemas import (
    FaultRequest,
    OrderRequest,
    ReliefRequest,
    SwitchAction,
    ThresholdUpdate,
    TransferRequest,
)

router = APIRouter()


def _public(result: dict) -> dict:
    """去掉内部以 _ 开头的字段，避免把图对象序列化出去。"""
    return {k: v for k, v in result.items() if not k.startswith("_")}


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)


def _rules_of(result: dict) -> list:
    return sorted({s.get("rule") for s in (result.get("trace") or []) if s.get("rule")})


def _log(action: str, headline: str, result: dict, t0: float, detail: Optional[dict] = None,
         ok: bool = True) -> None:
    audit.record(
        "reasoning", action, headline, rules=_rules_of(result) if ok else [],
        elapsed_ms=_ms(t0), ok=ok, detail=detail,
    )


@router.get("/rules")
def rules() -> dict:
    return {"count": len(RULES), "rules": RULES}


@router.get("/thresholds")
def read_thresholds() -> dict:
    """当前生效的推理阈值 + 基线值 + 每项的允许区间与影响规则。"""
    return get_thresholds().describe()


@router.put("/thresholds")
def write_thresholds(payload: ThresholdUpdate) -> dict:
    """运行期调整阈值：改完立即影响过载判定与转供可行性，重启回到配置文件值。"""
    values, errors = get_thresholds().update(payload.values)
    if errors:
        raise HTTPException(400, "；".join(errors))
    return {"values": values, "thresholds": get_thresholds().describe()}


@router.post("/thresholds/reset")
def reset_thresholds() -> dict:
    get_thresholds().reset()
    return get_thresholds().describe()


@router.post("/outage")
def outage(payload: SwitchAction) -> dict:
    t0 = time.time()
    try:
        data = _public(get_engine().outage(payload.switch_id, payload.state))
    except ValueError as exc:
        audit.record("reasoning", "失电分析", f"失败：{exc}", elapsed_ms=_ms(t0), ok=False,
                     detail=payload.model_dump())
        raise HTTPException(400, str(exc)) from exc
    _log("失电分析", f"{payload.switch_id} {payload.state} → 失电 {data['households']} 户",
         data, t0, payload.model_dump())
    return data


@router.post("/transfer")
def transfer(payload: TransferRequest) -> dict:
    t0 = time.time()
    engine = get_engine()
    override = None
    if payload.target_feeder_load_kw is not None:
        override = {f.id: float(payload.target_feeder_load_kw) for f in engine.model.feeders}
    try:
        if payload.states:
            data = engine.transfer_plans(state_overrides=payload.states, feeder_load_override=override)
        else:
            data = engine.transfer_plans(payload.switch_id or None, payload.state,
                                         feeder_load_override=override)
    except ValueError as exc:
        audit.record("reasoning", "转供分析", f"失败：{exc}", elapsed_ms=_ms(t0), ok=False,
                     detail=payload.model_dump())
        raise HTTPException(400, str(exc)) from exc
    _log("转供分析",
         f"候选 {len(data['plans'])} 个，可行 {data['feasible_count']} 个",
         data, t0, {**payload.model_dump(), "plans": len(data["plans"])})
    return data


@router.get("/overload")
def overload() -> dict:
    return get_engine().load_report()


@router.post("/relief")
def relief(payload: ReliefRequest) -> dict:
    t0 = time.time()
    try:
        data = get_engine().transformer_relief(payload.transformer_id)
    except ValueError as exc:
        audit.record("reasoning", "过载处置", f"失败：{exc}", elapsed_ms=_ms(t0), ok=False,
                     detail=payload.model_dump())
        raise HTTPException(400, str(exc)) from exc
    _log("过载处置", f"{payload.transformer_id} 超出 {data['excess_kw']:.0f} kW", data, t0,
         payload.model_dump())
    return data


@router.post("/maintenance")
def maintenance(payload: OrderRequest) -> dict:
    t0 = time.time()
    try:
        result = get_engine().maintenance_scope(payload.order_id)
    except ValueError as exc:
        audit.record("reasoning", "检修停电推算", f"失败：{exc}", elapsed_ms=_ms(t0), ok=False,
                     detail=payload.model_dump())
        raise HTTPException(400, str(exc)) from exc
    if result.get("outage"):
        result["outage"] = _public(result["outage"])
    _log("检修停电推算",
         f"{payload.order_id} → 断开 {result.get('switch')}，停电 "
         f"{(result.get('outage') or {}).get('households', 0)} 户",
         result, t0, payload.model_dump())
    return result


@router.post("/fault")
def fault(payload: FaultRequest) -> dict:
    t0 = time.time()
    try:
        result = get_engine().fault_chain(payload.fault_id)
    except ValueError as exc:
        audit.record("reasoning", "故障链路分析", f"失败：{exc}", elapsed_ms=_ms(t0), ok=False,
                     detail=payload.model_dump())
        raise HTTPException(400, str(exc)) from exc
    if result.get("outage"):
        result["outage"] = _public(result["outage"])
    _log("故障链路分析",
         f"{payload.fault_id} → 跳闸 {result.get('trip_switch')}，停电 "
         f"{(result.get('outage') or {}).get('households', 0)} 户",
         result, t0, payload.model_dump())
    return result


@router.get("/consistency")
def consistency() -> dict:
    return get_engine().consistency_check()


@router.get("/verify")
def verify() -> dict:
    report = run_all(get_engine())
    return report


@router.post("/owl-check")
def owl_check(payload: Optional[SwitchAction] = None) -> dict:
    """把指定工况下的带电/失电结论断言回 OWL，做不相交自检。"""
    engine = get_engine()
    if payload is None:
        result = engine.outage()
    else:
        result = engine.outage(payload.switch_id, payload.state)
    load = engine.load_report()
    check = owl_self_check(
        sorted(result["_energized"]),
        [d["id"] for d in result["de_energized"]],
        [r["id"] for r in load["overloaded"]],
        [r["id"] for r in load["heavy"]],
    )
    return {
        "switch": payload.model_dump() if payload else None,
        "households": result["households"],
        "owl": check,
    }
