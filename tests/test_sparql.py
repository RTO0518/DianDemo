# -*- coding: utf-8 -*-
"""
SPARQL 接口测试。

这个接口把「查询能力」开放给了外部输入，所以安全边界必须逐条验证：
只读、不误杀、不改主图、有上限。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.sparql import EXAMPLES, _build_query, _guard  # noqa: E402
from app.reasoner.owl import inferred_graph  # noqa: E402

ALLOWED = [
    "SELECT ?s WHERE { ?s a grid:Feeder }",
    "ASK { ?s a grid:Feeder }",
    "SELECT ?s WHERE { ?s ?p ?o } LIMIT 5",
    "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o } LIMIT 3",
    "DESCRIBE <http://grid.ontology.local/data#TR_A>",
]

REJECTED = [
    ("INSERT DATA { <a:b> <a:c> <a:d> }", "写操作"),
    ("DELETE WHERE { ?s ?p ?o }", "写操作"),
    ("DROP GRAPH <http://x>", "写操作"),
    ("CLEAR ALL", "写操作"),
    ("LOAD <http://x> INTO GRAPH <http://y>", "写操作"),
    ("WITH <http://g> DELETE { ?s ?p ?o } WHERE { ?s ?p ?o }", "写操作"),
    ("", "空语句"),
    ("HELLO WORLD", "非查询语句"),
]


@pytest.mark.parametrize("query", ALLOWED)
def test_readonly_queries_allowed(query: str) -> None:
    assert _guard(query) is None, f"只读查询被误杀：{query}"


@pytest.mark.parametrize("query,reason", REJECTED)
def test_write_operations_blocked(query: str, reason: str) -> None:
    err = _guard(query)
    assert err, f"写操作未被拦截（{reason}）：{query}"


def test_keyword_in_string_literal_not_blocked() -> None:
    """设备名里带 delete 这类词不能被当成写操作。"""
    q = 'SELECT ?s WHERE { ?s rdfs:label "DELETE 按钮" }'
    assert _guard(q) is None


def test_keyword_in_variable_name_not_blocked() -> None:
    q = "SELECT ?deleteMe WHERE { ?s ?p ?deleteMe } LIMIT 1"
    assert _guard(q) is None


def test_keyword_in_comment_not_blocked() -> None:
    q = "# 这里说明一下 INSERT 的用法\nSELECT ?s WHERE { ?s a grid:Feeder }"
    assert _guard(q) is None


def test_prefix_autocompleted() -> None:
    built = _build_query("SELECT ?s WHERE { ?s a grid:Feeder }")
    assert built.startswith("PREFIX grid:")
    assert "PREFIX data:" in built


def test_existing_prefix_not_duplicated() -> None:
    q = "PREFIX grid: <http://grid.ontology.local/onto#>\nSELECT ?s WHERE { ?s a grid:Feeder }"
    assert _build_query(q) == q


def test_every_example_executes_with_rows() -> None:
    """示例查询是给甲方点开就能用的，不能有跑不通或空结果的。"""
    g = inferred_graph()
    for ex in EXAMPLES:
        assert _guard(ex["query"]) is None, f"{ex['id']} 未通过守卫"
        result = g.query(_build_query(ex["query"]))
        rows = list(result)
        assert rows, f"{ex['id']}《{ex['title']}》返回空结果"
        assert ex["title"] and ex["description"]


def test_query_does_not_mutate_inferred_graph() -> None:
    g = inferred_graph()
    before = len(g)
    g.query(_build_query("SELECT ?s WHERE { ?s ?p ?o } LIMIT 10"))
    assert len(g) == before


def test_inferred_graph_is_strictly_larger() -> None:
    """闭包后必须比显式断言多——否则说明 OWL 推理没真正跑起来。"""
    from app.reasoner.engine import get_engine

    raw = len(get_engine().model.graph)
    full = len(inferred_graph())
    assert full > raw
    assert full - raw > 500
