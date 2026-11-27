# -*- coding: utf-8 -*-
"""
运行审计：把「谁在什么时候让系统做了什么、依据哪条规则、得出什么结论」留痕。

为什么演示系统也要做这件事
--------------------------
甲方服务的是电力公司，只要涉及调度辅助，第一句就会问「你这结论能不能追溯」。
本项目把可追溯性做成三层：

    1. 单次结论级  —— 每个推理返回值都带 trace（规则编号 + 依据 + 数据）；
    2. 会话级      —— Agent 每次对话返回完整工具调用日志；
    3. 系统级      —— 本模块，把上述两层落到磁盘，可按时间/类型/规则检索。

写入采用 JSONL 追加，不上数据库：演示环境零依赖、可直接用记事本看、便于甲方拷走核对。
审计失败绝不影响主流程（吞异常），这是刻意设计——审计是旁路，不是关键路径。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
AUDIT_FILE = ROOT / "data" / "audit.jsonl"
MAX_BYTES = 4 * 1024 * 1024  # 单文件上限，超过后轮转为 .1

_LOCK = threading.Lock()
_SEQ = 0


def _next_seq() -> int:
    global _SEQ
    with _LOCK:
        _SEQ += 1
        return _SEQ


def _rotate_if_needed() -> None:
    try:
        if AUDIT_FILE.exists() and AUDIT_FILE.stat().st_size > MAX_BYTES:
            backup = AUDIT_FILE.with_suffix(".jsonl.1")
            if backup.exists():
                backup.unlink()
            os.replace(AUDIT_FILE, backup)
    except OSError:
        pass


def record(
    kind: str,
    action: str,
    summary: str,
    *,
    rules: Optional[List[str]] = None,
    elapsed_ms: int = 0,
    ok: bool = True,
    detail: Optional[dict] = None,
    actor: str = "web",
) -> Optional[dict]:
    """
    写一条审计记录。返回写入的记录（便于调用方回显），失败返回 None。
    kind: chat / reasoning / sparql / ingest / system
    """
    try:
        entry = {
            "seq": _next_seq(),
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "kind": kind,
            "actor": actor,
            "action": action,
            "summary": summary,
            "rules": sorted(set(rules or [])),
            "ok": bool(ok),
            "elapsed_ms": int(elapsed_ms),
        }
        if detail:
            entry["detail"] = detail

        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            _rotate_if_needed()
            with AUDIT_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
    except Exception:  # noqa: BLE001 - 审计是旁路，绝不能拖垮业务
        return None


def _read_all() -> List[dict]:
    if not AUDIT_FILE.exists():
        return []
    rows: List[dict] = []
    try:
        with AUDIT_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # 半行（写入中断）直接跳过，不影响后续
    except OSError:
        return []
    return rows


def query(kind: str = "", limit: int = 50, offset: int = 0, keyword: str = "") -> dict:
    """倒序返回最近记录。"""
    rows = _read_all()
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    if keyword:
        kw = keyword.lower()
        rows = [
            r for r in rows
            if kw in json.dumps(r, ensure_ascii=False).lower()
        ]
    total = len(rows)
    rows = list(reversed(rows))
    page = rows[offset: offset + limit]
    return {"total": total, "offset": offset, "limit": limit, "records": page}


def summary() -> dict:
    rows = _read_all()
    by_kind: Dict[str, int] = {}
    by_rule: Dict[str, int] = {}
    by_action: Dict[str, int] = {}
    failed = 0
    slowest = None
    for r in rows:
        by_kind[r.get("kind", "-")] = by_kind.get(r.get("kind", "-"), 0) + 1
        for rule in r.get("rules") or []:
            by_rule[rule] = by_rule.get(rule, 0) + 1
        act = r.get("action", "-")
        by_action[act] = by_action.get(act, 0) + 1
        if not r.get("ok", True):
            failed += 1
        if slowest is None or r.get("elapsed_ms", 0) > slowest.get("elapsed_ms", 0):
            slowest = r
    return {
        "total": len(rows),
        "failed": failed,
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "by_action": dict(sorted(by_action.items(), key=lambda kv: -kv[1])[:12]),
        "by_rule": dict(sorted(by_rule.items())),
        "slowest": slowest,
        "first_at": rows[0]["ts"] if rows else None,
        "last_at": rows[-1]["ts"] if rows else None,
        "file": str(AUDIT_FILE.relative_to(ROOT)).replace("\\", "/"),
        "note": "审计文件为 JSONL，一行一条；可用记事本直接查看，也可作为交付物一并提交。",
    }


def clear() -> int:
    """清空审计（仅演示重置用；生产环境应改为只允许归档、不允许删除）。"""
    rows = _read_all()
    n = len(rows)
    try:
        if AUDIT_FILE.exists():
            AUDIT_FILE.unlink()
    except OSError:
        pass
    return n
