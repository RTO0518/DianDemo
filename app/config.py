# -*- coding: utf-8 -*-
"""运行配置。所有密钥只从环境变量/.env 读取，不进代码、不进仓库。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class Settings:
    # 模型提供方：openai_compat | mock
    llm_provider: str = field(default_factory=lambda: _get("LLM_PROVIDER", "mock").lower())
    llm_base_url: str = field(default_factory=lambda: _get("LLM_BASE_URL", "https://api.deepseek.com/v1"))
    llm_api_key: str = field(default_factory=lambda: _get("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: _get("LLM_MODEL", "deepseek-chat"))
    llm_temperature: float = field(default_factory=lambda: float(_get("LLM_TEMPERATURE", "0.1") or 0.1))
    llm_timeout: float = field(default_factory=lambda: float(_get("LLM_TIMEOUT", "60") or 60))
    max_tool_rounds: int = field(default_factory=lambda: int(_get("MAX_TOOL_ROUNDS", "5") or 5))
    host: str = field(default_factory=lambda: _get("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_get("PORT", "8000") or 8000))

    @property
    def llm_ready(self) -> bool:
        return self.llm_provider == "openai_compat" and bool(self.llm_api_key)

    def public_info(self) -> dict:
        """给前端展示的模型信息，绝不返回密钥。"""
        return {
            "provider": self.llm_provider,
            "model": self.llm_model if self.llm_provider != "mock" else "离线规则引擎（mock）",
            "base_url": self.llm_base_url if self.llm_provider != "mock" else "-",
            "ready": self.llm_ready or self.llm_provider == "mock",
            "mode": "online" if self.llm_ready else "offline",
        }


settings = Settings()
