# -*- coding: utf-8 -*-
"""
台账治理：把甲方给的 CSV 变成「可以入本体」的干净数据，并且把不干净的地方说清楚。

治理不是「能读进来就行」，而是三件事：
    1. 说清认了哪些列（映射结果），没认的列要报出来，避免甲方以为导进去了；
    2. 每条问题定位到行号 + 字段 + 规则编号，甲方改数据时不用猜；
    3. 分错误与警告：错误阻断入库，警告只提示（例如口径不一致可能是正常的统计时差）。

规则编号（V1~V9）与需求文档第 1 模块的校验要求一一对应，可直接作为验收清单。
"""
from __future__ import annotations

import base64
import csv
import io
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .mapping import (
    FIELD_INDEX,
    LOAD_BEARING,
    REQUIRED_RELATION,
    resolve_columns,
    resolve_type,
)

CHECKS: List[dict] = [
    {"code": "V1", "name": "必填字段非空", "level": "error",
     "description": "设备编号、设备类型为空的行无法建实体。"},
    {"code": "V2", "name": "设备编号唯一", "level": "error",
     "description": "编号重复会导致 RDF 里两个设备被合并成一个，必须先拆开。"},
    {"code": "V3", "name": "设备类型可识别", "level": "error",
     "description": "类型必须能映射到本体里的类，否则无法确定它是什么设备。"},
    {"code": "V4", "name": "数值合法", "level": "error",
     "description": "容量必须大于 0，负荷不得为负，户数必须是非负整数。"},
    {"code": "V5", "name": "关系引用存在", "level": "error",
     "description": "所属馈线、上级设备填的编号必须能在本表里找到。"},
    {"code": "V6", "name": "关系类型匹配", "level": "error",
     "description": "配变的上级应是线路段、台区的上级应是配变、馈线的上级应是变电站。"},
    {"code": "V7", "name": "供电路径连通", "level": "warning",
     "description": "从设备沿上级一路回溯应能到达变电站；断链的设备无法参与失电推理。"},
    {"code": "V8", "name": "类型专属字段完整", "level": "error",
     "description": "配变/馈线必须给容量与负荷，台区必须给户数，开关必须给状态。"},
    {"code": "V9", "name": "负荷口径一致", "level": "warning",
     "description": "馈线负荷应等于其所带配变负荷之和，不一致通常是统计时点不同。"},
]

CHECK_INDEX = {c["code"]: c for c in CHECKS}

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5")

# 上级类型约束：子类型 → 允许的上级类型集合
PARENT_ALLOWED = {
    "Feeder": {"Substation"},
    "LineSection": {"LineSection", "Feeder"},
    "SectionSwitch": {"LineSection", "Feeder"},
    "Breaker": {"LineSection", "Feeder", "Substation"},
    "TieSwitch": {"LineSection", "Feeder"},
    "DistributionTransformer": {"LineSection", "Feeder"},
    "TransformerDistrict": {"DistributionTransformer"},
    "ResidentialCustomer": {"TransformerDistrict"},
    "CommercialCustomer": {"TransformerDistrict"},
}

STATE_TRUE = {"合", "闭合", "合位", "合闸", "1", "true", "yes", "y", "closed", "on"}
STATE_FALSE = {"分", "断开", "分位", "分闸", "0", "false", "no", "n", "open", "off"}
BOOL_TRUE = {"是", "1", "true", "yes", "y", "常开"}
BOOL_FALSE = {"否", "0", "false", "no", "n", "常闭", ""}


@dataclass
class DeviceRow:
    lineno: int
    code: str = ""
    cls: str = ""
    raw_type: str = ""
    name: str = ""
    feeder: str = ""
    parent: str = ""
    rated: Optional[float] = None
    load: Optional[float] = None
    households: Optional[int] = None
    state: str = ""
    normally_open: bool = False
    voltage: Optional[float] = None
    address: str = ""
    is_critical: bool = False
    issues: List[dict] = field(default_factory=list)

    def add(self, code: str, msg: str, field_name: str = "") -> None:
        meta = CHECK_INDEX[code]
        self.issues.append({
            "row": self.lineno,
            "level": meta["level"],
            "code": code,
            "rule": meta["name"],
            "field": field_name,
            "message": msg,
        })


# ---------------------------------------------------------------------------
# 解码与解析
# ---------------------------------------------------------------------------
def decode_bytes(raw_b64: str) -> Tuple[str, str]:
    """多编码尝试。中方单位导出的 CSV 常是 GBK，不能假定 UTF-8。"""
    data = base64.b64decode(raw_b64)
    for enc in ENCODINGS:
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8(替换非法字符)"


def _sniff_delimiter(text: str) -> str:
    head = text.splitlines()[0] if text.splitlines() else ""
    counts = {",": head.count(","), ";": head.count(";"), "\t": head.count("\t")}
    return max(counts, key=counts.get) if max(counts.values()) > 0 else ","


def parse_csv(text: str) -> Tuple[List[str], List[List[str]]]:
    text = text.lstrip("\ufeff")
    # 误传图片、压缩包、Excel 原生格式（xlsx 是 zip）时会走到这里。
    # csv 模块遇到 NUL 直接抛 _csv.Error，最终表现为 500，这里提前给一句人话。
    if "\x00" in text:
        raise ValueError("文件包含二进制内容（NUL 字节），不是文本型 CSV。"
                         "如果是从 Excel 直接改扩展名得到的文件，请在 Excel 里另存为「CSV UTF-8」。")
    delimiter = _sniff_delimiter(text)
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = [r for r in reader if any((c or "").strip() for c in r)]
    except csv.Error as exc:
        raise ValueError(f"CSV 解析失败：{exc}。请检查引号是否成对、分隔符是否一致。") from exc
    if not rows:
        return [], []
    return [str(c).strip() for c in rows[0]], rows[1:]


# ---------------------------------------------------------------------------
# 取值解析
# ---------------------------------------------------------------------------
def _num(raw: str) -> Tuple[Optional[float], str]:
    """返回 (值, 错误说明)。容忍千分位、单位后缀、百分号。"""
    if raw is None:
        return None, ""
    s = str(raw).strip().replace(",", "").replace("，", "")
    if not s:
        return None, ""
    for suffix in ("kVA", "kva", "KVA", "kW", "kw", "KW", "MVA", "mva", "%", "户", "元", "A", "V"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    try:
        return float(s), ""
    except ValueError:
        return None, f"「{raw}」不是数字"


def _int(raw: str) -> Tuple[Optional[int], str]:
    val, err = _num(raw)
    if err:
        return None, err
    if val is None:
        return None, ""
    if abs(val - round(val)) > 1e-6:
        return None, f"「{raw}」不是整数"
    return int(round(val)), ""


def _state(raw: str) -> Tuple[str, str]:
    s = (raw or "").strip().lower()
    if not s:
        return "", ""
    if s in STATE_TRUE:
        return "closed", ""
    if s in STATE_FALSE:
        return "open", ""
    return "", f"「{raw}」无法识别为合/分"


def _bool(raw: str) -> Tuple[bool, str]:
    s = (raw or "").strip().lower()
    if s in BOOL_TRUE:
        return True, ""
    if s in BOOL_FALSE:
        return False, ""
    return False, f"「{raw}」无法识别为是/否"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def validate(text: str, filename: str = "", encoding: str = "utf-8") -> dict:
    try:
        headers, body = parse_csv(text)
    except ValueError as exc:
        return {
            "filename": filename, "encoding": encoding, "passed": False,
            "rows_total": 0, "rows_valid": 0, "issues": [], "checks": CHECKS,
            "devices_detail": [], "columns": {"detected": [], "mapping": [],
                                              "unmapped": [], "missing_required": [], "conflicts": []},
            "by_code": {}, "duplicates": {}, "type_stat": {},
            "devices": 0,
            "summary": f"无法解析：{exc}",
            "parse_error": str(exc),
        }
    if not headers:
        return {
            "filename": filename, "encoding": encoding, "passed": False,
            "rows_total": 0, "rows_valid": 0, "issues": [], "checks": CHECKS,
            "devices_detail": [], "columns": {"detected": [], "mapping": [],
                                              "unmapped": [], "missing_required": [], "conflicts": []},
            "by_code": {}, "duplicates": {}, "type_stat": {},
            "devices": 0,
            "summary": "文件为空或无法解析出表头",
        }

    cols = resolve_columns(headers)
    mapping: Dict[str, int] = cols["mapping"]

    def cell(row: List[str], key: str) -> str:
        idx = mapping.get(key)
        if idx is None or idx >= len(row):
            return ""
        return str(row[idx]).strip()

    devices: Dict[str, DeviceRow] = {}
    issues: List[dict] = []
    duplicates: Dict[str, List[int]] = {}

    for offset, row in enumerate(body):
        lineno = offset + 2  # 1 是表头
        d = DeviceRow(lineno=lineno)

        d.code = cell(row, "code")
        d.raw_type = cell(row, "type")
        d.name = cell(row, "name") or d.code
        d.feeder = cell(row, "feeder")
        d.parent = cell(row, "parent")
        d.address = cell(row, "address")

        if not d.code:
            d.add("V1", "设备编号为空", "设备编号")
        if not d.raw_type:
            d.add("V1", "设备类型为空", "设备类型")
        else:
            d.cls = resolve_type(d.raw_type) or ""
            if not d.cls:
                d.add("V3", f"设备类型「{d.raw_type}」未收录，无法映射到本体类", "设备类型")

        for key, label in (("rated_kw", "额定容量"), ("load_kw", "当前负荷"), ("voltage_kv", "电压等级")):
            val, err = _num(cell(row, key))
            if err:
                d.add("V4", f"{label}{err}", label)
            elif key == "rated_kw":
                d.rated = val
            elif key == "load_kw":
                d.load = val
            else:
                d.voltage = val

        hh, hh_err = _int(cell(row, "households"))
        if hh_err:
            d.add("V4", f"户数{hh_err}", "户数")
        d.households = hh

        st, st_err = _state(cell(row, "switch_state"))
        if st_err:
            d.add("V4", f"开关状态{st_err}，应填 合/分 或 1/0", "开关状态")
        d.state = st

        no, no_err = _bool(cell(row, "normally_open"))
        if no_err:
            d.add("V4", f"常开标记{no_err}，应填 是/否", "常开")
        d.normally_open = no

        crit, crit_err = _bool(cell(row, "is_critical"))
        if crit_err:
            d.add("V4", f"重要用户标记{crit_err}，应填 是/否", "是否重要用户")
        d.is_critical = crit

        if d.code:
            if d.code in devices:
                duplicates.setdefault(d.code, [devices[d.code].lineno]).append(lineno)
                d.add("V2", f"设备编号「{d.code}」与第 {devices[d.code].lineno} 行重复", "设备编号")
            else:
                devices[d.code] = d

        # 数值合法性（V4）
        if d.rated is not None and d.rated <= 0:
            d.add("V4", f"额定容量必须大于 0，当前 {d.rated:g}", "额定容量")
        if d.load is not None and d.load < 0:
            d.add("V4", f"当前负荷不得为负，当前 {d.load:g}", "当前负荷")
        if d.households is not None and d.households < 0:
            d.add("V4", f"户数不得为负，当前 {d.households}", "户数")

        # 类型专属必填（V8）
        if d.cls:
            if d.cls in LOAD_BEARING:
                if d.rated is None:
                    d.add("V8", f"{d.raw_type}缺少额定容量，无法做过载判定", "额定容量")
                if d.load is None:
                    d.add("V8", f"{d.raw_type}缺少当前负荷，无法做过载判定", "当前负荷")
            if d.cls == "TransformerDistrict" and d.households is None:
                d.add("V8", "台区缺少户数，停电影响将无法按户统计", "户数")
            if d.cls in ("SectionSwitch", "TieSwitch", "Breaker") and not d.state:
                d.add("V8", "开关缺少运行状态，拓扑仿真无法确定初始工况", "开关状态")
            for rel in REQUIRED_RELATION.get(d.cls, ()):
                if not getattr(d, rel):
                    label = FIELD_INDEX[rel]["label"]
                    d.add("V8", f"{d.raw_type}缺少{label}", label)

        for item in d.issues:
            issues.append(item)

    # ---- V5 / V6：关系引用与类型匹配（需要全表，放在第二遍）----
    for d in devices.values():
        for rel, label in (("feeder", "所属馈线"), ("parent", "上级设备")):
            ref = getattr(d, rel)
            if not ref:
                continue
            if ref == d.code:
                item = {"row": d.lineno, "level": "error", "code": "V5", "rule": CHECK_INDEX["V5"]["name"],
                        "field": label, "message": f"{label}指向自己（{ref}），形成自环"}
                d.issues.append(item)
                issues.append(item)
                continue
            target = devices.get(ref)
            if target is None:
                item = {"row": d.lineno, "level": "error", "code": "V5", "rule": CHECK_INDEX["V5"]["name"],
                        "field": label,
                        "message": f"{label}「{ref}」在本表中不存在，请补全该设备或改正编号"}
                d.issues.append(item)
                issues.append(item)
                continue
            allowed = PARENT_ALLOWED.get(d.cls or "", set())
            if rel == "parent" and allowed and target.cls and target.cls not in allowed and d.cls:
                allowed_cn = "、".join(sorted(allowed))
                item = {"row": d.lineno, "level": "error", "code": "V6", "rule": CHECK_INDEX["V6"]["name"],
                        "field": label,
                        "message": f"{d.raw_type}的上级不能是 {target.raw_type}（{ref}），应为 {allowed_cn}"}
                d.issues.append(item)
                issues.append(item)
            if rel == "feeder" and target.cls and target.cls != "Feeder":
                item = {"row": d.lineno, "level": "error", "code": "V6", "rule": CHECK_INDEX["V6"]["name"],
                        "field": label,
                        "message": f"所属馈线「{ref}」的类型是 {target.raw_type}，不是馈线"}
                d.issues.append(item)
                issues.append(item)

    # ---- V7：供电路径连通性 ----
    def reach_substation(code: str, depth: int = 0) -> bool:
        if depth > 12:
            return False
        d = devices.get(code)
        if d is None:
            return False
        if d.cls == "Substation":
            return True
        nxt = d.parent or (d.feeder if d.cls != "Feeder" else "")
        if not nxt:
            return False
        return reach_substation(nxt, depth + 1)

    for d in devices.values():
        if d.cls in ("Substation", "TransformerDistrict"):
            continue
        if not d.cls:
            continue
        if not reach_substation(d.code):
            item = {"row": d.lineno, "level": "warning", "code": "V7",
                    "rule": CHECK_INDEX["V7"]["name"], "field": "上级设备",
                    "message": "沿上级关系回溯不到变电站，该设备的失电推理将不可用（请检查是否漏填上级）"}
            d.issues.append(item)
            issues.append(item)

    # ---- V9：馈线负荷口径 ----
    for d in devices.values():
        if d.cls != "Feeder" or d.load is None:
            continue
        total = sum(
            x.load for x in devices.values()
            if x.cls == "DistributionTransformer" and x.feeder == d.code and x.load is not None
        )
        if abs(total - d.load) > 1e-6:
            item = {"row": d.lineno, "level": "warning", "code": "V9",
                    "rule": CHECK_INDEX["V9"]["name"], "field": "当前负荷",
                    "message": f"馈线负荷 {d.load:g} kW 与所带配变合计 {total:g} kW 不一致"
                               f"（差 {d.load - total:+g} kW）"}
            d.issues.append(item)
            issues.append(item)

    # ---- 汇总 ----
    error_rows = {i["row"] for i in issues if i["level"] == "error"}
    warning_rows = {i["row"] for i in issues if i["level"] == "warning"}
    rows_total = len(body)
    rows_valid = rows_total - len(error_rows)
    by_code: Dict[str, int] = {}
    for i in issues:
        by_code[i["code"]] = by_code.get(i["code"], 0) + 1

    type_stat: Dict[str, int] = {}
    for d in devices.values():
        label = d.raw_type or "未填写"
        type_stat[label] = type_stat.get(label, 0) + 1

    passed = len(error_rows) == 0 and not cols["missing"]
    parts = [f"共 {rows_total} 行"]
    if cols["missing"]:
        parts.append(f"缺必填列 {'、'.join(cols['missing'])}")
    parts.append(f"{rows_valid} 行通过")
    if error_rows:
        parts.append(f"{len(error_rows)} 行存在阻断性错误")
    if warning_rows:
        parts.append(f"{len(warning_rows)} 行有提示")
    if duplicates:
        parts.append(f"{len(duplicates)} 个编号重复")

    return {
        "filename": filename,
        "encoding": encoding,
        "passed": passed,
        "rows_total": rows_total,
        "rows_valid": rows_valid,
        "devices": len(devices),
        "type_stat": dict(sorted(type_stat.items(), key=lambda kv: -kv[1])),
        "columns": {
            "detected": headers,
            "mapping": [
                {"column": headers[idx], "field": key, "label": FIELD_INDEX[key]["label"]}
                for key, idx in sorted(mapping.items(), key=lambda kv: kv[1])
            ],
            "unmapped": cols["unmapped"],
            "missing_required": cols["missing"],
            "conflicts": cols["conflicts"],
        },
        "issues": sorted(issues, key=lambda i: (i["row"], i["code"])),
        "by_code": by_code,
        "duplicates": duplicates,
        "checks": CHECKS,
        "summary": "；".join(parts),
        "devices_detail": [
            {
                "code": d.code, "type": d.raw_type, "cls": d.cls, "name": d.name,
                "feeder": d.feeder, "parent": d.parent,
                "rated_kw": d.rated, "load_kw": d.load, "households": d.households,
                "state": d.state, "normally_open": d.normally_open,
                "is_critical": d.is_critical,
                "lineno": d.lineno,
                "errors": len([i for i in d.issues if i["level"] == "error"]),
                "warnings": len([i for i in d.issues if i["level"] == "warning"]),
            }
            for d in sorted(devices.values(), key=lambda x: x.lineno)
        ],
    }
