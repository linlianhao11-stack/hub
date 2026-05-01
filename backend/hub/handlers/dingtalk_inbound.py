"""钉钉入站消息 task handler。

职责：
- 解析命令：/绑定 X / /解绑 / 帮助 → 直走 BindingService
- 已绑定 + ERP 启用：调 ChainAgent → 自然语言多轮对话
- 多轮编号选择（Plan 4 遗产）：从 ConversationState 取候选项 → 调 execute_selected_*
- 低置信度（Plan 4 遗产）：保 state，让用户回"是"确认
- RE_CONFIRM 识别：用户回"是/确认/yes"→ user_just_confirmed=True 传给 ChainAgent
- 降级兜底：
  - ChainAgent.run 抛出**未被内部捕获**的异常（如 Redis 死了 / 配置错）
    → 降级 RuleParser → rule 命中执行 use case，否则发友好文案
  - LLMServiceError / asyncio.TimeoutError / PromptTooLargeError 等已被 ChainAgent
    内部转为 AgentResult.error_result，**不触发降级**；用户直接收到 ChainAgent 翻译的
    友好错误文案（"AI 响应超时"等）

依赖外部注入（避免直连）：
- binding_service / identity_service / sender（Plan 3）
- chain_agent（Plan 6 新）/ rule_parser（fallback，可选）
- conversation_state / query_product_usecase / query_customer_history_usecase /
  require_permissions（Plan 4，保留供 pending_choice / pending_confirm 路径）
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
from hub.ports import ParsedIntent

logger = logging.getLogger("hub.handler.dingtalk_inbound")


RE_BIND = re.compile(r"^/?绑定\s+(\S+)\s*$")
RE_UNBIND = re.compile(r"^/?解绑\s*$")
RE_HELP = re.compile(r"^/?(help|帮助|\?|菜单)\s*$", re.IGNORECASE)

# RE_CONFIRM：识别用户确认写操作的语义词（Plan 6 §2161-2174）
# 仅匹配整条消息是确认词的情况（防"是的""确认一下" 等误判）
# Plan 6 Task 10 加：RE_CONFIRM 比 rule_parser 既有 RE_CONFIRM 多了 "ok" / "确定"，
# 让 pending_confirm（绑定二次确认）和 RE_CONFIRM（写门禁确认）都用更宽的识别。
# 用户在两类确认场景下回 "ok" 也能识别。
RE_CONFIRM = re.compile(r"^\s*(是|确认|yes|y|ok|确定)\s*$", re.IGNORECASE)


async def handle_inbound(
    task_data: dict, *,
    binding_service,
    identity_service,
    sender,
    # Plan 6 新依赖
    chain_agent=None,          # ChainAgent（主路径）
    rule_parser=None,          # RuleParser（ChainAgent 未预期异常时降级 fallback）
    # Plan 4 遗产——保留供 pending_choice / pending_confirm 路径使用
    conversation_state=None,
    query_product_usecase=None,
    query_customer_history_usecase=None,
    require_permissions=None,
    # 可观测性
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
            # ====== 第一层：Rule 命令路由（不需要 IdentityService）======
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

            # ====== 第二层：身份解析 + ERP 状态检查 ======
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

            # ====== 第三层：Plan 4 pending_choice / pending_confirm 多轮回路（保留不动）======
            # 取多轮上下文（仅在 conversation_state 注入时有效）
            state = await conversation_state.load(channel_userid) if conversation_state else None
            parser_context: dict = {}
            if state:
                if state.get("pending_choice"):
                    parser_context["pending_choice"] = "yes"
                if state.get("pending_confirm"):
                    parser_context["pending_confirm"] = "yes"

            # 优先处理 pending_choice（数字编号回路）
            # M4：复用 rule_parser.RE_NUMBER（r"^\s*(\d{1,3})\s*$"）保证一致性
            from hub.intent.rule_parser import RE_NUMBER
            if state and state.get("pending_choice") and RE_NUMBER.match(content):
                try:
                    # 构造一个 select_choice intent 交给既有 _handle_select_choice
                    choice_intent = ParsedIntent(
                        intent_type="select_choice",
                        fields={"choice": int(content)},
                        confidence=1.0, parser="pending_choice",
                    )
                    await _handle_select_choice(
                        choice_intent, state, channel_userid, sender,
                        conversation_state, resolution,
                        query_product_usecase, query_customer_history_usecase,
                        require_permissions,
                    )
                    record["final_status"] = "success"
                except BizError as e:
                    await _send_text(sender, channel_userid, str(e))
                    record["final_status"] = "failed_user"
                return

            # pending_confirm 路由：仅在有 state.pending_confirm 且内容匹配确认词时触发
            # 注意：这个路径是绑定二次确认（Plan 3/4），与 RE_CONFIRM（Plan 6 写门禁确认）独立
            if state and state.get("pending_confirm") and RE_CONFIRM.match(content):
                try:
                    await _execute_confirmed(
                        state, channel_userid, sender, conversation_state, resolution,
                        query_product_usecase, query_customer_history_usecase,
                        require_permissions,
                    )
                    record["final_status"] = "success"
                except BizError as e:
                    await _send_text(sender, channel_userid, str(e))
                    record["final_status"] = "failed_user"
                return

            # ====== 第四层：ChainAgent 业务主路径（Plan 6 新）======
            if chain_agent is None:
                # 没有 ChainAgent 时降级为友好提示（兼容测试 / 未配置场景）
                await _send_text(sender, channel_userid,
                                 "我没听懂，请发送「帮助」查看可用功能。")
                record["final_status"] = "failed_user"
                return

            # 标注解析器（落 task_log.intent_parser，admin UI 用来区分 agent vs rule fallback 路径）
            # Plan 6 chain_agent 不再有"discrete intent"概念，confidence 留 null
            record["intent_parser"] = "agent"

            # RE_CONFIRM 识别（Plan 6 §2161）：用户整条消息是确认词
            # → user_just_confirmed=True 让 ChainAgent 调 confirm_gate.confirm_all_pending
            user_just_confirmed = bool(RE_CONFIRM.match(content))

            try:
                agent_result = await chain_agent.run(
                    user_message=content,
                    hub_user_id=resolution.hub_user_id,
                    conversation_id=conversation_id,
                    acting_as=resolution.erp_user_id,
                    channel_userid=channel_userid,
                    user_just_confirmed=user_just_confirmed,
                )
            except BizError as e:
                # BizError（权限拒绝等）：翻译成中文给用户
                await _send_text(sender, channel_userid, str(e))
                record["final_status"] = "failed_user"
                return
            except Exception:
                # ChainAgent 未预期异常（Redis 死 / 网络超时未被 agent 内部 catch 等）
                # → 降级 RuleParser
                logger.exception(
                    "ChainAgent 抛出未预期异常，降级 RuleParser conv=%s", conversation_id,
                )
                await _fallback_to_rule_parser(
                    content=content, rule_parser=rule_parser,
                    sender=sender, channel_userid=channel_userid,
                    record=record, parser_context=parser_context,
                    resolution=resolution,
                    query_product=query_product_usecase,
                    query_customer=query_customer_history_usecase,
                    require_permissions=require_permissions,
                )
                return

            # agent 返回 AgentResult（kind=text/clarification/error）
            if agent_result.kind == "error":
                # ChainAgent 已翻译过的友好错误（"AI 响应超时" 之类）→ 直接发给用户
                await _send_text(
                    sender, channel_userid,
                    agent_result.error or "AI 处理出了点问题",
                )
                record["final_status"] = "failed_system_final"
                return

            # success 路径：text 或 clarification 都直接发文本
            final_text = agent_result.text or "（无回复）"
            await _send_text(sender, channel_userid, final_text)
            record["final_status"] = "success"
            record["agent_kind"] = agent_result.kind

        finally:
            # 还原 sender.send_text，避免共享实例上累积包装
            sender.send_text = original_send_text


async def _fallback_to_rule_parser(
    *, content: str, rule_parser,
    sender, channel_userid: str,
    record: dict, parser_context: dict,
    resolution=None, query_product=None, query_customer=None, require_permissions=None,
) -> None:
    """ChainAgent 未预期异常时降级走 RuleParser。

    I1 修复（plan §2982）：rule 命中后直接调 _execute_intent 执行 use case，
    不再让用户'重发同格式'陷入循环。
    仅 unknown / low_confidence 时才发"AI 服务暂不可用"友好文案。

    parser_context 在当前 fallback 路径下通常为空（pending 状态都已在 handler 主路径
    early return 了）；保留参数为未来可能的 RuleParser context-aware 解析预留（M8）。

    注：record["agent_kind"] / record["fallback"] 仅落 SSE live publisher，
    不写 TaskLog（model 没字段）。Task 13 admin 决策链页面如需展示，
    应在 Task 13 加 model 字段 + migration（M3）。
    """
    if rule_parser is None:
        await _send_text(sender, channel_userid, "AI 处理出了点问题，请稍后重试")
        record["final_status"] = "failed_system_final"
        return
    try:
        intent = await rule_parser.parse(content, context=parser_context)
    except Exception:
        logger.exception("RuleParser fallback 也失败")
        await _send_text(sender, channel_userid, "AI 处理出了点问题，请稍后重试")
        record["final_status"] = "failed_system_final"
        return

    # 标注解析器为 rule（覆盖之前的 "agent" 标签）+ 落置信度
    record["intent_parser"] = "rule"
    record["intent_confidence"] = getattr(intent, "confidence", None)

    # unknown / low_confidence → 友好兜底文案
    # Plan 6 起 LLM agent 是主路径，RuleParser 仅做 ChainAgent 异常时的兜底兜底；
    # rule 也匹配不到的话，告诉用户稍后重试（不要让用户去模仿"查 SKU50139"这种过时格式）
    if intent.intent_type == "unknown" or getattr(intent, "confidence", 1.0) < 0.5:
        await _send_text(
            sender, channel_userid,
            "AI 处理出了点问题，请稍后再试一次。",
        )
        record["final_status"] = "failed_system_final"
        return

    # rule 命中 → 直接执行 use case（不再让用户重发）
    record["fallback"] = "rule"
    record["final_status"] = "fallback_to_rule"
    try:
        await _execute_intent(
            intent, channel_userid, sender, resolution,
            query_product, query_customer, require_permissions,
        )
    except BizError as e:
        await _send_text(sender, channel_userid, str(e))
        record["final_status"] = "failed_user"
    except Exception:
        logger.exception("RuleParser fallback 执行 use case 失败")
        await _send_text(sender, channel_userid, "AI 服务暂不可用，请稍后重试")
        record["final_status"] = "failed_system_final"


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



async def _send_text(sender, userid: str, text: str) -> None:
    """发送失败让异常上抛，由 WorkerRuntime 转入死信流，不静默 ACK。"""
    await sender.send_text(dingtalk_userid=userid, text=text)
