"""养护资金业务规则：状态流转、额度核定与筛选口径都收在这里。

额度只在「提交审批」批复当次核定：以批复金额重算已用金额与剩余额度，
核定结果落库，列表、详情、导出读的是同一份数据；同一笔资金重复提交
审批只生效一次。批复金额或已用金额无法核算成数字时，保留原额度不动，
并把原因通过返回消息说明。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "fund"
REQUIRED_FIELDS = ["资金编号", "费用类别", "项目名称"]
MONEY_FIELDS = ["批复金额", "已用金额", "剩余额度"]
STATUS_ORDER = ["待审批", "已批复", "执行中", "已超支"]
ACTION_RULES = {"提交审批": "已批复", "确认批复": "执行中", "标记超支": "已超支"}
# 没有“反向动作”，但已核定的超支标记要在后续流转里保留，不能被通用流转逻辑抹掉。
NEGATIVE_ACTIONS = []
APPROVED_STATUSES = {"已批复", "执行中", "已超支"}


def parse_amount(value: Any) -> float | None:
    """把金额字段折算成数值；空值视为 0，无法解析时返回 None。允许负数（超支剩余额度）。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0.0
    if isinstance(value, bool):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def to_amount(value: Any) -> float | None:
    """登记/批复录入口径：空值视为 0，无法解析或为负数时返回 None。"""
    amount = parse_amount(value)
    if amount is None:
        return None
    return amount if amount >= 0 else None


def is_overspent(entry: dict[str, Any]) -> bool:
    """剩余额度为负数即超支；字段缺失或不可核算时不算超支。"""
    raw = entry.get("剩余额度")
    if raw is None:
        return False
    remaining = parse_amount(raw)
    return remaining is not None and remaining < 0


class FundService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._filter_rows(keyword=keyword, status=status)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def summarize(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """按给定行集合汇总金额；与列表逐行合计同源，保证卡片与列表对得上。"""
        approved_total = 0.0
        used_total = 0.0
        remaining_total = 0.0
        overspent = 0
        for row in rows:
            approved_raw = row.get("批复金额")
            used_raw = row.get("已用金额")
            remaining_raw = row.get("剩余额度")
            # 待审批记录尚未核定，金额为 None 时不计入合计，不能当成 0。
            if approved_raw is not None:
                approved = parse_amount(approved_raw)
                if approved is not None:
                    approved_total += approved
            if used_raw is not None:
                used = parse_amount(used_raw)
                if used is not None:
                    used_total += used
            if remaining_raw is not None:
                remaining = parse_amount(remaining_raw)
                if remaining is not None:
                    remaining_total += remaining
            if row.get("abnormal") is True or is_overspent(row):
                overspent += 1
        return {
            "批复总额": round(approved_total, 2),
            "已用金额合计": round(used_total, 2),
            "剩余额度合计": round(remaining_total, 2),
            "超支项目": overspent,
        }

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is not None:
            self._sync_status_field(entry)
        return entry

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 登记时只收登记项；金额字段能转成数字才带上，缺失的留空，等批复时再核定，
        # 避免待审批记录凭空出现 0 元剩余额度，污染列表合计。
        for field in MONEY_FIELDS:
            raw = values.get(field)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                continue
            amount = to_amount(raw)
            if amount is not None:
                entry[field] = amount
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        self._sync_status_field(entry)
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"资金记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于养护资金可执行范围"
        values = values or {}
        if action == "提交审批":
            return self._submit_for_approval(entry, values)
        return self._advance_status(entry, action)

    # ---- 内部规则 ----

    def _submit_for_approval(
        self, entry: dict[str, Any], values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        # 幂等：已有批复记录（历史金额）不再重算，重复审批只生效一次。
        if entry.get("status") in APPROVED_STATUSES or entry.get("quota_locked"):
            return entry, "该笔资金已批复，额度已核定，重复审批不生效"

        # 批复金额允许在提交动作里给出；没给就沿用登记时的申报金额。
        approved_raw = values.get("批复金额", entry.get("批复金额"))
        used_raw = values.get("已用金额", entry.get("已用金额"))
        reasons: list[str] = []
        # 批复金额是核定基准，缺失或不可核算都不能默认成 0。
        if approved_raw is None or (isinstance(approved_raw, str) and not str(approved_raw).strip()):
            approved = None
        else:
            approved = to_amount(approved_raw)
        if approved is None:
            reasons.append("批复金额缺失或不是有效数字")
        # 已用金额没登记视为尚未支出（按 0 计）；填了但不是数字才算不出来。
        if used_raw is None or (isinstance(used_raw, str) and not str(used_raw).strip()):
            used = 0.0
        else:
            used = to_amount(used_raw)
        if used is None:
            reasons.append("已用金额缺失或不是有效数字")
        if reasons:
            # 额度算不出来：状态与原额度都保持不变，并说明缺的是什么。
            return None, "额度无法核定，已保留原额度：" + "、".join(reasons)

        remaining = round(approved - used, 2)
        entry["批复金额"] = approved
        entry["已用金额"] = used
        entry["剩余额度"] = remaining
        entry["quota_locked"] = True
        entry["status"] = "已批复"
        entry["pending"] = True
        entry["abnormal"] = remaining < 0
        self._sync_status_field(entry)
        if remaining < 0:
            return entry, f"资金记录已批复，剩余额度 {remaining:.2f}，已超支，请单独核对"
        return entry, f"资金记录已批复，剩余额度核定为 {remaining:.2f}"

    def _advance_status(self, entry: dict[str, Any], action: str) -> tuple[dict[str, Any], str]:
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return entry, f"目标状态「{target}」不在允许的状态序列里"
        was_overspent = entry.get("abnormal") is True or is_overspent(entry)
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        # 超支是额度核定结果，后续「确认批复/标记超支」流转不能把标记冲掉。
        if action == "标记超支":
            entry["abnormal"] = True
        elif was_overspent:
            entry["abnormal"] = True
        self._sync_status_field(entry)
        return entry, f"资金记录已{action}"

    def _filter_rows(
        self, *, keyword: str | None, status: str | None
    ) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("资金编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        for row in rows:
            self._sync_status_field(row)
        return rows

    def _sync_status_field(self, entry: dict[str, Any]) -> None:
        """列表用「资金状态」列展示状态，写入时与内部 status 保持一致。

        同时保证金额三键恒存在（未核定为 None），详情/导出/逐行合计读取时
        不会因为缺键而口径不一致。
        """
        entry["资金状态"] = entry.get("status")
        for field in MONEY_FIELDS:
            entry.setdefault(field, None)
