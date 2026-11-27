# -*- coding: utf-8 -*-
"""对话接口。"""
from __future__ import annotations

from fastapi import APIRouter

from ..agent.llm import provider_status
from ..agent.orchestrator import GridAgent
from ..agent.tools import TOOL_SCHEMAS
from .schemas import ChatRequest

router = APIRouter()

_AGENT = GridAgent()


@router.get("/status")
def status() -> dict:
    return {
        "provider": provider_status(),
        "tools": [
            {"name": t["function"]["name"], "description": t["function"]["description"]}
            for t in TOOL_SCHEMAS
        ],
    }


@router.post("")
def chat(payload: ChatRequest) -> dict:
    history = [{"role": t.role, "content": t.content} for t in payload.history]
    return _AGENT.chat(payload.message, history)
