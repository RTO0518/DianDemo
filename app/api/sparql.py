# -*- coding: utf-8 -*-
"""
SPARQL 查询接口。

为什么要有这一层
----------------
甲方需求里把「SPARQL 查询」列为知识图谱模块的必备能力。它的意义不只是查数据：
本体的价值在于「换一种问法不用改代码」——设备台账换一批、关系加一种，
只要 SPARQL 写得出来就能查，不需要动后端。

安全边界（这是本文件最重要的部分）
--------------------------------
1. 只放行只读查询（SELECT / ASK / CONSTRUCT / DESCRIBE），任何 Update 操作一律拒绝；
2. 关键字判定前先剥掉字符串字面量与注释，避免「名字里带 delete 的设备」被误杀，
   也避免把写操作藏在字符串里绕过检查；
3. 在图的副本上执行，查询无论如何都改不到内存里的主图；
4. 行数上限 + 执行超时，防止一条笛卡尔积查询把演示机拖死。
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.query import Result

from .. import audit
from ..reasoner.engine import get_engine
from ..reasoner.owl import inferred_graph
from .schemas import SparqlQuery

router = APIRouter()

ONTO_NS = "http://grid.ontology.local/onto#"
DATA_NS = "http://grid.ontology.local/data#"

# 自动补全的前缀。
# 注意：rdflib 的 Prologue 以「命名空间 URI」为键，两个不同前缀指向同一 URI 时后者会覆盖前者，
# 导致 "Unknown namespace prefix" —— 所以本体命名空间只用 grid: 一个别名，与 TTL 文件保持一致。
AUTO_PREFIX = """PREFIX grid: <http://grid.ontology.local/onto#>
PREFIX data: <http://grid.ontology.local/data#>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:  <http://www.w3.org/2002/07/owl#>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
"""

# 只读查询的合法入口关键字
ALLOWED_FORMS = ("SELECT", "ASK", "CONSTRUCT", "DESCRIBE")

# SPARQL 1.1 Update 的全部写操作关键字
FORBIDDEN_OPS = (
    "INSERT", "DELETE", "DROP", "CLEAR", "LOAD",
    "CREATE", "ADD", "MOVE", "COPY", "WITH",
)

QUERY_TIMEOUT = 10.0  # 秒


# ---------------------------------------------------------------------------
# 静态审查
# ---------------------------------------------------------------------------
_STRING_RE = re.compile(r'"""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\'|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S)
_COMMENT_RE = re.compile(r"#[^\n]*")
_IRI_RE = re.compile(r"<[^>\n]*>")
_VAR_RE = re.compile(r"[?$][A-Za-z_][A-Za-z0-9_]*")


def _strip_noise(query: str) -> str:
    """剥掉字符串字面量、IRI、注释、变量名，只留下真正的语法关键字。"""
    text = _STRING_RE.sub(" ", query)
    text = _COMMENT_RE.sub(" ", text)
    text = _IRI_RE.sub(" ", text)
    text = _VAR_RE.sub(" ", text)
    return text


def _guard(query: str) -> Optional[str]:
    """返回错误说明；None 表示可以执行。"""
    stripped = _strip_noise(query).upper()

    for op in FORBIDDEN_OPS:
        if re.search(rf"\b{op}\b", stripped):
            return (f"检测到写操作关键字 {op}。本接口只提供只读查询，"
                    f"修改数据请走数据接入接口，避免演示环境被误改。")

    # 首个有效关键字必须是四种查询形式之一（PREFIX / BASE / DEFINE 允许放在前面）
    tokens = re.findall(r"\b[A-Z]+\b", stripped)
    form = None
    for tok in tokens:
        if tok in ("PREFIX", "BASE", "DEFINE"):
            continue
        form = tok
        break
    if form not in ALLOWED_FORMS:
        return f"查询必须以 {' / '.join(ALLOWED_FORMS)} 开头，当前识别到的是 {form or '空语句'}。"

    if not re.search(r"\bWHERE\b|\{", stripped) and form in ("SELECT", "ASK"):
        return "查询缺少 WHERE 子句。"

    return None


# ---------------------------------------------------------------------------
# 结果序列化
# ---------------------------------------------------------------------------
def _local(uri: str) -> str:
    if "#" in uri:
        return uri.rsplit("#", 1)[-1]
    return uri.rstrip("/").rsplit("/", 1)[-1]


def _term(t) -> Optional[dict]:
    if t is None:
        return None
    if isinstance(t, URIRef):
        s = str(t)
        return {"type": "uri", "value": s, "local": _local(s)}
    if isinstance(t, Literal):
        return {
            "type": "literal",
            "value": str(t),
            "datatype": _local(str(t.datatype)) if t.datatype else None,
            "lang": t.language,
        }
    if isinstance(t, BNode):
        return {"type": "bnode", "value": f"_:{t}"}
    return {"type": "unknown", "value": str(t)}


def _serialize(result: Result, limit: int) -> dict:
    if result.type == "ASK":
        return {"kind": "ask", "boolean": bool(result.askAnswer), "variables": [], "rows": [],
                "row_count": 0, "truncated": False}

    if result.type in ("CONSTRUCT", "DESCRIBE"):
        triples: List[dict] = []
        truncated = False
        for s, p, o in result:
            if len(triples) >= limit:
                truncated = True
                break
            triples.append({"subject": _term(s), "predicate": _term(p), "object": _term(o)})
        return {"kind": "graph", "variables": ["subject", "predicate", "object"],
                "rows": triples, "row_count": len(triples), "truncated": truncated}

    variables = [str(v) for v in (result.vars or [])]
    rows: List[dict] = []
    truncated = False
    for row in result:
        if len(rows) >= limit:
            truncated = True
            break
        rows.append({variables[i]: _term(row[i]) for i in range(len(variables))})
    return {"kind": "select", "variables": variables, "rows": rows,
            "row_count": len(rows), "truncated": truncated}


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------
def _build_query(query: str) -> str:
    """没写 PREFIX 时自动补全，让业务人员不用记命名空间。"""
    if re.search(r"(?i)\bprefix\b", query):
        return query
    return AUTO_PREFIX + query


def _run(graph: Graph, query: str, limit: int) -> dict:
    prepared = _build_query(query)
    result = graph.query(prepared)
    payload = _serialize(result, limit)
    payload["query"] = prepared
    return payload


@router.post("/query")
def sparql(payload: SparqlQuery) -> dict:
    """执行只读 SPARQL 查询（默认在 OWL 闭包后的推理图上）。"""
    err = _guard(payload.query)
    if err:
        audit.record("sparql", "SPARQL 查询", f"被拒绝：{err}", ok=False,
                     detail={"query": payload.query[:400]})
        raise HTTPException(400, err)

    # 在副本上跑：无论如何都动不到主图
    try:
        source = inferred_graph()
    except Exception:  # noqa: BLE001 - 闭包失败时退回原始断言图
        source = get_engine().model.graph
    graph = Graph()
    for prefix, ns in source.namespaces():
        graph.bind(prefix, ns)
    for triple in source:
        graph.add(triple)

    started = time.time()
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_run, graph, payload.query, payload.limit)
    try:
        data = future.result(timeout=QUERY_TIMEOUT)
    except FutureTimeout:
        future.cancel()
        executor.shutdown(wait=False)
        raise HTTPException(504, f"查询超过 {QUERY_TIMEOUT:.0f} 秒未返回，请缩小检索范围或加 LIMIT")
    except HTTPException:
        executor.shutdown(wait=False)
        raise
    except Exception as exc:  # noqa: BLE001
        executor.shutdown(wait=False)
        raise HTTPException(400, f"SPARQL 语法或执行错误：{exc}") from exc
    else:
        executor.shutdown(wait=False)

    data["elapsed_ms"] = int((time.time() - started) * 1000)
    data["graph_triples"] = len(graph)
    data["graph_scope"] = "OWL RL 推理闭包后的知识图谱（含隐式三元组）"

    audit.record(
        "sparql", "SPARQL 查询",
        f"{data['kind']} 返回 {data['row_count']} 行，耗时 {data['elapsed_ms']} ms",
        elapsed_ms=data["elapsed_ms"], ok=True,
        detail={"query": payload.query[:400], "rows": data["row_count"]},
    )
    return data


@router.get("/stats")
def stats() -> dict:
    engine = get_engine()
    raw = len(engine.model.graph)
    try:
        full = len(inferred_graph())
    except Exception:  # noqa: BLE001
        full = raw
    return {
        "raw_triples": raw,
        "inferred_triples": full,
        "delta": full - raw,
        "note": "raw 为显式断言，inferred 为 OWL 2 RL 闭包后结果；SPARQL 默认在后者上执行",
        "namespaces": {"onto": ONTO_NS, "data": DATA_NS},
    }


EXAMPLES: List[dict] = [
    {
        "id": "E1",
        "title": "全部配变与实时负载率",
        "description": "本体里没有「负载率」这个属性——它是由额定容量和当前负荷现算出来的，说明 SPARQL 能查派生量。",
        "query": """SELECT ?code ?name ?rated ?load (ROUND(?load / ?rated * 1000) / 10 AS ?rate_pct)
WHERE {
  ?t a grid:DistributionTransformer ;
     grid:code ?code ;
     rdfs:label ?name ;
     grid:ratedCapacityKW ?rated ;
     grid:currentLoadKW ?load .
}
ORDER BY DESC(?rate_pct)""",
    },
    {
        "id": "E2",
        "title": "按馈线统计配变数与户数",
        "description": "GROUP BY + SUM，验证「配变→馈线」与「台区→配变」两条归属关系是否建对。",
        "query": """SELECT ?feeder (COUNT(DISTINCT ?t) AS ?transformers) (SUM(?hh) AS ?households)
WHERE {
  ?t a grid:DistributionTransformer ;
     grid:belongsToFeeder ?f .
  ?f grid:code ?feeder .
  ?d a grid:TransformerDistrict ;
     grid:hasTransformer ?t ;
     grid:householdCount ?hh .
}
GROUP BY ?feeder
ORDER BY ?feeder""",
    },
    {
        "id": "E3",
        "title": "开关分位与常开标记",
        "description": "查开关的当前运行状态与常开标记，用于核对拓扑仿真的初始工况。",
        "query": """SELECT ?code ?name ?type ?state ?normally_open
WHERE {
  ?s a ?type ;
     grid:code ?code ;
     rdfs:label ?name ;
     grid:hasOperatingState ?st ;
     grid:normallyOpen ?normally_open .
  BIND(STRAFTER(STR(?st), "#") AS ?state)
  FILTER(?type IN (grid:SectionSwitch, grid:TieSwitch, grid:Breaker))
}
ORDER BY ?code""",
    },
    {
        "id": "E4",
        "title": "联络开关跨接的馈线数",
        "description": "转供分析的拓扑前提：每台联络开关必须恰好跨接两条馈线（公理 A7）。",
        "query": """SELECT ?switch ?name (COUNT(DISTINCT ?feeder) AS ?feeder_count)
WHERE {
  ?s a grid:TieSwitch ;
     grid:code ?switch ;
     rdfs:label ?name ;
     grid:connectsFeeder ?feeder .
}
GROUP BY ?switch ?name
ORDER BY ?switch""",
    },
    {
        "id": "E5",
        "title": "类层次（子类关系）",
        "description": "验证 rdfs:subClassOf 链路是否完整，是本体结构自检最直接的一条查询。",
        "query": """SELECT ?class ?parent
WHERE {
  ?class rdfs:subClassOf ?parent .
  FILTER(STRSTARTS(STR(?class), "http://grid.ontology.local/onto#"))
  FILTER(STRSTARTS(STR(?parent), "http://grid.ontology.local/onto#"))
  FILTER(?class != ?parent)
}
ORDER BY ?parent ?class""",
    },
    {
        "id": "E6",
        "title": "公理清单及其约束类型",
        "description": "把公理的编号、类型、业务理由一次列出，用于向甲方逐条说明建模依据。",
        "query": """SELECT ?axiom_id ?type ?label ?rationale
WHERE {
  ?node grid:axiomId ?axiom_id ;
        grid:axiomType ?type ;
        rdfs:label ?label .
  OPTIONAL { ?node grid:rationale ?rationale }
}
ORDER BY ?axiom_id""",
    },
    {
        "id": "E7",
        "title": "反查：一个用户凭什么带电",
        "description": "从用户出发沿 servedByDistrict / hasTransformer / locatedOn / belongsToFeeder / belongsToSubstation 回溯到变电站，即供电路径的语义表达。",
        "query": """SELECT ?customer ?district ?transformer ?section ?feeder ?substation
WHERE {
  ?c a grid:ResidentialCustomer ;
     grid:code ?customer ;
     grid:servedByDistrict ?d .
  ?d grid:code ?district ;
     grid:hasTransformer ?t .
  ?t grid:code ?transformer ;
     grid:locatedOn ?sec .
  ?sec grid:code ?section ;
       grid:belongsToFeeder ?f .
  ?f grid:code ?feeder ;
     grid:belongsToSubstation ?sub .
  ?sub grid:code ?substation .
}
LIMIT 20""",
    },
    {
        "id": "E8",
        "title": "故障记录与受影响设备",
        "description": "事件分支的查询：故障→设备→所属馈线，是故障工单联动的数据基础。",
        "query": """SELECT ?fault ?type ?time ?equipment ?equipment_type
WHERE {
  ?f a grid:Fault ;
     grid:faultType ?type ;
     grid:faultTime ?time ;
     grid:affectsEquipment ?eq .
  BIND(STRAFTER(STR(?f), "#") AS ?fault)
  BIND(STRAFTER(STR(?eq), "#") AS ?equipment)
  ?eq a ?equipment_type .
  FILTER(?equipment_type IN (grid:LineSection, grid:DistributionTransformer, grid:Switch, grid:SectionSwitch, grid:Breaker))
}
ORDER BY ?fault""",
    },
]


@router.get("/examples")
def examples() -> dict:
    return {"count": len(EXAMPLES), "examples": EXAMPLES}
