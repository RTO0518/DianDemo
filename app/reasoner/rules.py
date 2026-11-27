# -*- coding: utf-8 -*-
"""
规则清单与纯函数。

规则编号贯穿全项目：推理链、界面、验证报告、设计文档都引用同一套编号，
保证「一条规则 ↔ 一条公理 ↔ 一段代码」可以互相对上。
"""
from __future__ import annotations

from collections import deque
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .graph import Edge, GridModel
from .thresholds import get_thresholds

# 负载率阈值（规则 R4）不再硬编码：统一从 app/reasoner/thresholds.py 读取。
# 下面两个名字保留，只为兼容旧代码的 `from .rules import OVERLOAD_THRESHOLD`，
# 其值在每次访问时现算（模块级 __getattr__），改配置后不会拿到过期快照。


def __getattr__(name: str) -> float:
    if name == "OVERLOAD_THRESHOLD":
        return get_thresholds().get("overload_rate")
    if name == "HEAVY_LOAD_THRESHOLD":
        return get_thresholds().get("heavy_rate")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

RULES: List[dict] = [
    {
        "id": "R1",
        "name": "供电路径搜索",
        "description": "从变电站母线出发，沿闭合开关做广度优先搜索。凡能被搜索到的设备即为带电设备；"
                       "开关分位构成搜索边界，这是全部失电推理的基础。",
        "axioms": ["A1", "A9"],
        "inputs": ["开关状态", "拓扑关系"],
        "output": "带电设备集合 + 每台设备的最短供电路径",
    },
    {
        "id": "R2",
        "name": "故障传播与失电判定",
        "description": "物理上仍连接电网、但在 R1 中不可达的设备判定为失电。上级开关断开即切断其下游全部设备；"
                       "若存在闭合的联络通道，设备经对侧馈线恢复带电，失电判定自动失效。",
        "axioms": ["A1", "A2", "A12"],
        "inputs": ["R1 的带电集合"],
        "output": "失电设备清单",
    },
    {
        "id": "R3",
        "name": "影响范围与停电户数",
        "description": "把失电配变映射到所属台区，累加台区户数；同时统计用户实体、重要用户数量与停电范围占比。"
                       "台区与配变的一对一约束保证不重不漏。",
        "axioms": ["A5", "A6", "A15"],
        "inputs": ["失电配变"],
        "output": "台区清单、户数、重要用户、停电占比",
    },
    {
        "id": "R4",
        "name": "过载与重载判定",
        "description": "负载率 = 当前负荷 / 额定容量。达到过载阈值（默认 100%）判为过载，"
                       "达到重载阈值（默认 80%）判为重载。阈值来自 data/thresholds.yaml，"
                       "可由甲方在界面实时调整；结论同时写入本体（OverloadedTransformer / HeavyLoadTransformer）。",
        "axioms": ["A13", "A14"],
        "inputs": ["配变与馈线的负荷、容量"],
        "output": "过载清单、重载清单及负载率",
    },
    {
        "id": "R5",
        "name": "负荷转供可行性判定",
        "description": "寻找一端带电、一端失电的联络开关，模拟合闸；校验对侧馈线备用容量（扣除预留比例）"
                       "是否覆盖转移负荷，并复核转供后负载率是否越过 transfer_max_rate。不足时给出容量缺口。",
        "axioms": ["A7", "A8"],
        "inputs": ["失电集合", "联络开关", "对侧馈线容量与负荷"],
        "output": "可行 / 不可行结论 + 容量缺口",
    },
    {
        "id": "R6",
        "name": "转供方案生成与排序",
        "description": "把可行的转供动作组织成方案：经哪台联络开关、转出转入哪条馈线、转移多少负荷、"
                       "恢复多少户、转供前后对侧负载率，并按转供后负载率升序排序。",
        "axioms": ["A7"],
        "inputs": ["R5 的判定结果"],
        "output": "转供方案列表",
    },
    {
        "id": "R7",
        "name": "重要用户优先保障",
        "description": "统计失电范围内的医院、工厂、商业中心等重要负荷，转供方案排序时优先保障；"
                       "重要用户数作为方案评价指标之一输出。",
        "axioms": ["A6"],
        "inputs": ["失电台区下的用户"],
        "output": "重要用户清单与数量",
    },
    {
        "id": "R8",
        "name": "计划检修停电范围推算",
        "description": "由工单指向的设备反查隔离开关，模拟断开后推算计划停电的台区与户数，"
                       "并判断是否存在转供通道。",
        "axioms": ["A10"],
        "inputs": ["检修工单", "被检修设备"],
        "output": "计划停电范围 + 是否可转供",
    },
    {
        "id": "R9",
        "name": "故障—停电—工单闭环",
        "description": "故障事件定位到设备后，推出停电事件与影响台区，并给出应派发的抢修工单与班组，"
                       "形成从故障到处置的完整链路。",
        "axioms": ["A10", "A15"],
        "inputs": ["故障记录"],
        "output": "停电事件 + 建议工单",
    },
    {
        "id": "R10",
        "name": "基数约束一致性校验",
        "description": "校验「馈线必有唯一变电站」「配变必装设于唯一线路段」「台区恰好一台配变」"
                       "「联络开关恰好连接两条馈线」等基数公理，发现数据质量问题。",
        "axioms": ["A3", "A4", "A5", "A7"],
        "inputs": ["全量实例数据"],
        "output": "数据质量问题清单",
    },
    {
        "id": "R11",
        "name": "带电与失电互斥校验",
        "description": "把 R1/R2 的结论断言回本体后，由 OWL 不相交公理校验同一设备不会既带电又失电，"
                       "这是推理结果的自检机制。",
        "axioms": ["A12"],
        "inputs": ["带电集合", "失电集合"],
        "output": "一致性结论",
    },
    {
        "id": "R12",
        "name": "转供后复核",
        "description": "模拟合上联络开关后重跑 R1/R2，核对恢复供电的台区与户数是否与方案预期一致，"
                       "防止方案在纸上成立、在现场不成立。",
        "axioms": ["A1", "A2"],
        "inputs": ["转供方案"],
        "output": "复核结果（恢复户数、是否全部恢复）",
    },
]

RULE_INDEX: Dict[str, dict] = {r["id"]: r for r in RULES}


def bfs_energized(model: GridModel, state: Dict[str, str]) -> Tuple[Set[str], Dict[str, Tuple[str, str | None]]]:
    """
    R1 的实现：从所有变电站出发做广度优先搜索。

    返回 (带电设备集合, 供电路径父指针)。父指针用于向用户解释「这台设备是从哪条路带上电的」。
    """
    visited: Set[str] = set()
    parent: Dict[str, Tuple[str, str | None]] = {}
    queue: deque[str] = deque()

    for sub in model.substations:
        visited.add(sub.id)
        parent[sub.id] = ("", None)
        queue.append(sub.id)

    while queue:
        node = queue.popleft()
        for edge in model.neighbors(node):
            if edge.switch_id and state.get(edge.switch_id, "open") == "open":
                continue
            nxt = edge.other(node)
            if nxt in visited:
                continue
            visited.add(nxt)
            parent[nxt] = (node, edge.switch_id)
            queue.append(nxt)

    return visited, parent


def load_rate(load_kw: float, rated_kw: float) -> float:
    if rated_kw <= 0:
        return 0.0
    return load_kw / rated_kw


def classify_load(rate: float, overload: Optional[float] = None, heavy: Optional[float] = None) -> str:
    """
    R4 的判定区间。

    阈值默认取运行期配置；显式传入则用传入值（验证脚本、场景对比时用来固定口径）。
    """
    if overload is None:
        overload = get_thresholds().get("overload_rate")
    if heavy is None:
        heavy = get_thresholds().get("heavy_rate")
    if rate >= overload:
        return "overloaded"
    if rate >= heavy:
        return "heavy"
    return "normal"


def path_to_source(parent: Dict[str, Tuple[str, str | None]], node: str) -> List[dict]:
    """回溯供电路径，输出可读的链路。"""
    chain: List[dict] = []
    cur = node
    guard = 0
    while cur in parent and guard < 64:
        prev, sw = parent[cur]
        if not prev:
            break
        chain.append({"from": prev, "to": cur, "via_switch": sw})
        cur = prev
        guard += 1
    return list(reversed(chain))
