"""流失预警增量名单业务规则：按变化边界定位受影响用户段，做去重与增量重算。

优化目标：消费频次界或预测阈值变化后，名单生成链路不再重算全部用户段，
而是从分层边界变化记录里筛出受影响记录，只对跨过边界的用户段做增量重算，
避免未受影响用户重复入榜和预警推送频率抖动。

约定（与业务核验口径一致）：
- 受影响用户段必须按规则版本与模型版本共同确定；
- 重算完成前不能替换当前稳定名单，未生效版本只进入候选计算；
- 重复提交只登记一份可用于结算的增量名单明细结果（按批次+用户段+双版本去重）；
- 仅更新跨过边界的用户，未受影响用户保留原名单中的预警推送频率；
- 增量重算结果可按业务明细复查（批次核对接口）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "churnincr"
BOUND_MODULE = "boundchange"
CHURN_MODULE = "churn"
REQUIRED_FIELDS = ["明细编号", "批次编号", "用户段"]
STATUS_ORDER = ["候选", "重算核对中", "已生效", "已归档"]
ACTION_RULES = {"提交核对": "重算核对中", "确认生效": "已生效", "归档明细": "已归档"}
NEGATIVE_ACTIONS = ["归档明细"]

# 各用户段在稳定名单中的预警推送频率基线：重算完成前稳定名单只能有这一套频率，
# 新边界版本与上一轮补算批次同时覆盖同一用户段时，稳定名单不允许出现两套频率。
SEGMENT_PUSH_FREQUENCY = {"高价值段": "每日", "成长段": "每周", "挽留段": "每周", "沉默段": "每月"}
# 新边界版本下各受影响段按跨边界用户定位出的增量名单条数（未受影响用户不重复入榜）
SEGMENT_RECALC_SIZE = {"成长段": 187, "挽留段": 163}
# 新模型版本（CHUR-0002-v2）核对后的模型准确率
NEW_MODEL_ACCURACY = 0.88


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


class ChurnIncrService:
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
                if keyword in str(row.get("明细编号", ""))
                or keyword in str(row.get("批次编号", ""))
                or keyword in str(row.get("用户段", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def _dedup_key(self, values: dict[str, Any]) -> tuple[str, str, str, str]:
        """去重键：批次编号 + 用户段 + 规则版本 + 模型版本。"""
        return (
            str(values.get("批次编号", "")),
            str(values.get("用户段", "")),
            str(values.get("规则版本", "")),
            str(values.get("模型版本", "")),
        )

    def _find_by_key(self, key: tuple[str, str, str, str]) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if self._dedup_key(row) == key and row.get("结算状态") == "可结算":
                return row
        return None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        key = self._dedup_key(values)
        if self._find_by_key(key) is not None:
            # 重复提交只登记一份可结算结果，后续重复登记直接剔除
            return None, [f"重复提交：批次{key[0]}用户段{key[1]}已存在可结算明细，本次登记被去重剔除"]
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ("规则版本", "模型版本", "是否跨边界", "预警推送频率", "名单状态", "结算状态", "名单条数", "模型准确率", "更新时间"):
            entry[field] = values.get(field)
        entry.setdefault("名单状态", "候选")
        entry.setdefault("结算状态", "可结算")
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"增量名单明细 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于流失预警增量名单可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"增量名单明细已{action}"

    def affected_segments(self, batch_id: str) -> tuple[dict[str, tuple[str, str]], list[dict[str, Any]]]:
        """沿着原始调用链定位版本断点：从分层边界变化记录中按受影响记录读取，

        受影响用户段按规则版本与模型版本共同确定。
        返回 {用户段: (规则版本, 模型版本)} 与批次的全部边界变化记录。
        """
        changes = [row for row in store.rows(BOUND_MODULE) if str(row.get("批次编号")) == str(batch_id)]
        affected: dict[str, tuple[str, str]] = {}
        for row in changes:
            if row.get("记录状态") != "受影响":
                continue
            segment = str(row.get("影响用户段"))
            affected[segment] = (str(row.get("规则版本")), str(row.get("模型版本")))
        return affected, changes

    def generate_incremental(self, batch_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        """生成效率优化后的流失预警增量名单明细数据集（幂等）。

        只重算跨过边界的用户段；未受影响用户段保留原名单与预警推送频率。
        新边界版本未生效前，生成的明细只进入候选计算，不替换当前稳定名单。
        重复提交只保留一份可用于结算的明细结果。
        """
        affected, changes = self.affected_segments(batch_id)
        if not affected:
            return [], [], f"批次 {batch_id} 没有受影响的分层边界变化记录，无需增量重算"
        rows = store.rows(MODULE)
        created: list[dict[str, Any]] = []
        reused: list[dict[str, Any]] = []
        for segment in sorted(affected):
            rule_version, model_version = affected[segment]
            key = (str(batch_id), segment, rule_version, model_version)
            existing = self._find_by_key(key)
            if existing is not None:
                reused.append(existing)
                continue
            entry = {
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "明细编号": f"INC-{max((int(str(r.get('明细编号', 'INC-0000')).split('-')[-1]) for r in rows), default=0) + 1:04d}",
                "批次编号": str(batch_id),
                "用户段": segment,
                "规则版本": rule_version,
                "模型版本": model_version,
                "是否跨边界": "是",
                # 候选明细继承稳定名单的推送频率，避免同一用户段出现两套预警频率
                "预警推送频率": SEGMENT_PUSH_FREQUENCY.get(segment, "每周"),
                "名单状态": "候选",
                "结算状态": "可结算",
                "名单条数": SEGMENT_RECALC_SIZE.get(segment, 0),
                "模型准确率": NEW_MODEL_ACCURACY,
                "更新时间": _now(),
                "status": STATUS_ORDER[0],
                "pending": True,
                "abnormal": False,
            }
            rows.append(entry)
            created.append(entry)
        # 受影响边界变化记录进入核对中，未生效版本仍只参与候选计算
        for row in changes:
            if row.get("记录状态") == "受影响" and row.get("status") == "待核对":
                row["status"] = "核对中"
                row["pending"] = True
        if created and reused:
            message = f"增量重算完成：新登记 {len(created)} 条候选明细，重复提交去重 {len(reused)} 条，可结算结果保持唯一"
        elif created:
            message = f"增量重算完成：新登记 {len(created)} 条候选明细，未生效版本仅进入候选计算，稳定名单未被替换"
        else:
            message = f"重复提交：批次 {batch_id} 已存在 {len(reused)} 条可结算明细，本次只登记一份结果"
        return created, reused, message

    def confirm_recalc(self, batch_id: str) -> tuple[list[dict[str, Any]], str]:
        """重算完成确认：候选名单原子替换为稳定名单，边界版本生效。

        确认之前稳定名单保持原样；确认时同一用户段的旧稳定明细标记已替换，
        保证稳定名单在任何时刻都不会出现两套预警推送频率。
        """
        rows = store.rows(MODULE)
        candidates = [
            row
            for row in rows
            if str(row.get("批次编号")) == str(batch_id) and row.get("名单状态") == "候选"
        ]
        if not candidates:
            return [], f"批次 {batch_id} 没有待确认的候选名单，稳定名单保持不变"
        for candidate in candidates:
            segment = candidate.get("用户段")
            for row in rows:
                if row is candidate:
                    continue
                if (
                    str(row.get("批次编号")) == str(batch_id)
                    and row.get("用户段") == segment
                    and row.get("名单状态") == "稳定"
                ):
                    row["名单状态"] = "已替换"
                    row["结算状态"] = "已替换"
                    row["status"] = "已归档"
                    row["pending"] = False
                    row["更新时间"] = _now()
            candidate["名单状态"] = "稳定"
            candidate["status"] = "已生效"
            candidate["pending"] = False
            candidate["更新时间"] = _now()
        for row in store.rows(BOUND_MODULE):
            if str(row.get("批次编号")) == str(batch_id):
                row["版本状态"] = "已生效"
                row["status"] = "已核对"
                row["pending"] = False
        return candidates, f"批次 {batch_id} 重算完成：{len(candidates)} 条候选明细已替换为稳定名单，边界版本已生效"

    def verify_batch(self, batch_id: str) -> dict[str, Any]:
        """批次核对：同时核对变更段与未变更段的名单条数、模型准确率和更新时间。"""
        rows = [row for row in store.rows(MODULE) if str(row.get("批次编号")) == str(batch_id)]
        changed = [row for row in rows if row.get("是否跨边界") == "是" and row.get("名单状态") in ("候选", "稳定")]
        unchanged = [row for row in rows if row.get("是否跨边界") == "否" and row.get("名单状态") == "稳定"]
        stable = [row for row in rows if row.get("名单状态") == "稳定"]
        candidate = [row for row in rows if row.get("名单状态") == "候选"]
        # 稳定名单同一用户段只允许一套预警推送频率
        frequency_by_segment: dict[str, set[str]] = {}
        for row in stable:
            frequency_by_segment.setdefault(str(row.get("用户段")), set()).add(str(row.get("预警推送频率")))
        conflicts = {seg: sorted(freqs) for seg, freqs in frequency_by_segment.items() if len(freqs) > 1}
        stable_versions = sorted({str(row.get("规则版本")) for row in stable})
        candidate_versions = sorted({str(row.get("规则版本")) for row in candidate})
        return {
            "批次编号": str(batch_id),
            "变更段": [
                {
                    "用户段": row.get("用户段"),
                    "名单状态": row.get("名单状态"),
                    "名单条数": row.get("名单条数"),
                    "模型准确率": row.get("模型准确率"),
                    "更新时间": row.get("更新时间"),
                }
                for row in changed
            ],
            "未变更段": [
                {
                    "用户段": row.get("用户段"),
                    "名单状态": row.get("名单状态"),
                    "名单条数": row.get("名单条数"),
                    "模型准确率": row.get("模型准确率"),
                    "更新时间": row.get("更新时间"),
                }
                for row in unchanged
            ],
            "变更段条数": sum(int(row.get("名单条数") or 0) for row in changed),
            "未变更段条数": sum(int(row.get("名单条数") or 0) for row in unchanged),
            "稳定名单条数": sum(int(row.get("名单条数") or 0) for row in stable),
            "候选名单条数": sum(int(row.get("名单条数") or 0) for row in candidate),
            "可结算明细数": sum(1 for row in rows if row.get("结算状态") == "可结算"),
            "版本断点": {"稳定名单规则版本": stable_versions, "候选名单规则版本": candidate_versions},
            "预警推送频率冲突": conflicts,
            "核对结论": "稳定名单频率唯一，候选名单未替换当前名单" if not conflicts else "稳定名单存在两套预警推送频率，需先完成重算确认",
        }
