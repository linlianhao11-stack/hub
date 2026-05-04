"""HUB Worker 进程入口。"""
from __future__ import annotations

import asyncio
import logging

from hub.config import get_settings
from hub.database import close_db, init_db
from hub.worker_runtime import WorkerRuntime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hub.worker")


async def main():
    await init_db()
    settings = get_settings()

    from redis.asyncio import Redis

    from hub.adapters.channel.dingtalk_sender import DingTalkSender
    from hub.adapters.downstream.erp4 import Erp4Adapter
    from hub.agent.react.agent import ReActAgent
    from hub.agent.react.llm import build_chat_model
    from hub.agent.react.tools import ALL_TOOLS
    from hub.agent.react.tools._confirm_helper import set_confirm_gate
    from hub.agent.tools import draft_tools, erp_tools, generate_tools
    from hub.agent.tools.confirm_gate import ConfirmGate
    from hub.agent.types import AgentResult
    from hub.capabilities.factory import load_active_ai_provider
    from hub.crypto import decrypt_secret
    from hub.handlers.dingtalk_inbound import handle_inbound
    from hub.handlers.dingtalk_outbound import handle_outbound
    from hub.intent.rule_parser import RuleParser
    from hub.match.conversation_state import ConversationStateRepository
    from hub.models import ChannelApp, DownstreamSystem
    from hub.observability.live_stream import LiveStreamPublisher
    from hub.permissions import require_permissions
    from hub.services.binding_service import BindingService
    from hub.services.erp_active_cache import ErpActiveCache
    from hub.services.identity_service import IdentityService
    from hub.strategy.pricing import DefaultPricingStrategy
    from hub.usecases.query_customer_history import QueryCustomerHistoryUseCase
    from hub.usecases.query_product import QueryProductUseCase

    redis_client = Redis.from_url(settings.redis_url, decode_responses=False)
    live_publisher = LiveStreamPublisher(redis=redis_client)

    # 钉钉 + ERP 配置必须**双双就绪**才能注册 dingtalk_inbound handler——
    # 否则 worker 启动 + 收到入站消息时 binding_service=None，handler 直接 return，
    # WorkerRuntime 会 ACK 掉消息，**用户消息被静默丢弃**。
    channel_app: ChannelApp | None = None
    ds: DownstreamSystem | None = None
    while channel_app is None or ds is None:
        if channel_app is None:
            channel_app = await ChannelApp.filter(
                channel_type="dingtalk", status="active",
            ).first()
        if ds is None:
            ds = await DownstreamSystem.filter(
                downstream_type="erp", status="active",
            ).first()
        missing = []
        if channel_app is None:
            missing.append("钉钉应用")
        if ds is None:
            missing.append("ERP 下游")
        if missing:
            logger.info(
                f"等待初始化向导完成 [{', '.join(missing)}] 配置 ...（30 秒后重试）",
            )
            await asyncio.sleep(30)

    dt_app_key = decrypt_secret(channel_app.encrypted_app_key, purpose="config_secrets")
    dt_app_secret = decrypt_secret(channel_app.encrypted_app_secret, purpose="config_secrets")
    sender = DingTalkSender(
        app_key=dt_app_key, app_secret=dt_app_secret,
        robot_code=channel_app.robot_id or "",
    )

    erp_api_key = decrypt_secret(ds.encrypted_apikey, purpose="config_secrets")
    erp_adapter = Erp4Adapter(base_url=ds.base_url, api_key=erp_api_key)
    erp_active_cache = ErpActiveCache(erp_adapter=erp_adapter, ttl_seconds=600)
    identity_service = IdentityService(erp_active_cache=erp_active_cache)
    binding_service = BindingService(erp_adapter=erp_adapter)

    # 业务依赖（Plan 4）：多轮上下文 + 价格策略 + 业务用例（pending_choice/confirm 路径保留）
    ai_provider = await load_active_ai_provider()
    if ai_provider is None:
        logger.warning("ai_provider 表为空，ReActAgent 将无法调用 LLM（仅 RuleParser fallback 有效）")
    conversation_state = ConversationStateRepository(redis=redis_client, ttl_seconds=300)
    pricing = DefaultPricingStrategy(erp_adapter=erp_adapter)
    query_product = QueryProductUseCase(
        erp=erp_adapter, pricing=pricing, sender=sender, state=conversation_state,
    )
    query_customer = QueryCustomerHistoryUseCase(
        erp=erp_adapter, pricing=pricing, sender=sender, state=conversation_state,
    )

    # Plan 6 v10 Task 4.3：构造 ReActAgent（替代 GraphAgent）
    # 1. ConfirmGate（写门禁 pending/confirmed 状态）
    confirm_gate = ConfirmGate(redis_client)
    set_confirm_gate(confirm_gate)

    # 1b. ReAct read/write tool 全局依赖注入 — 跟 main.py(gateway) 一致。
    # 不注入会导致 worker 进程里 current_erp_adapter() / current_sender() 抛
    # "ERP adapter / DingTalkSender 未初始化"，钉钉真测直接挂。
    erp_tools.set_erp_adapter(erp_adapter)
    draft_tools.set_erp_adapter(erp_adapter)
    generate_tools.set_dependencies(sender=sender, erp=erp_adapter)

    # 2. chat model（ReActAgent 使用 LangChain ChatOpenAI 接口）
    agent_base_url = ai_provider.base_url if ai_provider else ""

    # 4. ReActAgent checkpointer
    # Plan 6 v9：用 AsyncPostgresSaver 让 LangGraph state 跨 worker 重启持久化，
    # 取代默认 in-process MemorySaver（重启即丢上下文，钉钉对话"前面说的事 bot 忘了"）。
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool
    import os

    pg_url = os.environ.get("HUB_DATABASE_URL", "")
    if pg_url.startswith("postgres://"):  # psycopg 要求 postgresql://
        pg_url = "postgresql://" + pg_url[len("postgres://"):]
    _checkpoint_pool = AsyncConnectionPool(
        pg_url, open=False, max_size=4,
        kwargs={"autocommit": True, "prepare_threshold": 0},  # langgraph 要求 autocommit
    )
    await _checkpoint_pool.open()
    _checkpointer = AsyncPostgresSaver(_checkpoint_pool)
    try:
        await _checkpointer.setup()  # 创建 checkpoints / writes / blobs 表（幂等）
        logger.info("LangGraph AsyncPostgresSaver checkpoint tables ready")
    except Exception as e:
        logger.exception("AsyncPostgresSaver.setup 失败 — checkpoint 仍可工作但跨重启 hydrate 不保证: %s", e)

    chat_model = build_chat_model(
        api_key=ai_provider.api_key if ai_provider else "",
        base_url=agent_base_url,
        model=ai_provider.model if ai_provider else "deepseek-v4-flash",
        temperature=0.0,
        max_tokens=4096,
    )
    graph_agent_inner = ReActAgent(
        chat_model=chat_model,
        tools=ALL_TOOLS,
        checkpointer=_checkpointer,
        recursion_limit=15,
    )

    # 7. GraphAgentAdapter：包装 ReActAgent，让其接口与 handler 期望的 AgentResult 兼容
    class GraphAgentAdapter:
        """v10 简化版：只透传 user_message → ReActAgent.run() → text reply。"""

        async def run(
            self,
            *,
            user_message: str,
            hub_user_id: int,
            conversation_id: str,
            acting_as: int | None = None,
            channel_userid: str | None = None,
        ) -> AgentResult:
            cid_short = (conversation_id or "")[-12:]
            logger.info(
                "[GA-IN] cid=...%s user=%d msg=%r",
                cid_short, hub_user_id, user_message[:200],
            )
            result = await graph_agent_inner.run(
                user_message=user_message,
                hub_user_id=hub_user_id,
                conversation_id=conversation_id,
                acting_as=acting_as,
                channel_userid=channel_userid or "",
            )
            logger.info(
                "[GA-OUT] cid=...%s reply=%r",
                cid_short, (result or "")[:200],
            )
            if result is None:
                return AgentResult.text_result("（无回复）")
            return AgentResult.text_result(result)

    chain_agent = GraphAgentAdapter()

    # RuleParser：保留作 GraphAgent 未预期异常时的降级兜底
    rule_parser = RuleParser()

    runtime = WorkerRuntime(redis_client=redis_client)

    async def dingtalk_inbound_handler(task_data):
        await handle_inbound(
            task_data,
            binding_service=binding_service,
            identity_service=identity_service,
            sender=sender,
            chain_agent=chain_agent,
            rule_parser=rule_parser,
            conversation_state=conversation_state,
            query_product_usecase=query_product,
            query_customer_history_usecase=query_customer,
            require_permissions=require_permissions,
            live_publisher=live_publisher,
        )

    async def dingtalk_outbound_handler(task_data):
        await handle_outbound(task_data, sender=sender)

    runtime.register("dingtalk_inbound", dingtalk_inbound_handler)
    runtime.register("dingtalk_outbound", dingtalk_outbound_handler)

    try:
        await runtime.run()
    finally:
        # close 顺序：先关 ai_provider，再关 LangGraph checkpoint pool，再关其他
        if ai_provider is not None:
            await ai_provider.aclose()
        # 清空 ReAct tool 全局依赖（防 stale 引用）+ ConfirmGate（防 stale gate）
        erp_tools.set_erp_adapter(None)
        draft_tools.set_erp_adapter(None)
        generate_tools.set_dependencies(sender=None, erp=None)
        set_confirm_gate(None)
        # LangGraph checkpoint pool — worker 重启 / 异常退出时不能漏关 PG 连接
        try:
            await _checkpoint_pool.close()
        except Exception:
            logger.exception("_checkpoint_pool.close 失败 — 继续走后续清理")
        await erp_adapter.aclose()
        await sender.aclose()
        await redis_client.aclose()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
