"""工程启动检查接口：状态机门禁推进、跨运行空间就绪探测、迁移回填与运行日志面板。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.startup import STAGE_LABELS, STAGES, StartupService

router = APIRouter(prefix="/api/startup", tags=["工程启动检查"])

service = StartupService()


@router.get("/stages")
def list_stages() -> dict[str, Any]:
    """状态机阶段序列：只能按返回顺序逐级推进。"""
    return {"stages": [{"key": key, "label": STAGE_LABELS[key]} for key in STAGES]}


@router.get("/gates")
def list_gates() -> dict[str, Any]:
    """工程汇总页：每个工程的门禁阶段与最近检查结论。"""
    return {"items": service.gates_overview()}


@router.get("/gates/{code}")
def get_gate(code: str) -> dict[str, Any]:
    """单个工程的门禁明细；无工程时给出可读说明。"""
    view = service.gate_view(code)
    if view is None:
        raise HTTPException(status_code=404, detail=f"养护工程 {code} 未在工程台账登记")
    return view


@router.post("/gates/{code}/advance", response_model=ActionResult)
def advance_gate(code: str, payload: EntryPayload) -> ActionResult:
    """推进门禁到目标阶段；跳级、倒序、配置前置件失败都会被拦下并说明原因。"""
    target = str(payload.values.get("stage") or "").strip()
    view, category, message = service.advance(code, target)
    entry = {"gate": view, "类别": category} if view is not None else None
    return ActionResult(ok=category in ("ok",), message=message, entry=entry)


@router.post("/gates/{code}/reset", response_model=ActionResult)
def reset_gate(code: str) -> ActionResult:
    """复位门禁：解除阻断、保留已通过阶段，继续未完成项。"""
    view, message = service.reset_gate(code)
    if view is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry={"gate": view})


@router.get("/milestones/{code}")
def get_milestones(code: str) -> dict[str, Any]:
    """里程碑裁决预览：冲突以计划基线裁决，未完成以计划基线为准；只读不写回。"""
    if service.gate_view(code) is None:
        raise HTTPException(status_code=404, detail=f"养护工程 {code} 未在工程台账登记")
    return {"工程编号": code, "items": service.adjudicate(code)}


@router.post("/migrate", response_model=ActionResult)
def migrate_legacy() -> ActionResult:
    """存量工程按存档资料迁移回填；已批准与历史工程保留不覆盖。"""
    result = service.migrate_legacy()
    return ActionResult(
        ok=True,
        message=f"迁移回填 {len(result['migrated'])} 项，保留已批准与历史工程 {len(result['preserved'])} 项",
        entry=result,
    )


@router.get("/channels")
def list_channels() -> dict[str, Any]:
    """外部服务通道清单：正常、超时、不可达三种状态。"""
    return {"items": service.list_channels()}


@router.post("/channels/{channel_id}", response_model=ActionResult)
def set_channel(channel_id: int, payload: EntryPayload) -> ActionResult:
    """切换外部服务通道状态，用于演练超时与不可达的独立处置路径。"""
    state = str(payload.values.get("状态") or "").strip()
    channel, message = service.set_channel(channel_id, state)
    if channel is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=channel)


@router.post("/probe", response_model=ActionResult)
def run_probe(payload: EntryPayload) -> ActionResult:
    """运行跨运行空间就绪探测批次；批次幂等，同批次号重复提交不重复写回。"""
    batch_id = str(payload.values.get("批次号") or "").strip()
    if not batch_id:
        return ActionResult(ok=False, message="缺少批次号，无法建立探测批次")
    codes = payload.values.get("项目")
    batch, message = service.run_probe_batch(
        batch_id, [str(code) for code in codes] if isinstance(codes, list) else None
    )
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/probe/{batch_id}")
def get_probe(batch_id: str) -> dict[str, Any]:
    """查询探测批次状态与各项结论。"""
    batch = service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"探测批次 {batch_id} 不存在")
    return batch


@router.post("/probe/{batch_id}/reset", response_model=ActionResult)
def reset_probe(batch_id: str) -> ActionResult:
    """复位批次：保留已完成项结论，继续未完成项。"""
    batch, message = service.reset_probe_batch(batch_id)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/logs", response_model=PageResult[dict])
def list_logs(
    page: int = 1,
    size: int = Query(default=50, description="运行日志面板每页条数"),
) -> PageResult[dict]:
    """运行日志面板：启动检查、就绪探测、迁移回填的结论都写在这里。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_logs(page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)
