# -*- coding: utf-8 -*-
"""
离线兜底模型。

存在的理由很实际：甲方会议室可能没网、在线 API 可能限流，现场演示不能因此中断。
它不生成任何数字，只做两件事——把问题映射成工具调用，把工具结果组织成中文表述。
换句话说，即使没有大模型，推理链路依然完整，只是「翻译」环节退化成正则匹配。
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from .base import LLMResponse, ToolCall

_SWITCH_RE = re.compile(r"\b(BR\d|S\d|L\d)\b", re.IGNORECASE)
_ORDER_RE = re.compile(r"(GD)[-_ ]?(2026)[-_ ]?(\d{3})", re.IGNORECASE)
_FAULT_RE = re.compile(r"\b(F\d)\b", re.IGNORECASE)
_TR_RE = re.compile(r"(?:配变)\s*([A-H])", re.IGNORECASE)


class MockProvider:
    name = "mock"

    def __init__(self) -> None:
        self._seq = 0

    def describe(self) -> dict:
        return {"provider": "mock", "model": "离线规则引擎", "base_url": "-"}

    # ------------------------------------------------------------------
    def _next_id(self) -> str:
        self._seq += 1
        return f"call_mock_{self._seq}"

    def _intent(self, text: str) -> List[ToolCall]:
        t = text.strip()
        switch_match = _SWITCH_RE.search(t)
        switch_id = switch_match.group(1).upper() if switch_match else ""
        order_match = _ORDER_RE.search(t)
        fault_match = _FAULT_RE.search(t)
        tr_match = _TR_RE.search(t)

        if order_match:
            oid = f"GD_{order_match.group(2)}_{order_match.group(3)}"
            return [ToolCall(self._next_id(), "maintenance_scope", {"order_id": oid})]

        if "检修" in t or "工单" in t:
            return [ToolCall(self._next_id(), "maintenance_scope", {"order_id": "GD_2026_018"})]

        if fault_match or "故障" in t:
            return [ToolCall(self._next_id(), "fault_analysis", {"fault_id": (fault_match.group(1).upper() if fault_match else "F1")})]

        if tr_match and ("过载" in t or "重载" in t or "切换" in t or "备用" in t):
            return [ToolCall(self._next_id(), "transformer_relief", {"transformer_id": f"TR_{tr_match.group(1).upper()}"})]

        if "转供" in t or "联络" in t or "倒负荷" in t or "备用线路" in t:
            args: Dict[str, object] = {"switch_id": switch_id or "S1", "state": "open"}
            peak = re.search(r"(\d{3,4})\s*kW", t)
            if peak and ("高峰" in t or "负荷" in t):
                args["target_feeder_load_kw"] = float(peak.group(1))
            return [ToolCall(self._next_id(), "plan_transfer", args)]

        if "过载" in t or "重载" in t or "负载率" in t:
            return [ToolCall(self._next_id(), "check_overload", {})]

        if "失效" in t or "失电" in t or "停电" in t or "断开" in t or "跳闸" in t or "分闸" in t:
            return [ToolCall(self._next_id(), "simulate_switch", {"switch_id": switch_id or "S1", "state": "open"})]

        if "本体" in t or "公理" in t or "类" in t:
            return [ToolCall(self._next_id(), "get_ontology", {})]

        if "规则" in t:
            return [ToolCall(self._next_id(), "list_rules", {})]

        if "校验" in t or "数据质量" in t or "一致" in t:
            return [ToolCall(self._next_id(), "consistency_check", {})]

        if switch_id or "配变" in t or "台区" in t or "馈线" in t:
            kw = switch_id or tr_match.group(0) if tr_match else switch_id or "F01"
            return [ToolCall(self._next_id(), "query_device", {"keyword": kw})]

        return []

    # ------------------------------------------------------------------
    @staticmethod
    def _fmt_rate(v: Optional[float]) -> str:
        return f"{v:.1%}" if isinstance(v, (int, float)) else "-"

    def _compose(self, tool_name: str, data: dict) -> str:
        if "error" in data:
            return f"工具调用失败：{data['error']}"

        if tool_name == "simulate_switch":
            districts = "、".join(f"{d['label']}（{d['households']} 户）" for d in data.get("districts", []))
            critical = "、".join(c["label"] for c in data.get("critical_customers", []))
            sw = data.get("switch") or {}
            state_cn = "分位（断开）" if sw.get("to") == "open" else "合位（闭合）"
            parts = [
                f"{sw.get('id', '')} {state_cn}后，失电台区 {len(data.get('districts', []))} 个，"
                f"共 {data.get('households', 0)} 户（占全网 {data.get('total_households', 0)} 户的 "
                f"{data.get('ratio', 0):.1%}）。",
                f"受影响台区：{districts or '无'}。",
            ]
            if data.get("affected_feeders"):
                parts.append(f"涉及馈线：{'、'.join(data['affected_feeders'])}。")
            if critical:
                parts.append(f"重要用户：{critical}，需优先保障。")
            trace = data.get("trace", [])
            if trace:
                parts.append("推理依据：" + "；".join(t["detail"] for t in trace[:3]) + "。")
            return "".join(parts)

        if tool_name == "plan_transfer":
            plans = data.get("plans", [])
            if not plans:
                return data.get("message") or "失电区没有可用的联络通道，不具备转供条件。"
            p = plans[0]
            if p["feasible"]:
                return (
                    f"可以转供。建议合上 {p['via_switch_label']}（{p['via_switch']}），"
                    f"把 {p['from_feeder']} 的负荷转由 {p['to_feeder_label']} 供电："
                    f"转移 {p['transferred_kw']:.0f} kW，恢复 {p['restored_households']} 户，"
                    f"对侧负载率由 {self._fmt_rate(p['target_rate_before'])} 升至 "
                    f"{self._fmt_rate(p['target_rate_after'])}，转供后备用 {p['spare_kw']:.0f} kW。"
                    f"复核结果：剩余失电 {p['recheck']['remaining_households']} 户。"
                )
            return (
                f"不能转供。对侧备用容量仅 {p['spare_kw']:.0f} kW，而需转移 {p['transferred_kw']:.0f} kW，"
                f"缺口 {p['gap_kw']:.0f} kW；强行转供会使对侧负载率达到 "
                f"{self._fmt_rate(p['target_rate_after'])}。建议先压减 {p['gap_kw']:.0f} kW 负荷，或调用发电车保电。"
            )

        if tool_name == "check_overload":
            over = data.get("overloaded", [])
            heavy = data.get("heavy", [])
            seg = []
            seg.append("过载配变：" + ("、".join(
                f"{x['label']}（{x['load_kw']:.0f}/{x['rated_kw']:.0f} kW，{x['rate']:.0%}）" for x in over
            ) or "无"))
            seg.append("重载配变：" + ("、".join(
                f"{x['label']}（{x['load_kw']:.0f}/{x['rated_kw']:.0f} kW，{x['rate']:.0%}）" for x in heavy
            ) or "无"))
            feeders = data.get("feeders", [])
            if feeders:
                seg.append("馈线负载率：" + "、".join(
                    f"{f['label']} {f['rate']:.0%}" for f in feeders
                ))
            return "；".join(seg) + "。"

        if tool_name == "transformer_relief":
            t = data.get("transformer", {})
            opts = data.get("options", [])
            return (
                f"{t.get('label', '')} 当前 {t.get('load_kw', 0):.0f} kW / {t.get('rated_kw', 0):.0f} kW，"
                f"负载率 {self._fmt_rate(t.get('rate'))}，超出额定 {data.get('excess_kw', 0):.0f} kW。"
                f"不能靠转供解决：台区恰好一台配变（公理 A5），10kV 联络转移的是线路段负荷，"
                f"无法把单个台区的低压负荷切给另一台配变。建议：{opts[0]['detail'] if opts else '切负荷'}。"
            )

        if tool_name == "consistency_check":
            return data.get("summary", "") + (
                "，数据满足基数约束。" if data.get("passed") else
                "：" + "；".join(i["message"] for i in data.get("issues", []))
            )

        if tool_name == "maintenance_scope":
            return (
                f"工单 {data.get('order_label', data.get('order'))} 检修 {data.get('equipment')}，"
                f"需断开 {data.get('switch')}；计划停电影响 {data.get('households')} 户，"
                f"涉及 {'、'.join(data.get('districts', []))}；"
                + ("存在可行转供方案。" if data.get("feasible_transfer") else "该支线无联络通道，无法转供，建议发电车保电或调整工期。")
            )

        if tool_name == "fault_analysis":
            return (
                f"故障 {data.get('fault_label', '')} 位于 {data.get('equipment')}，"
                f"上游 {data.get('trip_switch')} 保护动作跳闸，造成 {data.get('households')} 户失电"
                f"（{'、'.join(data.get('districts', []))}）。"
                + (data.get("backfeed_suggestion") or "无转供通道，等待抢修。")
            )

        if tool_name == "get_ontology":
            if "matched" in data:
                return "匹配到的类：" + "、".join(f"{c['label']}（{c['name']}）" for c in data["matched"])
            return (
                f"本体共 {data.get('class_count', 0)} 个类、{len(data.get('axioms', []))} 条公理。"
                f"关键公理包括：" + "；".join(f"{a['id']} {a['label']}" for a in data.get("axioms", [])[:6]) + "。"
            )

        if tool_name == "list_rules":
            return f"共 {data.get('count', 0)} 条推理规则：" + "；".join(
                f"{r['id']} {r['name']}" for r in data.get("rules", [])
            ) + "。"

        if tool_name == "query_device":
            devices = data.get("devices", [])
            if not devices:
                return f"没有找到与「{data.get('keyword')}」匹配的设备。"
            d = devices[0]
            extra = d.get("extra", {})
            desc = f"{d['label']}（{d['id']}，{d.get('classes', [''])[0]}）"
            if "rate" in extra:
                desc += f"，负载率 {self._fmt_rate(extra['rate'])}"
            if "households" in extra:
                desc += f"，{extra['households']} 户"
            if "state" in extra:
                desc += f"，当前{'合位' if extra['state'] == 'closed' else '分位'}"
            return desc + "。"

        return json.dumps(data, ensure_ascii=False)[:600]

    # ------------------------------------------------------------------
    def chat(self, messages: List[dict], tools: Optional[List[dict]] = None) -> LLMResponse:
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        if tool_messages:
            name = tool_messages[-1].get("name", "")
            try:
                data = json.loads(tool_messages[-1].get("content", "{}"))
            except json.JSONDecodeError:
                data = {}
            return LLMResponse(content=self._compose(name, data))

        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = m.get("content", "")
                break

        calls = self._intent(user_text)
        if calls:
            return LLMResponse(content="", tool_calls=calls)

        return LLMResponse(content=(
            "我是配电网本体推理助手，可以回答这几类问题：\n"
            "1. 开关动作影响：某台开关断开后哪些台区、多少户失电；\n"
            "2. 负荷转供：能不能转、经哪台联络开关、转移多少负荷、对侧会不会过载；\n"
            "3. 过载分析：哪些配变过载或重载，怎么处置；\n"
            "4. 检修与故障：工单的停电范围、故障的跳闸开关与恢复建议；\n"
            "5. 本体本身：有哪些类、哪些公理、哪些推理规则。\n"
            "例如可以问：S1 断开后哪些用户失电？合上 L1 能转供多少户？"
        ))
