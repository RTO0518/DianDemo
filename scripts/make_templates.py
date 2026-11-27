# -*- coding: utf-8 -*-
"""
从当前实例图反向导出「甲方台账模板」，保证模板示例与演示数据完全一致。

如果模板里随手编几个编号，甲方拿去填完导入会对不上现有推理结果，
所以这里直接读 data/grid-instances.ttl 生成，模板即真值。
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

from rdflib import Graph, Namespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.reasoner.graph import GridModel  # noqa: E402

OUT = ROOT / "data" / "templates"

GRID = Namespace("http://grid.ontology.local/onto#")
RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")

LEDGER_HEADER = [
    "设备编号", "设备类型", "设备名称", "所属馈线", "上级设备",
    "额定容量(kW)", "当前负荷(kW)", "户数", "开关状态", "常开",
    "是否重要用户", "电压等级(kV)", "地址",
]

TYPE_CN = {
    "Substation": "变电站",
    "Feeder": "馈线",
    "LineSection": "线路段",
    "SectionSwitch": "分段开关",
    "TieSwitch": "联络开关",
    "Breaker": "断路器",
    "DistributionTransformer": "配变",
    "TransformerDistrict": "台区",
    "ResidentialCustomer": "居民用户",
    "CommercialCustomer": "商业用户",
}

ORDER = ["Substation", "Feeder", "LineSection", "Breaker", "SectionSwitch", "TieSwitch",
         "DistributionTransformer", "TransformerDistrict",
         "ResidentialCustomer", "CommercialCustomer"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    g = Graph()
    g.parse(str(ROOT / "data" / "grid-instances.ttl"), format="turtle")
    model = GridModel.load()

    def local(uri) -> str:
        return str(uri).split("#")[-1]

    rows = []
    for cls in ORDER:
        for s in sorted(set(g.subjects(None, GRID[cls])), key=str):
            code_v = g.value(s, GRID.code)
            if code_v is None:
                continue
            code = str(code_v)
            name = g.value(s, RDFS.label)
            feeder = g.value(s, GRID.belongsToFeeder)
            parent = None
            if cls == "Feeder":
                parent = g.value(s, GRID.belongsToSubstation)
            elif cls == "LineSection":
                parent = g.value(s, GRID.upstreamOf)
            elif cls == "DistributionTransformer":
                parent = g.value(s, GRID.locatedOn)
            elif cls == "TransformerDistrict":
                parent = g.value(s, GRID.hasTransformer)
            elif cls in ("ResidentialCustomer", "CommercialCustomer"):
                parent = g.value(s, GRID.servedByDistrict)
            elif cls in ("Breaker", "SectionSwitch", "TieSwitch"):
                parent = g.value(s, GRID.connectsFrom)

            rated = g.value(s, GRID.ratedCapacityKW)
            load = g.value(s, GRID.currentLoadKW)
            hh = g.value(s, GRID.householdCount)
            volt = g.value(s, GRID.voltageLevelKV)
            st = g.value(s, GRID.hasOperatingState)
            no = g.value(s, GRID.normallyOpen)
            crit = g.value(s, GRID.isCriticalLoad)
            addr = g.value(s, GRID.address)
            name_plain = g.value(s, GRID.name)

            rows.append({
                "设备编号": code,
                "设备类型": TYPE_CN.get(cls, cls),
                "设备名称": str(name_plain or name or code),
                "所属馈线": local(feeder) if feeder is not None else "",
                "上级设备": local(parent) if parent is not None else "",
                "额定容量(kW)": f"{float(rated):g}" if rated is not None else "",
                "当前负荷(kW)": f"{float(load):g}" if load is not None else "",
                "户数": str(int(hh)) if hh is not None else "",
                "开关状态": ("合" if local(st) == "ClosedState" else "分") if st is not None else "",
                "常开": ("是" if bool(no.toPython()) else "否") if no is not None else "",
                "是否重要用户": ("是" if bool(crit.toPython()) else "否") if crit is not None else "",
                "电压等级(kV)": f"{float(volt):g}" if volt is not None else "",
                "地址": str(addr) if addr is not None else "",
            })

    ledger = OUT / "设备台账模板.csv"
    with ledger.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_HEADER)
        w.writeheader()
        w.writerows(rows)

    topo_rows = []
    seen = set()
    # 拓扑表必须是「完整电气连接」而不是「只有开关的边」：
    # 配变挂接、馈线—变电站这类固定连接也要在里面，否则连通性校验会全线报警。
    for edge in model.edges:
        key = tuple(sorted((edge.src, edge.dst)))
        if key in seen:
            continue
        seen.add(key)
        topo_rows.append({
            "起点设备": edge.src,
            "终点设备": edge.dst,
            "连接开关": edge.switch_id or "",
        })
    topo_rows.sort(key=lambda r: (r["起点设备"], r["终点设备"]))

    topo = OUT / "拓扑关系模板.csv"
    with topo.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["起点设备", "终点设备", "连接开关"])
        w.writeheader()
        w.writerows(topo_rows)

    print(f"设备台账 {len(rows)} 行 → {ledger}")
    print(f"拓扑关系 {len(topo_rows)} 行 → {topo}")


if __name__ == "__main__":
    main()
