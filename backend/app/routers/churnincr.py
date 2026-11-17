"""流失预警增量名单接口：按变化边界定位受影响用户段，生成效率优化后的增量名单明细数据集。

除了常规列表与动作，还提供：
- 受影响用户段定位（按规则版本与模型版本共同确定）
- 增量名单生成（幂等，重复提交只登记一份可结算结果）
- 重算完成确认（候选名单原子替换稳定名单）
- 批次核对（变更段与未变更段的名单条数、模型准确率、更新时间）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.churnincr import ChurnIncrService

router = APIRouter(prefix="/api/churnincr", tags=["流失预警增量名单"])

service = ChurnIncrService()

LIST_FIELDS = ["明细编号", "批次编号", "用户段", "规则版本", "模型版本", "是否跨边界", "预警推送频率", "名单状态", "结算状态", "名单条数", "模型准确率", "更新时间"]
STATUSES = ["候选", "重算核对中", "已生效", "已归档"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按明细编号、批次编号或用户段检索"),
    status: str | None = Query(default=None, description="候选、重算核对中、已生效、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按编号与状态过滤流失预警增量名单；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/affected/{batch_id}", response_model=dict)
def affected_segments(batch_id: str) -> dict[str, Any]:
    """沿着原始调用链定位版本断点：按批次读取受影响边界变化记录，返回按规则版本与模型版本共同确定的受影响用户段。"""
    affected, changes = service.affected_segments(batch_id)
    return {
        "批次编号": batch_id,
        "受影响用户段": [
            {"用户段": segment, "规则版本": versions[0], "模型版本": versions[1]}
            for segment, versions in sorted(affected.items())
        ],
        "边界变化记录数": len(changes),
        "未生效版本仅候选": all(row.get("版本状态") != "已生效" for row in changes),
    }


@router.get("/verify/{batch_id}", response_model=dict)
def verify_batch(batch_id: str) -> dict[str, Any]:
    """批次核对：同时核对变更段与未变更段的名单条数、模型准确率和更新时间。"""
    return service.verify_batch(batch_id)


@router.post("/generate", response_model=dict)
def generate_incremental(payload: EntryPayload) -> dict[str, Any]:
    """生成效率优化后的流失预警增量名单明细数据集。

    仅更新跨过边界的用户段，未受影响用户保留原名单中的预警推送频率；
    未生效版本只进入候选计算，不替换当前稳定名单；重复提交只登记一份可结算结果。
    """
    batch_id = str(payload.values.get("批次编号") or "").strip()
    if not batch_id:
        raise HTTPException(status_code=400, detail="缺少批次编号，无法定位批次核对对象")
    created, reused, message = service.generate_incremental(batch_id)
    return {
        "ok": True,
        "message": message,
        "批次编号": batch_id,
        "新登记": created,
        "重复去重": reused,
        "可结算明细数": len(created) + len(reused),
    }


@router.post("/confirm", response_model=dict)
def confirm_recalc(payload: EntryPayload) -> dict[str, Any]:
    """重算完成确认：候选名单原子替换为稳定名单，边界版本生效；确认前稳定名单不被替换。"""
    batch_id = str(payload.values.get("批次编号") or "").strip()
    if not batch_id:
        raise HTTPException(status_code=400, detail="缺少批次编号，无法确认重算")
    confirmed, message = service.confirm_recalc(batch_id)
    return {"ok": bool(confirmed), "message": message, "批次编号": batch_id, "生效明细": confirmed}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条增量名单明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"增量名单明细 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条增量名单明细，重复提交会被去重剔除并说明原因。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"{'、'.join(missing)}")
    return ActionResult(ok=True, message="增量名单明细已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条增量名单明细执行提交核对、确认生效、归档明细；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出流失预警增量名单清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "churnincr", "total": total, "items": items}
