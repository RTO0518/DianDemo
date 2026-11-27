# -*- coding: utf-8 -*-
"""OpenAI 协议兼容的模型接入（在线 API 与本地推理框架共用一份实现）。"""
from __future__ import annotations

import json
from typing import List, Optional

from openai import OpenAI

from .base import LLMResponse, ToolCall


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str, model: str, temperature: float = 0.1, timeout: float = 60.0):
        # 本地 vLLM / Ollama / Xinference 多数不校验 key，这里给个占位值避免 SDK 报错
        self.client = OpenAI(base_url=base_url, api_key=api_key or "not-needed", timeout=timeout)
        self.base_url = base_url
        self.model = model
        self.temperature = temperature

    def describe(self) -> dict:
        return {"provider": self.name, "model": self.model, "base_url": self.base_url}

    def chat(self, messages: List[dict], tools: Optional[List[dict]] = None) -> LLMResponse:
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0].message

        tool_calls: List[ToolCall] = []
        for call in (choice.tool_calls or []):
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=call.id, name=call.function.name, arguments=args))

        raw = choice.model_dump(exclude_none=True)
        return LLMResponse(
            content=choice.content or "",
            tool_calls=tool_calls,
            raw_message=raw,
            usage={
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
                "completion_tokens": getattr(resp.usage, "completion_tokens", None),
            } if resp.usage else {},
        )
