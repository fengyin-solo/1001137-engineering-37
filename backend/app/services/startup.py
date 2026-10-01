"""工程启动检查与跨运行空间就绪检查。

状态机门禁：基础数据核验 → 配置探测 → 外部服务连通 → 开放发布，只能逐级推进，
跳级或倒序一律阻断；空态、缺失、超时、不可达各自走独立处置路径，不共用兜底。
检查结论回写工程台账、路段待办与运行日志面板；探测任务按批次幂等，复位后继续
未完成项，已批准工程不被覆盖。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.config import settings
from app.store import store

MODULE_PROJECT = "project"
MODULE_SECTION = "road_section"
MODULE_MATERIAL = "material"
TABLE_GATE = "startup_gate"
TABLE_BATCH = "probe_batch"
TABLE_LOG = "run_log"
TABLE_BASELINE = "plan_baseline"
TABLE_ARCHIVE = "archive_record"
TABLE_CHANNEL = "probe_channel"

STAGES = ["base_data", "config_probe", "external_connect", "release"]
STAGE_LABELS = {
    "base_data": "基础数据核验",
    "config_probe": "配置探测",
    "external_connect": "外部服务连通",
    "release": "开放发布",
}

# 处置路径：空态、缺失、超时、不可达等各自独立，不共用一条兜底
REMEDIES = {
    "no_project": "工程台账无此工程，请先在养护工程台账登记",
    "empty": "基础数据空态，先登记路段与材料等基础数据再核验",
    "missing": "关键数据缺失，按存档资料迁移回填或手工补齐后重新核验",
    "boundary_missing": "施工路段边界缺失，补全路段登记与起止桩号后重新探测",
    "timeout": "外部服务超时，稍后重试或切换备用通道",
    "unreachable": "数据源不可达，检查链路恢复后重新探测",
    "config": "配置或前置件未就绪，补齐计划基线后复位重验，启动保持阻断",
}

LOG_LEVELS = {
    "ok": "信息",
    "empty": "提示",
    "missing": "告警",
    "boundary_missing": "告警",
    "timeout": "告警",
    "unreachable": "错误",
    "no_project": "错误",
    "config": "错误",
}

APPROVED_STATUSES = ["施工中", "已竣工", "已验收"]  # 已批准工程：探测与迁移不得覆盖
REQUIRED_PROJECT_FIELDS = ["工程名称", "工程类型", "施工路段", "承建单位"]
CHANNEL_STATES = ["正常", "超时", "不可达"]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(table: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1


def _find_project(code: str) -> dict[str, Any] | None:
    for row in store.rows(MODULE_PROJECT):
        if str(row.get("工程编号", "")) == code:
            return row
    return None


def _find_section(name: str) -> dict[str, Any] | None:
    """施工路段可以按路段名称或路段编号挂上管养路段。"""
    if not name:
        return None
    for row in store.rows(MODULE_SECTION):
        if name in (str(row.get("路段名称", "")), str(row.get("路段编号", ""))):
            return row
    return None


def _parse_reported(raw: str) -> dict[str, str]:
    """里程碑填报串：「开工:2026-09-05;竣工:2026-10-20」。"""
    reported: dict[str, str] = {}
    for part in str(raw or "").split(";"):
        if ":" in part:
            name, _, day = part.partition(":")
            if name.strip() and day.strip():
                reported[name.strip()] = day.strip()
    return reported


class StartupService:
    """启动检查门禁与就绪探测的业务规则都收在这里，路由层不做判断。"""

    # ---------- 运行日志面板 ----------

    def _log(self, source: str, category: str, content: str) -> None:
        store.rows(TABLE_LOG).append({
            "id": _next_id(TABLE_LOG),
            "时间": _now(),
            "来源": source,
            "级别": LOG_LEVELS.get(category, "信息"),
            "内容": content,
        })

    def list_logs(self, *, page: int = 1, size: int = 50) -> tuple[list[dict[str, Any]], int]:
        rows = list(reversed(store.rows(TABLE_LOG)))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    # ---------- 状态机门禁 ----------

    def _gate(self, code: str) -> dict[str, Any]:
        for row in store.rows(TABLE_GATE):
            if row.get("工程编号") == code:
                return row
        gate = {
            "id": _next_id(TABLE_GATE),
            "工程编号": code,
            "已通过": [],
            "阻断": False,
            "阻断原因": "",
            "结论": {},
            "已发布": False,
        }
        store.rows(TABLE_GATE).append(gate)
        return gate

    def gate_view(self, code: str) -> dict[str, Any] | None:
        project = _find_project(code)
        if project is None:
            return None
        gate = self._gate(code)
        passed: list[str] = gate["已通过"]
        stages = []
        for index, key in enumerate(STAGES):
            if key in passed:
                state = "通过"
            elif index == len(passed) and not gate["阻断"]:
                state = "待推进"
            elif index == len(passed) and gate["阻断"]:
                state = "阻断"
            else:
                state = "未解锁"
            stages.append({
                "key": key,
                "label": STAGE_LABELS[key],
                "状态": state,
                "结论": gate["结论"].get(key),
            })
        current = "已发布" if gate["已发布"] else STAGE_LABELS[STAGES[min(len(passed), len(STAGES) - 1)]]
        return {
            "工程编号": code,
            "当前阶段": current,
            "阻断": gate["阻断"],
            "阻断原因": gate["阻断原因"],
            "已发布": gate["已发布"],
            "启动检查": project.get("启动检查", ""),
            "就绪检查": project.get("就绪检查", ""),
            "stages": stages,
        }

    def gates_overview(self) -> list[dict[str, Any]]:
        """工程汇总页：每个工程的门禁阶段与最近检查结论。"""
        views = []
        for project in store.rows(MODULE_PROJECT):
            view = self.gate_view(str(project.get("工程编号", "")))
            if view is not None:
                views.append(view)
        return views

    def advance(self, code: str, target: str) -> tuple[dict[str, Any] | None, str, str]:
        """推进门禁：只允许逐级推进，跳级或倒序直接阻断。"""
        project = _find_project(code)
        if project is None:
            self._log("启动检查", "no_project", f"{code} 推进被拒：{REMEDIES['no_project']}")
            return None, "no_project", f"无工程：{REMEDIES['no_project']}"
        if target not in STAGES:
            return None, "unknown", f"阶段「{target}」不在状态机序列里"
        gate = self._gate(code)
        if gate["阻断"]:
            return None, "config", f"启动已阻断：{gate['阻断原因']}，请先复位门禁"
        passed: list[str] = gate["已通过"]
        index = STAGES.index(target)
        if index < len(passed):
            return None, "reverse", f"不允许倒序：{STAGE_LABELS[target]}已通过，只能继续未完成阶段"
        if index > len(passed):
            expect = STAGE_LABELS[STAGES[len(passed)]]
            return None, "skip", f"不允许跳级：请先完成{expect}，再推进{STAGE_LABELS[target]}"
        check = self._run_stage(target, project)
        gate["结论"][target] = {**check, "时间": _now()}
        if check["结果"] == "通过":
            passed.append(target)
            if target == "release":
                gate["已发布"] = True
            message = f"{STAGE_LABELS[target]}通过"
        else:
            message = f"{STAGE_LABELS[target]}未通过：{check['明细']}"
            if check["处置"]:
                message += f"；处置：{check['处置']}"
            if target == "config_probe":
                # 配置或前置件失败必须阻断启动
                gate["阻断"] = True
                gate["阻断原因"] = check["明细"]
                message += "，启动已阻断"
        self._write_back_gate(project, target, check)
        return self.gate_view(code), check["类别"], message

    def reset_gate(self, code: str) -> tuple[dict[str, Any] | None, str]:
        """复位门禁：解除阻断、保留已通过阶段，继续未完成项。"""
        if _find_project(code) is None:
            return None, f"无工程：{REMEDIES['no_project']}"
        gate = self._gate(code)
        gate["阻断"] = False
        gate["阻断原因"] = ""
        kept = len(gate["已通过"])
        self._log("启动检查", "ok", f"{code} 门禁复位，保留已通过 {kept} 个阶段，继续未完成项")
        return self.gate_view(code), f"门禁已复位，已通过 {kept} 个阶段保留，继续未完成项"

    def _run_stage(self, stage: str, project: dict[str, Any]) -> dict[str, Any]:
        checks = {
            "base_data": self._check_base_data,
            "config_probe": self._check_config,
            "external_connect": self._check_external,
            "release": self._check_release,
        }
        return checks[stage](project)

    def _check_base_data(self, project: dict[str, Any]) -> dict[str, Any]:
        # 空态：路段或材料等基础数据整体为空，与字段缺失分开处置
        if not store.rows(MODULE_SECTION) or not store.rows(MODULE_MATERIAL):
            return {"结果": "未通过", "类别": "empty", "处置": REMEDIES["empty"],
                    "明细": "路段或材料基础数据为空"}
        # 超时/不可达：读取存档资料依赖档案数据源，通道异常时各自独立处置
        archive = self._channel_state("档案数据源")
        if archive == "超时":
            return {"结果": "未通过", "类别": "timeout", "处置": REMEDIES["timeout"],
                    "明细": "读取存档资料超时"}
        if archive == "不可达":
            return {"结果": "未通过", "类别": "unreachable", "处置": REMEDIES["unreachable"],
                    "明细": "档案数据源不可达"}
        missing = [field for field in REQUIRED_PROJECT_FIELDS
                   if not str(project.get(field) or "").strip()]
        if missing:
            return {"结果": "未通过", "类别": "missing", "处置": REMEDIES["missing"],
                    "明细": "缺失字段：" + "、".join(missing)}
        return {"结果": "通过", "类别": "ok", "处置": "", "明细": "基础数据齐全"}

    def _check_config(self, project: dict[str, Any]) -> dict[str, Any]:
        problems = []
        if settings.page_size_default <= 0 or settings.page_size_max < settings.page_size_default:
            problems.append("分页配置非法")
        code = str(project.get("工程编号", ""))
        if not any(row.get("工程编号") == code for row in store.rows(TABLE_BASELINE)):
            problems.append("计划基线未建立")
        if problems:
            return {"结果": "未通过", "类别": "config", "处置": REMEDIES["config"],
                    "明细": "、".join(problems)}
        return {"结果": "通过", "类别": "ok", "处置": "", "明细": "配置与前置件就绪"}

    def _check_external(self, project: dict[str, Any]) -> dict[str, Any]:
        for channel in store.rows(TABLE_CHANNEL):
            state = str(channel.get("状态", ""))
            if state == "超时":
                return {"结果": "未通过", "类别": "timeout", "处置": REMEDIES["timeout"],
                        "明细": f"{channel.get('通道', '')}超时"}
            if state == "不可达":
                return {"结果": "未通过", "类别": "unreachable", "处置": REMEDIES["unreachable"],
                        "明细": f"{channel.get('通道', '')}不可达"}
        return {"结果": "通过", "类别": "ok", "处置": "", "明细": "外部服务全部连通"}

    def _check_release(self, project: dict[str, Any]) -> dict[str, Any]:
        milestones = self.adjudicate(str(project.get("工程编号", "")), persist=True)
        summary = "；".join(f"{item['里程碑']}→{item['裁决日期']}" for item in milestones) or "无里程碑基线"
        return {"结果": "通过", "类别": "ok", "处置": "",
                "明细": f"里程碑按基线裁决完成（{summary}），开放发布"}

    def _write_back_gate(self, project: dict[str, Any], stage: str, check: dict[str, Any]) -> None:
        """检查结论回写工程台账、路段待办和运行日志面板。"""
        code = str(project.get("工程编号", ""))
        label = STAGE_LABELS[stage]
        project["启动检查"] = "已开放发布" if stage == "release" and check["结果"] == "通过" \
            else f"{label}·{check['结果']}"
        section = _find_section(str(project.get("施工路段", "") or ""))
        if section is not None:
            section["待办"] = f"{code} {label}{check['结果']}，{check['处置'] or '按计划推进'}"
        self._log("启动检查", check["类别"], f"{code} {label}{check['结果']}：{check['明细']}")

    # ---------- 里程碑裁决 ----------

    def adjudicate(self, code: str, *, persist: bool = False) -> list[dict[str, Any]]:
        """冲突里程碑以计划基线裁决；未完成里程碑以计划基线为准。"""
        project = _find_project(code)
        if project is None:
            return []
        reported = _parse_reported(str(project.get("里程碑填报", "") or ""))
        milestones = []
        for row in store.rows(TABLE_BASELINE):
            if row.get("工程编号") != code:
                continue
            name = str(row.get("里程碑", ""))
            filled = reported.get(name, "")
            baseline = str(row.get("基线日期", ""))
            if filled and filled != baseline:
                verdict = "填报与基线冲突，以计划基线裁决"
            elif not filled:
                verdict = "未完成，按计划基线执行"
            else:
                verdict = "与基线一致"
            milestones.append({
                "里程碑": name,
                "基线日期": baseline,
                "填报日期": filled,
                "裁决日期": baseline,
                "结论": verdict,
            })
        if persist:
            project["里程碑裁决"] = "；".join(
                f"{item['里程碑']}→{item['裁决日期']}" for item in milestones
            ) or "无里程碑基线"
            self._log("启动检查", "ok", f"{code} 里程碑裁决：{project['里程碑裁决']}")
        return milestones

    # ---------- 存量工程迁移回填 ----------

    def migrate_legacy(self) -> dict[str, Any]:
        """存量工程按存档资料迁移回填；已批准与历史工程按存档资料保留，不覆盖。"""
        migrated: list[str] = []
        preserved: list[str] = []
        untouched: list[str] = []
        for project in store.rows(MODULE_PROJECT):
            code = str(project.get("工程编号", ""))
            if str(project.get("status", "")) in APPROVED_STATUSES:
                preserved.append(code)
                continue
            archives = {str(row.get("资料项", "")): str(row.get("资料值", ""))
                        for row in store.rows(TABLE_ARCHIVE) if row.get("工程编号") == code}
            filled = [field for field in REQUIRED_PROJECT_FIELDS
                      if not str(project.get(field) or "").strip() and archives.get(field)]
            if not filled:
                untouched.append(code)
                continue
            for field in filled:
                project[field] = archives[field]
            project["迁移回填"] = "已回填：" + "、".join(filled)
            migrated.append(code)
        self._log("迁移回填", "ok",
                  f"存量工程迁移回填 {len(migrated)} 项（{'、'.join(migrated) or '无'}），"
                  f"已批准与历史工程保留 {len(preserved)} 项（{'、'.join(preserved) or '无'}）")
        return {"migrated": migrated, "preserved": preserved, "untouched": untouched}

    # ---------- 跨运行空间就绪检查 ----------

    def _channel_state(self, name: str) -> str:
        for channel in store.rows(TABLE_CHANNEL):
            if channel.get("通道") == name:
                return str(channel.get("状态", ""))
        return "正常"

    def list_channels(self) -> list[dict[str, Any]]:
        return store.rows(TABLE_CHANNEL)

    def set_channel(self, channel_id: int, state: str) -> tuple[dict[str, Any] | None, str]:
        channel = store.find(TABLE_CHANNEL, channel_id)
        if channel is None:
            return None, f"外部服务通道 {channel_id} 不存在"
        if state not in CHANNEL_STATES:
            return None, f"通道状态「{state}」不在允许范围：{'、'.join(CHANNEL_STATES)}"
        channel["状态"] = state
        self._log("就绪检查", "ok", f"外部服务通道 {channel['通道']} 切换为 {state}")
        return channel, f"通道 {channel['通道']} 已切换为 {state}"

    def _probe_project(self, code: str) -> dict[str, Any]:
        """相符性探测：工程台账 × 施工路段 × 材料计划，逐类给出处置路径。"""
        project = _find_project(code)
        if project is None:
            return {"工程编号": code, "结果": "未就绪", "类别": "no_project",
                    "处置": REMEDIES["no_project"], "明细": "工程台账无此工程"}
        section = _find_section(str(project.get("施工路段", "") or ""))
        if section is None or not str(section.get("起止桩号", "") or "").strip():
            return {"工程编号": code, "结果": "未就绪", "类别": "boundary_missing",
                    "处置": REMEDIES["boundary_missing"],
                    "明细": "施工路段未登记或起止桩号缺失"}
        for channel in store.rows(TABLE_CHANNEL):
            state = str(channel.get("状态", ""))
            if state == "超时":
                return {"工程编号": code, "结果": "未就绪", "类别": "timeout",
                        "处置": REMEDIES["timeout"], "明细": f"{channel.get('通道', '')}超时"}
            if state == "不可达":
                return {"工程编号": code, "结果": "未就绪", "类别": "unreachable",
                        "处置": REMEDIES["unreachable"], "明细": f"{channel.get('通道', '')}不可达"}
        materials = store.rows(MODULE_MATERIAL)
        if not materials:
            return {"工程编号": code, "结果": "未就绪", "类别": "empty",
                    "处置": REMEDIES["empty"], "明细": "材料计划为空"}
        usable = [row for row in materials
                  if row.get("存放地点") == project.get("施工路段") and row.get("status") == "在库"]
        if not usable:
            return {"工程编号": code, "结果": "未就绪", "类别": "missing",
                    "处置": REMEDIES["missing"], "明细": "施工路段无在库材料，材料计划未落实"}
        return {"工程编号": code, "结果": "就绪", "类别": "ok", "处置": "",
                "明细": "工程台账、施工路段、材料计划三方相符"}

    def _write_back_probe(self, code: str, conclusion: dict[str, Any]) -> None:
        """探测结论写至工程汇总页、路段清单和运行日志面板；不覆盖已批准工程。"""
        project = _find_project(code)
        if project is not None:
            if str(project.get("status", "")) in APPROVED_STATUSES:
                self._log("就绪检查", "ok", f"{code} 已批准，探测结论不覆盖工程台账")
            else:
                project["就绪检查"] = f"{conclusion['结果']}：{conclusion['明细']}"
            section = _find_section(str(project.get("施工路段", "") or ""))
            if section is not None:
                section["就绪结论"] = f"{code} {conclusion['结果']}"
        self._log("就绪检查", conclusion["类别"],
                  f"{code} 相符性探测{conclusion['结果']}：{conclusion['明细']}")

    def _batch(self, batch_id: str) -> dict[str, Any] | None:
        for row in store.rows(TABLE_BATCH):
            if row.get("批次号") == batch_id:
                return row
        return None

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        return self._batch(batch_id)

    def run_probe_batch(self, batch_id: str, codes: list[str] | None = None
                        ) -> tuple[dict[str, Any], str]:
        """探测任务按批次幂等：已完成的批次直接返回既有结论，不重复写回。"""
        batch = self._batch(batch_id)
        if batch is None:
            batch = {
                "id": _next_id(TABLE_BATCH),
                "批次号": batch_id,
                "项目": codes or [str(row.get("工程编号", "")) for row in store.rows(MODULE_PROJECT)],
                "已完成": {},
                "状态": "进行中",
            }
            store.rows(TABLE_BATCH).append(batch)
        if batch["状态"] == "已完成":
            return batch, f"批次 {batch_id} 已完成，幂等返回既有结论"
        for code in batch["项目"]:
            if code in batch["已完成"]:
                continue  # 复位后继续未完成项，已完成项不重跑
            conclusion = self._probe_project(code)
            batch["已完成"][code] = conclusion
            self._write_back_probe(code, conclusion)
        batch["状态"] = "已完成"
        ready = sum(1 for item in batch["已完成"].values() if item["结果"] == "就绪")
        self._log("就绪检查", "ok",
                  f"批次 {batch_id} 探测完成：{ready}/{len(batch['项目'])} 项就绪")
        return batch, f"批次 {batch_id} 探测完成，{ready}/{len(batch['项目'])} 项就绪"

    def reset_probe_batch(self, batch_id: str) -> tuple[dict[str, Any] | None, str]:
        """复位批次：保留已完成项结论，状态回到进行中，继续未完成项。"""
        batch = self._batch(batch_id)
        if batch is None:
            return None, f"探测批次 {batch_id} 不存在"
        batch["状态"] = "进行中"
        done = len(batch["已完成"])
        self._log("就绪检查", "ok", f"批次 {batch_id} 已复位，保留 {done} 项已完成结论，继续未完成项")
        return batch, f"批次 {batch_id} 已复位，{done} 项已完成结论保留，继续未完成项"
