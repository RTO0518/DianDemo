# -*- coding: utf-8 -*-
"""
推理阈值：集中管理全部「可调数字」，让规则与代码解耦。

设计动机
--------
甲方需求文档给出的 80% / 100% 是演示取值，并非电力行业强制标准；真实的过载界限
随电压等级、设备类型、负荷性质而变。因此阈值必须满足三条：

    1. 不硬编码在推理逻辑里；
    2. 可被甲方在界面上现场调整，立即改变结论；
    3. 每条结论的 trace 里能说明「这一次用的是哪个阈值」，便于复核。

实现上是一个带校验、带默认值、带元信息的单例。文件值作为基线，
运行期修改只存在内存里，重启即回到文件值——避免演示时把配置改乱带不回来。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[2]
THRESHOLD_FILE = ROOT / "data" / "thresholds.yaml"

# 内置兜底：文件缺失或损坏时也能启动
DEFAULTS: Dict[str, float] = {
    "overload_rate": 1.00,
    "heavy_rate": 0.80,
    "transfer_max_rate": 1.00,
    "capacity_reserve_rate": 0.00,
}

# 每个阈值的元信息：名称、单位、区间、影响哪条规则、给甲方的提示
META: List[dict] = [
    {
        "key": "overload_rate",
        "label": "过载阈值",
        "unit": "%",
        "percent": True,
        "min": 0.50,
        "max": 2.00,
        "rules": ["R4"],
        "description": "负载率超过该值判定为过载。国网常用 100%，部分单位对老旧配变按 80% 管控。",
    },
    {
        "key": "heavy_rate",
        "label": "重载阈值",
        "unit": "%",
        "percent": True,
        "min": 0.30,
        "max": 1.50,
        "rules": ["R4"],
        "description": "负载率处于该值与过载阈值之间判定为重载（预警），需列入增容观察。",
    },
    {
        "key": "transfer_max_rate",
        "label": "转供后允许最高负载率",
        "unit": "%",
        "percent": True,
        "min": 0.50,
        "max": 1.20,
        "rules": ["R5", "R6"],
        "description": "转供方案合闸后，对侧馈线负载率不得越过的上限。取 100% 表示可满载运行。",
    },
    {
        "key": "capacity_reserve_rate",
        "label": "转供预留容量比例",
        "unit": "%",
        "percent": True,
        "min": 0.00,
        "max": 0.50,
        "rules": ["R5"],
        "description": "对侧馈线在转供后仍需保留的备用容量比例，用于模拟调度对 N-1 裕度的要求。",
    },
]

META_INDEX = {m["key"]: m for m in META}

_INVALID = object()


class Thresholds:
    """线程安全的阈值容器。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: Dict[str, float] = dict(DEFAULTS)
        self._source = "内置默认值"
        self.reload()

    # ---------------- 文件 ----------------
    def reload(self) -> None:
        """从 data/thresholds.yaml 重新加载基线。"""
        with self._lock:
            values = dict(DEFAULTS)
            source = "内置默认值"
            if THRESHOLD_FILE.exists():
                try:
                    raw = yaml.safe_load(THRESHOLD_FILE.read_text(encoding="utf-8")) or {}
                    if isinstance(raw, dict):
                        for key in DEFAULTS:
                            if key in raw and raw[key] is not None:
                                values[key] = float(raw[key])
                        source = "data/thresholds.yaml"
                except Exception:  # noqa: BLE001 - 配置坏了不能拖垮服务
                    source = "内置默认值（配置文件解析失败）"
            self._values = values
            self._source = source

    # ---------------- 读写 ----------------
    def get(self, key: str, default: float = 0.0) -> float:
        with self._lock:
            return float(self._values.get(key, DEFAULTS.get(key, default)))

    def current(self, baseline: bool = False) -> Dict[str, float]:
        """baseline=True 时返回文件基线值（不含运行期覆盖），用于界面显示「已改动」。"""
        with self._lock:
            if not baseline:
                return dict(self._values)
        base = dict(DEFAULTS)
        if THRESHOLD_FILE.exists():
            try:
                raw = yaml.safe_load(THRESHOLD_FILE.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    for key in DEFAULTS:
                        if key in raw and raw[key] is not None:
                            base[key] = float(raw[key])
            except Exception:  # noqa: BLE001
                pass
        return base

    def describe(self) -> dict:
        """给前端的完整描述：当前值 + 基线值 + 元信息 + 是否被改动。"""
        current = self.current()
        base = self.current(baseline=True)
        return {
            "values": current,
            "baseline": base,
            "modified": {k: (abs(current[k] - base[k]) > 1e-9) for k in current},
            "source": self._source,
            "items": [
                {
                    **m,
                    "value": current[m["key"]],
                    "baseline": base[m["key"]],
                }
                for m in META
            ],
            "note": "需求文档中的 80%/100% 为演示取值，正式阈值需由甲方按运行规程确认。",
        }

    def update(self, patch: Dict[str, Any]) -> Tuple[Dict[str, float], List[str]]:
        """
        批量修改。返回 (修改后的全量值, 错误列表)。
        逐项校验：未知键、非数字、越界一律拒绝，不做「部分成功」的隐式容错。
        """
        errors: List[str] = []
        parsed: Dict[str, float] = {}
        for key, raw in (patch or {}).items():
            meta = META_INDEX.get(key)
            if meta is None:
                errors.append(f"未知的配置项：{key}")
                continue
            if raw is None or raw == _INVALID:
                errors.append(f"{meta['label']} 不能为空")
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                errors.append(f"{meta['label']} 必须是数字，收到 {raw!r}")
                continue
            if val != val or val in (float("inf"), float("-inf")):  # NaN / inf
                errors.append(f"{meta['label']} 不是有效数值")
                continue
            if not (meta["min"] <= val <= meta["max"]):
                errors.append(
                    f"{meta['label']} 超出允许区间 [{meta['min']:.2f}, {meta['max']:.2f}]，收到 {val:.2f}"
                )
                continue
            parsed[key] = val

        if errors:
            return self.current(), errors

        # 交叉校验：重载阈值必须低于过载阈值，否则判定区间自相矛盾
        merged = {**self.current(), **parsed}
        if merged["heavy_rate"] >= merged["overload_rate"]:
            return self.current(), [
                f"重载阈值（{merged['heavy_rate']:.0%}）必须低于过载阈值（{merged['overload_rate']:.0%}），"
                "否则「重载」判定区间为空"
            ]

        with self._lock:
            self._values = merged
        return dict(merged), []

    def reset(self) -> Dict[str, float]:
        self.reload()
        return self.current()

    def restore(self, values: Dict[str, float]) -> Dict[str, float]:
        """
        无声恢复（不校验）。只给内部用：验证脚本临时切回基线跑基准题，
        跑完把演示现场在用的阈值原样放回去，不能因为校验把演示状态弄丢。
        """
        with self._lock:
            merged = dict(self._values)
            for key, val in (values or {}).items():
                if key in DEFAULTS:
                    try:
                        merged[key] = float(val)
                    except (TypeError, ValueError):
                        continue
            self._values = merged
            return dict(self._values)


_THRESHOLDS: Thresholds | None = None


def get_thresholds() -> Thresholds:
    global _THRESHOLDS
    if _THRESHOLDS is None:
        _THRESHOLDS = Thresholds()
    return _THRESHOLDS
