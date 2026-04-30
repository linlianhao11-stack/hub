"""Plan 6 Task 4：MemoryWriter 异步抽事实 + should_extract gate。"""
from __future__ import annotations
import logging
from typing import Any

from hub.agent.memory.persistent import (
    UserMemoryService, CustomerMemoryService, ProductMemoryService,
)
from hub.models.conversation import ToolCallLog


logger = logging.getLogger("hub.agent.memory.writer")


_EXTRACTION_PROMPT = """从下面的对话历史 + tool 调用日志中抽取事实，写入三层 memory：

1. user_facts: 当前用户偏好 / 工作习惯（如"喜欢付款条款 30 天"）
2. customer_facts: 关于客户的事实（如"阿里巴巴最近三月平均月单 50 万"）
3. product_facts: 关于商品的事实（如"讯飞 X5 Pro 春节断货 2 周"）

格式 JSON：
{
  "user_facts": [{"fact": "string", "confidence": 0.0-1.0}],
  "customer_facts": [{"customer_id": int, "fact": "string"}],
  "product_facts": [{"product_id": int, "fact": "string"}]
}

只抽**有商业价值**的事实；闲聊 / 重复 / 无意义内容跳过。confidence < 0.6 不写。
"""


class MemoryWriter:
    """对话结束后异步触发；先 should_extract gate，再 LLM mini round 抽取写库。"""

    def __init__(self, user: UserMemoryService,
                 customer: CustomerMemoryService,
                 product: ProductMemoryService):
        self.user = user
        self.customer = customer
        self.product = product

    @staticmethod
    def should_extract(*, tool_call_logs: list[ToolCallLog],
                       rounds_count: int) -> bool:
        """重要性 gate（spec §3.3）。任一满足即抽。"""
        if rounds_count >= 4:
            return True
        for log in tool_call_logs:
            if log.tool_name.startswith(("create_", "generate_")):
                return True
            result = log.result_json or {}
            if "customer_id" in str(result) or "product_id" in str(result):
                return True
        return False

    async def extract_and_write(self, *, conversation_id: str,
                                hub_user_id: int,
                                tool_call_logs: list[ToolCallLog],
                                rounds_count: int,
                                ai_provider: Any) -> None:
        """对话结束后调（asyncio.create_task）。fail-soft：抽取失败仅 log."""
        if not self.should_extract(tool_call_logs=tool_call_logs,
                                   rounds_count=rounds_count):
            return

        # 拼对话 + tool log 摘要给 LLM
        try:
            summary = self._build_extraction_input(tool_call_logs)
            result = await ai_provider.parse_intent(
                text=_EXTRACTION_PROMPT + "\n\n" + summary,
                schema={
                    "user_facts": [{"fact": "string", "confidence": "float"}],
                    "customer_facts": [{"customer_id": "int", "fact": "string"}],
                    "product_facts": [{"product_id": "int", "fact": "string"}],
                },
            )
        except Exception:
            logger.exception(
                "MemoryWriter.extract_and_write LLM 抽取失败 conv=%s",
                conversation_id,
            )
            return

        await self._upsert_all(hub_user_id, conversation_id, result or {})

    @staticmethod
    def _build_extraction_input(tool_call_logs: list[ToolCallLog]) -> str:
        """把 tool_call_logs 简化成可读文本。"""
        lines = []
        for log in tool_call_logs:
            lines.append(
                f"- {log.tool_name}({log.args_json}) "
                f"→ {str(log.result_json)[:200]}"
            )
        return "\n".join(lines)

    async def _upsert_all(self, hub_user_id: int, conversation_id: str,
                          extraction: dict) -> None:
        """三层分别 upsert。"""
        # user_facts
        u_facts = [
            f for f in (extraction.get("user_facts") or [])
            if f.get("confidence", 0) >= 0.6
        ]
        for f in u_facts:
            f["source_conversation"] = conversation_id
        if u_facts:
            await self.user.upsert_facts(hub_user_id, new_facts=u_facts)

        # customer_facts
        for f in extraction.get("customer_facts") or []:
            cid = f.get("customer_id")
            if isinstance(cid, int):
                await self.customer.upsert_facts(cid, new_facts=[
                    {"fact": f.get("fact"), "source_conversation": conversation_id},
                ])

        # product_facts
        for f in extraction.get("product_facts") or []:
            pid = f.get("product_id")
            if isinstance(pid, int):
                await self.product.upsert_facts(pid, new_facts=[
                    {"fact": f.get("fact"), "source_conversation": conversation_id},
                ])
