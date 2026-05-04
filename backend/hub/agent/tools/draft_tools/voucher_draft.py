from __future__ import annotations

import logging

from tortoise.exceptions import IntegrityError

import hub.agent.tools.draft_tools as _pkg
from hub.agent.tools.types import ToolArgsValidationError

logger = logging.getLogger("hub.agent.tools.draft_tools")


# ===== Plan 6 v9 Task 2.2：strict tool schema（spec §1.3 / §5.2）=====

CREATE_VOUCHER_DRAFT_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "create_voucher_draft",
        "strict": True,
        "description": "创建凭证草稿（挂会计审批 inbox）",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "voucher_data",
                "rule_matched",
            ],
            "properties": {
                "voucher_data": {
                    "type": "object",
                    "description": "凭证内容，必须含 entries / total_amount / summary",
                    "additionalProperties": True,
                },
                "rule_matched": {
                    "type": "string",
                    "description": "匹配到的凭证模板名（可选）；如无传 ''",
                },
            },
        },
    },
    "_subgraphs": ["voucher"],
}


# ============================================================
# 辅助
# ============================================================

async def _get_max_voucher_amount() -> int:
    """从 system_config 读金额上限；默认 1_000_000。"""
    from hub.models.config import SystemConfig
    rec = await SystemConfig.filter(key="max_voucher_amount").first()
    if rec and rec.value is not None:
        try:
            return int(rec.value)
        except (ValueError, TypeError):
            pass
    return 1_000_000


def _validate_voucher_data(voucher_data: dict) -> None:
    """检查 voucher_data 含必填字段 (entries / total_amount / summary)。"""
    required = {"entries", "total_amount", "summary"}
    missing = required - set(voucher_data.keys())
    if missing:
        raise ToolArgsValidationError(
            f"voucher_data 缺少必填字段：{sorted(missing)}"
        )


def _validate_amount_within_limit(total_amount: float, max_amount: int) -> None:
    """total_amount 超限 → 抛 ToolArgsValidationError。"""
    if total_amount > max_amount:
        raise ToolArgsValidationError(
            f"凭证金额 {total_amount} 超过单笔上限 {max_amount}，"
            "请联系管理员或拆分处理。"
        )


def _approval_url(draft_id: int) -> str:
    return f"/admin/approvals/voucher#{draft_id}"


# ============================================================
# Tool 1：凭证草稿
# ============================================================

async def create_voucher_draft(
    voucher_data: dict,
    rule_matched: str | None = None,
    *,
    hub_user_id: int,
    conversation_id: str,
    acting_as_user_id: int,
    confirmation_action_id: str,
) -> dict:
    """创建凭证草稿，挂会计审批 inbox。

    Args:
        voucher_data: 凭证内容，必须含 entries / total_amount / summary
        rule_matched: 匹配到的凭证模板名（可选）

    M11 类型不变量：本 tool 仅写 HUB 草稿表（VoucherDraft），不调 ERP 写接口；
    ERP 落地由 admin 审批端点（admin/approvals/voucher/batch-approve）完成。
    未来重构不要把 ERP 写调用挪到此处。
    """
    # sentinel 归一化（spec §1.3 v3.4）：LLM 传 "" 当 optional → 归一化成 None
    rule_matched = rule_matched or None

    # M3: 先幂等查，再校验（回放路径不重新 query system_config）
    # 1. 幂等先查
    existing = await _pkg.VoucherDraft.filter(
        requester_hub_user_id=hub_user_id,
        confirmation_action_id=confirmation_action_id,
    ).first()
    if existing is not None:
        logger.info(
            "create_voucher_draft idempotent_replay: user=%s action_id=%s draft_id=%s",
            hub_user_id, confirmation_action_id, existing.id,
        )
        return {
            "draft_id": existing.id,
            "status": existing.status,
            "approval_url": _approval_url(existing.id),
            "idempotent_replay": True,
        }

    # 2. 入参校验（回放路径跳过，不重新 query system_config）
    _validate_voucher_data(voucher_data)
    total_amount = float(voucher_data.get("total_amount", 0))
    max_amount = await _get_max_voucher_amount()
    _validate_amount_within_limit(total_amount, max_amount)

    # 3. INSERT（可能 IntegrityError）
    try:
        draft = await _pkg.VoucherDraft.create(
            requester_hub_user_id=hub_user_id,
            voucher_data=voucher_data,
            rule_matched=rule_matched,
            status="pending",
            conversation_id=conversation_id,
            confirmation_action_id=confirmation_action_id,
        )
    except IntegrityError:
        # 并发竞争：回查
        existing = await _pkg.VoucherDraft.filter(
            requester_hub_user_id=hub_user_id,
            confirmation_action_id=confirmation_action_id,
        ).first()
        if existing is not None:
            logger.info(
                "create_voucher_draft concurrent IntegrityError → replay: "
                "user=%s action_id=%s draft_id=%s",
                hub_user_id, confirmation_action_id, existing.id,
            )
            return {
                "draft_id": existing.id,
                "status": existing.status,
                "approval_url": _approval_url(existing.id),
                "idempotent_replay": True,
            }
        # 回查不到：其他原因的 IntegrityError，reraise
        raise

    return {
        "draft_id": draft.id,
        "status": draft.status,
        "approval_url": _approval_url(draft.id),
        "idempotent_replay": False,
        "message": "凭证草稿已创建，等待会计审批。",
    }
