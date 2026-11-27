# -*- coding: utf-8 -*-
"""电网拓扑与运行方式接口。"""
from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, HTTPException

from ..reasoner.engine import get_engine
from ..reasoner.rules import bfs_energized, classify_load, load_rate
from .schemas import WorkState

router = APIRouter()


@router.get("/overview")
def overview() -> dict:
    return get_engine().overview()


@router.get("/topology")
def topology() -> dict:
    """单线图数据：开关作为可点击节点插入到它所在的边上。"""
    engine = get_engine()
    m = engine.model
    energized, _ = bfs_energized(m, m.state)

    nodes: List[dict] = []
    edges: List[dict] = []

    for dev in m.devices.values():
        if dev.kind not in ("substation", "feeder", "section", "transformer", "district"):
            continue
        item = {
            "id": dev.id,
            "label": dev.label,
            "type": dev.kind,
            "cls": dev.cls,
            "energized": dev.id in energized,
            "feeder": m.feeder_of.get(dev.id),
        }
        if dev.kind == "transformer":
            rated = m.num(dev, "ratedCapacityKW")
            load = m.num(dev, "currentLoadKW")
            rate = load_rate(load, rated)
            item.update({
                "rated_kw": rated, "load_kw": load, "rate": round(rate, 4),
                "status": classify_load(rate),
                "district": m.district_of_transformer.get(dev.id),
            })
            did = m.district_of_transformer.get(dev.id)
            if did and did in m.devices:
                item["households"] = int(m.num(m.devices[did], "householdCount"))
        elif dev.kind == "district":
            item.update({"households": int(m.num(dev, "householdCount"))})
        elif dev.kind == "feeder":
            rated = m.num(dev, "ratedCapacityKW")
            load = m.num(dev, "currentLoadKW")
            item.update({"rated_kw": rated, "load_kw": load, "rate": round(load_rate(load, rated), 4)})
        nodes.append(item)

    for sw in m.switches.values():
        a, b = sw.connects
        sw_node = f"SW_{sw.id}"
        nodes.append({
            "id": sw_node,
            "label": sw.label,
            "type": "switch",
            "cls": sw.cls_name,
            "code": sw.id,
            "state": sw.state,
            "normally_open": sw.normally_open,
            "energized": (a in energized) or (b in energized),
            "feeder": sw.feeder,
            "connects": [a, b],
        })
        edges.append({"source": a, "target": sw_node, "kind": "line"})
        edges.append({"source": sw_node, "target": b, "kind": "line"})

    # 没有开关的直连边（如配变挂接、馈线—变电站）
    switch_pairs = {frozenset(sw.connects) for sw in m.switches.values()}
    for e in m.edges:
        if e.switch_id:
            continue
        if frozenset((e.src, e.dst)) in switch_pairs:
            continue
        edges.append({"source": e.src, "target": e.dst, "kind": "fixed"})

    return {
        "nodes": nodes,
        "edges": edges,
        "census": m.census(),
        "states": m.state,
    }


@router.get("/device/{dev_id}")
def device(dev_id: str) -> dict:
    try:
        return get_engine().describe(dev_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/path/{dev_id}")
def supply_path(dev_id: str) -> dict:
    return {"device": dev_id, "path": get_engine().energized_path(dev_id)}


@router.post("/workstate")
def work_state(payload: WorkState) -> dict:
    """按给定的开关状态组合计算工况，用于拓扑页的交互式分合闸。"""
    engine = get_engine()
    try:
        result = engine.outage(state_overrides=payload.states)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {k: v for k, v in result.items() if not k.startswith("_")}
