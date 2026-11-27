# -*- coding: utf-8 -*-
"""本体与数据的结构性测试：文件能解析、约束能对上、口径能自洽。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from rdflib import Graph, Namespace, RDF, RDFS, OWL

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.reasoner.graph import GridModel  # noqa: E402

ONTO = Namespace("http://grid.ontology.local/onto#")
DATA = Namespace("http://grid.ontology.local/data#")

ONTO_TTL = ROOT / "ontology" / "grid-ontology.ttl"
INST_TTL = ROOT / "data" / "grid-instances.ttl"
ONTO_YAML = ROOT / "ontology" / "grid-ontology.yaml"


@pytest.fixture(scope="module")
def graph() -> Graph:
    g = Graph()
    g.parse(str(ONTO_TTL), format="turtle")
    g.parse(str(INST_TTL), format="turtle")
    return g


def test_ontology_meets_requirement(graph: Graph) -> None:
    """需求硬指标：≥15 个类、≥8 条公理。"""
    classes = list(graph.subjects(RDF.type, OWL.Class))
    axioms = {str(o) for _, _, o in graph.triples((None, ONTO.axiomId, None))}
    assert len(classes) >= 15, f"类数量不足：{len(classes)}"
    assert len(axioms) >= 8, f"公理数量不足：{len(axioms)}"


def test_class_hierarchy_is_acyclic(graph: Graph) -> None:
    parents = {}
    for cls in graph.subjects(RDF.type, OWL.Class):
        sups = [o for o in graph.objects(cls, RDFS.subClassOf)
                if isinstance(o, type(cls)) and (o, RDF.type, OWL.Class) in graph]
        parents[cls] = sups[0] if sups else None

    for cls in parents:
        seen, cur = set(), cls
        while cur is not None:
            assert cur not in seen, f"继承链存在环：{cur}"
            seen.add(cur)
            cur = parents.get(cur)


def test_every_class_has_label(graph: Graph) -> None:
    for cls in graph.subjects(RDF.type, OWL.Class):
        assert graph.value(cls, RDFS.label) is not None, f"{cls} 缺少中文标签"


def test_object_properties_have_domain_and_range(graph: Graph) -> None:
    for prop in graph.subjects(RDF.type, OWL.ObjectProperty):
        assert graph.value(prop, RDFS.domain) is not None, f"{prop} 缺少 domain"
        assert graph.value(prop, RDFS.range) is not None, f"{prop} 缺少 range"


def test_same_as_yaml_source_counts() -> None:
    """YAML 源与生成的 TTL 类数量必须一致，防止只改一边。"""
    spec = yaml.safe_load(ONTO_YAML.read_text(encoding="utf-8"))
    g = Graph()
    g.parse(str(ONTO_TTL), format="turtle")
    ttl_classes = {str(c).split("#")[-1] for c in g.subjects(RDF.type, OWL.Class)}
    yaml_classes = {c["name"] for c in spec["classes"]}
    assert yaml_classes == ttl_classes, yaml_classes ^ ttl_classes


def test_instance_referential_integrity(graph: Graph) -> None:
    """配变必须挂在存在的线路上，台区必须有配变，用户必须挂台区。"""
    for tr in graph.subjects(RDF.type, ONTO.DistributionTransformer):
        section = graph.value(tr, ONTO.locatedOn)
        assert section is not None
        assert (section, RDF.type, ONTO.LineSection) in graph

    for district in graph.subjects(RDF.type, ONTO.TransformerDistrict):
        trs = list(graph.objects(district, ONTO.hasTransformer))
        assert len(trs) == 1, f"台区 {district} 关联配变数量为 {len(trs)}"

    for customer in graph.subjects(RDF.type, ONTO.Customer):
        assert graph.value(customer, ONTO.servedByDistrict) is not None


def test_switch_states_are_explicit(graph: Graph) -> None:
    """开放世界假设下「未声明即未知」，因此每个开关必须有显式状态。"""
    switches = list(graph.subjects(RDF.type, ONTO.Switch))
    assert switches
    for sw in switches:
        state = graph.value(sw, ONTO.hasOperatingState)
        assert state is not None, f"开关 {sw} 缺少运行状态"
        assert state in (ONTO.OpenState, ONTO.ClosedState)


def test_feeder_load_matches_transformers(graph: Graph) -> None:
    """口径校验：馈线负荷等于所带配变负荷之和。"""
    for feeder in graph.subjects(RDF.type, ONTO.Feeder):
        declared = float(graph.value(feeder, ONTO.currentLoadKW))
        total = 0.0
        for tr in graph.subjects(RDF.type, ONTO.DistributionTransformer):
            if graph.value(tr, ONTO.belongsToFeeder) == feeder:
                total += float(graph.value(tr, ONTO.currentLoadKW))
        assert abs(declared - total) < 1e-6, f"{feeder} 口径不一致：{declared} vs {total}"


def test_grid_model_loads_all_devices() -> None:
    model = GridModel.load()
    assert len(model.substations) == 1
    assert len(model.feeders) == 2
    assert len(model.transformers) == 8
    assert len(model.districts) == 8
    assert model.census()["households"] == 2440
