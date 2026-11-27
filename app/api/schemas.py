# -*- coding: utf-8 -*-
"""接口请求体定义。"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class SwitchAction(BaseModel):
    switch_id: str = Field(..., description="开关编号，如 S1")
    state: str = Field("open", description="open=分闸，closed=合闸")


class TransferRequest(BaseModel):
    switch_id: str = Field("", description="导致失电的开关编号；与 states 二选一")
    state: str = Field("open")
    target_feeder_load_kw: Optional[float] = Field(
        None, ge=0, le=1e7,
        description="可选：模拟对侧馈线高峰负荷（kW），用于检验重载工况；必须为非负数",
    )
    states: Dict[str, str] = Field(default_factory=dict, description="可选：整组开关状态，优先于 switch_id")


class ReliefRequest(BaseModel):
    transformer_id: str


class OrderRequest(BaseModel):
    order_id: str


class FaultRequest(BaseModel):
    fault_id: str


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: List[ChatTurn] = Field(default_factory=list)


class WorkState(BaseModel):
    states: Dict[str, str] = Field(default_factory=dict, description="开关状态映射，如 {\"S1\": \"open\"}")


class ThresholdUpdate(BaseModel):
    values: Dict[str, float] = Field(
        ...,
        description="要修改的阈值，键见 GET /api/reason/thresholds 的 items[].key，"
                    "如 {\"overload_rate\": 1.1, \"heavy_rate\": 0.85}",
    )


class SparqlQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=8000, description="SPARQL 1.1 查询语句")
    limit: int = Field(200, ge=1, le=2000, description="返回行数上限")

