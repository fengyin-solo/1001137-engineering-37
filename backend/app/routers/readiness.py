"""启动门禁接口：四级状态机门禁、跨运行空间就绪检查、批次探针与处置路径。"""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.schemas import EntryPayload
from app.services.readiness import GateViolation, readiness_service

router = APIRouter(prefix="/api/readiness", tags=["启动门禁"])


def _violation(exc: GateViolation) -> JSONResponse:
    # 状态机违规（跳级/倒序/覆盖批准件）统一 409 阻断，而不是 200 静默放行
    return JSONResponse(status_code=409, content={"ok": False, "message": str(exc)})


@router.post("/bootstrap")
def bootstrap() -> dict[str, object]:
    """引导：迁移存档资料、建立门禁台账并执行首检；重复调用幂等。"""
    return readiness_service.bootstrap()


@router.get("/summary")
def summary() -> dict[str, object]:
    """工程汇总页：门禁总量、场景分布、外部服务/数据源状态与待办量。"""
    return readiness_service.summary()


@router.get("/gates")
def list_gates(scenario: str | None = Query(default=None, description="按阻断场景过滤")) -> dict[str, object]:
    """工程台账视角的门禁清单（检查结论回写工程台账）。"""
    return {"items": readiness_service.list_gates(scenario=scenario)}


@router.get("/gates/{code}")
def get_gate(code: str) -> dict[str, object]:
    gate = readiness_service.get_gate(code)
    if gate is None:
        return JSONResponse(status_code=404, content={"ok": False, "message": f"工程 {code} 不在门禁台账中"})
    return gate


@router.post("/gates/{code}/advance")
def advance_gate(code: str, payload: EntryPayload) -> JSONResponse:
    """核验并推进指定阶段；跳级、倒序、重复、覆盖批准件一律 409 阻断。"""
    stage = str(payload.values.get("stage") or "").strip()
    try:
        return JSONResponse(content=readiness_service.advance(code, stage))
    except GateViolation as exc:
        return _violation(exc)


@router.post("/gates/{code}/resolve")
def resolve_gate(code: str, payload: EntryPayload) -> JSONResponse:
    """按阻断场景分别处置（边界/材料/前置件/配置/超时/不可达各有独立路径）。"""
    kind = str(payload.values.get("kind") or "").strip()
    try:
        return JSONResponse(content=readiness_service.resolve(code, kind))
    except GateViolation as exc:
        return _violation(exc)


@router.get("/logs")
def list_logs(
    code: str | None = Query(default=None, description="按工程编号过滤运行日志"),
    limit: int = 100,
) -> dict[str, object]:
    """运行日志面板。"""
    return {"items": readiness_service.logs(code=code, limit=limit)}


@router.get("/todos")
def list_todos(
    road_code: str | None = Query(default=None, description="按路段编号过滤待办"),
    open_only: bool = Query(default=False, description="只看待办"),
) -> dict[str, object]:
    """路段待办面板：检查结论同步写入施工路段清单。"""
    return {"items": readiness_service.todos(road_code=road_code, open_only=open_only)}


@router.post("/probes/batch")
def create_batch() -> dict[str, object]:
    """按批次创建探测任务：幂等，有进行中批次时直接返回；已批准工程不入批。"""
    return readiness_service.create_batch()


@router.post("/probes/batch/run")
def run_batch() -> JSONResponse:
    """执行批次：完成项不重跑，受阻项保留为未完成项（复位后继续）。"""
    try:
        return JSONResponse(content=readiness_service.run_batch())
    except GateViolation as exc:
        return _violation(exc)


@router.post("/probes/reset")
def reset_probes() -> JSONResponse:
    """复位批次：未完成项回到待执行，已完成项保留，不覆盖已批准工程。"""
    try:
        return JSONResponse(content=readiness_service.reset_probes())
    except GateViolation as exc:
        return _violation(exc)


@router.post("/faults")
def inject_fault(payload: EntryPayload) -> JSONResponse:
    """故障注入（演示超时/不可达分流）：target=服务或数据源名，status=ok/timeout/unreachable。"""
    target = str(payload.values.get("target") or "").strip()
    status = str(payload.values.get("status") or "").strip()
    try:
        return JSONResponse(content=readiness_service.inject_fault(target, status))
    except GateViolation as exc:
        return _violation(exc)
