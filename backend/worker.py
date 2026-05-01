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
    from hub.agent.chain_agent import ChainAgent
    from hub.agent.context_builder import ContextBuilder
    from hub.agent.llm_client import AgentLLMClient
    from hub.agent.memory.loader import MemoryLoader
    from hub.agent.memory.persistent import (
        CustomerMemoryService,
        ProductMemoryService,
        UserMemoryService,
    )
    from hub.agent.memory.session import SessionMemory
    from hub.agent.prompt.builder import PromptBuilder
    from hub.agent.tools import analyze_tools, draft_tools, erp_tools, generate_tools
    from hub.agent.tools.confirm_gate import ConfirmGate
    from hub.agent.tools.registry import ToolRegistry
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
        logger.warning("ai_provider 表为空，ChainAgent 将无法调用 LLM（仅 RuleParser fallback 有效）")
    conversation_state = ConversationStateRepository(redis=redis_client, ttl_seconds=300)
    pricing = DefaultPricingStrategy(erp_adapter=erp_adapter)
    query_product = QueryProductUseCase(
        erp=erp_adapter, pricing=pricing, sender=sender, state=conversation_state,
    )
    query_customer = QueryCustomerHistoryUseCase(
        erp=erp_adapter, pricing=pricing, sender=sender, state=conversation_state,
    )

    # Plan 6：构造 ChainAgent（替代 chain_parser）
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

    # 4. MemoryLoader
    memory_loader = MemoryLoader(
        session=session_memory,
        user=UserMemoryService(),
        customer=CustomerMemoryService(),
        product=ProductMemoryService(),
    )

    # 5. PromptBuilder（v8 review P2-#3：从 SystemConfig 加载 admin 编辑的 business_dict 覆盖默认）
    # admin 后台改 SystemConfig.business_dict 后需重启 worker 生效（无热重载）
    prompt_builder = await PromptBuilder.from_db()

    # 6. ContextBuilder
    context_builder = ContextBuilder(prompt_builder=prompt_builder)

    # 7. AgentLLMClient（从 ai_provider 取 api_key / base_url / model）
    agent_llm = AgentLLMClient(
        api_key=ai_provider.api_key if ai_provider else "",
        base_url=ai_provider.base_url if ai_provider else "",
        model=ai_provider.model if ai_provider else "",
    )

    # 8. ChainAgent
    chain_agent = ChainAgent(
        llm=agent_llm,
        registry=tool_registry,
        confirm_gate=confirm_gate,
        session_memory=session_memory,
        memory_loader=memory_loader,
        context_builder=context_builder,
    )

    # RuleParser：保留作 ChainAgent 未预期异常时的降级兜底
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
