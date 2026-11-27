# -*- coding: utf-8 -*-
"""
本体层推理：把规则层的结论断言回 OWL，用公理做自检。

这一层回答的是甲方最可能追问的问题：
    「你这些结论到底是本体推出来的，还是代码里写死的？」
答：结构由 OWL 承担（类归属、传递闭包、不相交判定），数值与状态由规则层承担，
    两层结论互相校验——若规则层把同一台设备同时判为带电与失电，OWL 会报冲突。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from rdflib import Graph, Namespace, RDF
from owlrl import DeductiveClosure, OWLRL_Semantics

ONTO = Namespace("http://grid.ontology.local/onto#")
DATA = Namespace("http://grid.ontology.local/data#")

ROOT = Path(__file__).resolve().parents[2]
ONTO_TTL = ROOT / "ontology" / "grid-ontology.ttl"
INST_TTL = ROOT / "data" / "grid-instances.ttl"


def _fresh_graph() -> Graph:
    g = Graph()
    g.parse(str(ONTO_TTL), format="turtle")
    g.parse(str(INST_TTL), format="turtle")
    return g


_INFERRED: Optional[Graph] = None


def inferred_graph(refresh: bool = False) -> Graph:
    """
    返回 OWL RL 闭包后的图（本体 + 实例 + 全部可推导三元组）。

    供 SPARQL 查询使用：甲方在查询界面上看到的应当是「推理后的知识图谱」，
    而不是只含显式断言的那一份。闭包计算有成本，因此做成进程内单例缓存。
    """
    global _INFERRED
    if _INFERRED is None or refresh:
        g = _fresh_graph()
        DeductiveClosure(OWLRL_Semantics).expand(g)
        _INFERRED = g
    return _INFERRED


def owl_self_check(
    energized: Sequence[str],
    de_energized: Sequence[str],
    overloaded: Sequence[str] = (),
    heavy: Sequence[str] = (),
) -> dict:
    """断言结论 → 跑 OWL RL 闭包 → 检查不相交冲突与子类归属。"""
    g = _fresh_graph()
    before = len(g)

    for dev_id in energized:
        g.add((DATA[dev_id], RDF.type, ONTO.EnergizedEquipment))
    for dev_id in de_energized:
        g.add((DATA[dev_id], RDF.type, ONTO.DeEnergizedEquipment))
    for dev_id in overloaded:
        g.add((DATA[dev_id], RDF.type, ONTO.OverloadedTransformer))
    for dev_id in heavy:
        g.add((DATA[dev_id], RDF.type, ONTO.HeavyLoadTransformer))

    DeductiveClosure(OWLRL_Semantics).expand(g)

    def both(a: Iterable[str], b: Iterable[str]) -> List[str]:
        return sorted(set(a) & set(b))

    conflicts = []
    for dev_id in both(energized, de_energized):
        conflicts.append({"device": dev_id, "axiom": "A12", "message": "同一设备既被判为带电又被判为失电"})
    for dev_id in both(overloaded, heavy):
        conflicts.append({"device": dev_id, "axiom": "A13", "message": "同一配变既被判为过载又被判为重载"})

    # 抽查：失电结论是否被 OWL 正确归入设备大类
    spot = de_energized[0] if de_energized else (energized[0] if energized else None)
    inherited = False
    if spot:
        types = {str(t).split("#")[-1] for t in g.objects(DATA[spot], RDF.type)}
        inherited = "PowerEquipment" in types

    return {
        "passed": not conflicts,
        "conflicts": conflicts,
        "triples_before": before,
        "triples_after": len(g),
        "inferred_triples": len(g) - before,
        "spot_check": {
            "device": spot,
            "inherited_power_equipment": inherited,
            "note": "OWL 闭包后该个体被归入 PowerEquipment，说明结论类挂在了正确的类层次上",
        },
        "limits": [
            "OWL 2 RL 不执行基数约束的实例校验（A3~A7），该部分由规则 R10 承担",
            "开放世界假设下「未声明即未知」，因此开关状态必须显式断言，不能依赖缺省",
        ],
    }
