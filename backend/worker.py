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
    from hub.agent.graph.agent import GraphAgent
    from hub.agent.llm_client import DeepSeekLLMClient
    from hub.agent.memory.session import SessionMemory
    from hub.agent.tools import analyze_tools, draft_tools, erp_tools, generate_tools
    from hub.agent.tools.confirm_gate import ConfirmGate
    from hub.agent.tools.registry import ToolRegistry
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
        logger.warning("ai_provider 表为空，GraphAgent 将无法调用 LLM（仅 RuleParser fallback 有效）")
    conversation_state = ConversationStateRepository(redis=redis_client, ttl_seconds=300)
    pricing = DefaultPricingStrategy(erp_adapter=erp_adapter)
    query_product = QueryProductUseCase(
        erp=erp_adapter, pricing=pricing, sender=sender, state=conversation_state,
    )
    query_customer = QueryCustomerHistoryUseCase(
        erp=erp_adapter, pricing=pricing, sender=sender, state=conversation_state,
    )

    # Plan 6 v9 Task 7.2：构造 GraphAgent（替代 ChainAgent）
    # 1. ConfirmGate（写门禁 pending/confirmed 状态）
    confirm_gate = ConfirmGate(redis_client)

    # 2. SessionMemory（对话历史 + entity refs）
    session_memory = SessionMemory(redis_client)

    # 3. ToolRegistry + 注册所有 tool（read / generate / write_draft / analyze）
    tool_registry = ToolRegistry(
        confirm_gate=confirm_gate,
        session_memory=session_memory,
    )
    erp_tools.set_erp_adapter(erp_adapter)
    erp_tools.register_all(tool_registry)
    analyze_tools.register_all(tool_registry)
    generate_tools.set_dependencies(sender=sender, erp=erp_adapter)
    generate_tools.register_all(tool_registry)
    draft_tools.set_erp_adapter(erp_adapter)
    draft_tools.register_all(tool_registry)

    # 4. DeepSeekLLMClient（GraphAgent 使用 DeepSeek beta endpoint）
    # Plan 6 v9：GraphAgent router / chat / extract_context 都用 prefix completion +
    # strict mode + thinking 控制 — 这些是 DeepSeek **beta** endpoint 才支持的特性。
    # admin 后台配的 base_url 若是 main（https://api.deepseek.com）会让所有调用 400。
    # 策略：admin 配的 url 含 /beta 就照用（允许自建代理），否则强制升级到 BETA_URL。
    from hub.agent.llm_client import DEEPSEEK_BETA_URL
    admin_base_url = ai_provider.base_url if ai_provider else ""
    if admin_base_url and "/beta" in admin_base_url:
        agent_base_url = admin_base_url
    else:
        agent_base_url = DEEPSEEK_BETA_URL
        if admin_base_url:
            logger.info(
                "GraphAgent 将 base_url 从 %r 升级到 beta endpoint %r"
                "（router/strict/thinking 需 beta 支持）",
                admin_base_url, DEEPSEEK_BETA_URL,
            )
    agent_llm = DeepSeekLLMClient(
        api_key=ai_provider.api_key if ai_provider else "",
        base_url=agent_base_url,
        model=ai_provider.model if ai_provider else "deepseek-v4-flash",
    )

    # 5. tool_executor 闭包：将 GraphAgent 的两参数调用转换为 ToolRegistry.call
    #    hub_user_id / acting_as / conversation_id 从调用上下文中由 contextvars 注入，
    #    避免 GraphAgent 实例绑定单个用户上下文（多用户共享同一 worker 实例）。
    import contextvars
    _tool_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar(
        "tool_ctx", default={}
    )

    async def tool_executor(name: str, args: dict) -> object:
        ctx = _tool_ctx.get()
        return await tool_registry.call(
            name, args,
            hub_user_id=ctx.get("hub_user_id", 0),
            acting_as=ctx.get("acting_as", 0),
            conversation_id=ctx.get("conversation_id", ""),
            round_idx=ctx.get("round_idx", 0),
        )

    # 6. GraphAgent（Plan 6 v9 主 agent）
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

    graph_agent_inner = GraphAgent(
        llm=agent_llm,
        registry=tool_registry,
        confirm_gate=confirm_gate,
        session_memory=session_memory,
        tool_executor=tool_executor,
        checkpointer=_checkpointer,
    )

    # 7. GraphAgentAdapter：包装 GraphAgent，让其接口与 handler 期望的 AgentResult 兼容，
    #    并在调用前设置 tool_ctx contextvar（注入 hub_user_id / acting_as / conversation_id）
    class GraphAgentAdapter:
        """将 GraphAgent.run(str|None) 包装为 AgentResult，同时注入 tool_ctx。"""

        async def run(
            self,
            *,
            user_message: str,
            hub_user_id: int,
            conversation_id: str,
            acting_as: int | None = None,
            channel_userid: str | None = None,
        ) -> AgentResult:
            # Plan 6 v9 debug：每条消息进出都 log，方便排查"上下文不连贯"问题
            cid_short = (conversation_id or "")[-12:]
            logger.info(
                "[GA-IN] cid=...%s user=%d msg=%r",
                cid_short, hub_user_id, user_message[:200],
            )
            # peek 上轮 state（cross-turn checkpoint hydrate 验证）
            try:
                from hub.agent.graph.config import build_langgraph_config
                cfg = build_langgraph_config(
                    conversation_id=conversation_id, hub_user_id=hub_user_id,
                )
                snap = await graph_agent_inner.compiled_graph.aget_state(cfg)
                if snap and snap.values:
                    s = snap.values

                    # LangGraph state values 里 Pydantic 对象保留为对象（不是 dict），
                    # 用 getattr 而非 .get() 兼容两种形态（旧 hub_user_id 没存的对话也能 read）。
                    def _peek(obj, attr):
                        if obj is None:
                            return None
                        if isinstance(obj, dict):
                            return obj.get(attr)
                        return getattr(obj, attr, None)

                    logger.info(
                        "[GA-PRE-STATE] cid=...%s active_subgraph=%r "
                        "candidate_customers=%d candidate_products=%s "
                        "customer=%s products=%d items=%d shipping_addr=%r",
                        cid_short,
                        s.get("active_subgraph"),
                        len(s.get("candidate_customers") or []),
                        list((s.get("candidate_products") or {}).keys()),
                        _peek(s.get("customer"), "name"),
                        len(s.get("products") or []),
                        len(s.get("items") or []),
                        _peek(s.get("shipping"), "address"),
                    )
                else:
                    logger.info("[GA-PRE-STATE] cid=...%s (无上轮 state，新会话)", cid_short)
            except Exception as e:
                logger.warning("[GA-PRE-STATE] peek failed: %s", e)

            token = _tool_ctx.set({
                "hub_user_id": hub_user_id,
                "acting_as": acting_as or 0,
                "conversation_id": conversation_id,
                "round_idx": 0,
            })
            try:
                result = await graph_agent_inner.run(
                    user_message=user_message,
                    hub_user_id=hub_user_id,
                    conversation_id=conversation_id,
                    acting_as=acting_as,
                    channel_userid=channel_userid,
                )
            finally:
                _tool_ctx.reset(token)

            # 出口 log + post-state
            try:
                cfg = build_langgraph_config(
                    conversation_id=conversation_id, hub_user_id=hub_user_id,
                )
                snap = await graph_agent_inner.compiled_graph.aget_state(cfg)
                post_intent = None
                if snap and snap.values:
                    intent_obj = snap.values.get("intent")
                    post_intent = intent_obj.value if hasattr(intent_obj, "value") else intent_obj
                logger.info(
                    "[GA-OUT] cid=...%s intent=%s reply=%r",
                    cid_short, post_intent, (result or "")[:200],
                )
            except Exception as e:
                logger.warning("[GA-OUT] state read failed: %s; reply=%r", e, (result or "")[:200])

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
        # close 顺序：先关 agent_llm（Plan 6 Task 6 自建 httpx client），再关其他
        await agent_llm.aclose()
        if ai_provider is not None:
            await ai_provider.aclose()
        await erp_adapter.aclose()
        await sender.aclose()
        await redis_client.aclose()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
