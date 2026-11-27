# -*- coding: utf-8 -*-
"""
FastAPI 应用入口。

启动：
    D:/Conda_env/EduRAG/python.exe -m uvicorn app.main:app --reload --port 8000
或：
    D:/Conda_env/EduRAG/python.exe run.py
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import audit_api, chat, grid, ingest, ontology, reasoning, sparql
from .config import settings
from .reasoner.engine import get_engine

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

app = FastAPI(
    title="配电网本体模型与知识推理",
    description="基于 OWL 本体的配电网故障传播、过载判定与负荷转供推理系统",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ontology.router, prefix="/api/ontology", tags=["本体"])
app.include_router(grid.router, prefix="/api/grid", tags=["电网"])
app.include_router(reasoning.router, prefix="/api/reason", tags=["推理"])
app.include_router(chat.router, prefix="/api/chat", tags=["对话"])
app.include_router(sparql.router, prefix="/api/sparql", tags=["SPARQL"])
app.include_router(ingest.router, prefix="/api/ingest", tags=["数据接入"])
app.include_router(audit_api.router, prefix="/api/audit", tags=["审计"])


@app.on_event("startup")
def _warmup() -> None:
    """预加载图与引擎，避免首屏请求卡顿。"""
    get_engine()


@app.get("/api/health")
def health() -> dict:
    engine = get_engine()
    return {
        "status": "ok",
        "version": "1.0.0",
        "census": engine.model.census(),
        "provider": settings.public_info(),
    }


@app.exception_handler(Exception)
async def _unhandled(request, exc: Exception) -> JSONResponse:  # pragma: no cover
    return JSONResponse(status_code=500, content={"detail": f"服务端异常：{exc}"})


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(WEB / "index.html"))
