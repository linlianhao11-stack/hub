"""Memory 系统组件工厂 —— worker.py / 测试启动时一次性构建。

把三层 service + SessionMemory + Loader/Writer 的组装逻辑集中在这里，
避免 worker.py 入口文件膨胀，也方便单元测试单独构造。
"""
from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from hub.agent.memory.loader import MemoryLoader
from hub.agent.memory.persistent import (
    CustomerMemoryService, ProductMemoryService, UserMemoryService,
)
from hub.agent.memory.session import SessionMemory
from hub.agent.memory.writer import MemoryWriter
from hub.agent.react.tools._invoke import set_session_memory


@dataclass(frozen=True)
class MemoryStack:
    """构造好的一组 memory 组件。"""
    session: SessionMemory
    user_svc: UserMemoryService
    customer_svc: CustomerMemoryService
    product_svc: ProductMemoryService
    loader: MemoryLoader
    writer: MemoryWriter


def build_memory_stack(*, redis: Redis) -> MemoryStack:
    """组装一组 memory 组件并把 SessionMemory 注入 ReAct invoke helper。

    调用方拿到 stack 后:
      - 把 stack.loader / stack.writer 传给 ReActAgent 构造
      - SessionMemory 已经通过 set_session_memory 注入 invoke_business_tool,
        无需再手动传给 ReAct tool
    """
    session = SessionMemory(redis=redis)
    user_svc = UserMemoryService()
    customer_svc = CustomerMemoryService()
    product_svc = ProductMemoryService()
    loader = MemoryLoader(
        session=session, user=user_svc,
        customer=customer_svc, product=product_svc,
    )
    writer = MemoryWriter(
        user=user_svc, customer=customer_svc, product=product_svc,
    )
    set_session_memory(session)
    return MemoryStack(
        session=session,
        user_svc=user_svc,
        customer_svc=customer_svc,
        product_svc=product_svc,
        loader=loader,
        writer=writer,
    )
