"""RFM分层业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "rfm"
CHURN_MODULE = "churn"
REQUIRED_FIELDS = ["规则编号", "最近消费天界", "消费频次界"]
STATUS_ORDER = ["草稿", "规则评审中", "已启用", "已停用"]
ACTION_RULES = {"提交规则评审": "规则评审中", "启用规则": "已启用", "停用规则": "已停用"}
NEGATIVE_ACTIONS = ["停用规则"]
# 整组提交时的前进路径：逐条核对通过后沿状态序列推进一步，终态记录保持不动
FORWARD_STEP = {"草稿": "规则评审中", "规则评审中": "已启用"}
# RFM分层状态同步到流失预警清单的映射
CHURN_STATUS_SYNC = {"已启用": "已部署", "已停用": "已下线"}


class RfmService:
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
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
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
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def _sync_churn_status(self, entry: dict[str, Any]) -> list[str]:
        """RFM分层状态同步到流失预警清单：规则启用/停用后，同序号预警模型同步部署/下线。"""
        target = CHURN_STATUS_SYNC.get(str(entry.get("status", "")))
        if target is None:
            return []
        serial = str(entry.get("规则编号", "")).rsplit("-", 1)[-1]
        synced: list[str] = []
        for row in store.rows(CHURN_MODULE):
            if str(row.get("模型编号", "")).rsplit("-", 1)[-1] != serial:
                continue
            if row.get("status") == target:
                continue
            row["status"] = target
            row["pending"] = False
            row["abnormal"] = target == "已下线"
            synced.append(str(row.get("模型编号")))
        return synced

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"分层规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于RFM分层可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        synced = self._sync_churn_status(entry)
        message = f"分层规则已{action}"
        if synced:
            message += f"，状态已同步到流失预警清单：{'、'.join(synced)}"
        return entry, message

    def batch_submit(self, entry_ids: list[int]) -> tuple[list[dict[str, Any]], str]:
        """多选记录逐条核对后整组提交：每条记录沿前进路径推进一步并同步流失预警清单。"""
        results: list[dict[str, Any]] = []
        for entry_id in entry_ids:
            entry = store.find(MODULE, int(entry_id))
            if entry is None:
                results.append({"id": entry_id, "ok": False, "message": f"分层规则 {entry_id} 不存在或已归档"})
                continue
            current = str(entry.get("status", ""))
            target = FORWARD_STEP.get(current)
            if target is None:
                results.append({
                    "id": entry_id,
                    "规则编号": entry.get("规则编号"),
                    "ok": True,
                    "status": current,
                    "message": f"已处于{current}，整组提交中保持不动",
                })
                continue
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = False
            synced = self._sync_churn_status(entry)
            message = f"核对通过，{current}→{target}"
            if synced:
                message += f"，状态已同步到流失预警清单：{'、'.join(synced)}"
            results.append({
                "id": entry_id,
                "规则编号": entry.get("规则编号"),
                "ok": True,
                "status": target,
                "message": message,
            })
        advanced = sum(1 for item in results if item.get("ok") and "→" in str(item.get("message", "")))
        return results, f"整组提交完成：共 {len(entry_ids)} 条记录，{advanced} 条核对推进，状态同步到流失预警清单"
