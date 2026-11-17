"""分层边界变化记录业务规则：状态流转、字段校验与筛选口径都收在这里。

这些记录用于效率改进：消费频次界或预测阈值变化后，名单生成链路不再全量重算，
而是按这里的受影响记录定位需要增量重算的用户段。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "boundchange"
REQUIRED_FIELDS = ["变化编号", "批次编号", "影响用户段"]
STATUS_ORDER = ["待核对", "核对中", "已核对", "已归档"]
ACTION_RULES = {"开始核对": "核对中", "核对通过": "已核对", "归档记录": "已归档"}
NEGATIVE_ACTIONS = ["归档记录"]


class BoundchangeService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("变化编号", "")) or keyword in str(row.get("批次编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ("规则版本", "模型版本", "变化边界", "旧边界值", "新边界值", "记录状态", "版本状态"):
            entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"边界变化记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于分层边界变化记录可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"边界变化记录已{action}"
