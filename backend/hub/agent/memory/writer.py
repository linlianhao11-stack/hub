"""Plan 6 Task 4：MemoryWriter 异步抽事实 + should_extract gate。"""
from __future__ import annotations

import logging
from typing import Any

from hub.agent.memory.persistent import (
    CustomerMemoryService,
    ProductMemoryService,
    UserMemoryService,
)
from hub.models.conversation import ToolCallLog

logger = logging.getLogger("hub.agent.memory.writer")


# I3: customer/product facts 也加 confidence 字段 + filter（与 user_facts 对齐）
_EXTRACTION_PROMPT = """从下面的对话历史 + tool 调用日志中抽取事实，写入三层 memory：

1. user_facts: 当前用户偏好 / 工作习惯（如"喜欢付款条款 30 天"）
2. customer_facts: 关于客户的事实（如"阿里巴巴最近三月平均月单 50 万"）
3. product_facts: 关于商品的事实（如"讯飞 X5 Pro 春节断货 2 周"）

格式 JSON：
{
  "user_facts": [{"fact": "string", "confidence": 0.0-1.0}],
  "customer_facts": [{"customer_id": int, "fact": "string", "confidence": 0.0-1.0}],
  "product_facts": [{"product_id": int, "fact": "string", "confidence": 0.0-1.0}]
}

只抽**有商业价值**的事实；闲聊 / 重复 / 无意义内容跳过。confidence < 0.6 不写。
"""


def _safe_list(value: Any) -> list:
    """C2: 防御非 list 输入（LLM 可能返回 null / string 等）。"""
    return value if isinstance(value, list) else []


# I2: 模块级 EntityExtractor 实例，用于 should_extract 的结构化检查
from hub.agent.tools.entity_extractor import EntityExtractor  # noqa: E402

_EXTRACTOR = EntityExtractor()


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
        """重要性 gate（spec §3.3）。任一满足即抽。

        I2: 复用 EntityExtractor.extract().has_any() 替代字面 substring match，
        避免 "customer_identifier_v2" 等假阳性。
        """
        if rounds_count >= 4:
            return True
        for log in tool_call_logs:
            if log.tool_name.startswith(("create_", "generate_")):
                return True
            if log.result_json and _EXTRACTOR.extract(log.result_json).has_any():
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
                    "customer_facts": [{"customer_id": "int", "fact": "string", "confidence": "float"}],
                    "product_facts": [{"product_id": "int", "fact": "string", "confidence": "float"}],
                },
            )
        except Exception:
            logger.exception(
                "MemoryWriter.extract_and_write LLM 抽取失败 conv=%s",
                conversation_id,
            )
            return

        # C2: _upsert_all DB 异常不阻塞业务（asyncio.create_task 中 unhandled 会变 warning）
        try:
            await self._upsert_all(hub_user_id, conversation_id, result or {})
        except Exception:
            logger.exception(
                "MemoryWriter._upsert_all 写库失败 conv=%s（不阻塞业务，待下次抽取兜底）",
                conversation_id,
            )

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
        """三层分别 upsert。

        C2: 调用方已用 try/except 包裹，此处异常会向上抛被 caller 捕获 log。
        C2: _safe_list 防御 LLM 返回非 list 入参。
        I3: customer/product 同样按 confidence >= 0.6 过滤。
        """
        # user_facts
        u_facts = [
            f for f in _safe_list(extraction.get("user_facts"))
            if isinstance(f, dict) and f.get("confidence", 0) >= 0.6
        ]
        for f in u_facts:
            f["source_conversation"] = conversation_id
        if u_facts:
            await self.user.upsert_facts(hub_user_id, new_facts=u_facts)

        # customer_facts — I3: 加 confidence 过滤
        for f in _safe_list(extraction.get("customer_facts")):
            cid = f.get("customer_id")
            if isinstance(cid, int) and isinstance(f, dict) and f.get("confidence", 0) >= 0.6:
                await self.customer.upsert_facts(cid, new_facts=[
                    {
                        "fact": f.get("fact"),
                        "confidence": f.get("confidence"),
                        "source_conversation": conversation_id,
                    },
                ])

        # product_facts — I3: 加 confidence 过滤
        for f in _safe_list(extraction.get("product_facts")):
            pid = f.get("product_id")
            if isinstance(pid, int) and isinstance(f, dict) and f.get("confidence", 0) >= 0.6:
                await self.product.upsert_facts(pid, new_facts=[
                    {
                        "fact": f.get("fact"),
                        "confidence": f.get("confidence"),
                        "source_conversation": conversation_id,
                    },
                ])
