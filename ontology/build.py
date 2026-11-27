#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本体与实例的构建脚本。

职责：
1. 读取 YAML 源（人类可读、可 review、可版本管理）
2. 生成标准 OWL/TTL（机器可推理、Protégé 可打开）
3. 执行结构与数据校验，任何不自洽直接失败退出

用法：
    python ontology/build.py            # 全量构建 + 校验
    python ontology/build.py --check    # 只校验，不写文件
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml
from rdflib import Graph, Literal, Namespace, URIRef, BNode
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS, XSD

ROOT = Path(__file__).resolve().parent
ONTO_YAML = ROOT / "grid-ontology.yaml"
ONTO_TTL = ROOT / "grid-ontology.ttl"
INST_YAML = ROOT.parent / "data" / "grid-instances.yaml"
INST_TTL = ROOT.parent / "data" / "grid-instances.ttl"

ONTO_NS = Namespace("http://grid.ontology.local/onto#")
DATA_NS = Namespace("http://grid.ontology.local/data#")

MIN_CLASSES = 15
MIN_AXIOMS = 8

PREFIXES = {
    "grid": ONTO_NS,
    "data": DATA_NS,
    "owl": OWL,
    "rdf": RDF,
    "rdfs": RDFS,
    "xsd": XSD,
}


class BuildError(RuntimeError):
    """构建期校验失败。"""


# --------------------------------------------------------------------------
# 本体构建
# --------------------------------------------------------------------------
def _xsd(name: str) -> URIRef:
    return URIRef(f"{XSD}{name.split(':')[-1]}")


def build_ontology(spec: Dict[str, Any]) -> tuple[Graph, Dict[str, Any]]:
    g = Graph()
    for prefix, ns in PREFIXES.items():
        g.bind(prefix, ns)

    meta = spec["meta"]
    base = URIRef(meta["base_uri"].rstrip("#"))
    g.add((base, RDF.type, OWL.Ontology))
    g.add((base, RDFS.label, Literal(meta["name"], lang="zh")))
    g.add((base, OWL.versionInfo, Literal(meta["version"])))
    g.add((base, RDFS.comment, Literal(meta["description"], lang="zh")))

    classes = spec.get("classes", [])
    obj_props = spec.get("object_properties", [])
    data_props = spec.get("data_properties", [])
    individuals = spec.get("individuals", [])
    axioms = spec.get("axioms", [])

    class_names = {c["name"] for c in classes}

    # ---- 类 ----
    for cls in classes:
        uri = ONTO_NS[cls["name"]]
        g.add((uri, RDF.type, OWL.Class))
        g.add((uri, RDFS.label, Literal(cls["label"], lang="zh")))
        if cls.get("comment"):
            g.add((uri, RDFS.comment, Literal(cls["comment"], lang="zh")))
        if cls.get("parent"):
            if cls["parent"] not in class_names:
                raise BuildError(f"类 {cls['name']} 的父类 {cls['parent']} 不存在")
            g.add((uri, RDFS.subClassOf, ONTO_NS[cls["parent"]]))
        if cls.get("abstract"):
            g.add((uri, ONTO_NS.isAbstract, Literal(True)))
        if cls.get("inferred"):
            g.add((uri, ONTO_NS.isInferredClass, Literal(True)))

    # ---- 属性 ----
    def _emit_property(p: Dict[str, Any], is_object: bool) -> None:
        uri = ONTO_NS[p["name"]]
        g.add((uri, RDF.type, OWL.ObjectProperty if is_object else OWL.DatatypeProperty))
        g.add((uri, RDFS.label, Literal(p["label"], lang="zh")))
        if p.get("comment"):
            g.add((uri, RDFS.comment, Literal(p["comment"], lang="zh")))
        if p.get("domain"):
            g.add((uri, RDFS.domain, ONTO_NS[p["domain"]]))
        if p.get("range"):
            target = ONTO_NS[p["range"]] if is_object else _xsd(p["range"])
            g.add((uri, RDFS.range, target))
        if p.get("inverse"):
            g.add((uri, OWL.inverseOf, ONTO_NS[p["inverse"]]))
        if p.get("transitive"):
            g.add((uri, RDF.type, OWL.TransitiveProperty))
        if p.get("functional"):
            g.add((uri, RDF.type, OWL.FunctionalProperty))
        if p.get("unit"):
            g.add((uri, ONTO_NS.unit, Literal(p["unit"])))

    for p in obj_props:
        _emit_property(p, True)
    for p in data_props:
        _emit_property(p, False)

    # ---- 个体 ----
    for ind in individuals:
        uri = ONTO_NS[ind["name"]]
        g.add((uri, RDF.type, OWL.NamedIndividual))
        g.add((uri, RDF.type, ONTO_NS[ind["class"]]))
        g.add((uri, RDFS.label, Literal(ind["label"], lang="zh")))
        if ind.get("code"):
            g.add((uri, ONTO_NS.code, Literal(ind["code"])))

    # ---- 公理 ----
    # 每条公理都额外登记一个注解节点：编号、名称、类型、作用对象、设计理由。
    # 这样 16 条公理在文件里形态一致，界面与文档可以逐条核对，不依赖 OWL 构造的差异。
    for ax in axioms:
        ax_id = ax["id"]
        if "label" not in ax or "rationale" not in ax:
            raise BuildError(f"公理 {ax_id} 缺少 label 或 rationale")
        kind = ax["type"]
        reg = BNode()
        g.add((reg, ONTO_NS.axiomId, Literal(ax_id)))
        g.add((reg, ONTO_NS.axiomType, Literal(kind)))
        g.add((reg, RDFS.label, Literal(ax["label"], lang="zh")))
        g.add((reg, ONTO_NS.rationale, Literal(ax["rationale"], lang="zh")))
        target = ax.get("class") or ax.get("property") or ax.get("class_a") or ax.get("implies")
        if target:
            g.add((reg, ONTO_NS.appliesTo, ONTO_NS[target]))

        def _annotate(node) -> None:
            g.add((node, ONTO_NS.axiomId, Literal(ax_id)))
            g.add((node, RDFS.comment, Literal(ax["label"], lang="zh")))
            g.add((node, ONTO_NS.rationale, Literal(ax["rationale"], lang="zh")))

        if kind == "transitive":
            prop = ONTO_NS[ax["property"]]
            g.add((prop, RDF.type, OWL.TransitiveProperty))
            _annotate(prop)
        elif kind == "inverse":
            prop = ONTO_NS[ax["property"]]
            g.add((prop, OWL.inverseOf, ONTO_NS[ax["inverse"]]))
            _annotate(prop)
        elif kind == "functional":
            prop = ONTO_NS[ax["property"]]
            g.add((prop, RDF.type, OWL.FunctionalProperty))
            _annotate(prop)
        elif kind == "qualified_cardinality":
            restr = BNode()
            g.add((restr, RDF.type, OWL.Restriction))
            g.add((restr, OWL.onProperty, ONTO_NS[ax["property"]]))
            if "exactly" in ax:
                g.add((restr, OWL.qualifiedCardinality, Literal(ax["exactly"], datatype=XSD.nonNegativeInteger)))
            elif "min" in ax:
                g.add((restr, OWL.minQualifiedCardinality, Literal(ax["min"], datatype=XSD.nonNegativeInteger)))
            elif "max" in ax:
                g.add((restr, OWL.maxQualifiedCardinality, Literal(ax["max"], datatype=XSD.nonNegativeInteger)))
            else:
                raise BuildError(f"公理 {ax_id} 缺少 exactly/min/max")
            g.add((restr, OWL.onClass, ONTO_NS[ax["filler"]]))
            g.add((ONTO_NS[ax["class"]], RDFS.subClassOf, restr))
            _annotate(restr)
        elif kind == "subclass_value":
            restr = BNode()
            g.add((restr, RDF.type, OWL.Restriction))
            g.add((restr, OWL.onProperty, ONTO_NS[ax["property"]]))
            g.add((restr, OWL.hasValue, Literal(ax["value"], datatype=_xsd(ax.get("datatype", "xsd:string")))))
            g.add((ONTO_NS[ax["class"]], RDFS.subClassOf, restr))
            _annotate(restr)
        elif kind == "subclass_restriction":
            restr = BNode()
            g.add((restr, RDF.type, OWL.Restriction))
            g.add((restr, OWL.onProperty, ONTO_NS[ax["on_property"]]))
            g.add((restr, OWL.someValuesFrom, ONTO_NS[ax["some"]]))
            g.add((ONTO_NS[ax["class"]], RDFS.subClassOf, restr))
            _annotate(restr)
        elif kind == "disjoint_with":
            g.add((ONTO_NS[ax["class_a"]], OWL.disjointWith, ONTO_NS[ax["class_b"]]))
        elif kind == "all_disjoint":
            node = BNode()
            g.add((node, RDF.type, OWL.AllDisjointClasses))
            lst = BNode()
            Collection(g, lst, [ONTO_NS[c] for c in ax["classes"]])
            g.add((node, OWL.members, lst))
            _annotate(node)
        elif kind == "property_chain":
            prop = ONTO_NS[ax["implies"]]
            lst = BNode()
            Collection(g, lst, [ONTO_NS[p] for p in ax["chain"]])
            g.add((prop, OWL.propertyChainAxiom, lst))
            _annotate(prop)
        else:
            raise BuildError(f"公理 {ax_id} 使用了未知类型 {kind}")

    stats = {
        "classes": len(classes),
        "object_properties": len(obj_props),
        "data_properties": len(data_props),
        "individuals": len(individuals),
        "axioms": len(axioms),
        "triples": len(g),
    }
    return g, stats


def validate_ontology(spec: Dict[str, Any], g: Graph, stats: Dict[str, Any]) -> List[str]:
    """结构校验：类数、规则数、引用完整性、继承无环。"""
    problems: List[str] = []
    classes = {c["name"]: c for c in spec.get("classes", [])}

    if stats["classes"] < MIN_CLASSES:
        problems.append(f"类数量 {stats['classes']} 少于要求 {MIN_CLASSES}")
    if stats["axioms"] < MIN_AXIOMS:
        problems.append(f"公理数量 {stats['axioms']} 少于要求 {MIN_AXIOMS}")

    # 继承环检测
    for name in classes:
        seen, cur = set(), name
        while cur:
            if cur in seen:
                problems.append(f"类 {name} 的继承链存在环：{cur}")
                break
            seen.add(cur)
            cur = classes.get(cur, {}).get("parent")

    # 属性引用完整性
    prop_names = {p["name"] for p in spec.get("object_properties", [])} | {
        p["name"] for p in spec.get("data_properties", [])
    }
    for p in spec.get("object_properties", []) + spec.get("data_properties", []):
        for ref_field in ("domain", "range"):
            ref = p.get(ref_field)
            if not ref:
                continue
            if ref.startswith("xsd:"):
                continue
            if ref not in classes:
                problems.append(f"属性 {p['name']} 的 {ref_field} 指向不存在的类 {ref}")
        if p.get("inverse") and p["inverse"] not in prop_names:
            problems.append(f"属性 {p['name']} 的逆属性 {p['inverse']} 不存在")
        if p.get("inverse"):
            other = next((q for q in spec["object_properties"] if q["name"] == p["inverse"]), None)
            if other and other.get("inverse") != p["name"]:
                problems.append(f"逆属性不成对：{p['name']} 声明 {p['inverse']}，但后者未反向声明")

    # 公理引用完整性
    for ax in spec.get("axioms", []):
        for field in ("class", "filler", "some", "class_a", "class_b"):
            ref = ax.get(field)
            if ref and ref not in classes:
                problems.append(f"公理 {ax['id']} 的 {field} 引用了未知类 {ref}")
        for field in ("property", "implies", "on_property", "inverse"):
            ref = ax.get(field)
            if ref and ref not in prop_names:
                problems.append(f"公理 {ax['id']} 的 {field} 引用了未知属性 {ref}")
        for ref in ax.get("classes", []) or []:
            if ref not in classes:
                problems.append(f"公理 {ax['id']} 引用了未知类 {ref}")
        for ref in ax.get("chain", []) or []:
            if ref not in {p["name"] for p in spec.get("object_properties", [])}:
                problems.append(f"公理 {ax['id']} 的链中包含未知属性 {ref}")

    # OWL 层可被 rdflib 解析
    try:
        g.serialize(format="turtle")
    except Exception as exc:  # pragma: no cover
        problems.append(f"TTL 序列化失败：{exc}")

    return problems


# --------------------------------------------------------------------------
# 实例构建
# --------------------------------------------------------------------------
def build_instances(data: Dict[str, Any]) -> tuple[Graph, Dict[str, Any]]:
    g = Graph()
    for prefix, ns in PREFIXES.items():
        g.bind(prefix, ns)

    def node(obj_id: str) -> URIRef:
        return DATA_NS[obj_id]

    def lit(value, datatype=None):
        if datatype:
            return Literal(value, datatype=datatype)
        return Literal(value)

    subs = data.get("substations", [])
    feeders = data.get("feeders", [])
    sections = data.get("sections", [])
    switches = data.get("switches", [])
    transformers = data.get("transformers", [])
    districts = data.get("districts", [])
    customers = data.get("customers", [])
    teams = data.get("teams", [])
    faults = data.get("faults", [])
    orders = data.get("orders", [])
    records = data.get("load_records", [])

    for s in subs:
        u = node(s["id"])
        g.add((u, RDF.type, ONTO_NS.Substation))
        g.add((u, RDFS.label, Literal(s["name"], lang="zh")))
        g.add((u, ONTO_NS.code, Literal(s["id"])))
        g.add((u, ONTO_NS.voltageLevelKV, lit(s["voltage_kv"], XSD.float)))
        for f in s.get("feeders", []):
            g.add((u, ONTO_NS.hasFeeder, node(f)))

    for f in feeders:
        u = node(f["id"])
        g.add((u, RDF.type, ONTO_NS.Feeder))
        g.add((u, RDFS.label, Literal(f["name"], lang="zh")))
        g.add((u, ONTO_NS.code, Literal(f["id"])))
        g.add((u, ONTO_NS.belongsToSubstation, node(f["substation"])))
        g.add((u, ONTO_NS.voltageLevelKV, lit(f["voltage_kv"], XSD.float)))
        g.add((u, ONTO_NS.ratedCapacityKW, lit(f["rated_kw"], XSD.float)))
        g.add((u, ONTO_NS.currentLoadKW, lit(f["load_kw"], XSD.float)))
        g.add((u, ONTO_NS.name, lit(f["name"])))

    for s in sections:
        u = node(s["id"])
        g.add((u, RDF.type, ONTO_NS.LineSection))
        g.add((u, RDFS.label, Literal(s["name"], lang="zh")))
        g.add((u, ONTO_NS.code, Literal(s["id"])))
        g.add((u, ONTO_NS.belongsToFeeder, node(s["feeder"])))
        g.add((u, ONTO_NS.upstreamOf, node(s["upstream"])))
        if s.get("length_km") is not None:
            g.add((u, ONTO_NS.lengthKM, lit(s["length_km"], XSD.float)))
        if s.get("comment"):
            g.add((u, RDFS.comment, Literal(s["comment"], lang="zh")))

    for sw in switches:
        u = node(sw["id"])
        g.add((u, RDF.type, ONTO_NS[sw["cls"]]))
        g.add((u, RDF.type, ONTO_NS.Switch))
        g.add((u, RDFS.label, Literal(sw["name"], lang="zh")))
        g.add((u, ONTO_NS.code, Literal(sw["id"])))
        g.add((u, ONTO_NS.belongsToFeeder, node(sw["feeder"])))
        state_cls = "ClosedState" if sw["state"] == "closed" else "OpenState"
        g.add((u, ONTO_NS.hasOperatingState, ONTO_NS[state_cls]))
        g.add((u, ONTO_NS.normallyOpen, lit(bool(sw.get("normally_open", False)), XSD.boolean)))
        g.add((u, ONTO_NS.connectsFrom, node(sw["connects"][0])))
        g.add((u, ONTO_NS.connectsTo, node(sw["connects"][1])))
        if sw.get("comment"):
            g.add((u, RDFS.comment, Literal(sw["comment"], lang="zh")))

    # 联络开关连接的馈线（由两端设备所属馈线推导）
    feeder_of = {s["id"]: s["feeder"] for s in sections}
    for sw in switches:
        if sw["cls"] != "TieSwitch":
            continue
        u = node(sw["id"])
        connected = []
        for endpoint in sw["connects"]:
            if endpoint in feeder_of:
                connected.append(feeder_of[endpoint])
            elif any(f["id"] == endpoint for f in feeders):
                connected.append(endpoint)
        for f in dict.fromkeys(connected):
            g.add((u, ONTO_NS.connectsFeeder, node(f)))

    for t in transformers:
        u = node(t["id"])
        g.add((u, RDF.type, ONTO_NS.DistributionTransformer))
        g.add((u, RDFS.label, Literal(t["name"], lang="zh")))
        g.add((u, ONTO_NS.code, Literal(t["id"])))
        g.add((u, ONTO_NS.belongsToFeeder, node(t["feeder"])))
        g.add((u, ONTO_NS.locatedOn, node(t["section"])))
        g.add((u, ONTO_NS.upstreamOf, node(t["section"])))
        g.add((u, ONTO_NS.ratedCapacityKW, lit(t["rated_kw"], XSD.float)))
        g.add((u, ONTO_NS.currentLoadKW, lit(t["load_kw"], XSD.float)))
        g.add((u, ONTO_NS.voltageLevelKV, lit(10.0, XSD.float)))
        if t.get("comment"):
            g.add((u, RDFS.comment, Literal(t["comment"], lang="zh")))

    for d in districts:
        u = node(d["id"])
        g.add((u, RDF.type, ONTO_NS.TransformerDistrict))
        g.add((u, RDFS.label, Literal(d["name"], lang="zh")))
        g.add((u, ONTO_NS.code, Literal(d["id"])))
        g.add((u, ONTO_NS.servedByFeeder, node(d["feeder"])))
        g.add((u, ONTO_NS.hasTransformer, node(d["transformer"])))
        g.add((u, ONTO_NS.householdCount, lit(d["households"], XSD.integer)))
        g.add((u, ONTO_NS.customerCount, lit(d.get("customers_count", 1), XSD.integer)))
        if d.get("address"):
            g.add((u, ONTO_NS.address, Literal(d["address"], lang="zh")))

    for c in customers:
        u = node(c["id"])
        g.add((u, RDF.type, ONTO_NS[c["cls"]]))
        g.add((u, RDF.type, ONTO_NS.Customer))
        g.add((u, RDFS.label, Literal(c["name"], lang="zh")))
        g.add((u, ONTO_NS.code, Literal(c["id"])))
        g.add((u, ONTO_NS.servedByDistrict, node(c["district"])))
        g.add((u, ONTO_NS.isCriticalLoad, lit(bool(c.get("critical", False)), XSD.boolean)))

    for t in teams:
        u = node(t["id"])
        g.add((u, RDF.type, ONTO_NS.MaintenanceTeam))
        g.add((u, RDFS.label, Literal(t["name"], lang="zh")))
        g.add((u, ONTO_NS.code, Literal(t["id"])))
        if t.get("members"):
            g.add((u, ONTO_NS.name, lit(f"{t['name']}（{t['members']}人）")))

    for f in faults:
        u = node(f["id"])
        g.add((u, RDF.type, ONTO_NS.Fault))
        g.add((u, RDFS.label, Literal(f["name"], lang="zh")))
        g.add((u, ONTO_NS.code, Literal(f["id"])))
        g.add((u, ONTO_NS.affectsEquipment, node(f["equipment"])))
        g.add((u, ONTO_NS.faultType, Literal(f["type"], lang="zh")))
        g.add((u, ONTO_NS.faultTime, lit(f["time"], XSD.dateTime)))
        g.add((u, ONTO_NS.durationMinutes, lit(f["duration_min"], XSD.integer)))
        if f.get("description"):
            g.add((u, RDFS.comment, Literal(f["description"], lang="zh")))

    for o in orders:
        u = node(o["id"])
        g.add((u, RDF.type, ONTO_NS.MaintenanceOrder))
        g.add((u, RDFS.label, Literal(o["name"], lang="zh")))
        g.add((u, ONTO_NS.code, Literal(o["id"])))
        g.add((u, ONTO_NS.targetsEquipment, node(o["equipment"])))
        g.add((u, ONTO_NS.assignedToTeam, node(o["team"])))
        g.add((u, ONTO_NS.orderStatus, Literal(o["status"], lang="zh")))
        g.add((u, ONTO_NS.plannedStart, lit(o["start"], XSD.dateTime)))
        g.add((u, ONTO_NS.plannedEnd, lit(o["end"], XSD.dateTime)))
        g.add((u, ONTO_NS.priority, lit(o.get("priority", 3), XSD.integer)))
        if o.get("fault"):
            g.add((u, ONTO_NS.handlesFault, node(o["fault"])))

    for r in records:
        u = node(r["id"])
        g.add((u, RDF.type, ONTO_NS.LoadRecord))
        g.add((u, RDFS.label, Literal(r["id"], lang="zh")))
        g.add((u, ONTO_NS.code, Literal(r["id"])))
        g.add((u, ONTO_NS.currentLoadKW, lit(r["load_kw"], XSD.float)))
        g.add((u, ONTO_NS.faultTime, lit(r["time"], XSD.dateTime)))
        g.add((node(r["transformer"]), ONTO_NS.hasLoadRecord, u))

    stats = {
        "triples": len(g),
        "substations": len(subs),
        "feeders": len(feeders),
        "sections": len(sections),
        "switches": len(switches),
        "transformers": len(transformers),
        "districts": len(districts),
        "customers": len(customers),
        "faults": len(faults),
        "orders": len(orders),
        "households": sum(d["households"] for d in districts),
    }
    return g, stats


def validate_instances(data: Dict[str, Any]) -> List[str]:
    """数据自洽校验：拓扑引用、容量与负荷口径、台区与配变一一对应。"""
    problems: List[str] = []
    feeders = {f["id"]: f for f in data.get("feeders", [])}
    sections = {s["id"]: s for s in data.get("sections", [])}
    transformers = {t["id"]: t for t in data.get("transformers", [])}
    districts = {d["id"]: d for d in data.get("districts", [])}
    subs = {s["id"]: s for s in data.get("substations", [])}

    # 引用完整性
    for s in sections.values():
        if s["feeder"] not in feeders:
            problems.append(f"线路段 {s['id']} 的馈线 {s['feeder']} 不存在")
        up = s["upstream"]
        if up not in feeders and up not in sections and up not in subs:
            problems.append(f"线路段 {s['id']} 的上游 {up} 不存在")
        if up == s["id"]:
            problems.append(f"线路段 {s['id']} 的上游指向自身")

    for t in transformers.values():
        if t["section"] not in sections:
            problems.append(f"配变 {t['id']} 的线路段 {t['section']} 不存在")
        if t["feeder"] not in feeders:
            problems.append(f"配变 {t['id']} 的馈线 {t['feeder']} 不存在")
        if t["load_kw"] > t["rated_kw"] * 1.5:
            problems.append(f"配变 {t['id']} 负荷 {t['load_kw']} 超过额定 1.5 倍，数据可疑")

    for d in districts.values():
        if d["transformer"] not in transformers:
            problems.append(f"台区 {d['id']} 的配变 {d['transformer']} 不存在")
        if transformers.get(d["transformer"], {}).get("district") not in (None, d["id"]):
            problems.append(f"台区 {d['id']} 与配变 {d['transformer']} 的归属不一致")

    assigned = [d["transformer"] for d in districts.values()]
    if len(assigned) != len(set(assigned)):
        problems.append("存在一台配变被多个台区引用（违反 A5 基数约束）")
    for tid in transformers:
        if tid not in assigned:
            problems.append(f"配变 {tid} 未归属任何台区（违反 A5 基数约束）")

    # 口径校验：馈线负荷 == 其配变负荷之和
    for fid, f in feeders.items():
        total = sum(t["load_kw"] for t in transformers.values() if t["feeder"] == fid)
        if abs(total - f["load_kw"]) > 1e-6:
            problems.append(
                f"馈线 {fid} 负荷口径不一致：属性 {f['load_kw']} kW，配变合计 {total} kW"
            )
        if f["load_kw"] > f["rated_kw"]:
            problems.append(f"馈线 {fid} 当前负荷超过额定容量，正常运行状态不成立")

    # 联络开关必须连接两条不同馈线
    feeder_of = {s["id"]: s["feeder"] for s in sections.values()}
    for sw in data.get("switches", []):
        if len(sw["connects"]) != 2:
            problems.append(f"开关 {sw['id']} 的连接端不是 2 个（违反 A7）")
        if sw["cls"] == "TieSwitch":
            fs = {feeder_of.get(e) for e in sw["connects"] if feeder_of.get(e)}
            if len(fs) != 2:
                problems.append(f"联络开关 {sw['id']} 未连接两条不同馈线（违反 A7）")

    # 用户必须挂在存在的台区下
    for c in data.get("customers", []):
        if c["district"] not in districts:
            problems.append(f"用户 {c['id']} 的台区 {c['district']} 不存在")

    return problems


# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="构建配电网本体与实例")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args()

    spec = yaml.safe_load(ONTO_YAML.read_text(encoding="utf-8"))
    onto_graph, onto_stats = build_ontology(spec)
    onto_problems = validate_ontology(spec, onto_graph, onto_stats)

    data = yaml.safe_load(INST_YAML.read_text(encoding="utf-8"))
    inst_graph, inst_stats = build_instances(data)
    inst_problems = validate_instances(data)

    problems = onto_problems + inst_problems
    print("=" * 62)
    print("本体统计")
    print("=" * 62)
    for k, v in onto_stats.items():
        print(f"  {k:<20} {v}")
    print("-" * 62)
    print("实例统计")
    print("-" * 62)
    for k, v in inst_stats.items():
        print(f"  {k:<20} {v}")

    if problems:
        print("\n发现 %d 处问题：" % len(problems))
        for p in problems:
            print("  [x]", p)
        return 1

    print("\n校验通过：类数量 %d（要求 >= %d），公理数量 %d（要求 >= %d）"
          % (onto_stats["classes"], MIN_CLASSES, onto_stats["axioms"], MIN_AXIOMS))

    if not args.check:
        onto_graph.serialize(destination=str(ONTO_TTL), format="turtle")
        inst_graph.serialize(destination=str(INST_TTL), format="turtle")
        print(f"已写出 {ONTO_TTL}")
        print(f"已写出 {INST_TTL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
