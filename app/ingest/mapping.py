# -*- coding: utf-8 -*-
"""
字段映射定义：把甲方台账的「人话列名」映射到本体的属性。

为什么映射要单独建模
--------------------
甲方的设备台账不是按我们的本体设计的。同一种东西在不同单位叫法不同：
    「配变」「配电变压器」「公用变压器」「变压器」
    「所属馈线」「馈线编号」「线路名称」
硬编码列名，换一家单位就得改代码。这里把「可接受的别名集合 + 目标字段 + 类型 + 约束」
做成数据，接入逻辑只认标准字段名，甲方台账怎么起名都能对上。

新增一种叫法只需要往 aliases 里加一项，不需要动校验和构图代码。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# 设备类型：台账里怎么写 → 映射到本体的哪个类
TYPE_ALIASES: Dict[str, str] = {
    "变电站": "Substation",
    "开关站": "Substation",
    "馈线": "Feeder",
    "线路": "Feeder",
    "10kv线路": "Feeder",
    "线路段": "LineSection",
    "线段": "LineSection",
    "分段开关": "SectionSwitch",
    "分段断路器": "SectionSwitch",
    "联络开关": "TieSwitch",
    "联络": "TieSwitch",
    "断路器": "Breaker",
    "出线开关": "Breaker",
    "配变": "DistributionTransformer",
    "配电变压器": "DistributionTransformer",
    "公用变压器": "DistributionTransformer",
    "变压器": "DistributionTransformer",
    "台区": "TransformerDistrict",
    "配电台区": "TransformerDistrict",
    "用户": "ResidentialCustomer",
    "居民用户": "ResidentialCustomer",
    "商业用户": "CommercialCustomer",
    "单位用户": "CommercialCustomer",
    "工业用户": "CommercialCustomer",
}

# 需要容量与负荷的类型
LOAD_BEARING = ("Feeder", "DistributionTransformer")

# 各类型在台账里的必填关系字段
REQUIRED_RELATION = {
    "Feeder": ("parent",),              # 必须挂到变电站
    "LineSection": ("feeder",),         # 必须属于某条馈线
    "SectionSwitch": ("feeder",),
    "TieSwitch": ("feeder",),
    "Breaker": ("feeder",),
    "DistributionTransformer": ("feeder", "parent"),  # 属于馈线 + 装设在线路段
    "TransformerDistrict": ("parent",),  # 必须挂到配变
    "ResidentialCustomer": ("parent",),  # 必须挂到台区
    "CommercialCustomer": ("parent",),
}

# 台账字段定义：标准字段名 → 显示名 / 别名 / 类型 / 是否必填 / 说明
FIELDS: List[dict] = [
    {
        "key": "code",
        "label": "设备编号",
        "aliases": ["编号", "设备编码", "设备id", "id", "code", "设备标识"],
        "type": "string",
        "required": True,
        "example": "TR_A",
        "description": "全局唯一标识，会作为 RDF 实例的本地名。建议用字母数字与下划线。",
    },
    {
        "key": "type",
        "label": "设备类型",
        "aliases": ["类型", "设备分类", "设备种类", "type", "类别"],
        "type": "enum",
        "required": True,
        "example": "配变",
        "description": "取值见 type_options，会映射到本体的对应类。",
    },
    {
        "key": "name",
        "label": "设备名称",
        "aliases": ["名称", "设备名", "设备名称", "中文名", "name"],
        "type": "string",
        "required": False,
        "example": "配变A",
        "description": "缺省时用设备编号代替。",
    },
    {
        "key": "feeder",
        "label": "所属馈线",
        "aliases": ["馈线", "馈线编号", "所属线路", "线路编号", "所属馈线编号"],
        "type": "string",
        "required": False,
        "example": "FX",
        "description": "填馈线的设备编号。线路段、开关、配变必填。",
    },
    {
        "key": "parent",
        "label": "上级设备",
        "aliases": ["上游设备", "上级", "所属设备", "父设备", "安装位置", "所属线路段"],
        "type": "string",
        "required": False,
        "example": "LS2",
        "description": "变电站←馈线←线路段←配变←台区，按这个顺序逐级填上一级。",
    },
    {
        "key": "rated_kw",
        "label": "额定容量(kW)",
        "aliases": ["容量", "额定容量", "额定容量kva", "额定容量kw", "容量kva", "rated_capacity"],
        "type": "float",
        "required": False,
        "example": "630",
        "description": "馈线与配变必填，且必须大于 0。",
    },
    {
        "key": "load_kw",
        "label": "当前负荷(kW)",
        "aliases": ["负荷", "当前负荷", "实时负荷", "负荷kw", "load", "current_load"],
        "type": "float",
        "required": False,
        "example": "380",
        "description": "馈线、配变必填，不得为负。",
    },
    {
        "key": "households",
        "label": "户数",
        "aliases": ["台区户数", "用户数", "户数", "覆盖户数", "households"],
        "type": "int",
        "required": False,
        "example": "500",
        "description": "台区必填，为正整数。这是停电影响户数的唯一来源。",
    },
    {
        "key": "switch_state",
        "label": "开关状态",
        "aliases": ["状态", "运行状态", "分合状态", "开关状态", "state"],
        "type": "enum",
        "required": False,
        "example": "合",
        "description": "只对开关类设备有效：合/闭合/1 表示合位，分/断开/0 表示分位。",
    },
    {
        "key": "normally_open",
        "label": "常开",
        "aliases": ["常开状态", "是否常开", "常开", "normally_open"],
        "type": "bool",
        "required": False,
        "example": "否",
        "description": "联络开关通常为常开。影响「恢复运行」时的初始工况。",
    },
    {
        "key": "is_critical",
        "label": "是否重要用户",
        "aliases": ["重要用户", "重要负荷", "是否重要负荷", "is_critical", "critical"],
        "type": "bool",
        "required": False,
        "example": "否",
        "description": "只对用户类设备有效。重要用户会在失电分析中被单独列出，转供排序时优先保障（规则 R7）。",
    },
    {
        "key": "voltage_kv",
        "label": "电压等级(kV)",
        "aliases": ["电压等级", "电压", "电压kv", "voltage"],
        "type": "float",
        "required": False,
        "example": "10",
        "description": "可选，用于按电压等级分组统计。",
    },
    {
        "key": "address",
        "label": "地址",
        "aliases": ["位置", "地址", "安装地址", "address"],
        "type": "string",
        "required": False,
        "example": "城东工业园",
        "description": "可选，台区、用户常用。",
    },
]

FIELD_INDEX: Dict[str, dict] = {f["key"]: f for f in FIELDS}


def _norm(text: str) -> str:
    """列名归一：去空格、全角括号转半角、去掉单位后缀、转小写。"""
    if text is None:
        return ""
    s = str(text).strip()
    s = s.replace("（", "(").replace("）", ")").replace("：", ":")
    s = s.replace(" ", "").replace("\u3000", "")
    return s.lower()


# 归一后的别名 → 标准字段名
_ALIAS_MAP: Dict[str, str] = {}
for _f in FIELDS:
    _ALIAS_MAP[_norm(_f["label"])] = _f["key"]
    _ALIAS_MAP[_norm(_f["key"])] = _f["key"]
    for _a in _f["aliases"]:
        _ALIAS_MAP[_norm(_a)] = _f["key"]


def resolve_columns(headers: List[str]) -> dict:
    """
    把 CSV 表头解析成 {标准字段: 列下标}。
    返回 {mapping, unmapped, missing, conflicts}——甲方的表头怎么起名都能尽量对上。
    """
    mapping: Dict[str, int] = {}
    unmapped: List[str] = []
    conflicts: List[dict] = []

    for idx, raw in enumerate(headers):
        key = _ALIAS_MAP.get(_norm(raw))
        if not key:
            unmapped.append(str(raw).strip())
            continue
        if key in mapping:
            conflicts.append({
                "field": key,
                "label": FIELD_INDEX[key]["label"],
                "columns": [headers[mapping[key]], raw],
                "picked": headers[mapping[key]],
                "message": f"多个列都指向「{FIELD_INDEX[key]['label']}」，已取第一个",
            })
            continue
        mapping[key] = idx

    missing = [f["label"] for f in FIELDS if f["required"] and f["key"] not in mapping]
    return {"mapping": mapping, "unmapped": unmapped, "missing": missing, "conflicts": conflicts}


def resolve_type(raw: str) -> Optional[str]:
    """设备类型中文名 → 本体类名；无法识别返回 None。"""
    return TYPE_ALIASES.get(_norm(raw))


def type_options() -> List[dict]:
    """给界面展示的「可填哪些类型」。"""
    grouped: Dict[str, List[str]] = {}
    for alias, cls in TYPE_ALIASES.items():
        grouped.setdefault(cls, []).append(alias)
    return [{"class": cls, "aliases": sorted(set(v))} for cls, v in sorted(grouped.items())]
