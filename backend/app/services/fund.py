"""养护资金业务规则：状态流转、字段校验与筛选口径都收在这里。

额度口径（唯一的计算入口是本模块，路由层不参与）：
- 提交审批：按本次批复金额与已用金额重算「剩余额度 = 批复金额 − 已用金额」，
  已用为负即判定超支，状态落到「已超支」并单独标出；否则落到「已批复」。
- 同一笔资金只允许审批一次：再次提交会被拦下，已固化的批复/已用/剩余额度
  以及历史记录一律不动。
- 批复金额或已用金额算不成有效数字时，不覆盖任何金额字段，保留原来的剩余额度，
  并把原因通过返回信息说明。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.store import store

MODULE = "fund"
REQUIRED_FIELDS = ["资金编号", "费用类别", "项目名称"]
STATUS_ORDER = ["待审批", "已批复", "执行中", "已超支"]
ACTION_RULES = {"提交审批": "已批复", "确认批复": "执行中", "标记超支": "已超支"}
NEGATIVE_ACTIONS = ["标记超支"]

APPROVED_FIELD = "批复金额"
USED_FIELD = "已用金额"
REMAINING_FIELD = "剩余额度"
MONEY_FIELDS = [APPROVED_FIELD, USED_FIELD, REMAINING_FIELD]


def _to_amount(value: Any) -> Decimal | None:
    """把入参解析成非负金额（保留两位小数）；空值、非数字、负数都返回 None。"""
    amount = _to_decimal(value)
    if amount is None or amount < 0:
        return None
    return amount.quantize(Decimal("0.01"))


def _to_decimal(value: Any) -> Decimal | None:
    """把已存储的金额读成数字（允许负数，超支行的剩余额度就是负值）。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    return amount


class FundService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._filtered_rows(keyword=keyword, status=status)
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
        entry["资金状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 新登记的资金还没批复：金额字段留空，等提交审批时一次性固化。
        entry["approved"] = False
        for field in MONEY_FIELDS:
            entry[field] = None
        if values.get("审批人员"):
            entry["审批人员"] = values.get("审批人员")
        rows.append(entry)
        return entry, []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"资金记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于养护资金可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        if action == "提交审批":
            return self._approve(entry, values or {})

        # 确认批复 / 标记超支只做状态流转，金额一律不重算，历史额度原样保留。
        entry["status"] = target
        entry["资金状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"资金记录已{action}"

    def _approve(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """提交审批的幂等落账：重复审批直接拒绝，金额无效则保留原额度并说明原因。"""
        if entry.get("approved"):
            return None, "该笔资金已审批过，重复提交不再生效；批复金额与剩余额度维持原值"

        approved = _to_amount(values.get(APPROVED_FIELD, entry.get(APPROVED_FIELD)))
        if approved is None:
            return None, (
                f"{APPROVED_FIELD}为空或不是有效非负数字，无法重算额度；"
                f"{REMAINING_FIELD}保留原值「{entry.get(REMAINING_FIELD) if entry.get(REMAINING_FIELD) is not None else '未填写'}」，"
                "请补正批复金额后重新提交"
            )

        # 已用金额允许留空，留空按 0 处理。
        used_raw = values.get(USED_FIELD, entry.get(USED_FIELD))
        if used_raw is None or (isinstance(used_raw, str) and not used_raw.strip()):
            used = Decimal("0.00")
        else:
            used = _to_amount(used_raw)
            if used is None:
                return None, (
                    f"{USED_FIELD}不是有效非负数字，无法重算额度；"
                    f"{REMAINING_FIELD}保留原值「{entry.get(REMAINING_FIELD) if entry.get(REMAINING_FIELD) is not None else '未填写'}」，"
                    "请补正已用金额后重新提交"
                )

        remaining = (approved - used).quantize(Decimal("0.01"))
        overspent = remaining < 0

        entry[APPROVED_FIELD] = float(approved)
        entry[USED_FIELD] = float(used)
        entry[REMAINING_FIELD] = float(remaining)
        entry["approved"] = True
        entry["status"] = STATUS_ORDER[-1] if overspent else "已批复"
        entry["资金状态"] = entry["status"]
        entry["pending"] = not overspent
        entry["abnormal"] = overspent

        approver = values.get("审批人员")
        if approver:
            entry["审批人员"] = approver

        if overspent:
            return entry, (
                f"资金记录已提交审批并标记为超支：{APPROVED_FIELD}{approved}，"
                f"{USED_FIELD}{used}，{REMAINING_FIELD}{remaining}"
            )
        return entry, (
            f"资金记录已提交审批：{REMAINING_FIELD}按{APPROVED_FIELD}{approved}−"
            f"{USED_FIELD}{used}重算为{remaining}"
        )

    def summary(
        self, *, keyword: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        """按当前筛选口径汇总额度：卡片数字必须和列表合计同源，避免只算当前页。"""
        rows = self._filtered_rows(keyword=keyword, status=status)
        approved_total = Decimal("0.00")
        used_total = Decimal("0.00")
        remaining_total = Decimal("0.00")
        overspent = 0
        for row in rows:
            approved = _to_decimal(row.get(APPROVED_FIELD))
            used = _to_decimal(row.get(USED_FIELD))
            remaining = _to_decimal(row.get(REMAINING_FIELD))
            if approved is not None:
                approved_total += approved
            if used is not None:
                used_total += used
            if remaining is not None:
                remaining_total += remaining
            if row.get("abnormal") or (remaining is not None and remaining < 0):
                overspent += 1
        return {
            "批复总额": float(approved_total.quantize(Decimal("0.01"))),
            "已用金额合计": float(used_total.quantize(Decimal("0.01"))),
            "剩余额度合计": float(remaining_total.quantize(Decimal("0.01"))),
            "超支项目": overspent,
        }

    def _filtered_rows(
        self, *, keyword: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("资金编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return rows
