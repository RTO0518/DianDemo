# -*- coding: utf-8 -*-
"""本体相关接口：类层次、属性、公理、关系图、版本。"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import yaml
from fastapi import APIRouter, HTTPException
from rdflib import OWL, RDF, RDFS, URIRef

from ..reasoner.engine import get_engine
from ..reasoner.graph import ONTO

router = APIRouter()

ROOT = Path(__file__).resolve().parents[2]

TOP_BRANCH = {
    "PowerEquipment": "设备",
    "Area": "区域",
    "Customer": "用户",
    "Event": "事件",
    "Measurement": "量测",
    "OperatingState": "状态",
}


def _local(uri) -> str:
    return str(uri).split("#")[-1]


def _class_tree(g) -> List[dict]:
    nodes: Dict[str, dict] = {}
    # 排序遍历：set 的迭代顺序受字符串 hash 随机化影响，不排序会导致
    # 每次重启后类层次图的布局都不一样，演示时讲解人对不上位置
    for s in sorted(set(g.subjects(RDF.type, OWL.Class)), key=str):
        name = _local(s)
        label = g.value(s, RDFS.label)
        comment = g.value(s, RDFS.comment)
        # 一个类可能同时有「父类」和「匿名约束」两种 subClassOf，必须只取指向命名类的那个
        parents = [o for o in g.objects(s, RDFS.subClassOf)
                   if str(o).startswith(str(ONTO)) and (o, RDF.type, OWL.Class) in g]
        parent = parents[0] if parents else None
        parent_name = _local(parent) if parent is not None else None
        nodes[name] = {
            "name": name,
            "label": str(label) if label else name,
            "comment": str(comment) if comment else "",
            "parent": parent_name,
            "children": [],
            "abstract": bool(g.value(s, ONTO.isAbstract) or False),
            "inferred": bool(g.value(s, ONTO.isInferredClass) or False),
        }
    for n in sorted(nodes.values(), key=lambda x: x["name"]):
        if n["parent"] in nodes:
            nodes[n["parent"]]["children"].append(n["name"])
    for n in nodes.values():
        n["children"].sort()
    roots = sorted((n for n in nodes.values() if not n["parent"]), key=lambda x: x["name"])
    return roots, nodes


@router.get("/version")
def version() -> dict:
    """
    本体与实例数据的版本信息。

    甲方做验收和后续维护时最常问的两句是「这版本体是哪一版」「实例数据什么时候的」。
    用内容哈希而不是人工版本号：任何人改了 TTL，哈希就变，无法说谎。
    """
    targets = {
        "ontology_ttl": ROOT / "ontology" / "grid-ontology.ttl",
        "ontology_source": ROOT / "ontology" / "grid-ontology.yaml",
        "instances_ttl": ROOT / "data" / "grid-instances.ttl",
        "thresholds": ROOT / "data" / "thresholds.yaml",
    }
    files: Dict[str, dict] = {}
    for key, path in targets.items():
        if not path.exists():
            continue
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        files[key] = {
            "file": str(path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": digest,
            "short": digest[:12],
            "size": len(raw),
            "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        }

    meta: dict = {}
    src = targets["ontology_source"]
    if src.exists():
        try:
            doc = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
            meta = doc.get("meta", {}) or {}
        except Exception:  # noqa: BLE001
            meta = {}

    engine = get_engine()
    g = engine.model.graph
    counts = {
        "triples": len(g),
        "classes": len(set(g.subjects(RDF.type, OWL.Class))),
        "object_properties": len(set(g.subjects(RDF.type, OWL.ObjectProperty))),
        "data_properties": len(set(g.subjects(RDF.type, OWL.DatatypeProperty))),
        "axioms": len({str(o) for _, _, o in g.triples((None, ONTO.axiomId, None))}),
        "instances": len(engine.model.devices),
    }

    return {
        "ontology_version": meta.get("version", "未标注"),
        "ontology_name": meta.get("name", ""),
        "standards": meta.get("standards", []),
        "files": files,
        "counts": counts,
        "census": engine.model.census(),
        "note": "哈希基于文件内容，任何改动都会改变 short 值；交付时可将此页截图作为版本凭证。",
    }


@router.get("/schema")
def schema() -> dict:
    engine = get_engine()
    g = engine.model.graph

    roots, nodes = _class_tree(g)

    obj_props = []
    for s in g.subjects(RDF.type, OWL.ObjectProperty):
        name = _local(s)
        obj_props.append({
            "name": name,
            "label": str(g.value(s, RDFS.label) or name),
            "comment": str(g.value(s, RDFS.comment) or ""),
            "domain": _local(g.value(s, RDFS.domain)) if g.value(s, RDFS.domain) else "",
            "range": _local(g.value(s, RDFS.range)) if g.value(s, RDFS.range) else "",
            "inverse": _local(g.value(s, OWL.inverseOf)) if g.value(s, OWL.inverseOf) else "",
            "transitive": (s, RDF.type, OWL.TransitiveProperty) in g,
            "functional": (s, RDF.type, OWL.FunctionalProperty) in g,
        })
    obj_props.sort(key=lambda p: p["name"])

    data_props = []
    for s in g.subjects(RDF.type, OWL.DatatypeProperty):
        name = _local(s)
        data_props.append({
            "name": name,
            "label": str(g.value(s, RDFS.label) or name),
            "comment": str(g.value(s, RDFS.comment) or ""),
            "domain": _local(g.value(s, RDFS.domain)) if g.value(s, RDFS.domain) else "",
            "range": _local(g.value(s, RDFS.range)) if g.value(s, RDFS.range) else "",
            "unit": str(g.value(s, ONTO.unit) or ""),
        })
    data_props.sort(key=lambda p: p["name"])

    axioms = []
    seen = set()
    for s, _, o in g.triples((None, ONTO.axiomId, None)):
        ax_id = str(o)
        if ax_id in seen:
            continue
        seen.add(ax_id)
        axioms.append({
            "id": ax_id,
            "label": str(g.value(s, RDFS.label) or g.value(s, RDFS.comment) or ""),
            "rationale": str(g.value(s, ONTO.rationale) or ""),
            "type": str(g.value(s, ONTO.axiomType) or ""),
        })
    axioms.sort(key=lambda a: a["id"])

    return {
        "tree": roots,
        "classes": sorted(nodes.values(), key=lambda n: n["name"]),
        "class_count": len(nodes),
        "object_properties": obj_props,
        "data_properties": data_props,
        "object_property_count": len(obj_props),
        "data_property_count": len(data_props),
        "axioms": axioms,
        "axiom_count": len(axioms),
    }


@router.get("/graph")
def graph_view(include_properties: bool = True) -> dict:
    """返回给前端画关系图的数据：类继承边 + 对象属性边。"""
    engine = get_engine()
    g = engine.model.graph
    _, nodes = _class_tree(g)

    # 分支归属，用于配色
    def branch_of(name: str) -> str:
        cur = name
        seen = set()
        while cur and cur in nodes and cur not in seen:
            seen.add(cur)
            if cur in TOP_BRANCH:
                return TOP_BRANCH[cur]
            cur = nodes[cur]["parent"]
        return "其他"

    degree = defaultdict(int)
    edges = []
    for n in nodes.values():
        if n["parent"] in nodes:
            edges.append({"source": n["parent"], "target": n["name"], "kind": "继承", "label": "subClassOf"})
            degree[n["parent"]] += 1
            degree[n["name"]] += 1

    if include_properties:
        for s in g.subjects(RDF.type, OWL.ObjectProperty):
            dom = g.value(s, RDFS.domain)
            rng = g.value(s, RDFS.range)
            if dom is None or rng is None:
                continue
            d, r = _local(dom), _local(rng)
            if d not in nodes or r not in nodes or d == r:
                continue
            edges.append({
                "source": d, "target": r, "kind": "属性",
                "label": str(g.value(s, RDFS.label) or _local(s)),
                "property": _local(s),
            })
            degree[d] += 1
            degree[r] += 1

    out_nodes = [{
        "id": n["name"],
        "name": n["name"],
        "label": n["label"],
        "category": branch_of(n["name"]),
        "comment": n["comment"],
        "abstract": n["abstract"],
        "inferred": n["inferred"],
        "size": 16 + min(degree[n["name"]] * 3, 22),
    } for n in nodes.values()]

    return {"nodes": out_nodes, "edges": edges, "categories": sorted(set(TOP_BRANCH.values()))}


@router.get("/class/{name}")
def class_detail(name: str) -> dict:
    engine = get_engine()
    g = engine.model.graph
    uri = ONTO[name]
    if (uri, RDF.type, OWL.Class) not in g:
        raise HTTPException(404, f"类 {name} 不存在")

    label = g.value(uri, RDFS.label)
    comment = g.value(uri, RDFS.comment)
    parents = [_local(p) for p in g.objects(uri, RDFS.subClassOf) if _local(p) in {_local(x) for x in g.subjects(RDF.type, OWL.Class)}]
    children = [_local(c) for c in g.subjects(RDFS.subClassOf, uri)]

    props = []
    for p in g.subjects(RDF.type, OWL.ObjectProperty):
        dom, rng = g.value(p, RDFS.domain), g.value(p, RDFS.range)
        if dom == uri or rng == uri:
            props.append({
                "name": _local(p),
                "label": str(g.value(p, RDFS.label) or _local(p)),
                "direction": "out" if dom == uri else "in",
                "other": _local(rng) if dom == uri else _local(dom),
            })
    data_props = []
    for p in g.subjects(RDF.type, OWL.DatatypeProperty):
        if g.value(p, RDFS.domain) == uri:
            data_props.append({
                "name": _local(p),
                "label": str(g.value(p, RDFS.label) or _local(p)),
                "range": _local(g.value(p, RDFS.range)) if g.value(p, RDFS.range) else "",
                "unit": str(g.value(p, ONTO.unit) or ""),
            })

    axioms = []
    for s, _, o in g.triples((None, ONTO.axiomId, None)):
        subject_refs = {str(o2) for o2 in g.objects(s, OWL.onProperty)} | {str(o2) for o2 in g.objects(s, OWL.onClass)}
        if str(uri) in subject_refs or (s == uri):
            axioms.append({"id": str(o), "label": str(g.value(s, RDFS.comment) or "")})

    # 统计实例时把子类一并算上：问「电力设备有多少实例」时，答案不该是 0
    descendants = set()
    frontier = [uri]
    while frontier:
        cur = frontier.pop()
        for sub in g.subjects(RDFS.subClassOf, cur):
            if isinstance(sub, URIRef) and sub not in descendants:
                descendants.add(sub)
                frontier.append(sub)
    instances = []
    for cls in {uri} | descendants:
        instances.extend(str(i).split("#")[-1] for i in g.subjects(RDF.type, cls))
    instances = sorted(set(instances))
    return {
        "name": name,
        "label": str(label) if label else name,
        "comment": str(comment) if comment else "",
        "parents": parents,
        "children": children,
        "object_properties": props,
        "data_properties": data_props,
        "axioms": axioms,
        "instances": instances[:60],
        "instance_count": len(instances),
        "instance_scope": "含子类",
    }
