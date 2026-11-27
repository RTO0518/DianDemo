# -*- coding: utf-8 -*-
"""
大模型适配层。

这是留给甲方做本地化部署的那个接口：上层 Agent 只依赖 LLMProvider 协议，
换模型只需要改 .env 里的 LLM_BASE_URL，代码零改动。

    LLM_PROVIDER=openai_compat
    LLM_BASE_URL=https://api.deepseek.com/v1      # 在线
    LLM_BASE_URL=http://127.0.0.1:8000/v1         # 本地 vLLM
    LLM_BASE_URL=http://127.0.0.1:11434/v1        # 本地 Ollama
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw_message: Optional[dict] = None
    usage: Dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    """模型提供方协议。实现方只需保证 chat() 的输入输出形状一致。"""

    name: str

    def chat(self, messages: List[dict], tools: Optional[List[dict]] = None) -> LLMResponse:
        ...

    def describe(self) -> dict:
        ...
