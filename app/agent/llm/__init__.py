# -*- coding: utf-8 -*-
"""模型工厂：按配置返回在线或离线实现，业务层无需感知差别。"""
from __future__ import annotations

from .base import LLMProvider
from .mock import MockProvider
from .openai_compat import OpenAICompatProvider
from ...config import settings


def get_provider() -> LLMProvider:
    if settings.llm_provider == "openai_compat" and settings.llm_api_key:
        return OpenAICompatProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout,
        )
    return MockProvider()


def provider_status() -> dict:
    info = settings.public_info()
    if settings.llm_provider == "openai_compat" and not settings.llm_api_key:
        info["note"] = "已配置在线模型但缺少 LLM_API_KEY，已自动降级为离线模式"
    return info
