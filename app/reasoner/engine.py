# -*- coding: utf-8 -*-
"""
推理引擎：把本体里的公理与规则变成可执行的推理，并对每一步给出可读的推理链。

对外暴露的都是「业务问题」，对内所有结论都附带 trace（规则编号 + 依据 + 数据）。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from rdflib import URIRef

from .graph import DATA, ONTO, Device, GridModel, get_model
from .rules import (
    RULES,
    RULE_INDEX,
    bfs_energized,
    classify_load,
    load_rate,
    path_to_source,
)
from .thresholds import get_thresholds


class ReasoningEngine:
    def __init__(self, model: Optional[GridModel] = None):
        self.model = model or get_model()
        self._build_customer_index()

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------
    def _build_customer_index(self) -> None:
        g = self.model.graph
        self.cust_of_district: Dict[str, List[str]] = defaultdict(list)
        for c in self.model.customers:
            uri = g.value(c.uri, ONTO.servedByDistrict)
            if isinstance(uri, URIRef):
                self.cust_of_district[str(uri).split("#")[-1]].append(c.id)

    def _t(self, trace: List[dict], rule: str, action: str, detail: str) -> None:
        trace.append({"rule": rule, "rule_name": RULE_INDEX[rule]["name"], "action": action, "detail": detail})

    def _apply(
        self, switch_id: Optional[str], new_state: Optional[str], state_overrides: Optional[Dict[str, str]]
    ) -> Tuple[Dict[str, str], List[dict]]:
        m = self.model
        state = dict(m.state)
        changed: List[dict] = []
        if switch_id:
            if switch_id not in m.switches:
                raise ValueError(f"不存在编号为 {switch_id} 的开关")
            if new_state not in ("open", "closed"):
                raise ValueError("开关状态只能是 open 或 closed")
            changed.append({"id": switch_id, "from": state[switch_id], "to": new_state})
            state[switch_id] = new_state
        for sid, val in (state_overrides or {}).items():
            if sid not in m.switches:
                raise ValueError(f"不存在编号为 {sid} 的开关")
            if val not in ("open", "closed"):
                raise ValueError(f"开关 {sid} 的状态非法：{val}")
            if state.get(sid) != val:
                changed.append({"id": sid, "from": state[sid], "to": val})
            state[sid] = val
        return state, changed

    @staticmethod
    def _state_cn(state: str) -> str:
        return "合位" if state == "closed" else "分位"

    # ------------------------------------------------------------------
    # R1 + R2 + R3 + R7：失电分析
    # ------------------------------------------------------------------
    def outage(
        self,
        switch_id: Optional[str] = None,
        new_state: Optional[str] = None,
        state_overrides: Optional[Dict[str, str]] = None,
    ) -> dict:
        m = self.model
        trace: List[dict] = []
        state, changed = self._apply(switch_id, new_state, state_overrides)

        for ch in changed:
            sw = m.switches[ch["id"]]
            self._t(
                trace, "R2", f"施加开关动作：{sw.label}（{sw.id}）",
                f"{sw.id} 由{self._state_cn(ch['from'])}改为{self._state_cn(ch['to'])}，"
                f"其两端为 {sw.connects[0]} 与 {sw.connects[1]}",
            )

        energized, parent = bfs_energized(m, state)
        total_equipment = len([d for d in m.devices.values() if d.kind in ("section", "transformer", "feeder")])
        self._t(
            trace, "R1", "从变电站母线搜索供电路径",
            f"以 {len(m.substations)} 座变电站为源点，沿闭合开关广度优先搜索，"
            f"共 {len(energized)} 个电气节点带电（设备总数 {total_equipment}）",
        )

        dead_devices = [
            d for d in m.devices.values()
            if d.kind in ("section", "transformer", "feeder") and d.id not in energized
        ]
        dead_transformers = [t for t in m.transformers if t.id not in energized]
        self._t(
            trace, "R2", "判定失电设备",
            f"物理连接仍在但供电路径不可达：{len(dead_devices)} 台设备失电，"
            f"其中配变 {len(dead_transformers)} 台（{'、'.join(t.id for t in dead_transformers) or '无'}）",
        )

        districts: List[dict] = []
        households = 0
        customers: List[dict] = []
        critical: List[dict] = []
        for t in dead_transformers:
            did = m.district_of_transformer.get(t.id)
            if not did or did not in m.devices:
                continue
            d = m.devices[did]
            hh = int(m.num(d, "householdCount"))
            households += hh
            cust_ids = self.cust_of_district.get(did, [])
            districts.append({
                "id": did, "label": d.label, "households": hh,
                "transformer": t.id, "feeder": m.feeder_of.get(t.id),
                "customers": [m.devices[c].label for c in cust_ids if c in m.devices],
            })
            for cid in cust_ids:
                c = m.devices.get(cid)
                if not c:
                    continue
                critical_val = m.graph.value(c.uri, ONTO.isCriticalLoad)
                is_critical = bool(critical_val.toPython()) if critical_val is not None else False
                item = {"id": cid, "label": c.label, "district": did, "critical": is_critical}
                customers.append(item)
                if is_critical:
                    critical.append(item)

        districts.sort(key=lambda d: d["id"])
        dead_transformers.sort(key=lambda t: t.id)
        census = m.census()
        ratio = households / census["households"] if census["households"] else 0.0
        self._t(
            trace, "R3", "汇总影响范围",
            f"失电台区 {len(districts)} 个（{'、'.join(d['label'] for d in districts) or '无'}），"
            f"影响 {households} 户，占全网 {census['households']} 户的 {ratio:.1%}",
        )
        if critical:
            self._t(trace, "R7", "识别重要用户",
                      "失电范围内含重要负荷：" + "、".join(c["label"] for c in critical))
        else:
            self._t(trace, "R7", "识别重要用户", "失电范围内无重要负荷")

        # 失电区旁是否有可用的联络通道
        adjacent_ties = []
        dead_ids = {d.id for d in dead_devices}
        for sw in m.switches.values():
            if sw.cls_name != "TieSwitch":
                continue
            a, b = sw.connects
            if (a in dead_ids) != (b in dead_ids):
                adjacent_ties.append({
                    "id": sw.id, "label": sw.label, "state": state.get(sw.id),
                    "live_side": b if a in dead_ids else a,
                })
        if adjacent_ties:
            desc = "；".join(
                f"{t['label']}（{t['id']}）{self._state_cn(t['state'])}，可尝试经 {t['live_side']} 侧转供"
                for t in adjacent_ties
            )
            self._t(trace, "R2", "检查备用供电通道", desc)
        else:
            self._t(trace, "R2", "检查备用供电通道", "失电区无联络开关接入，不具备转供条件")

        return {
            "switch": changed[0] if changed else None,
            "states": {k: v for k, v in state.items()},
            "energized_count": len(energized),
            "de_energized": [
                {"id": d.id, "label": d.label, "kind": d.kind, "feeder": m.feeder_of.get(d.id)}
                for d in dead_devices
            ],
            "de_energized_transformers": [
                {"id": t.id, "label": t.label, "feeder": m.feeder_of.get(t.id)} for t in dead_transformers
            ],
            "affected_districts": districts,
            "households": households,
            "customers": customers,
            "critical_customers": critical,
            "affected_feeders": sorted({m.feeder_of.get(t.id, "") for t in dead_transformers} - {""}),
            "ratio": round(ratio, 4),
            "total_households": census["households"],
            "trace": trace,
            "_energized": energized,
            "_parent": parent,
            "_state": state,
        }

    # ------------------------------------------------------------------
    # R4：过载与重载
    # ------------------------------------------------------------------
    def load_report(self) -> dict:
        m = self.model
        trace: List[dict] = []
        rows = []
        for t in m.transformers:
            rated = m.num(t, "ratedCapacityKW")
            load = m.num(t, "currentLoadKW")
            rate = load_rate(load, rated)
            rows.append({
                "id": t.id, "label": t.label, "feeder": m.feeder_of.get(t.id),
                "rated_kw": rated, "load_kw": load, "rate": round(rate, 4),
                "status": classify_load(rate),
                "district": m.district_of_transformer.get(t.id),
            })
        rows.sort(key=lambda r: r["rate"], reverse=True)

        overloaded = [r for r in rows if r["status"] == "overloaded"]
        heavy = [r for r in rows if r["status"] == "heavy"]
        th = get_thresholds()
        ov, hv = th.get("overload_rate"), th.get("heavy_rate")
        self._t(
            trace, "R4", "计算配变负载率",
            f"阈值取自 {th.describe()['source']}：过载 ≥ {ov:.0%}、重载 ≥ {hv:.0%}；"
            f"共 {len(rows)} 台配变，过载 {len(overloaded)} 台，重载 {len(heavy)} 台",
        )
        for r in overloaded:
            self._t(trace, "R4", f"过载：{r['label']}",
                      f"{r['load_kw']:.0f} kW / {r['rated_kw']:.0f} kW = {r['rate']:.1%}，超出额定 "
                      f"{r['load_kw'] - r['rated_kw']:.0f} kW")
        for r in heavy:
            self._t(trace, "R4", f"重载：{r['label']}",
                      f"{r['load_kw']:.0f} kW / {r['rated_kw']:.0f} kW = {r['rate']:.1%}，接近额定上限")

        feeders = []
        for f in m.feeders:
            rated = m.num(f, "ratedCapacityKW")
            load = m.num(f, "currentLoadKW")
            rate = load_rate(load, rated)
            feeders.append({
                "id": f.id, "label": f.label, "rated_kw": rated, "load_kw": load,
                "rate": round(rate, 4), "spare_kw": round(rated - load, 2),
            })
        return {
            "transformers": rows,
            "feeders": feeders,
            "overloaded": overloaded,
            "heavy": heavy,
            "overloaded_count": len(overloaded),
            "heavy_count": len(heavy),
            "trace": trace,
        }

    # ------------------------------------------------------------------
    # R5 + R6 + R12：转供
    # ------------------------------------------------------------------
    def transfer_plans(
        self,
        switch_id: Optional[str] = None,
        new_state: Optional[str] = None,
        state_overrides: Optional[Dict[str, str]] = None,
        feeder_load_override: Optional[Dict[str, float]] = None,
    ) -> dict:
        m = self.model
        trace: List[dict] = []
        # engine 是公开方法，可能被 Agent 工具或脚本直接调用，不能只靠接口层校验
        for fid, val in (feeder_load_override or {}).items():
            if fid not in m.devices or m.devices[fid].kind != "feeder":
                raise ValueError(f"馈线负荷覆盖参数里的 {fid} 不是有效馈线")
            if val is None or val < 0:
                raise ValueError(f"馈线 {fid} 的模拟负荷不能为负：{val}")
        state, _ = self._apply(switch_id, new_state, state_overrides)
        energized, _ = bfs_energized(m, state)
        dead_tr = [t for t in m.transformers if t.id not in energized]

        if not dead_tr:
            self._t(trace, "R5", "判定转供需求", "当前运行方式下无失电配变，无需转供")
            return {"plans": [], "feasible_count": 0, "trace": trace,
                    "message": "当前无失电配变，无需转供"}

        dead_ids = {t.id for t in dead_tr}
        self._t(trace, "R5", "统计待转供负荷",
                  f"失电配变 {len(dead_tr)} 台，合计负荷 {sum(m.num(t, 'currentLoadKW') for t in dead_tr):.0f} kW")

        plans: List[dict] = []
        for sw in m.switches.values():
            if sw.cls_name != "TieSwitch" or state.get(sw.id) != "open":
                continue
            a, b = sw.connects
            a_live, b_live = a in energized, b in energized
            if a_live == b_live:
                continue
            live_side = a if a_live else b
            target_feeder = m.feeder_of_device(live_side)
            if not target_feeder or target_feeder not in m.devices:
                continue

            sim_state = dict(state)
            sim_state[sw.id] = "closed"
            energized_after, _ = bfs_energized(m, sim_state)
            restored = [t for t in dead_tr if t.id in energized_after]
            if not restored:
                continue

            transferred = sum(m.num(t, "currentLoadKW") for t in restored)
            restored_hh = sum(
                int(m.num(m.devices[m.district_of_transformer[t.id]], "householdCount"))
                for t in restored if t.id in m.district_of_transformer
            )
            target = m.devices[target_feeder]
            rated = m.num(target, "ratedCapacityKW")
            load_before = float((feeder_load_override or {}).get(target_feeder, m.num(target, "currentLoadKW")))
            th = get_thresholds()
            reserve_rate = th.get("capacity_reserve_rate")
            max_rate = th.get("transfer_max_rate")

            # 可用备用 = 额定 - 当前负荷 - 必须预留的容量
            spare = rated - load_before - rated * reserve_rate
            gap = max(0.0, transferred - spare)
            load_after = load_before + transferred
            rate_before = load_rate(load_before, rated)
            rate_after = load_rate(load_after, rated)
            feasible = transferred <= spare and rate_after <= max_rate

            reserve_note = (f"，预留 {reserve_rate:.0%} 后可用 {spare:.0f} kW"
                            if reserve_rate > 0 else "")

            self._t(
                trace, "R5", f"评估联络开关 {sw.label}（{sw.id}）",
                f"带电侧 {live_side} 属馈线 {target.label}，转移负荷 {transferred:.0f} kW；"
                f"对侧额定 {rated:.0f} kW、当前 {load_before:.0f} kW{reserve_note} → "
                + ("容量满足" if feasible else f"容量不足，缺口 {gap:.0f} kW")
                + f"；转供后负载率 {rate_after:.1%}，上限 {max_rate:.0%}",
            )

            if feasible:
                reason = (f"对侧 {target.label} 可用备用 {spare:.0f} kW ≥ 转移负荷 {transferred:.0f} kW，"
                          f"转供后负载率 {rate_after:.1%}，未越过 {max_rate:.0%} 上限")
            else:
                reason = (f"对侧 {target.label} 可用备用仅 {spare:.0f} kW，转移负荷 {transferred:.0f} kW，"
                          f"缺口 {gap:.0f} kW，强行转供将使其升至 {rate_after:.1%}"
                          f"（上限 {max_rate:.0%}）")

            # R12 复核：合闸后重新搜索，核对失电是否真的消除
            recheck = self.outage(state_overrides={**state, sw.id: "closed"})
            recheck_ok = recheck["households"] == 0 or not feasible
            self._t(
                trace, "R12", f"转供后复核（{sw.id} 合位）",
                f"重新搜索供电路径：剩余失电 {recheck['households']} 户，"
                f"恢复 {restored_hh} 户，复核{'通过' if recheck_ok else '不一致'}",
            )

            plans.append({
                "id": f"TP-{len(plans) + 1}",
                "via_switch": sw.id,
                "via_switch_label": sw.label,
                "from_feeder": m.feeder_of_device(sw.connects[0] if not a_live else sw.connects[1]),
                "to_feeder": target_feeder,
                "to_feeder_label": target.label,
                "transferred_kw": round(transferred, 2),
                "restored_transformers": [t.id for t in restored],
                "restored_districts": [
                    m.district_of_transformer[t.id] for t in restored if t.id in m.district_of_transformer
                ],
                "restored_households": restored_hh,
                "target_load_before_kw": round(load_before, 2),
                "target_load_after_kw": round(load_after, 2),
                "target_rate_before": round(rate_before, 4),
                "target_rate_after": round(rate_after, 4),
                "spare_kw": round(spare, 2),
                # 转供后剩余容量：额定扣除转供后负荷。与 spare_kw（转供前可用备用）不是一个数，
                # 界面上若把前者说成"转供后余量"会被甲方当场算错。
                "spare_after_kw": round(rated - load_after, 2),
                "gap_kw": round(gap, 2),
                "feasible": feasible,
                "reason": reason,
                "recheck": {"remaining_households": recheck["households"], "passed": recheck_ok},
            })

        plans.sort(key=lambda p: (not p["feasible"], p["target_rate_after"]))
        for i, p in enumerate(plans, start=1):
            p["id"] = f"TP-{i}"

        feasible_count = sum(1 for p in plans if p["feasible"])
        if plans:
            best = plans[0]
            self._t(
                trace, "R6", "生成并排序转供方案",
                f"共 {len(plans)} 个候选通道，可行 {feasible_count} 个；"
                f"首选 {best['via_switch']}：{best['from_feeder']} → {best['to_feeder']}，"
                f"转移 {best['transferred_kw']:.0f} kW，恢复 {best['restored_households']} 户",
            )
        else:
            self._t(trace, "R6", "生成并排序转供方案", "失电区没有可用的联络通道，无法转供")

        return {
            "plans": plans,
            "feasible_count": feasible_count,
            "trace": trace,
            "message": "" if plans else "失电区无可用联络通道，不具备转供条件",
        }

    # ------------------------------------------------------------------
    # Q：配变过载能否靠转供解决
    # ------------------------------------------------------------------
    def transformer_relief(self, transformer_id: str) -> dict:
        m = self.model
        t = m.devices.get(transformer_id)
        if not t or t.kind != "transformer":
            raise ValueError(f"不存在配变 {transformer_id}")
        district_id = m.district_of_transformer.get(transformer_id)
        district = m.devices.get(district_id) if district_id else None
        rated = m.num(t, "ratedCapacityKW")
        load = m.num(t, "currentLoadKW")
        rate = load_rate(load, rated)
        excess = max(0.0, load - rated)
        trace: List[dict] = []
        self._t(trace, "R4", f"核对 {t.label} 负载",
                  f"{load:.0f} kW / {rated:.0f} kW = {rate:.1%}，超出额定 {excess:.0f} kW")
        self._t(trace, "R10", "核对台区配变基数",
                  f"公理 A5 规定台区恰好含 1 台配变；{district.label if district else district_id} "
                  f"仅装设 {t.label}，台区内无第二台配变可切换")
        self._t(trace, "R5", "检查转供通道",
                  "联络开关位于 10kV 馈线之间，转移的是整条线路段的负荷，"
                  "无法把单个台区的低压负荷切到另一台配变")

        options = [
            {"action": "切负荷", "detail": f"切除约 {excess:.0f} kW 可降至额定容量", "feasible": True},
            {"action": "增容改造",
             "detail": f"按重载阈值 {get_thresholds().get('heavy_rate'):.0%} 反算，"
                       f"容量应不低于 {load / max(get_thresholds().get('heavy_rate'), 0.05):.0f} kVA",
             "feasible": True},
            {"action": "低压侧负荷转移", "detail": "需新建低压联络，当前网架不具备", "feasible": False},
        ]
        self._t(trace, "R6", "给出处置建议",
                  f"无转供通道，建议优先切负荷 {excess:.0f} kW，并列入增容计划")

        return {
            "transformer": {"id": t.id, "label": t.label, "rated_kw": rated, "load_kw": load, "rate": round(rate, 4)},
            "district": {"id": district_id, "label": district.label if district else ""},
            "can_transfer": False,
            "excess_kw": round(excess, 2),
            "options": options,
            "trace": trace,
        }

    # ------------------------------------------------------------------
    # R8：计划检修停电范围
    # ------------------------------------------------------------------
    def maintenance_scope(self, order_id: str) -> dict:
        m = self.model
        g = m.graph
        order = m.devices.get(order_id)
        if not order or order.kind != "order":
            raise ValueError(f"不存在工单 {order_id}")
        equip_uri = g.value(order.uri, ONTO.targetsEquipment)
        if equip_uri is None:
            raise ValueError(f"工单 {order_id} 未关联设备")
        equip_id = str(equip_uri).split("#")[-1]
        equip = m.devices.get(equip_id)
        trace: List[dict] = []

        status = m.text(order, "orderStatus")
        start = m.text(order, "plannedStart")
        end = m.text(order, "plannedEnd")
        self._t(trace, "R8", "读取工单",
                  f"{order.label}（{order_id}）状态 {status}，计划 {start} 至 {end}，"
                  f"检修设备 {equip.label if equip else equip_id}")

        switch = self._isolating_switch(equip_id)
        if not switch:
            self._t(trace, "R8", "寻找隔离开关", "未找到可隔离该设备的开关，需人工确认停电方案")
            return {"order": order_id, "equipment": equip_id, "switch": None, "outage": None, "trace": trace}

        self._t(trace, "R8", "确定隔离操作",
                  f"断开 {switch.label}（{switch.id}，连接 {switch.connects[0]} 与 {switch.connects[1]}）"
                  f"可将 {equip.label if equip else equip_id} 与电源隔离")

        result = self.outage(switch.id, "open")
        transfer = self.transfer_plans(switch.id, "open")
        self._t(
            trace, "R8", "推算计划停电影响",
            f"计划停电 {result['households']} 户，涉及台区 "
            f"{'、'.join(d['label'] for d in result['affected_districts']) or '无'}；"
            + ("存在可行转供方案" if transfer["feasible_count"] else "无可行转供方案，需发电车或改期"),
        )
        return {
            "order": order_id,
            "order_label": order.label,
            "equipment": equip_id,
            "switch": switch.id,
            "outage": result,
            "transfer": transfer,
            "trace": trace,
        }

    def _isolating_switch(self, equip_id: str):
        """找出能把该设备与电源隔离的开关：优先取上游侧开关。"""
        m = self.model
        candidates = [sw for sw in m.switches.values() if equip_id in sw.connects]
        if not candidates:
            return None
        # 设备的上游对象
        dev = m.devices.get(equip_id)
        upstream = None
        if dev is not None:
            up = m.graph.value(dev.uri, ONTO.upstreamOf)
            if isinstance(up, URIRef):
                upstream = str(up).split("#")[-1]
        for sw in candidates:
            if upstream in sw.connects:
                return sw
        return candidates[0]

    # ------------------------------------------------------------------
    # R9：故障—停电—工单
    # ------------------------------------------------------------------
    def fault_chain(self, fault_id: str) -> dict:
        m = self.model
        g = m.graph
        fault = m.devices.get(fault_id)
        if not fault or fault.kind != "fault":
            raise ValueError(f"不存在故障记录 {fault_id}")
        equip_uri = g.value(fault.uri, ONTO.affectsEquipment)
        equip_id = str(equip_uri).split("#")[-1] if equip_uri else ""
        equip = m.devices.get(equip_id)
        trace: List[dict] = []
        self._t(trace, "R9", "定位故障设备",
                  f"{fault.label}（{fault_id}）发生于 {equip.label if equip else equip_id}，"
                  f"类型 {m.text(fault, 'faultType')}")

        switch = self._isolating_switch(equip_id)
        if not switch:
            return {"fault": fault_id, "equipment": equip_id, "trace": trace}
        self._t(trace, "R9", "保护动作分析",
                  f"故障点上游的 {switch.label}（{switch.id}）保护动作跳闸，馈线 "
                  f"{m.feeder_of.get(equip_id, '')} 失电")

        outage = self.outage(switch.id, "open")
        transfer = self.transfer_plans(switch.id, "open")
        self._t(
            trace, "R9", "生成停电事件",
            f"停电事件：影响 {len(outage['affected_districts'])} 个台区、{outage['households']} 户、"
            f"重要用户 {len(outage['critical_customers'])} 个",
        )
        backfeed = ""
        if transfer["feasible_count"]:
            p = transfer["plans"][0]
            backfeed = f"建议合上 {p['via_switch']}，由 {p['to_feeder_label']} 转供，恢复 {p['restored_households']} 户"
            self._t(trace, "R9", "给出处置建议", backfeed)
        else:
            self._t(trace, "R9", "给出处置建议", "无转供通道，需发电车保电或等待抢修")

        return {
            "fault": fault_id,
            "fault_label": fault.label,
            "equipment": equip_id,
            "trip_switch": switch.id,
            "outage": outage,
            "transfer": transfer,
            "backfeed_suggestion": backfeed,
            "trace": trace,
        }

    # ------------------------------------------------------------------
    # R10：一致性校验
    # ------------------------------------------------------------------
    def consistency_check(self) -> dict:
        m = self.model
        g = m.graph
        issues: List[dict] = []
        checks: List[dict] = []

        def add(code: str, message: str, axiom: str, level: str = "error") -> None:
            issues.append({"code": code, "message": message, "axiom": axiom, "level": level})

        # 馈线必须有唯一变电站
        for f in m.feeders:
            subs = list(g.objects(f.uri, ONTO.belongsToSubstation))
            if len(subs) != 1:
                add("C1", f"馈线 {f.label} 关联 {len(subs)} 个变电站，违反唯一电源点", "A3")
        checks.append({"axiom": "A3", "name": "馈线唯一电源点", "targets": len(m.feeders)})

        # 配变必须装设于唯一线路段
        for t in m.transformers:
            secs = list(g.objects(t.uri, ONTO.locatedOn))
            if len(secs) != 1:
                add("C2", f"配变 {t.label} 关联 {len(secs)} 个线路段", "A4")
        checks.append({"axiom": "A4", "name": "配变唯一装设位置", "targets": len(m.transformers)})

        # 台区恰好一台配变
        for d in m.districts:
            trs = list(g.objects(d.uri, ONTO.hasTransformer))
            if len(trs) != 1:
                add("C3", f"台区 {d.label} 关联 {len(trs)} 台配变", "A5")
        for t in m.transformers:
            if t.id not in m.district_of_transformer:
                add("C4", f"配变 {t.label} 未归属任何台区", "A5")
        checks.append({"axiom": "A5", "name": "台区与配变一一对应", "targets": len(m.districts)})

        # 用户必须挂台区
        for c in m.customers:
            if not isinstance(g.value(c.uri, ONTO.servedByDistrict), URIRef):
                add("C5", f"用户 {c.label} 未关联台区", "A6")
        checks.append({"axiom": "A6", "name": "用户唯一供电台区", "targets": len(m.customers)})

        # 联络开关连两条馈线
        for sw in m.switches.values():
            if sw.cls_name != "TieSwitch":
                continue
            fs = list(g.objects(sw.uri, ONTO.connectsFeeder))
            if len(fs) != 2:
                add("C6", f"联络开关 {sw.label} 连接 {len(fs)} 条馈线", "A7")
        checks.append({"axiom": "A7", "name": "联络开关连接两条馈线",
                       "targets": len([s for s in m.switches.values() if s.cls_name == "TieSwitch"])})

        # 口径：馈线负荷 = 配变负荷之和
        for f in m.feeders:
            total = sum(m.num(t, "currentLoadKW") for t in m.transformers_of_feeder(f.id))
            if abs(total - m.num(f, "currentLoadKW")) > 1e-6:
                add("C7", f"馈线 {f.label} 负荷 {m.num(f, 'currentLoadKW'):.0f} kW 与所带配变合计 "
                          f"{total:.0f} kW 不一致", "A3", level="warning")
        checks.append({"axiom": "口径", "name": "馈线负荷与配变合计一致", "targets": len(m.feeders)})

        return {
            "issues": issues,
            "checks": checks,
            "passed": len([i for i in issues if i["level"] == "error"]) == 0,
            "summary": f"共执行 {len(checks)} 类基数与口径校验，发现 {len(issues)} 处问题",
            "trace": [{
                "rule": "R10", "rule_name": "基数约束一致性校验",
                "action": "校验本体基数公理",
                "detail": f"执行 {len(checks)} 类校验，发现 {len(issues)} 处问题",
            }],
        }

    # ------------------------------------------------------------------
    # 查询与概览
    # ------------------------------------------------------------------
    def overview(self) -> dict:
        m = self.model
        load = self.load_report()
        return {
            "census": m.census(),
            "feeders": load["feeders"],
            "switches": [
                {"id": s.id, "label": s.label, "cls": s.cls_name, "state": s.state,
                 "normally_open": s.normally_open, "connects": list(s.connects), "feeder": s.feeder}
                for s in m.switches.values()
            ],
            "overloaded": load["overloaded"],
            "heavy": load["heavy"],
        }

    def device_info(self, keyword: str) -> List[dict]:
        m = self.model
        key = keyword.strip().upper()
        hits = []
        for d in m.devices.values():
            if key and (key in d.id.upper() or keyword in d.label):
                hits.append(self.describe(d.id))
        return hits[:20]

    def describe(self, dev_id: str) -> dict:
        m = self.model
        d = m.devices.get(dev_id)
        if not d:
            raise ValueError(f"不存在设备 {dev_id}")
        props = {}
        for pred, obj in m.graph.predicate_objects(d.uri):
            name = str(pred).split("#")[-1]
            if name in ("type", "") or "rdf-syntax" in str(pred):
                continue
            val = str(obj).split("#")[-1]
            props[name] = val
        extra = {}
        if d.kind == "transformer":
            rated = m.num(d, "ratedCapacityKW")
            load = m.num(d, "currentLoadKW")
            extra = {"rated_kw": rated, "load_kw": load, "rate": round(load_rate(load, rated), 4),
                     "status": classify_load(load_rate(load, rated)),
                     "district": m.district_of_transformer.get(d.id)}
        elif d.kind == "district":
            extra = {"households": int(m.num(d, "householdCount")),
                     "transformer": m.transformer_of_district.get(d.id),
                     "customers": [m.devices[c].label for c in self.cust_of_district.get(d.id, []) if c in m.devices]}
        elif d.kind == "switch":
            sw = m.switches[d.id]
            extra = {"state": sw.state, "cls": sw.cls_name, "connects": list(sw.connects),
                     "normally_open": sw.normally_open}
        return {"id": d.id, "label": d.label, "classes": d.classes, "kind": d.kind,
                "comment": d.comment, "properties": props, "extra": extra}

    def energized_path(self, dev_id: str) -> List[dict]:
        """给出某设备当前的供电路径（用于解释「它凭什么带电」）。"""
        m = self.model
        energized, parent = bfs_energized(m, m.state)
        if dev_id not in energized:
            return []
        chain = path_to_source(parent, dev_id)
        out = []
        for step in chain:
            src = m.devices.get(step["from"])
            dst = m.devices.get(step["to"])
            out.append({
                "from": step["from"], "from_label": src.label if src else step["from"],
                "to": step["to"], "to_label": dst.label if dst else step["to"],
                "via_switch": step["via_switch"],
                "via_switch_label": m.switches[step["via_switch"]].label if step["via_switch"] else None,
            })
        return out


_ENGINE: Optional[ReasoningEngine] = None


def get_engine(refresh: bool = False) -> ReasoningEngine:
    global _ENGINE
    if _ENGINE is None or refresh:
        _ENGINE = ReasoningEngine(get_model(refresh))
    return _ENGINE
