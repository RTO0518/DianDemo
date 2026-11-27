# -*- coding: utf-8 -*-
"""
Agent 编排：LLM → 工具调用 → 推理引擎 → 结果回填 → LLM 组织语言。

关键约束：模型不参与计算。它只能决定「调哪个工具」，数字由引擎给出。
每次对话都返回完整的 trace，供界面展示推理链、供人工复核。
"""
from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from .. import audit
from ..config import settings
from ..reasoner.rules import RULE_INDEX
from .llm import get_provider
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS, call_tool


class GridAgent:
    def __init__(self, provider=None):
        self.provider = provider or get_provider()

    # ------------------------------------------------------------------
    def _summarize_result(self, name: str, result: dict) -> str:
        """给界面看的一句话结果摘要，不含长文本。"""
        if "error" in result:
            return f"失败：{result['error']}"
        if name == "simulate_switch":
            return f"{result.get('switch', {}).get('id', '')} → 失电 {result.get('households', 0)} 户"
        if name == "plan_transfer":
            plans = result.get("plans", [])
            if not plans:
                return "无可用转供通道"
            p = plans[0]
            return (f"{p['via_switch']} → {p['to_feeder']}，转移 {p['transferred_kw']:.0f} kW，"
                    f"{'可行' if p['feasible'] else '不可行'}")
        if name == "check_overload":
            return f"过载 {len(result.get('overloaded', []))} 台，重载 {len(result.get('heavy', []))} 台"
        if name == "query_device":
            return f"命中 {result.get('count', 0)} 台设备"
        if name == "get_ontology":
            return f"{result.get('class_count', 0)} 个类，{len(result.get('axioms', []))} 条公理"
        if name == "list_rules":
            return f"{result.get('count', 0)} 条规则"
        if name == "maintenance_scope":
            return f"{result.get('switch')} 停电 {result.get('households')} 户"
        if name == "fault_analysis":
            return f"{result.get('trip_switch')} 跳闸，{result.get('households')} 户失电"
        if name == "consistency_check":
            return "校验通过" if result.get("passed") else f"发现 {len(result.get('issues', []))} 处问题"
        if name == "transformer_relief":
            return f"{result.get('transformer', {}).get('label', '')} 超出 {result.get('excess_kw', 0):.0f} kW"
        return "完成"

    # ------------------------------------------------------------------
    def chat(self, message: str, history: Optional[List[dict]] = None) -> dict:
        started = time.time()
        messages: List[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for turn in (history or [])[-8:]:
            role = turn.get("role")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        tool_log: List[dict] = []
        trace: List[dict] = []
        answer = ""
        error = ""

        try:
            for _ in range(settings.max_tool_rounds):
                resp = self.provider.chat(messages, TOOL_SCHEMAS)
                if not resp.tool_calls:
                    answer = resp.content.strip()
                    break

                messages.append({
                    "role": "assistant",
                    "content": resp.content or "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                        for call in resp.tool_calls
                    ],
                })

                for call in resp.tool_calls:
                    t0 = time.time()
                    result = call_tool(call.name, call.arguments)
                    elapsed = int((time.time() - t0) * 1000)
                    tool_log.append({
                        "name": call.name,
                        "arguments": call.arguments,
                        "summary": self._summarize_result(call.name, result),
                        "ok": "error" not in result,
                        "elapsed_ms": elapsed,
                    })
                    for step in result.get("trace", []) or []:
                        if step not in trace:
                            trace.append(step)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
            else:
                answer = "问题涉及的步骤较多，已超出单次分析轮次。请把问题拆成单步再问，例如先问「S1 断开影响多少户」，再问「能否转供」。"
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            answer = ("模型调用失败：" + error +
                      "\n\n如果是现场演示，可把 .env 中的 LLM_PROVIDER 改为 mock 使用离线推理，功能与数字不受影响。")

        if not answer:
            answer = "未能生成回答，请换个问法或检查模型配置。"

        elapsed_ms = int((time.time() - started) * 1000)
        rules_cited = sorted({t["rule"] for t in trace if t.get("rule") in RULE_INDEX})

        audit.record(
            "chat",
            "智能问答",
            (answer or "")[:160].replace("\n", " "),
            rules=rules_cited,
            elapsed_ms=elapsed_ms,
            ok=not error,
            detail={
                "question": message,
                "tools": [t["name"] for t in tool_log],
                "provider": self.provider.describe().get("model", ""),
                "error": error or None,
            },
        )

        return {
            "answer": answer,
            "tool_calls": tool_log,
            "trace": trace,
            "provider": self.provider.describe(),
            "elapsed_ms": elapsed_ms,
            "error": error,
            "rules_cited": rules_cited,
        }
