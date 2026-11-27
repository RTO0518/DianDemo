# -*- coding: utf-8 -*-
"""
台账 → RDF 三元组。

这一步是「数据接入」和「知识图谱」之间那条线，也是甲方最想看的东西：
一份 Excel 台账，怎么变成可以推理的图。

只生成片段用于预览与核对，不做自动替换：
演示环境的运行数据是精心构造的验证用例，被一份还没确认的台账覆盖掉，
会连带让 8 道验证题全部失效。确认无误后由实施人员替换 data/grid-instances.ttl 并重启，
这是有意的工程约束，不是能力缺失。
"""
from __future__ import annotations

from typing import Dict, List

PREFIX = """@prefix data: <http://grid.ontology.local/data#> .
@prefix grid: <http://grid.ontology.local/onto#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
"""

# 需要同时断言父类的多继承类型（与本体定义一致，让 OWL 闭包能正确归类）
EXTRA_TYPES: Dict[str, List[str]] = {
    "SectionSwitch": ["Switch"],
    "TieSwitch": ["Switch"],
    "Breaker": ["Switch"],
    "ResidentialCustomer": ["Customer"],
    "CommercialCustomer": ["Customer"],
}

# 各类型的上级关系谓词
PARENT_PREDICATE: Dict[str, str] = {
    "Feeder": "belongsToSubstation",
    "LineSection": "upstreamOf",
    "DistributionTransformer": "locatedOn",
    "TransformerDistrict": "hasTransformer",
    "ResidentialCustomer": "servedByDistrict",
    "CommercialCustomer": "servedByDistrict",
}


def _q(text: str) -> str:
    """字符串转义：反斜杠、引号、换行。"""
    return (text or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _f(value: float) -> str:
    return f'"{float(value):g}"^^xsd:float'


def to_turtle(devices: List[dict], topology: List[dict] | None = None) -> str:
    """
    devices   = validate() 返回的 devices_detail（只取无错误的行）
    topology  = [{"from": "LS1", "to": "LS2", "switch": "S1"}]，用于补全开关两端
    """
    lines: List[str] = [PREFIX]
    topo = topology or []

    # 开关两端：从拓扑表反查
    switch_ends: Dict[str, Dict[str, str]] = {}
    for edge in topo:
        sid = (edge.get("switch") or "").strip()
        if not sid:
            continue
        slot = switch_ends.setdefault(sid, {})
        slot.setdefault("from", (edge.get("from") or "").strip())
        slot.setdefault("to", (edge.get("to") or "").strip())

    for d in devices:
        if d.get("errors", 0):
            continue
        cls = d.get("cls") or ""
        if not cls:
            continue
        code = d["code"]
        types = [cls] + EXTRA_TYPES.get(cls, [])
        lines.append(f"data:{code} a " + ",\n        ".join(f"grid:{t}" for t in types) + " ;")
        lines.append(f'    grid:code "{_q(code)}" ;')
        if d.get("name"):
            lines.append(f'    rdfs:label "{_q(d["name"])}"@zh ;')

        parent = (d.get("parent") or "").strip()
        feeder = (d.get("feeder") or "").strip()
        if parent and cls in PARENT_PREDICATE:
            lines.append(f"    grid:{PARENT_PREDICATE[cls]} data:{parent} ;")
        if feeder and cls != "Feeder":
            lines.append(f"    grid:belongsToFeeder data:{feeder} ;")

        if d.get("rated_kw") is not None:
            lines.append(f"    grid:ratedCapacityKW {_f(d['rated_kw'])} ;")
        if d.get("load_kw") is not None:
            lines.append(f"    grid:currentLoadKW {_f(d['load_kw'])} ;")
        if d.get("households") is not None:
            lines.append(f"    grid:householdCount {int(d['households'])} ;")
        if d.get("voltage_kv") is not None:
            lines.append(f"    grid:voltageLevelKV {_f(d['voltage_kv'])} ;")
        if cls in ("ResidentialCustomer", "CommercialCustomer"):
            lines.append(f"    grid:isCriticalLoad {'true' if d.get('is_critical') else 'false'} ;")
        if cls in ("SectionSwitch", "TieSwitch", "Breaker"):
            state = "ClosedState" if d.get("state") == "closed" else "OpenState"
            lines.append(f"    grid:hasOperatingState grid:{state} ;")
            lines.append(f"    grid:normallyOpen {'true' if d.get('normally_open') else 'false'} ;")
            ends = switch_ends.get(code)
            if ends and ends.get("from") and ends.get("to"):
                lines.append(f"    grid:connectsFrom data:{ends['from']} ;")
                lines.append(f"    grid:connectsTo data:{ends['to']} ;")
            else:
                lines.append("    # TODO 开关两端（connectsFrom / connectsTo）需由拓扑关系表补充，"
                             "否则拓扑仿真无法把它插入到线路上")
        if d.get("address"):
            lines.append(f'    grid:address "{_q(d["address"])}"@zh ;')

        # 收尾：把最后一个分号换成句点
        lines[-1] = lines[-1].rstrip()
        if lines[-1].endswith(";"):
            lines[-1] = lines[-1][:-1].rstrip() + " ."
        else:
            lines.append("    .")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def stats(devices: List[dict], topology: List[dict] | None = None) -> dict:
    usable = [d for d in devices if not d.get("errors", 0) and d.get("cls")]
    skip = [d for d in devices if d.get("errors", 0)]
    return {
        "device_count": len(usable),
        "skipped": len(skip),
        "skipped_codes": [d["code"] for d in skip][:20],
        "switch_edges": len([e for e in (topology or []) if (e.get("switch") or "").strip()]),
        "estimated_triples": sum(
            5
            + (1 if d.get("parent") else 0)
            + (1 if d.get("feeder") else 0)
            + (2 if d.get("rated_kw") is not None else 0)
            + (1 if d.get("households") is not None else 0)
            + (1 if d.get("is_critical") else 0)
            + (3 if d.get("cls") in ("SectionSwitch", "TieSwitch", "Breaker") else 0)
            for d in usable
        ),
    }
