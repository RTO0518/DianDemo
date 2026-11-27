# -*- coding: utf-8 -*-
"""
从 OWL 实例图加载配电网模型。

设计要点：
    拓扑的唯一来源是 TTL（即本体文件），代码里没有任何硬编码的电网结构。
    这保证了「改本体会改变推理结果」这一本体系统的基本承诺。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from rdflib import Graph, Namespace, RDF, RDFS, URIRef

ONTO = Namespace("http://grid.ontology.local/onto#")
DATA = Namespace("http://grid.ontology.local/data#")

ROOT = Path(__file__).resolve().parents[2]
ONTO_TTL = ROOT / "ontology" / "grid-ontology.ttl"
INST_TTL = ROOT / "data" / "grid-instances.ttl"

# 显示用类名优先级：越靠前越具体
CLASS_PRIORITY = [
    "TieSwitch", "SectionSwitch", "Breaker", "Switch",
    "Substation", "Feeder", "LineSection", "DistributionTransformer",
    "TransformerDistrict", "ResidentialCustomer", "CommercialCustomer", "Customer",
    "Fault", "OutageEvent", "MaintenanceOrder", "LoadTransferPlan",
    "LoadRecord", "MaintenanceTeam", "OperatingState",
]

KIND_OF_CLASS = {
    "Substation": "substation",
    "Feeder": "feeder",
    "LineSection": "section",
    "Switch": "switch",
    "SectionSwitch": "switch",
    "TieSwitch": "switch",
    "Breaker": "switch",
    "DistributionTransformer": "transformer",
    "TransformerDistrict": "district",
    "Customer": "customer",
    "ResidentialCustomer": "customer",
    "CommercialCustomer": "customer",
    "Fault": "fault",
    "MaintenanceOrder": "order",
    "LoadRecord": "load_record",
    "MaintenanceTeam": "team",
}


@dataclass
class Device:
    id: str
    label: str
    uri: URIRef
    classes: List[str] = field(default_factory=list)
    kind: str = ""
    comment: str = ""

    @property
    def cls(self) -> str:
        """最具体的类名，用于界面展示。"""
        for name in CLASS_PRIORITY:
            if name in self.classes:
                return name
        return self.classes[0] if self.classes else "Entity"


@dataclass
class Edge:
    src: str
    dst: str
    switch_id: Optional[str] = None

    def other(self, node: str) -> str:
        return self.dst if node == self.src else self.src

    def as_dict(self) -> dict:
        return {"source": self.src, "target": self.dst, "switch": self.switch_id}


@dataclass
class SwitchInfo(Device):
    cls_name: str = ""
    state: str = "closed"
    normally_open: bool = False
    connects: Tuple[str, str] = ("", "")
    feeder: Optional[str] = None


class GridModel:
    """把 TTL 图封装成可推理的电网对象。"""

    def __init__(self, graph: Graph):
        self.graph = graph
        self.devices: Dict[str, Device] = {}
        self.switches: Dict[str, SwitchInfo] = {}
        self.edges: List[Edge] = []
        self._adj: Dict[str, List[Edge]] = {}
        self.feeder_of: Dict[str, str] = {}
        self.district_of_transformer: Dict[str, str] = {}
        self.transformer_of_district: Dict[str, str] = {}
        self._build()

    # ---------------- 加载 ----------------
    @classmethod
    def load(cls, onto: Path = ONTO_TTL, inst: Path = INST_TTL) -> "GridModel":
        g = Graph()
        g.parse(str(onto), format="turtle")
        g.parse(str(inst), format="turtle")
        return cls(g)

    @staticmethod
    def _local(uri: URIRef) -> str:
        return str(uri).split("#")[-1]

    def _objects(self, subj: URIRef, pred: URIRef) -> List[URIRef]:
        return [o for o in self.graph.objects(subj, pred) if isinstance(o, URIRef)]

    def _value(self, subj: URIRef, pred: URIRef):
        for o in self.graph.objects(subj, pred):
            return o
        return None

    def _build(self) -> None:
        g = self.graph
        for subj in set(g.subjects(RDF.type, None)):
            types = [self._local(t) for t in g.objects(subj, RDF.type) if isinstance(t, URIRef)]
            kinds = [KIND_OF_CLASS[t] for t in types if t in KIND_OF_CLASS]
            if not kinds:
                continue
            label = self._value(subj, RDFS.label)
            comment = self._value(subj, RDFS.comment)
            dev_id = self._local(subj)
            dev = Device(
                id=dev_id,
                label=str(label) if label else dev_id,
                uri=subj,
                classes=types,
                kind=kinds[0],
                comment=str(comment) if comment else "",
            )
            self.devices[dev_id] = dev

        # 开关
        for dev in list(self.devices.values()):
            if dev.kind != "switch":
                continue
            state_uri = self._value(dev.uri, ONTO.hasOperatingState)
            state = "open"
            if state_uri is not None and self._local(state_uri) == "ClosedState":
                state = "closed"
            norm = self._value(dev.uri, ONTO.normallyOpen)
            norm_val = bool(norm.toPython()) if norm is not None else False
            connects = (
                self._local(self._value(dev.uri, ONTO.connectsFrom) or URIRef("")),
                self._local(self._value(dev.uri, ONTO.connectsTo) or URIRef("")),
            )
            feeder_uri = self._value(dev.uri, ONTO.belongsToFeeder)
            info = SwitchInfo(
                id=dev.id, label=dev.label, uri=dev.uri, classes=dev.classes,
                kind="switch", comment=dev.comment, cls_name=dev.cls,
                state=state,
                normally_open=norm_val,
                connects=connects,
                feeder=self._local(feeder_uri) if feeder_uri else None,
            )
            self.switches[dev.id] = info
            self.devices[dev.id] = info

        # 设备所属馈线
        for dev in self.devices.values():
            if dev.kind in ("section", "transformer"):
                f = self._value(dev.uri, ONTO.belongsToFeeder)
                if f is not None:
                    self.feeder_of[dev.id] = self._local(f)

        # 台区 ↔ 配变
        for dev in self.devices.values():
            if dev.kind == "district":
                t = self._value(dev.uri, ONTO.hasTransformer)
                if t is not None:
                    tid = self._local(t)
                    self.district_of_transformer[tid] = dev.id
                    self.transformer_of_district[dev.id] = tid

        # 边：线路段上游、配变挂接、馈线电源、开关连接
        edge_index: Dict[Tuple[str, str], Edge] = {}

        def add_edge(src: str, dst: str) -> Edge:
            key = (src, dst)
            if key not in edge_index:
                edge_index[key] = Edge(src, dst)
                self.edges.append(edge_index[key])
            return edge_index[key]

        for dev in self.devices.values():
            if dev.kind == "section":
                up = self._value(dev.uri, ONTO.upstreamOf)
                if up is not None:
                    add_edge(self._local(up), dev.id)
            elif dev.kind == "transformer":
                sec = self._value(dev.uri, ONTO.locatedOn)
                if sec is not None:
                    add_edge(self._local(sec), dev.id)
            elif dev.kind == "feeder":
                sub = self._value(dev.uri, ONTO.belongsToSubstation)
                if sub is not None:
                    add_edge(self._local(sub), dev.id)

        for sw in self.switches.values():
            a, b = sw.connects
            if not a or not b:
                continue
            edge = edge_index.get((a, b)) or edge_index.get((b, a))
            if edge is None:
                edge = add_edge(a, b)
            if edge.switch_id and edge.switch_id != sw.id:
                raise ValueError(f"边 {edge.src}->{edge.dst} 被两个开关同时占用：{edge.switch_id} / {sw.id}")
            edge.switch_id = sw.id

        self._adj = {}
        for e in self.edges:
            self._adj.setdefault(e.src, []).append(e)
            self._adj.setdefault(e.dst, []).append(e)
        self.state: Dict[str, str] = {sid: s.state for sid, s in self.switches.items()}

    # ---------------- 查询辅助 ----------------
    def by_kind(self, kind: str) -> List[Device]:
        return [d for d in self.devices.values() if d.kind == kind]

    @property
    def substations(self) -> List[Device]:
        return self.by_kind("substation")

    @property
    def feeders(self) -> List[Device]:
        return self.by_kind("feeder")

    @property
    def transformers(self) -> List[Device]:
        return self.by_kind("transformer")

    @property
    def districts(self) -> List[Device]:
        return self.by_kind("district")

    @property
    def customers(self) -> List[Device]:
        return self.by_kind("customer")

    def neighbors(self, node: str) -> Iterable[Edge]:
        return self._adj.get(node, [])

    def num(self, dev: Device, prop: str, default: float = 0.0) -> float:
        val = self._value(dev.uri, ONTO[prop])
        try:
            return float(val)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    def text(self, dev: Device, prop: str, default: str = "") -> str:
        val = self._value(dev.uri, ONTO[prop])
        return str(val) if val is not None else default

    def feeder_of_device(self, dev_id: str) -> Optional[str]:
        if dev_id in self.feeder_of:
            return self.feeder_of[dev_id]
        if dev_id in self.switches and self.switches[dev_id].feeder:
            return self.switches[dev_id].feeder
        return None

    def transformers_of_feeder(self, feeder_id: str) -> List[Device]:
        return [t for t in self.transformers if self.feeder_of.get(t.id) == feeder_id]

    def census(self) -> dict:
        """全域统计，供停电比例等指标使用。"""
        total_households = sum(int(self.num(d, "householdCount")) for d in self.districts)
        total_load = sum(self.num(f, "currentLoadKW") for f in self.feeders)
        total_capacity = sum(self.num(f, "ratedCapacityKW") for f in self.feeders)
        return {
            "households": total_households,
            "customers": len(self.customers),
            "feeders": len(self.feeders),
            "transformers": len(self.transformers),
            "districts": len(self.districts),
            "total_load_kw": total_load,
            "total_capacity_kw": total_capacity,
        }


_MODEL: Optional[GridModel] = None


def get_model(refresh: bool = False) -> GridModel:
    global _MODEL
    if _MODEL is None or refresh:
        _MODEL = GridModel.load()
    return _MODEL
