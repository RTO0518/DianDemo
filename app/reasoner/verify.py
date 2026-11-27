# -*- coding: utf-8 -*-
"""
推理验证用例（需求文档第 2 项）。

前 5 题是需求文档要求的核心题，后 3 题是评审现场最可能追问的扩展题：
可行转供、不可行转供、无解场景。每题都给出「预期答案」与「机器实际输出」，
两者一致才算本体真的支持推理。
"""
from __future__ import annotations

import threading
from typing import Callable, List, Optional

from .engine import ReasoningEngine, get_engine
from .thresholds import get_thresholds

# run_all 会临时把阈值切回基线再还原。多个请求同时进来时，
# 后一个可能把前一个的还原动作覆盖掉，导致演示现场设定的阈值被悄悄改回基线。
_VERIFY_LOCK = threading.Lock()


def _fmt_districts(districts: List[dict]) -> str:
    return "、".join(f"{d['label']}({d['households']}户)" for d in districts) or "无"


def q1(engine: ReasoningEngine) -> dict:
    r = engine.outage("S1", "open")
    return {
        "expected": "LS2、LS3 及所带 3 个台区失电：金桥小区 500 户、阳光花园 400 户、城东工业园 300 户，合计 1200 户",
        "actual": f"失电台区 {len(r['affected_districts'])} 个：{_fmt_districts(r['affected_districts'])}，合计 {r['households']} 户",
        "passed": r["households"] == 1200
                  and sorted(d["id"] for d in r["affected_districts"]) == ["TA", "TB", "TC"],
        "data": {"households": r["households"], "districts": [d["id"] for d in r["affected_districts"]]},
        "trace": r["trace"],
    }


def q2(engine: ReasoningEngine) -> dict:
    r = engine.outage("S1", "open")
    p = engine.transfer_plans("S1", "open")
    plan = p["plans"][0] if p["plans"] else None
    ok = bool(plan and plan["feasible"] and plan["restored_households"] == r["households"])
    return {
        "expected": "合上联络开关 L1 可由 F02 线转供，转移 1000 kW，恢复 1200 户，F02 负载率由 20% 升至 45%",
        "actual": (f"{plan['via_switch_label']} 合闸 → 由 {plan['to_feeder_label']} 转供，转移 "
                   f"{plan['transferred_kw']:.0f} kW，恢复 {plan['restored_households']} 户，"
                   f"对侧负载率 {plan['target_rate_before']:.0%} → {plan['target_rate_after']:.0%}，"
                   f"复核剩余失电 {plan['recheck']['remaining_households']} 户") if plan else "未找到可行方案",
        "passed": ok,
        "data": plan or {},
        "trace": p["trace"],
    }


def q3(engine: ReasoningEngine) -> dict:
    r = engine.load_report()
    over = [x["id"] for x in r["overloaded"]]
    heavy = [x["id"] for x in r["heavy"]]
    return {
        "expected": "配变 B 过载（420/400 = 105%）；配变 F 重载（200/250 = 80%）；其余配变正常",
        "actual": "过载：" + ("、".join(f"{x['label']} {x['rate']:.0%}" for x in r["overloaded"]) or "无")
                  + "；重载：" + ("、".join(f"{x['label']} {x['rate']:.0%}" for x in r["heavy"]) or "无"),
        "passed": over == ["TR_B"] and heavy == ["TR_F"],
        "data": {"overloaded": over, "heavy": heavy},
        "trace": r["trace"],
    }


def q4(engine: ReasoningEngine) -> dict:
    p = engine.transfer_plans("BR1", "open")
    plan = p["plans"][0] if p["plans"] else None
    return {
        "expected": "F01 线出口断路器跳闸，全线 1830 户失电；经 L1 将 1700 kW 全部转至 F02，F02 负载率 62.5%",
        "actual": (f"经 {plan['via_switch']} 转供 {plan['transferred_kw']:.0f} kW 至 {plan['to_feeder_label']}，"
                   f"恢复 {plan['restored_households']} 户，对侧负载率 {plan['target_rate_after']:.1%}，"
                   f"{'可行' if plan['feasible'] else '不可行'}") if plan else "未找到方案",
        "passed": bool(plan and plan["feasible"] and plan["transferred_kw"] == 1700
                       and round(plan["target_rate_after"], 3) == 0.625),
        "data": plan or {},
        "trace": p["trace"],
    }


def q5(engine: ReasoningEngine) -> dict:
    p = engine.transfer_plans("S1", "open", feeder_load_override={"FY": 3400})
    plan = p["plans"][0] if p["plans"] else None
    return {
        "expected": "F02 晚高峰负荷 3400 kW 时备用仅 600 kW，转供 1000 kW 缺口 400 kW，判定不可行",
        "actual": (f"备用 {plan['spare_kw']:.0f} kW < 转移 {plan['transferred_kw']:.0f} kW，"
                   f"缺口 {plan['gap_kw']:.0f} kW，转供后负载率将达 {plan['target_rate_after']:.0%}，判定不可行")
                  if plan else "未找到方案",
        "passed": bool(plan and not plan["feasible"] and plan["gap_kw"] == 400),
        "data": plan or {},
        "trace": p["trace"],
    }


def q6(engine: ReasoningEngine) -> dict:
    r = engine.maintenance_scope("GD_2026_018")
    outage = r["outage"]
    return {
        "expected": "GD-2026-018 检修 LS3 需断开 S2，仅城东工业园台区 300 户停电；该支线末端无联络，无法转供",
        "actual": (f"断开 {r['switch']}，影响 {outage['households']} 户"
                   f"（{_fmt_districts(outage['affected_districts'])}），"
                   f"可行转供方案 {r['transfer']['feasible_count']} 个"),
        "passed": outage["households"] == 300 and r["transfer"]["feasible_count"] == 0,
        "data": {"households": outage["households"], "feasible": r["transfer"]["feasible_count"]},
        "trace": r["trace"],
    }


def q7(engine: ReasoningEngine) -> dict:
    r = engine.transformer_relief("TR_B")
    return {
        "expected": "不能转供：台区恰好一台配变（公理 A5），10kV 联络开关转不了低压单台配变负荷；建议切负荷 20 kW 或增容",
        "actual": (f"可转供：否；超出额定 {r['excess_kw']:.0f} kW；"
                   f"处置建议：{r['options'][0]['detail']}"),
        "passed": r["can_transfer"] is False and r["excess_kw"] == 20,
        "data": r,
        "trace": r["trace"],
    }


def q8(engine: ReasoningEngine) -> dict:
    r = engine.consistency_check()
    return {
        "expected": "6 类基数与口径校验全部通过，无数据质量问题",
        "actual": r["summary"],
        "passed": r["passed"] and len(r["issues"]) == 0,
        "data": {"issues": r["issues"], "checks": r["checks"]},
        "trace": r["trace"],
    }


QUESTIONS: List[dict] = [
    {"id": "Q1", "question": "S1 分段开关断开后，哪些用户失电？影响多少户？", "rule": "R1/R2/R3", "run": q1},
    {"id": "Q2", "question": "S1 断开后能否通过联络开关转供？能恢复多少户？", "rule": "R5/R6/R12", "run": q2},
    {"id": "Q3", "question": "当前哪些配变过载或重载？", "rule": "R4", "run": q3},
    {"id": "Q4", "question": "F01 线出口断路器跳闸，最优转供方案是什么？", "rule": "R5/R6", "run": q4},
    {"id": "Q5", "question": "若 F02 线晚高峰负荷 3400 kW，还能转供吗？", "rule": "R5", "run": q5},
    {"id": "Q6", "question": "检修工单 GD-2026-018 会造成多大范围停电？能否转供？", "rule": "R8", "run": q6},
    {"id": "Q7", "question": "配变 B 过载，应切换到哪条备用线路？", "rule": "R4/R10", "run": q7},
    {"id": "Q8", "question": "当前实例数据是否满足本体的基数约束？", "rule": "R10", "run": q8},
]


def run_all(engine: Optional[ReasoningEngine] = None) -> dict:
    """
    跑全部基准题。

    这 8 道题的预期答案是在基线阈值（过载 100%、重载 80%、转供上限 100%）下得出的，
    所以跑之前临时切回基线，跑完把现场正在用的阈值原样放回去：
    演示时把阈值调到 90% 观察结论变化，不应该让验证页因此全红。
    """
    engine = engine or get_engine()
    th = get_thresholds()
    results = []
    with _VERIFY_LOCK:
        in_use = th.current()
        base = th.current(baseline=True)
        th.restore(base)
        try:
            for item in QUESTIONS:
                try:
                    outcome = item["run"](engine)
                except Exception as exc:  # 单题失败不影响其余题目
                    outcome = {"expected": "-", "actual": f"推理异常：{exc}", "passed": False,
                               "data": {}, "trace": []}
                results.append({
                    "id": item["id"],
                    "question": item["question"],
                    "rule": item["rule"],
                    **outcome,
                })
        finally:
            th.restore(in_use)
    passed = sum(1 for r in results if r["passed"])
    return {
        "results": results,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "summary": f"{passed}/{len(results)} 道推理题通过",
        "threshold_baseline": base,
    }


if __name__ == "__main__":
    report = run_all()
    for r in report["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"[{mark}] {r['id']} {r['question']}")
        print(f"       预期：{r['expected']}")
        print(f"       实际：{r['actual']}")
    print(report["summary"])
