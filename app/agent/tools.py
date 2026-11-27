# -*- coding: utf-8 -*-
"""
Agent 可调用的工具集。

设计原则：工具只做「查」和「算」，绝不做「编」。
大模型看到的所有数字都来自这里，答案里出现的数据必须能在工具返回中找到出处。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from ..reasoner.engine import ReasoningEngine, get_engine
from ..reasoner.rules import RULES

# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------


def simulate_switch(engine: ReasoningEngine, switch_id: str, state: str = "open") -> dict:
    """模拟开关分合，返回失电范围。"""
    result = engine.outage(switch_id, state)
    return {
        "switch": result["switch"],
        "states": result["states"],
        "households": result["households"],
        "total_households": result["total_households"],
        "ratio": result["ratio"],
        "districts": [
            {"id": d["id"], "label": d["label"], "households": d["households"], "feeder": d["feeder"]}
            for d in result["affected_districts"]
        ],
        "transformers": [t["id"] for t in result["de_energized_transformers"]],
        "customers": result["customers"],
        "critical_customers": result["critical_customers"],
        "affected_feeders": result["affected_feeders"],
        "trace": result["trace"],
    }


def plan_transfer(
    engine: ReasoningEngine,
    switch_id: str = "",
    state: str = "open",
    target_feeder_load_kw: float | None = None,
) -> dict:
    """给定开关动作，给出转供方案与可行性。"""
    override = {}
    if target_feeder_load_kw is not None:
        # 该参数用于模拟对侧馈线的高峰负荷，属于场景假设而非数据修改
        for f in engine.model.feeders:
            override[f.id] = float(target_feeder_load_kw)
    result = engine.transfer_plans(
        switch_id or None,
        state,
        feeder_load_override=override or None,
    )
    return {
        "plans": [
            {k: p[k] for k in (
                "id", "via_switch", "via_switch_label", "from_feeder", "to_feeder", "to_feeder_label",
                "transferred_kw", "restored_households", "restored_districts",
                "target_load_before_kw", "target_load_after_kw", "target_rate_before", "target_rate_after",
                "spare_kw", "spare_after_kw", "gap_kw", "feasible", "reason", "recheck",
            )}
            for p in result["plans"]
        ],
        "feasible_count": result["feasible_count"],
        "message": result["message"],
        "trace": result["trace"],
    }


def check_overload(engine: ReasoningEngine) -> dict:
    r = engine.load_report()
    return {
        "overloaded": r["overloaded"],
        "heavy": r["heavy"],
        "feeders": r["feeders"],
        "transformers": r["transformers"],
        "trace": r["trace"],
    }


def query_device(engine: ReasoningEngine, keyword: str) -> dict:
    hits = engine.device_info(keyword)
    return {"keyword": keyword, "count": len(hits), "devices": hits}


def get_ontology(engine: ReasoningEngine, class_name: str = "") -> dict:
    """返回本体结构信息，供「你有哪些类/公理」这类问题使用。"""
    g = engine.model.graph
    from rdflib import RDF, RDFS
    from ..reasoner.graph import ONTO

    classes = []
    for s in set(g.subjects(RDF.type, None)):
        if (s, RDF.type, None) and str(s).startswith(str(ONTO)) and \
                any(o == RDF.Class or str(o).endswith("#Class") for o in g.objects(s, RDF.type)):
            label = g.value(s, RDFS.label)
            comment = g.value(s, RDFS.comment)
            classes.append({
                "name": str(s).split("#")[-1],
                "label": str(label) if label else "",
                "comment": str(comment) if comment else "",
            })
    if class_name:
        key = class_name.strip()
        matched = [c for c in classes if key in c["name"] or key in c["label"]]
        return {"matched": matched}

    axioms = []
    seen = set()
    for s, p, o in g.triples((None, ONTO.axiomId, None)):
        ax_id = str(o)
        if ax_id in seen:
            continue
        seen.add(ax_id)
        axioms.append({
            "id": ax_id,
            "label": str(g.value(s, RDFS.comment) or ""),
            "rationale": str(g.value(s, ONTO.rationale) or ""),
        })
    return {
        "class_count": len(classes),
        "classes": sorted(classes, key=lambda c: c["name"]),
        "axioms": sorted(axioms, key=lambda a: a["id"]),
    }


def list_rules(engine: ReasoningEngine = None) -> dict:
    return {"count": len(RULES), "rules": RULES}


def maintenance_scope(engine: ReasoningEngine, order_id: str) -> dict:
    r = engine.maintenance_scope(order_id)
    return {
        "order": r["order"],
        "order_label": r.get("order_label"),
        "equipment": r["equipment"],
        "switch": r["switch"],
        "households": r["outage"]["households"] if r.get("outage") else None,
        "districts": [d["label"] for d in r["outage"]["affected_districts"]] if r.get("outage") else [],
        "feasible_transfer": r["transfer"]["feasible_count"] if r.get("transfer") else 0,
        "trace": r["trace"],
    }


def fault_analysis(engine: ReasoningEngine, fault_id: str) -> dict:
    r = engine.fault_chain(fault_id)
    return {
        "fault": r.get("fault"),
        "fault_label": r.get("fault_label"),
        "equipment": r.get("equipment"),
        "trip_switch": r.get("trip_switch"),
        "households": r["outage"]["households"] if r.get("outage") else None,
        "districts": [d["label"] for d in r["outage"]["affected_districts"]] if r.get("outage") else [],
        "backfeed_suggestion": r.get("backfeed_suggestion"),
        "trace": r.get("trace", []),
    }


def consistency_check(engine: ReasoningEngine) -> dict:
    r = engine.consistency_check()
    return {"passed": r["passed"], "issues": r["issues"], "checks": r["checks"], "summary": r["summary"],
            "trace": r["trace"]}


def transformer_relief(engine: ReasoningEngine, transformer_id: str) -> dict:
    return engine.transformer_relief(transformer_id)


# ---------------------------------------------------------------------------
# 工具注册表：名字 → (实现, 参数校验, 说明)
# ---------------------------------------------------------------------------
TOOL_IMPLS: Dict[str, Callable[..., dict]] = {
    "simulate_switch": simulate_switch,
    "plan_transfer": plan_transfer,
    "check_overload": check_overload,
    "query_device": query_device,
    "get_ontology": get_ontology,
    "list_rules": list_rules,
    "maintenance_scope": maintenance_scope,
    "fault_analysis": fault_analysis,
    "consistency_check": consistency_check,
    "transformer_relief": transformer_relief,
}

# OpenAI function-calling 格式的工具声明
TOOL_SCHEMAS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "simulate_switch",
            "description": "模拟某个开关分闸或合闸，返回失电台区、户数、受影响用户与重要用户。用于回答「XX 断开后哪些用户失电」。",
            "parameters": {
                "type": "object",
                "properties": {
                    "switch_id": {"type": "string", "description": "开关编号，如 S1、S2、BR1、L1"},
                    "state": {"type": "string", "enum": ["open", "closed"], "description": "目标状态，open=分闸，closed=合闸"},
                },
                "required": ["switch_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_transfer",
            "description": "在指定开关动作的前提下，计算负荷转供方案与可行性（含对侧馈线容量校验、转供后负载率、容量缺口）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "switch_id": {"type": "string", "description": "导致失电的开关编号"},
                    "state": {"type": "string", "enum": ["open", "closed"]},
                    "target_feeder_load_kw": {
                        "type": "number",
                        "description": "可选。模拟对侧馈线的高峰负荷（kW），用于检验重载工况下的转供能力",
                    },
                },
                "required": ["switch_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_overload",
            "description": "列出所有配变与馈线的负载率，返回过载清单、重载清单。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_device",
            "description": "按编号或名称模糊查询设备，返回类型、属性、负载率等。",
            "parameters": {
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "设备编号或名称关键字"}},
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ontology",
            "description": "查询本体模型结构：类清单、公理清单；也可传入类名查看匹配的类。",
            "parameters": {
                "type": "object",
                "properties": {"class_name": {"type": "string", "description": "可选，类名关键字"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_rules",
            "description": "列出 12 条推理规则的编号、名称、依赖公理与输入输出。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "maintenance_scope",
            "description": "按工单编号推算计划检修的停电范围、影响户数与是否可转供。",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string", "description": "工单编号，如 GD_2026_018"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fault_analysis",
            "description": "分析故障记录：定位设备、推断跳闸开关、算出停电范围与转供建议。",
            "parameters": {
                "type": "object",
                "properties": {"fault_id": {"type": "string", "description": "故障编号，如 F1"}},
                "required": ["fault_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consistency_check",
            "description": "执行本体基数约束与数据口径校验，返回数据质量问题。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transformer_relief",
            "description": "配变过载时的处置分析：能否靠转供解决、需要切多少负荷、建议增容容量。",
            "parameters": {
                "type": "object",
                "properties": {"transformer_id": {"type": "string", "description": "配变编号，如 TR_B"}},
                "required": ["transformer_id"],
            },
        },
    },
]

TOOL_DOCS = {t["function"]["name"]: t["function"]["description"] for t in TOOL_SCHEMAS}


def call_tool(name: str, arguments: Dict[str, Any]) -> dict:
    """统一入口，异常在这里收敛成结构化错误，避免把栈信息抛给模型。"""
    if name not in TOOL_IMPLS:
        return {"error": f"未注册的工具：{name}"}
    engine = get_engine()
    try:
        fn = TOOL_IMPLS[name]
        if name in ("list_rules", "check_overload", "consistency_check", "get_ontology"):
            result = fn(engine, **(arguments or {}))
        else:
            result = fn(engine, **(arguments or {}))
        return result
    except TypeError as exc:
        return {"error": f"参数不正确：{exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
