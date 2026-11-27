# -*- coding: utf-8 -*-
"""
拓扑关系表治理。

设备台账只能说明「有哪些设备」，说明不了「谁连着谁」。电气拓扑必须单独一张表：
每一行是「起点设备 — 开关 — 终点设备」，开关为空表示固定连接（如配变挂接在线上）。

这张表的错误代价最高：拓扑建错，后面故障传播、转供判定全部错，
而且错误会以「推理结论看起来很正常」的方式藏起来。所以单列规则 T1~T5。
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict, deque
from typing import Dict, List, Set, Tuple

TOPOLOGY_CHECKS: List[dict] = [
    {"code": "T1", "name": "端点设备存在", "level": "error",
     "description": "边的两端必须是台账里有的设备编号。"},
    {"code": "T2", "name": "边不被重复占用", "level": "error",
     "description": "同一对设备之间只能挂一台开关，两台开关串在一条边上会让状态判定失去意义。"},
    {"code": "T3", "name": "开关编号合法", "level": "error",
     "description": "填了开关就必须是台账里的开关类设备。"},
    {"code": "T4", "name": "端点不重复", "level": "error",
     "description": "起点与终点不能是同一台设备。"},
    {"code": "T5", "name": "全网连通到电源", "level": "warning",
     "description": "所有设备应能通过边集连通到某个变电站，孤岛设备无法参与推理。"},
]

TOPOLOGY_CHECK_INDEX = {c["code"]: c for c in TOPOLOGY_CHECKS}

# 低压侧对象：不属于 10kV 电气拓扑表，归属关系取自台账的「上级设备」
LOW_VOLTAGE = ("TransformerDistrict", "ResidentialCustomer", "CommercialCustomer")

HEADER_ALIASES = {
    "from": ("起点设备", "起点", "起始设备", "上游设备", "from", "source", "设备a"),
    "to": ("终点设备", "终点", "结束设备", "下游设备", "to", "target", "设备b"),
    "switch": ("连接开关", "开关", "开关编号", "switch", "开关设备"),
}


def _norm(s: str) -> str:
    return str(s or "").strip().replace(" ", "").replace("（", "(").replace("）", ")").lower()


def parse(text: str) -> Tuple[List[str], List[dict]]:
    text = text.lstrip("\ufeff")
    if "\x00" in text:
        raise ValueError("拓扑表包含二进制内容（NUL 字节），不是文本型 CSV")
    try:
        reader = csv.reader(io.StringIO(text))
        rows = [r for r in reader if any((c or "").strip() for c in r)]
    except csv.Error as exc:
        raise ValueError(f"拓扑表解析失败：{exc}") from exc
    if not rows:
        return [], []
    headers = [str(c).strip() for c in rows[0]]
    idx: Dict[str, int] = {}
    for key, aliases in HEADER_ALIASES.items():
        for i, h in enumerate(headers):
            if _norm(h) in {_norm(a) for a in aliases}:
                idx[key] = i
                break
    out: List[dict] = []
    for offset, row in enumerate(rows[1:]):
        item = {"lineno": offset + 2}
        for key in HEADER_ALIASES:
            i = idx.get(key)
            item[key] = str(row[i]).strip() if i is not None and i < len(row) else ""
        out.append(item)
    return headers, out


def validate(text: str, devices: List[dict], filename: str = "") -> dict:
    """devices 来自台账校验结果的 devices_detail（含 cls / errors）。"""
    try:
        headers, edges = parse(text)
    except ValueError as exc:
        return {"filename": filename, "passed": False, "edges": [], "issues": [],
                "rows_total": 0, "valid_edges": 0, "orphans": [],
                "checks": TOPOLOGY_CHECKS, "summary": f"无法解析：{exc}",
                "parse_error": str(exc)}
    if not headers:
        return {"filename": filename, "passed": False, "edges": [], "issues": [],
                "rows_total": 0, "valid_edges": 0, "orphans": [],
                "checks": TOPOLOGY_CHECKS, "summary": "拓扑表为空或无法解析出表头"}

    by_code: Dict[str, dict] = {d["code"]: d for d in devices if d.get("code")}
    switch_codes = {d["code"] for d in devices if d.get("cls") in ("SectionSwitch", "TieSwitch", "Breaker")}

    issues: List[dict] = []
    seen_pairs: Dict[Tuple[str, str], int] = {}
    valid_edges: List[dict] = []

    def add(code: str, lineno: int, msg: str, field_name: str = "") -> None:
        meta = TOPOLOGY_CHECK_INDEX[code]
        issues.append({
            "row": lineno, "level": meta["level"], "code": code,
            "rule": meta["name"], "field": field_name, "message": msg,
        })

    for e in edges:
        a, b, sw = e["from"], e["to"], e["switch"]
        ok = True
        if not a or not b:
            add("T1", e["lineno"], "起点或终点为空", "起点设备/终点设备")
            ok = False
        if a and a == b:
            add("T4", e["lineno"], f"起点与终点都是「{a}」，不能自连", "终点设备")
            ok = False
        for endpoint, label in ((a, "起点设备"), (b, "终点设备")):
            if endpoint and endpoint not in by_code:
                add("T1", e["lineno"], f"{label}「{endpoint}」在设备台账中不存在", label)
                ok = False
        if sw and sw not in switch_codes:
            if sw in by_code:
                add("T3", e["lineno"], f"「{sw}」在台账里的类型是 {by_code[sw].get('type')}，不是开关类设备", "连接开关")
            else:
                add("T3", e["lineno"], f"连接开关「{sw}」在设备台账中不存在", "连接开关")
            ok = False

        pair = tuple(sorted((a, b)))
        if a and b:
            if pair in seen_pairs:
                add("T2", e["lineno"],
                    f"「{a}」与「{b}」之间的边已在第 {seen_pairs[pair]} 行建立，重复定义", "起点设备/终点设备")
                ok = False
            else:
                seen_pairs[pair] = e["lineno"]

        if ok:
            valid_edges.append({"from": a, "to": b, "switch": sw, "lineno": e["lineno"]})

    # ---- T5 连通性 ----
    # 两点需要说明：
    #   1. 开关是「边上的设备」，本身不应作为端点出现。只要它在 switch 列被引用，
    #      就视为已接入网络，不参与孤岛判定——否则每台开关都会被误报为孤立设备。
    #   2. 台区与用户属于低压侧，不在 10kV 电气拓扑表里，其归属关系由台账的「上级设备」表达。
    #      所以把这些从属关系作为固定连接补进邻接表，但只补低压侧这几类，
    #      避免掩盖拓扑表漏填线路段之间连接的问题。
    adj: Dict[str, Set[str]] = defaultdict(set)
    for e in valid_edges:
        adj[e["from"]].add(e["to"])
        adj[e["to"]].add(e["from"])

    attached_switches = {e["switch"] for e in valid_edges if e.get("switch")}
    for d in devices:
        if d.get("cls") in LOW_VOLTAGE and d.get("parent") and d.get("code"):
            adj[d["parent"]].add(d["code"])
            adj[d["code"]].add(d["parent"])

    sources = [d["code"] for d in devices if d.get("cls") == "Substation"]
    reachable: Set[str] = set()
    queue: deque[str] = deque(sources)
    reachable.update(sources)
    while queue:
        cur = queue.popleft()
        for nxt in adj.get(cur, ()):
            if nxt not in reachable:
                reachable.add(nxt)
                queue.append(nxt)

    orphans = []
    for d in devices:
        code = d.get("code")
        if not code or d.get("cls") in (None, "", "Substation"):
            continue
        if code in attached_switches:
            continue
        if code not in reachable:
            orphans.append(code)

    if orphans:
        shown = "、".join(orphans[:12]) + ("…" if len(orphans) > 12 else "")
        add("T5", 0, f"{len(orphans)} 台设备与电源不连通：{shown}"
                     f"（如为孤立台区可忽略，否则检查拓扑表是否漏边）", "起点设备/终点设备")

    error_rows = {i["row"] for i in issues if i["level"] == "error"}
    passed = len(error_rows) == 0
    summary = (f"共 {len(edges)} 条边，{len(valid_edges)} 条有效"
               + (f"，{len(error_rows)} 行存在错误" if error_rows else "")
               + (f"，{len(orphans)} 台设备未连通" if orphans else ""))

    return {
        "filename": filename,
        "passed": passed,
        "rows_total": len(edges),
        "valid_edges": len(valid_edges),
        "edges": valid_edges,
        "issues": sorted(issues, key=lambda i: (i["row"], i["code"])),
        "orphans": orphans,
        "checks": TOPOLOGY_CHECKS,
        "summary": summary,
    }
