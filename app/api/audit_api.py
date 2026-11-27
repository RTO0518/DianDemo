# -*- coding: utf-8 -*-
"""运行审计接口：推理记录、对话记录、SPARQL 查询留痕。"""
from __future__ import annotations

from fastapi import APIRouter, Query

from .. import audit

router = APIRouter()


@router.get("/records")
def records(
    kind: str = Query("", description="chat / reasoning / sparql / ingest，留空为全部"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    keyword: str = Query("", description="全字段关键字过滤"),
) -> dict:
    return audit.query(kind=kind, limit=limit, offset=offset, keyword=keyword)


@router.get("/summary")
def summary() -> dict:
    return audit.summary()


@router.post("/clear")
def clear() -> dict:
    n = audit.clear()
    audit.record("system", "清空审计", f"清除 {n} 条历史审计记录", detail={"cleared": n})
    return {"cleared": n, "message": "演示重置：审计记录已清空"}
