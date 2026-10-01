"""养护工程启动门禁：四级状态机 + 跨运行空间就绪检查。

门禁阶段（只能逐级推进，跳级/倒序阻断）：
    基础数据核验 → 配置探测 → 外部服务连通 → 开放发布

异常分流（不共用兜底路径）：
    无工程（空态）/ 边界缺失、材料缺失、前置件缺失、配置缺失（缺失）
    / 外部超时（超时）/ 数据源不可达（不可达）分别给处置路径。

结论回写三处：工程台账（project 表）、路段待办（road_todo 表）、运行日志面板（run_log 表）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store
from app.services import readiness_data as spec

GATE_TABLE = "gate"
TODO_TABLE = "road_todo"
LOG_TABLE = "run_log"
TASK_TABLE = "probe_task"

TASK_PENDING = "待执行"
TASK_DONE = "完成"
TASK_BLOCKED = "受阻"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class GateViolation(Exception):
    """跳级、倒序或对已批准工程的非法推进。"""


class ReadinessService:
    # ------------------------------------------------------------------ 初始化

    def bootstrap(self) -> dict[str, Any]:
        """幂等引导：迁移存档、建门禁台账、执行首检。重启不重复、不覆盖批准件。"""
        if store.get_meta("readiness_bootstrapped"):
            return {"ok": True, "idempotent": True, "message": "门禁台账已存在，跳过引导"}

        store.set_meta("service_status", dict(spec.SERVICE_STATUS_INITIAL))
        store.set_meta("datasource_status", dict(spec.PRICE_DATASOURCE))
        store.set_meta("extra_prereqs", {})   # 处置后补齐的前置件
        store.set_meta("extra_configs", {})   # 处置后补录的配置

        self._log("INFO", "-", "系统", "启动门禁引导开始", "迁移存档资料并建立四级门禁台账")
        migrated = self.migrate_archives(log=False)
        for m in migrated:
            self._log("INFO", m["工程编号"], "系统", "存档迁移回填", m["archive_note"])

        for row in store.rows("project"):
            code = str(row.get("工程编号", ""))
            approved = code in spec.APPROVED_PROJECT_CODES
            # 历史/已竣工工程按存档资料保留在工程台账，不纳入启动门禁
            if row.get("status") == "已竣工" and not approved:
                self._log("INFO", code, "系统", "历史工程保留",
                          str(row.get("archive_note", "已竣工历史工程按台账资料保留，不参与启动门禁")))
                continue
            gate = self._new_gate(row, approved)
            store.rows(GATE_TABLE).append(gate)
            if approved:
                # 已批准工程：沿用批准结论，不跑任何探测
                self._stamp_approved(gate, row)
                self._log("INFO", code, "开放发布", "已批准工程沿用结论", spec.DISPOSITION["已批准"])
            else:
                self._cascade_checks(code, reason="首检")

        store.set_meta("readiness_bootstrapped", True)
        self._log("INFO", "-", "系统", "启动门禁引导完成",
                  f"纳管工程 {len(store.rows(GATE_TABLE))} 个，迁移存档 {len(migrated)} 份")
        return {"ok": True, "idempotent": False, "gates": len(store.rows(GATE_TABLE)), "migrated": len(migrated)}

    def migrate_archives(self, *, log: bool = True) -> list[dict[str, Any]]:
        """存量工程按存档资料迁移回填；台账已有同编号（含已批准工程）不覆盖。"""
        migrated: list[dict[str, Any]] = []
        existing = {str(r.get("工程编号")) for r in store.rows("project")}
        for archive in spec.ARCHIVE_PROJECTS:
            code = archive["工程编号"]
            if code in existing:
                # 已批准工程绝不覆盖，仅记录一条保留说明
                if log:
                    self._log("WARN", code, "存档迁移", "跳过已存在工程",
                              "存档资料不覆盖现行台账" + ("（已批准工程）" if code in spec.APPROVED_PROJECT_CODES else ""))
                continue
            entry = {
                "id": store.next_id("project"),
                "status": archive["status"],
                "pending": archive["status"] != "已竣工",
                "abnormal": False,
                "工程编号": code,
                "工程名称": archive["工程名称"],
                "工程类型": archive["工程类型"],
                "施工路段": archive["施工路段"],
                "承建单位": archive["承建单位"],
                "开工日期": archive["开工日期"],
                "竣工日期": archive["竣工日期"],
                "工程状态": archive["status"],
                "来源": "存档迁移",
                "archive_note": archive["archive_note"],
            }
            store.rows("project").append(entry)
            existing.add(code)
            migrated.append(archive)
            if log:
                self._log("INFO", code, "存档迁移", "按存档资料回填台账", archive["archive_note"])
        return migrated

    # ------------------------------------------------------------------ 台账查询

    def list_gates(self, *, scenario: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(GATE_TABLE)
        if scenario:
            rows = [r for r in rows if r.get("阻断场景") == scenario]
        return rows

    def get_gate(self, code: str) -> dict[str, Any] | None:
        for row in store.rows(GATE_TABLE):
            if row.get("工程编号") == code:
                return row
        return None

    def summary(self) -> dict[str, Any]:
        """工程汇总页数据：总量、受阻/放行、场景分布、外部服务与数据源状态。"""
        gates = store.rows(GATE_TABLE)
        scenario_counts: dict[str, int] = {}
        blocked = passed = approved = checking = 0
        for gate in gates:
            if gate["approved"]:
                approved += 1
            elif gate.get("release_ready"):
                passed += 1
            elif gate.get("阻断场景"):
                blocked += 1
                scenario_counts[gate["阻断场景"]] = scenario_counts.get(gate["阻断场景"], 0) + 1
            else:
                checking += 1
        return {
            "cards": [
                {"label": "纳管工程", "value": len(gates)},
                {"label": "阻断中", "value": blocked},
                {"label": "可开放", "value": passed},
                {"label": "已批准沿用", "value": approved},
            ],
            "scenario_counts": scenario_counts,
            "dispositions": spec.DISPOSITION,
            "stages": spec.STAGE_ORDER,
            "service_status": store.get_meta("service_status", {}),
            "datasource_status": store.get_meta("datasource_status", {}),
            "todos_open": sum(1 for t in store.rows(TODO_TABLE) if t["status"] == "待办"),
        }

    def logs(self, *, code: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        rows = store.rows(LOG_TABLE)
        if code:
            rows = [r for r in rows if r.get("工程编号") == code]
        return list(reversed(rows))[:limit]

    def todos(self, *, road_code: str | None = None, open_only: bool = False) -> list[dict[str, Any]]:
        rows = store.rows(TODO_TABLE)
        if road_code:
            rows = [r for r in rows if r.get("路段编号") == road_code]
        if open_only:
            rows = [r for r in rows if r.get("status") == "待办"]
        return list(reversed(rows))

    # ------------------------------------------------------------------ 状态机推进

    def advance(self, code: str, target_stage: str) -> dict[str, Any]:
        """核验目标阶段：只能核验“当前所在级”，通过才进入下一级。

        - target 在 current 之后：跳级阻断；
        - target 在 current 之前：倒序阻断；
        - 已开放发布后再提交：重复推进阻断；
        - 本级阻断时允许带同一目标复检（处置后的继续路径，不算倒序）。
        """
        gate = self.get_gate(code)
        if gate is None:
            raise GateViolation(f"工程 {code} 不在门禁台账中")
        if gate["approved"]:
            raise GateViolation(f"工程 {code} 已批准，门禁结论锁定，禁止重复推进或覆盖")
        if gate.get("release_ready"):
            raise GateViolation(f"工程 {code} 已开放发布，禁止重复推进")
        if target_stage not in spec.STAGE_ORDER:
            raise GateViolation(f"目标阶段「{target_stage}」不是合法门禁阶段")

        current_idx = int(gate["current_idx"])
        target_idx = spec.STAGE_ORDER.index(target_stage)
        if target_idx > current_idx:
            raise GateViolation(
                f"禁止跳级：工程 {code} 停留在「{spec.STAGE_ORDER[current_idx]}」，"
                f"必须逐级通过后才能到达「{target_stage}」"
            )
        if target_idx < current_idx:
            raise GateViolation(
                f"禁止倒序：工程 {code} 已越过「{target_stage}」，门禁结论不允许回退重判"
            )

        passed, scenario, detail, disposition = self._evaluate(code, current_idx)
        result = gate["stages"][current_idx]
        if not passed:
            result.update(status=spec.STAGE_BLOCKED, scenario=scenario, reason=detail,
                          disposition=disposition, checked_at=_now())
            gate["阻断场景"] = scenario
            gate["处置路径"] = disposition
            gate["updated_at"] = _now()
            self._upsert_todo(gate, spec.STAGE_ORDER[current_idx], scenario, disposition)
            self._writeback_ledger(code)
            self._log("WARN", code, spec.STAGE_ORDER[current_idx], "门禁阻断", f"{scenario}：{detail}")
            return {"ok": False, "stage": target_stage, "scenario": scenario,
                    "reason": detail, "disposition": disposition, "gate": gate}

        # 通过：落本级、进入下一级
        result.update(status=spec.STAGE_PASSED, scenario=None, reason=detail,
                      disposition=None, checked_at=_now())
        gate["阻断场景"] = None
        gate["处置路径"] = None
        gate["current_idx"] = current_idx + 1
        gate["updated_at"] = _now()
        self._close_todos(code, spec.STAGE_ORDER[current_idx])
        self._writeback_ledger(code)
        self._log("INFO", code, spec.STAGE_ORDER[current_idx], "门禁通过", detail)

        if current_idx + 1 == len(spec.STAGE_ORDER):
            self._release(gate)
        return {"ok": True, "stage": spec.STAGE_ORDER[current_idx], "gate": gate}

    def allow_start(self, code: str) -> tuple[bool, str]:
        """批准开工的硬门禁：配置或前置件失败等任何未放行情况都阻断启动。"""
        gate = self.get_gate(code)
        if gate is None:
            return False, f"工程 {code} 未建立启动门禁台账，禁止开工"
        if gate["approved"]:
            return True, "已批准工程，沿用批准结论"
        if gate.get("release_ready"):
            return True, "四级门禁已逐级通过，允许开工"
        stage = spec.STAGE_ORDER[min(int(gate["current_idx"]), len(spec.STAGE_ORDER) - 1)]
        scenario = gate.get("阻断场景") or "前置阶段未完成"
        return False, f"启动门禁未放行（卡在「{stage}」- {scenario}）：{gate.get('处置路径') or '请逐级完成核验'}"

    # ------------------------------------------------------------------ 处置路径

    def resolve(self, code: str, kind: str) -> dict[str, Any]:
        """按阻断场景分别处置；处置后把该阶段置为待复检，不自动跳级。"""
        gate = self.get_gate(code)
        if gate is None:
            raise GateViolation(f"工程 {code} 不在门禁台账中")
        if gate["approved"]:
            raise GateViolation(f"工程 {code} 已批准，处置入口锁定")
        if gate.get("release_ready"):
            raise GateViolation(f"工程 {code} 已开放发布，无需处置")

        project = self._project(code)
        message = ""
        if kind == "边界缺失":
            road = self._road(str(project.get("施工路段", "")))
            if road is None:
                raise GateViolation("施工路段台账中不存在该路段，无法补录边界")
            road["边界桩号"] = road.get("起止桩号") or ""
            message = f"已按起止桩号 {road['边界桩号']} 补录路段边界"
        elif kind == "材料缺失":
            plans = spec.MATERIAL_PLANS.get(code, [])
            known = {str(r.get("材料编号")) for r in store.rows("material")}
            added = 0
            for plan in plans:
                mcode = plan["材料编号"]
                if mcode not in known:
                    store.rows("material").append({
                        "id": store.next_id("material"),
                        "status": "在库", "pending": True, "abnormal": False,
                        "材料编号": mcode, "材料名称": f"{mcode} 补录材料", "材料类别": "补录",
                        "规格型号": "按材料计划", "供应商": "待询源", "进场日期": _now()[:10],
                        "存放地点": "待入库", "材料状态": "在库",
                    })
                    known.add(mcode)
                    added += 1
            message = f"已按材料计划补录台账材料 {added} 项并重新纳入相符性探测"
        elif kind == "前置件缺失":
            held = self._missing_prereqs(code)
            if not held:
                message = "前置件已齐全"
            else:
                extra = store.get_meta("extra_prereqs")
                extra.setdefault(code, []).extend(sorted(held))
                store.set_meta("extra_prereqs", extra)
                message = f"已补交前置件：{'、'.join(sorted(held))}"
        elif kind == "配置缺失":
            extra = store.get_meta("extra_configs")
            extra[code] = {"运行空间": f"{code} 补录运行空间", "数据同步周期": "5min", "作业窗口": "22:00-06:00"}
            store.set_meta("extra_configs", extra)
            message = "已补录运行空间与同步配置，可重新探测"
        elif kind == "外部超时":
            statuses = store.get_meta("service_status")
            recovered = []
            for svc in spec.PROJECT_SERVICES.get(code, []):
                if statuses.get(svc) == "timeout":
                    statuses[svc] = "ok"
                    recovered.append(svc)
            store.set_meta("service_status", statuses)
            message = f"外部服务已恢复连通：{'、'.join(recovered) or '无超时依赖'}，请重跑探针"
        elif kind == "数据源不可达":
            sources = store.get_meta("datasource_status")
            recovered = []
            for plan in spec.MATERIAL_PLANS.get(code, []):
                src = plan["数据来源"]
                if sources.get(src) == "unreachable":
                    sources[src] = "manual"
                    recovered.append(src)
            store.set_meta("datasource_status", sources)
            message = f"数据源 {('、'.join(recovered) or '')} 已转人工核验通道，核验通过后重跑探针"
        else:
            raise GateViolation(f"阻断场景「{kind}」没有对应的处置路径，不能走兜底放行")

        stage_idx = int(gate["current_idx"])
        gate["stages"][stage_idx]["status"] = spec.STAGE_RECHECK
        gate["阻断场景"] = None
        gate["处置路径"] = None
        gate["updated_at"] = _now()
        self._writeback_ledger(code)
        self._log("INFO", code, spec.STAGE_ORDER[stage_idx], "阻断处置完成", message)
        return {"ok": True, "kind": kind, "message": message, "gate": gate}

    def inject_fault(self, target: str, status: str) -> dict[str, Any]:
        """演示用故障注入：把外部服务置为 timeout / ok，把数据源置为 unreachable / ok。"""
        if status not in {"ok", "timeout", "unreachable"}:
            raise GateViolation("故障状态只支持 ok / timeout / unreachable")
        services = store.get_meta("service_status")
        sources = store.get_meta("datasource_status")
        if target in services:
            services[target] = status if status != "unreachable" else "unreachable"
            store.set_meta("service_status", services)
        elif target in sources:
            sources[target] = status
            store.set_meta("datasource_status", sources)
        else:
            raise GateViolation(f"探测目标「{target}」未登记")
        self._log("WARN", "-", "外部服务连通", "故障注入", f"{target} → {status}")
        return {"ok": True, "target": target, "status": status}

    # ------------------------------------------------------------------ 批次探针

    def create_batch(self) -> dict[str, Any]:
        """为每个未放行、未批准工程在当前阶段建探针；按批次幂等，已批准工程不入批。"""
        open_batch = store.get_meta("probe_open_batch")
        if open_batch:
            return {"ok": True, "idempotent": True, **open_batch}

        gates = [g for g in store.rows(GATE_TABLE) if not g["approved"] and not g.get("release_ready")]
        if not gates:
            # 无工程：空态独立处置路径，只登记空批次结论
            batch_no = self._next_batch_no()
            batch = {"batch_no": batch_no, "created_at": _now(), "total": 0, "done": 0, "status": "空批次"}
            store.set_meta("probe_open_batch", batch)
            self._log("WARN", "-", "批次探针", f"批次 {batch_no} 空态", spec.DISPOSITION["无工程"])
            return {"ok": True, "idempotent": False, **batch}

        batch_no = self._next_batch_no()
        tasks: list[dict[str, Any]] = []
        for gate in gates:
            idx = int(gate["current_idx"])
            tasks.append({
                "id": store.next_id(TASK_TABLE),
                "batch_no": batch_no,
                "工程编号": gate["工程编号"],
                "阶段": spec.STAGE_ORDER[min(idx, len(spec.STAGE_ORDER) - 1)],
                "stage_idx": idx,
                "status": TASK_PENDING,
                "scenario": None,
                "detail": None,
                "created_at": _now(),
            })
        store.rows(TASK_TABLE).extend(tasks)
        batch = {"batch_no": batch_no, "created_at": _now(), "total": len(tasks), "done": 0, "status": "进行中"}
        store.set_meta("probe_open_batch", batch)
        self._log("INFO", "-", "批次探针", f"批次 {batch_no} 已建", f"入批工程 {len(tasks)} 个，已批准工程不入批")
        return {"ok": True, "idempotent": False, **batch}

    def run_batch(self) -> dict[str, Any]:
        """执行开放批次：完成项不重跑（幂等），受阻项保留为未完成，逐级推进不跳级。"""
        batch = store.get_meta("probe_open_batch")
        if not batch:
            raise GateViolation("没有进行中的探测批次，请先创建批次")
        if batch["total"] == 0:
            batch["status"] = "空批次闭环"
            store.set_meta("probe_open_batch", None)
            self._log("INFO", "-", "批次探针", f"批次 {batch['batch_no']} 闭环", spec.DISPOSITION["无工程"])
            return {"ok": True, **batch, "tasks": []}

        results: list[dict[str, Any]] = []
        for task in self._batch_tasks(batch["batch_no"]):
            if task["status"] == TASK_DONE:
                results.append(task)  # 已完成不覆盖
                continue
            code = task["工程编号"]
            gate = self.get_gate(code)
            # 任务阶段可能已被前序批次推进，按门禁当前阶段重定向（仍是逐级）
            idx = int(gate["current_idx"])
            if gate.get("release_ready"):
                task["status"] = TASK_DONE
                task["detail"] = "已开放发布，跳过"
                results.append(task)
                continue
            passed, scenario, detail, disposition = self._evaluate(code, idx)
            task["stage_idx"] = idx
            task["阶段"] = spec.STAGE_ORDER[min(idx, len(spec.STAGE_ORDER) - 1)]
            if passed:
                task["status"] = TASK_DONE
                task["scenario"] = None
                task["detail"] = detail
                gate["stages"][idx].update(status=spec.STAGE_PASSED, scenario=None,
                                           reason=detail, disposition=None, checked_at=_now())
                gate["current_idx"] = idx + 1
                gate["阻断场景"] = None
                gate["处置路径"] = None
                self._close_todos(code, spec.STAGE_ORDER[idx])
                if idx + 1 == len(spec.STAGE_ORDER):
                    self._release(gate)
                self._log("INFO", code, task["阶段"], "批次探针通过", detail)
            else:
                task["status"] = TASK_BLOCKED
                task["scenario"] = scenario
                task["detail"] = f"{scenario}：{detail}"
                gate["stages"][idx].update(status=spec.STAGE_BLOCKED, scenario=scenario,
                                           reason=detail, disposition=disposition, checked_at=_now())
                gate["阻断场景"] = scenario
                gate["处置路径"] = disposition
                self._upsert_todo(gate, task["阶段"], scenario, disposition)
                self._log("WARN", code, task["阶段"], "批次探针受阻", task["detail"])
            self._writeback_ledger(code)
            results.append(task)

        done = sum(1 for t in results if t["status"] == TASK_DONE)
        blocked = sum(1 for t in results if t["status"] == TASK_BLOCKED)
        batch["done"] = done
        batch["status"] = "全部完成" if blocked == 0 else ("已完成" if done == batch["total"] else "有待完成项")
        if done == batch["total"]:
            store.set_meta("probe_open_batch", None)
        return {"ok": True, **batch, "blocked": blocked, "tasks": results}

    def reset_probes(self) -> dict[str, Any]:
        """复位：受阻/待执行项回到待执行以便继续，完成项保留，已批准工程从不入批故不受影响。"""
        batch = store.get_meta("probe_open_batch")
        if not batch:
            raise GateViolation("没有进行中的探测批次，无需复位")
        tasks = self._batch_tasks(batch["batch_no"])
        reset = 0
        for task in tasks:
            if task["status"] in (TASK_PENDING, TASK_BLOCKED):
                task["status"] = TASK_PENDING
                task["scenario"] = None
                task["detail"] = None
                reset += 1
            # TASK_DONE 保留，继续未完成项时不重复探测
        batch["status"] = "已复位"
        self._log("INFO", "-", "批次探针", f"批次 {batch['batch_no']} 复位",
                  f"未完成项 {reset} 个回到待执行，已完成项保留，已批准工程不受影响")
        return {"ok": True, **batch, "reset": reset}

    # ------------------------------------------------------------------ 核验引擎

    def _cascade_checks(self, code: str, *, reason: str) -> None:
        """首检：逐级核验到第一个受阻场景为止，不替工程跳级。"""
        gate = self.get_gate(code)
        idx = 0
        while idx < len(spec.STAGE_ORDER):
            passed, scenario, detail, disposition = self._evaluate(code, idx)
            result = gate["stages"][idx]
            result["checked_at"] = _now()
            if not passed:
                result.update(status=spec.STAGE_BLOCKED, scenario=scenario,
                              reason=detail, disposition=disposition)
                gate["阻断场景"] = scenario
                gate["处置路径"] = disposition
                self._upsert_todo(gate, spec.STAGE_ORDER[idx], scenario, disposition)
                self._log("WARN", code, spec.STAGE_ORDER[idx], f"{reason}阻断", f"{scenario}：{detail}")
                self._writeback_ledger(code)
                return
            result.update(status=spec.STAGE_PASSED, reason=detail)
            self._log("INFO", code, spec.STAGE_ORDER[idx], f"{reason}通过", detail)
            gate["current_idx"] = idx + 1
            idx += 1
        self._release(gate)

    def _evaluate(self, code: str, idx: int) -> tuple[bool, str | None, str, str | None]:
        """返回 (是否通过, 阻断场景, 说明, 处置路径)。四类异常各自独立返回，不做兜底合并。"""
        project = self._project(code)
        if project is None:
            return False, "无工程", f"台账中不存在工程 {code}", spec.DISPOSITION["无工程"]

        if idx == 0:
            return self._check_base_data(code, project)
        if idx == 1:
            return self._check_config(code, project)
        if idx == 2:
            return self._check_services(code, project)
        return self._check_release(code, project)

    def _check_base_data(self, code: str, project: dict[str, Any]) -> tuple[bool, str | None, str, str | None]:
        # 跨运行空间相符性：工程台账 ↔ 施工路段 ↔ 材料计划
        road_code = str(project.get("施工路段", ""))
        road = self._road(road_code)
        if road is None:
            return False, "边界缺失", f"施工路段 {road_code} 在路段台账中不存在，施工边界无法确认", spec.DISPOSITION["边界缺失"]
        if not str(road.get("边界桩号", "")).strip():
            return False, "边界缺失", f"路段「{road.get('路段名称')}」({road_code}) 边界桩号缺失，施工边界不闭合", spec.DISPOSITION["边界缺失"]

        plans = spec.MATERIAL_PLANS.get(code)
        if not plans:
            return False, "材料缺失", f"工程 {code} 未登记材料计划，三方相符性无法探测", spec.DISPOSITION["材料缺失"]
        known = {str(r.get("材料编号")) for r in store.rows("material")}
        missing = [p["材料编号"] for p in plans if p["材料编号"] not in known]
        if missing:
            return False, "材料缺失", f"材料计划中 {'、'.join(missing)} 在材料台账中不存在", spec.DISPOSITION["材料缺失"]

        sources = store.get_meta("datasource_status", {})
        unreachable = [p["数据来源"] for p in plans if sources.get(p["数据来源"]) == "unreachable"]
        if unreachable:
            return False, "数据源不可达", f"材料数据源 {'、'.join(sorted(set(unreachable)))} 连接不可达", spec.DISPOSITION["数据源不可达"]

        return True, None, f"台账/路段「{road.get('路段名称')}」/材料计划 {len(plans)} 项三方相符，边界 {road.get('边界桩号')}", None

    def _check_config(self, code: str, project: dict[str, Any]) -> tuple[bool, str | None, str, str | None]:
        required = spec.PROJECT_PREREQUISITES.get(code, [])
        missing_pieces = sorted(self._missing_prereqs(code))
        if missing_pieces:
            return False, "前置件缺失", f"前置件缺少：{'、'.join(missing_pieces)}", spec.DISPOSITION["前置件缺失"]

        config = self._config(code)
        if not config:
            return False, "配置缺失", f"工程 {code} 未探测到运行空间配置", spec.DISPOSITION["配置缺失"]
        for key in ("运行空间", "数据同步周期", "作业窗口"):
            if not str(config.get(key, "")).strip():
                return False, "配置缺失", f"运行配置项「{key}」为空", spec.DISPOSITION["配置缺失"]
        return True, None, f"前置件 {len(required)} 件齐全；运行空间「{config['运行空间']}」配置探测通过", None

    def _check_services(self, code: str, project: dict[str, Any]) -> tuple[bool, str | None, str, str | None]:
        statuses = store.get_meta("service_status", {})
        timed_out = [s for s in spec.PROJECT_SERVICES.get(code, []) if statuses.get(s) == "timeout"]
        if timed_out:
            return False, "外部超时", f"外部服务响应超时：{'、'.join(timed_out)}", spec.DISPOSITION["外部超时"]
        unreachable = [s for s in spec.PROJECT_SERVICES.get(code, []) if statuses.get(s) == "unreachable"]
        if unreachable:
            return False, "数据源不可达", f"外部服务连接不可达：{'、'.join(unreachable)}", spec.DISPOSITION["数据源不可达"]
        deps = spec.PROJECT_SERVICES.get(code, [])
        return True, None, f"外部依赖 {len(deps)} 项（{'、'.join(deps)}）全部连通", None

    def _check_release(self, code: str, project: dict[str, Any]) -> tuple[bool, str | None, str, str | None]:
        # 冲突里程碑以计划基线裁决；未完成里程碑以基线排期
        baseline = spec.PLAN_BASELINES.get(code)
        if baseline is None:
            return False, "基线缺失", f"工程 {code} 缺少计划基线，开放发布无裁决依据", spec.DISPOSITION["基线缺失"]
        notes: list[str] = []
        ledger_start = str(project.get("开工日期", ""))
        if ledger_start and ledger_start != baseline["plan_start"]:
            notes.append(f"开工里程碑冲突：台账 {ledger_start} → 按基线裁决为 {baseline['plan_start']}")
            project["开工日期"] = baseline["plan_start"]
        if not str(project.get("竣工日期", "")).strip():
            notes.append(f"未完成里程碑：竣工日期按计划基线 {baseline['plan_finish']} 排期，验收后回填")
        project["基线开工"] = baseline["plan_start"]
        project["基线竣工"] = baseline["plan_finish"]
        detail = "；".join(notes) if notes else "里程碑与计划基线一致"
        self._log("WARN" if notes else "INFO", code, "开放发布", "计划基线裁决", detail)
        return True, "基线冲突" if notes and any("冲突" in n for n in notes) else None, detail, None

    # ------------------------------------------------------------------ 内部工具

    def _release(self, gate: dict[str, Any]) -> None:
        code = str(gate["工程编号"])
        gate["current_idx"] = len(spec.STAGE_ORDER)
        gate["release_ready"] = True
        gate["阻断场景"] = None
        gate["处置路径"] = None
        gate["updated_at"] = _now()
        gate["stages"][-1].update(status=spec.STAGE_PASSED, checked_at=_now(),
                                  reason="开放发布：四级门禁逐级通过")
        self._close_todos(code, None)
        self._writeback_ledger(code, conclusion="可开放")
        self._log("INFO", code, "开放发布", "工程已放行", spec.DISPOSITION["可开放"])

    def _new_gate(self, row: dict[str, Any], approved: bool) -> dict[str, Any]:
        return {
            "id": store.next_id(GATE_TABLE),
            "工程编号": row.get("工程编号"),
            "工程名称": row.get("工程名称"),
            "路段编号": row.get("施工路段"),
            "approved": approved,
            "current_idx": 0,
            "stages": [
                {"stage": name, "status": spec.STAGE_PENDING, "scenario": None,
                 "reason": None, "disposition": None, "checked_at": None}
                for name in spec.STAGE_ORDER
            ],
            "阻断场景": None,
            "处置路径": None,
            "release_ready": False,
            "updated_at": _now(),
        }

    def _stamp_approved(self, gate: dict[str, Any], row: dict[str, Any]) -> None:
        gate["current_idx"] = len(spec.STAGE_ORDER)
        gate["release_ready"] = True
        for stage in gate["stages"]:
            stage.update(status="已批准", reason="沿用批准记录", disposition=spec.DISPOSITION["已批准"],
                         checked_at=_now())
        self._writeback_ledger(str(row.get("工程编号")), conclusion="已批准沿用")

    def _writeback_ledger(self, code: str, *, conclusion: str | None = None) -> None:
        """结论回写工程台账。"""
        project = self._project(code)
        if project is None:
            return
        gate = self.get_gate(code)
        if gate is None:
            return
        idx = min(int(gate["current_idx"]), len(spec.STAGE_ORDER) - 1)
        if conclusion is None:
            if gate.get("release_ready"):
                conclusion = "可开放"
            elif gate.get("阻断场景"):
                conclusion = f"阻断：{gate['阻断场景']}"
            else:
                conclusion = f"核验中：{spec.STAGE_ORDER[idx]}"
        project["门禁结论"] = conclusion
        project["门禁阶段"] = spec.STAGE_ORDER[idx]
        project["门禁更新时间"] = _now()

    def _upsert_todo(self, gate: dict[str, Any], stage: str, scenario: str, disposition: str | None) -> None:
        """结论回写路段待办：同工程+同阶段+同场景只保留一条待办（幂等）。"""
        code = str(gate["工程编号"])
        road = self._road(str(gate.get("路段编号", "")))
        for todo in store.rows(TODO_TABLE):
            if todo["工程编号"] == code and todo["阶段"] == stage and todo["场景"] == scenario and todo["status"] == "待办":
                todo["处置路径"] = disposition
                return
        store.rows(TODO_TABLE).append({
            "id": store.next_id(TODO_TABLE),
            "路段编号": gate.get("路段编号"),
            "路段名称": road.get("路段名称") if road else "路段台账缺失",
            "工程编号": code,
            "工程名称": gate.get("工程名称"),
            "阶段": stage,
            "场景": scenario,
            "处置路径": disposition,
            "status": "待办",
            "created_at": _now(),
        })

    def _close_todos(self, code: str, stage: str | None) -> None:
        for todo in store.rows(TODO_TABLE):
            if todo["工程编号"] != code or todo["status"] != "待办":
                continue
            if stage is None or todo["阶段"] == stage:
                todo["status"] = "已闭环"
                todo["closed_at"] = _now()

    def _log(self, level: str, code: str, stage: str, event: str, detail: str) -> None:
        store.rows(LOG_TABLE).append({
            "id": store.next_id(LOG_TABLE),
            "time": _now(), "level": level, "工程编号": code,
            "阶段": stage, "event": event, "detail": detail,
        })

    def _project(self, code: str) -> dict[str, Any] | None:
        for row in store.rows("project"):
            if str(row.get("工程编号")) == code:
                return row
        return None

    def _road(self, road_code: str) -> dict[str, Any] | None:
        for row in store.rows("road_section"):
            if str(row.get("路段编号")) == road_code:
                return row
        return None

    def _config(self, code: str) -> dict[str, str]:
        extra = store.get_meta("extra_configs", {})
        return {**spec.PROJECT_CONFIGS.get(code, {}), **extra.get(code, {})}

    def _missing_prereqs(self, code: str) -> set[str]:
        required = set(spec.PROJECT_PREREQUISITES.get(code, []))
        # 演示口径：PROJ-2026-003 初始缺交通组织方案；处置补交的前置件记入 extra_prereqs
        submitted = required - ({"交通组织方案"} if code == "PROJ-2026-003" else set())
        submitted |= set(store.get_meta("extra_prereqs", {}).get(code, []))
        return required - submitted

    def _batch_tasks(self, batch_no: str) -> list[dict[str, Any]]:
        return [t for t in store.rows(TASK_TABLE) if t["batch_no"] == batch_no]

    def _next_batch_no(self) -> str:
        return f"B{datetime.now().strftime('%Y%m%d%H%M%S')}"


readiness_service = ReadinessService()
