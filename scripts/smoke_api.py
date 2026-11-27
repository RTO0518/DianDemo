# -*- coding: utf-8 -*-
"""开发期自检脚本：逐个调用接口并打印关键字段。"""
import json
import sys
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8000"


def call(path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(BASE + path, data=data, method=method,
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                return resp.status, json.loads(raw) if raw else {}
            return resp.status, {"_html_len": len(raw)}
    except Exception as exc:  # noqa: BLE001
        body = getattr(exc, "read", lambda: b"")().decode("utf-8", "ignore")
        return getattr(exc, "code", 0), {"raw": body, "error": str(exc)}


def main() -> int:
    ok = True

    status, health = call("/api/health")
    print(f"[{status}] health: {health.get('census', {}).get('households')} 户")
    ok &= status == 200

    status, schema = call("/api/ontology/schema")
    if status == 200:
        print(f"[{status}] schema: 类 {schema['class_count']} / 对象属性 {schema['object_property_count']} "
              f"/ 数据属性 {schema['data_property_count']} / 公理 {schema['axiom_count']}")
    else:
        print(f"[{status}] schema 失败: {str(schema)[:300]}")
    ok &= status == 200

    status, graph = call("/api/ontology/graph")
    print(f"[{status}] graph: {len(graph.get('nodes', []))} 节点 {len(graph.get('edges', []))} 边")
    ok &= status == 200

    status, cls = call("/api/ontology/class/PowerEquipment")
    print(f"[{status}] class: {cls.get('label')} 子类 {len(cls.get('children', []))} 实例 {cls.get('instance_count')}")
    ok &= status == 200

    status, topo = call("/api/grid/topology")
    print(f"[{status}] topology: {len(topo.get('nodes', []))} 节点 {len(topo.get('edges', []))} 边")
    ok &= status == 200

    status, ws = call("/api/grid/workstate", "POST", {"states": {"S1": "open"}})
    print(f"[{status}] workstate: 失电 {ws.get('households')} 户，台区 {len(ws.get('affected_districts', []))}")
    ok &= status == 200

    status, tr = call("/api/reason/transfer", "POST", {"states": {"S1": "open"}})
    plans = tr.get("plans", [])
    print(f"[{status}] transfer: {len(plans)} 个方案，可行 {tr.get('feasible_count')}")
    ok &= status == 200

    status, ov = call("/api/reason/overload")
    print(f"[{status}] overload: 过载 {ov.get('overloaded_count')} 重载 {ov.get('heavy_count')}")
    ok &= status == 200

    status, relief = call("/api/reason/relief", "POST", {"transformer_id": "TR_B"})
    print(f"[{status}] relief: 可转供 {relief.get('can_transfer')}，超出 {relief.get('excess_kw')} kW")
    ok &= status == 200

    status, maint = call("/api/reason/maintenance", "POST", {"order_id": "GD_2026_018"})
    print(f"[{status}] maintenance: 开关 {maint.get('switch')}，户数 {(maint.get('outage') or {}).get('households')}")
    ok &= status == 200

    status, fault = call("/api/reason/fault", "POST", {"fault_id": "F1"})
    print(f"[{status}] fault: 跳闸 {fault.get('trip_switch')}，户数 {(fault.get('outage') or {}).get('households')}")
    ok &= status == 200

    status, cons = call("/api/reason/consistency")
    print(f"[{status}] consistency: {cons.get('summary')}")
    ok &= status == 200

    status, verify = call("/api/reason/verify")
    print(f"[{status}] verify: {verify.get('summary')}")
    ok &= status == 200

    status, owl = call("/api/reason/owl-check", "POST", {"switch_id": "S1", "state": "open"})
    print(f"[{status}] owl: 通过 {owl.get('owl', {}).get('passed')}，新增推理三元组 "
          f"{owl.get('owl', {}).get('inferred_triples')}")
    ok &= status == 200

    status, chat = call("/api/chat", "POST", {"message": "S1 断开后哪些用户失电？", "history": []})
    print(f"[{status}] chat: {chat.get('answer', '')[:60]}… 工具 {[t['name'] for t in chat.get('tool_calls', [])]}")
    ok &= status == 200

    # ---- 新增能力 ----
    status, ver = call("/api/ontology/version")
    files = ver.get("files", {})
    print(f"[{status}] version: v{ver.get('ontology_version')} 本体 "
          f"{(files.get('ontology_ttl') or {}).get('short')} / 实例 "
          f"{(files.get('instances_ttl') or {}).get('short')}")
    ok &= status == 200

    status, th = call("/api/reason/thresholds")
    print(f"[{status}] thresholds: {th.get('values')}")
    ok &= status == 200

    status, th2 = call("/api/reason/thresholds", "PUT", {"values": {"overload_rate": 0.95}})
    ok &= status == 200 and th2.get("values", {}).get("overload_rate") == 0.95
    status, ov2 = call("/api/reason/overload")
    print(f"[{status}] 阈值 95% 时: 过载 {ov2.get('overloaded_count')} 重载 {ov2.get('heavy_count')}"
          f"（trace: {(ov2.get('trace') or [{}])[0].get('detail', '')[:40]}）")
    ok &= status == 200
    call("/api/reason/thresholds/reset", "POST", {})

    status, sq = call("/api/sparql/query", "POST",
                       {"query": "SELECT (COUNT(*) AS ?n) WHERE { ?s a grid:DistributionTransformer }"})
    print(f"[{status}] sparql: {sq.get('row_count')} 行，图 {(sq.get('graph_triples'))} 三元组")
    ok &= status == 200 and sq.get("row_count") == 1

    status, sq_bad = call("/api/sparql/query", "POST", {"query": "DROP GRAPH <http://x>"})
    print(f"[{status}] sparql 写操作拦截: {'是' if status == 400 else '否 —— 异常！'}")
    ok &= status == 400

    status, ex = call("/api/sparql/examples")
    print(f"[{status}] sparql 示例: {ex.get('count')} 条")
    ok &= status == 200

    status, ing = call("/api/ingest/schema")
    print(f"[{status}] ingest schema: {len(ing.get('fields', []))} 字段 / {len(ing.get('checks', []))} 条校验规则")
    ok &= status == 200

    status, tpl = call("/api/ingest/templates")
    print(f"[{status}] ingest 模板: {[t['name'] for t in tpl.get('templates', [])]}")
    ok &= status == 200

    status, page = call("/")
    print(f"[{status}] 首页 HTML 长度 {page.get('_html_len', 0)}")
    ok &= status == 200 and page.get("_html_len", 0) > 3000
    print("\n全部接口通过" if ok else "\n存在失败接口")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
