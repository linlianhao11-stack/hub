"""钉钉入站消息 task handler。

职责：
- 解析命令：/绑定 X / /解绑 / 帮助 → 直走 BindingService
- 已绑定 + ERP 启用：调 ChainParser → 路由到对应 UseCase
- 多轮编号选择：从 ConversationState 取候选项 → 调 execute_selected_*
- 低置信度：保 state，让用户回"是"确认

依赖外部注入（避免直连）：
- binding_service / identity_service / sender（Plan 3）
- chain_parser / conversation_state / query_product_usecase /
  query_customer_history_usecase / require_permissions（Plan 4）
- live_publisher（Plan 5 task 6）：注入 LiveStreamPublisher 后，
  task_logger 退出时把脱敏事件 publish 到 Redis pubsub 让前端 SSE 收到

发送失败异常向上抛让 WorkerRuntime 转死信，不静默 ACK。
"""
from __future__ import annotations

import logging
import re

from hub import messages
from hub.error_codes import BizError, BizErrorCode, build_user_message
from hub.observability.task_logger import log_inbound_task

logger = logging.getLogger("hub.handler.dingtalk_inbound")


RE_BIND = re.compile(r"^/?绑定\s+(\S+)\s*$")
RE_UNBIND = re.compile(r"^/?解绑\s*$")
RE_HELP = re.compile(r"^/?(help|帮助|\?|菜单)\s*$", re.IGNORECASE)


async def handle_inbound(
    task_data: dict, *,
    binding_service,
    identity_service,
    sender,
    chain_parser=None,
    conversation_state=None,
    query_product_usecase=None,
    query_customer_history_usecase=None,
    require_permissions=None,
    live_publisher=None,
) -> None:
    payload = task_data.get("payload", {})
    task_id = task_data.get("task_id", "")
    channel_userid = payload.get("channel_userid", "")
    content = (payload.get("content") or "").strip()
    conversation_id = payload.get("conversation_id", "")

    async with log_inbound_task(
        task_id=task_id,
        channel_userid=channel_userid,
        content=content,
        conversation_id=conversation_id,
        live_publisher=live_publisher,
    ) as record:
        # 包装 sender.send_text 抓取回复内容到 record["response"]，
        # finally 必须还原（worker 持续运行，sender 是共享实例，避免下次任务串包装）
        original_send_text = sender.send_text

        async def _wrapped_send_text(*, dingtalk_userid, text, **kwargs):
            record["response"] = text
            return await original_send_text(
                dingtalk_userid=dingtalk_userid, text=text, **kwargs,
            )

        sender.send_text = _wrapped_send_text
        try:
            # ====== 以下 Plan 4 原 handle_inbound 函数体（除新增 record["final_status"]）======
            # 1. 绑定/解绑/帮助命令（不需要 IdentityService）
            m_bind = RE_BIND.match(content)
            if m_bind:
                result = await binding_service.initiate_binding(
                    dingtalk_userid=channel_userid, erp_username=m_bind.group(1),
                )
                await _send_text(sender, channel_userid, result.reply_text)
                record["final_status"] = "success"
                return

            if RE_UNBIND.match(content):
                result = await binding_service.unbind_self(dingtalk_userid=channel_userid)
                await _send_text(sender, channel_userid, result.reply_text)
                record["final_status"] = "success"
                return

            if RE_HELP.match(content):
                cmds = [
                    "/绑定 你的ERP用户名 — 绑定 ERP 账号",
                    "/解绑 — 解绑当前账号",
                    "查 SKU100 — 查商品",
                    "查 SKU100 给阿里 — 查客户历史价",
                ]
                await _send_text(sender, channel_userid, messages.help_message(cmds))
                record["final_status"] = "success"
                return

            # 2. 解析身份 + 检查 ERP 启用
            resolution = await identity_service.resolve(dingtalk_userid=channel_userid)
            if not resolution.found:
                await _send_text(sender, channel_userid,
                                 build_user_message(BizErrorCode.USER_NOT_BOUND))
                record["final_status"] = "failed_user"
                return
            if not resolution.erp_active:
                await _send_text(sender, channel_userid,
                                 build_user_message(BizErrorCode.USER_ERP_DISABLED))
                record["final_status"] = "failed_user"
                return

            # 3. 进入业务用例（Plan 3 注入 None 时降级为占位提示）
            if chain_parser is None:
                await _send_text(sender, channel_userid,
                                 "我没听懂，请发送「帮助」查看可用功能。")
                record["final_status"] = "failed_user"
                return

            # 取多轮上下文
            state = await conversation_state.load(channel_userid) if conversation_state else None
            parser_context: dict = {}
            if state:
                if state.get("pending_choice"):
                    parser_context["pending_choice"] = "yes"
                if state.get("pending_confirm"):
                    parser_context["pending_confirm"] = "yes"

            intent = await chain_parser.parse(content, context=parser_context)
            # 把意图解析结果写入 record（live stream + task_log 两边都用）
            record["intent_parser"] = intent.parser
            record["intent_confidence"] = intent.confidence

            # 4. 路由 + 权限校验
            try:
                if intent.intent_type == "select_choice":
                    await _handle_select_choice(
                        intent, state, channel_userid, sender,
                        conversation_state, resolution,
                        query_product_usecase, query_customer_history_usecase,
                        require_permissions,
                    )
                    record["final_status"] = "success"
                    return

                if intent.intent_type == "confirm_yes":
                    if state and state.get("pending_confirm"):
                        await _execute_confirmed(
                            state, channel_userid, sender, conversation_state, resolution,
                            query_product_usecase, query_customer_history_usecase,
                            require_permissions,
                        )
                    else:
                        await _send_text(sender, channel_userid,
                                         "没有需要确认的待办；请重新描述你的需求。")
                    record["final_status"] = "success"
                    return

                if intent.intent_type == "unknown":
                    await _send_text(sender, channel_userid,
                                     build_user_message(BizErrorCode.INTENT_LOW_CONFIDENCE))
                    record["final_status"] = "failed_user"
                    return

                if intent.notes == "low_confidence":
                    await conversation_state.save(channel_userid, {
                        "intent_type": intent.intent_type,
                        "fields": intent.fields,
                        "pending_confirm": "yes",
                    })
                    summary = _summarize_intent(intent)
                    await _send_text(
                        sender, channel_userid,
                        f"我大概理解为：{summary}\n\n如果是这个意思请回复「是」继续，"
                        f"否则请用更明确的方式重新描述。",
                    )
                    record["final_status"] = "success"
                    return

                # 高置信度直接执行
                await _execute_intent(
                    intent, channel_userid, sender, resolution,
                    query_product_usecase, query_customer_history_usecase,
                    require_permissions,
                )
                record["final_status"] = "success"
            except BizError as e:
                await _send_text(sender, channel_userid, str(e))
                record["final_status"] = "failed_user"
        finally:
            # 还原 sender.send_text，避免共享实例上累积包装
            sender.send_text = original_send_text


async def _execute_intent(
    intent, channel_userid, sender, resolution,
    query_product, query_customer, require_permissions,
):
    if intent.intent_type == "query_product":
        await require_permissions(resolution.hub_user_id, [
            "channel.dingtalk.use", "downstream.erp.use", "usecase.query_product.use",
        ])
        await query_product.execute(
            sku_or_keyword=intent.fields["sku_or_keyword"],
            dingtalk_userid=channel_userid, acting_as=resolution.erp_user_id,
        )
    elif intent.intent_type == "query_customer_history":
        await require_permissions(resolution.hub_user_id, [
            "channel.dingtalk.use", "downstream.erp.use",
            "usecase.query_customer_history.use",
        ])
        await query_customer.execute(
            sku_or_keyword=intent.fields["sku_or_keyword"],
            customer_keyword=intent.fields["customer_keyword"],
            dingtalk_userid=channel_userid, acting_as=resolution.erp_user_id,
        )
    else:
        await _send_text(sender, channel_userid,
                         build_user_message(BizErrorCode.INTENT_LOW_CONFIDENCE))


async def _handle_select_choice(
    intent, state, channel_userid, sender, conversation_state, resolution,
    query_product, query_customer, require_permissions,
):
    if not state or not state.get("candidates"):
        await _send_text(sender, channel_userid, "没有需要选择的待办；请重新描述你的需求。")
        return

    choice = intent.fields.get("choice", 0)
    candidates = state["candidates"]
    if not (1 <= choice <= len(candidates)):
        await _send_text(sender, channel_userid, "编号超出范围，请重新输入。")
        return

    selected = candidates[choice - 1]
    await conversation_state.clear(channel_userid)

    if state.get("intent_type") == "query_product":
        await require_permissions(resolution.hub_user_id, [
            "channel.dingtalk.use", "downstream.erp.use", "usecase.query_product.use",
        ])
        await query_product.execute_selected(
            product=selected,
            dingtalk_userid=channel_userid, acting_as=resolution.erp_user_id,
        )
    elif state.get("intent_type") == "query_customer_history":
        await require_permissions(resolution.hub_user_id, [
            "channel.dingtalk.use", "downstream.erp.use",
            "usecase.query_customer_history.use",
        ])
        if state.get("resource") == "客户":
            await query_customer.execute_selected_customer(
                customer=selected,
                sku_or_keyword=state["sku_or_keyword"],
                dingtalk_userid=channel_userid, acting_as=resolution.erp_user_id,
            )
        elif state.get("resource") == "商品":
            await query_customer.execute_selected_product(
                product=selected,
                customer_id=state["customer_id"],
                customer_name=state["customer_name"],
                dingtalk_userid=channel_userid, acting_as=resolution.erp_user_id,
            )


async def _execute_confirmed(
    state, channel_userid, sender, conversation_state, resolution,
    query_product, query_customer, require_permissions,
):
    from hub.ports import ParsedIntent
    intent = ParsedIntent(
        intent_type=state["intent_type"],
        fields=state.get("fields", {}),
        confidence=1.0, parser="confirmed",
    )
    await conversation_state.clear(channel_userid)
    await _execute_intent(
        intent, channel_userid, sender, resolution,
        query_product, query_customer, require_permissions,
    )


def _summarize_intent(intent) -> str:
    if intent.intent_type == "query_product":
        return f"查商品 {intent.fields.get('sku_or_keyword', '')}"
    if intent.intent_type == "query_customer_history":
        return (f"查 {intent.fields.get('sku_or_keyword', '')} "
                f"给客户「{intent.fields.get('customer_keyword', '')}」的历史价")
    return "未知操作"


async def _send_text(sender, userid: str, text: str) -> None:
    """发送失败让异常上抛，由 WorkerRuntime 转入死信流，不静默 ACK。"""
    await sender.send_text(dingtalk_userid=userid, text=text)
