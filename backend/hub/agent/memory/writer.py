"""MemoryWriter 异步抽事实 + should_extract gate。

v12 重构:输入维度从 ToolCallLog 切到 LangGraph state messages
  - 旧:用 tool_call 日志(tool_name + args_json + result_json)给 LLM 看
  - 新:用 LangChain BaseMessage 列表(HumanMessage / AIMessage / ToolMessage)
        渲染成对话原文 + tool 调用摘要,LLM 看完整语义抽事实

为什么换:
  - 用户原话(HumanMessage)和助手回复(AIMessage.content)只在 messages 里有,
    ToolCallLog 永远抓不到。"翼蓝以后都用现款"这种纯陈述会被漏掉。
  - 钉钉同一 conversation_id 是长期会话,limit(N) tool log 是裸滑窗,
    messages 来自 LangGraph state,语义完整且按 round 边界自然切。

prompt + helper 拆到 _extraction_input.py(让本文件在 250 行内)。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from hub.agent.memory._extraction_input import (
    EXTRACTION_PROMPT,
    build_extraction_input,
    extract_tool_call_names,
    msg_class_name,
    parse_tool_message_content,
)
from hub.agent.memory.persistent import (
    CustomerMemoryService,
    ProductMemoryService,
    UserMemoryService,
)
from hub.agent.tools.entity_extractor import EntityExtractor

logger = logging.getLogger("hub.agent.memory.writer")


_EXTRACTOR = EntityExtractor()


def _safe_list(value: Any) -> list:
    """防御非 list 输入(LLM 可能返回 null / string 等)。"""
    return value if isinstance(value, list) else []


class MemoryWriter:
    """对话结束后异步触发；先 should_extract gate，再 LLM mini round 抽取写库。"""

    def __init__(self, user: UserMemoryService,
                 customer: CustomerMemoryService,
                 product: ProductMemoryService):
        self.user = user
        self.customer = customer
        self.product = product

    @staticmethod
    def should_extract(*, messages: list, rounds_count: int) -> bool:
        """重要性 gate。任一满足即抽：
          - rounds_count >= 4
          - 含 create_*/generate_* 写类 tool 调用
          - 任一 ToolMessage 返回值含 customer_id / product_id 等业务实体
        """
        if rounds_count >= 4:
            return True
        for name in extract_tool_call_names(messages):
            if name.startswith(("create_", "generate_")):
                return True
        for msg in messages:
            if msg_class_name(msg) == "ToolMessage":
                parsed = parse_tool_message_content(msg)
                if parsed and _EXTRACTOR.extract(parsed).has_any():
                    return True
        return False

    async def extract_and_write(self, *, conversation_id: str,
                                hub_user_id: int,
                                messages: list,
                                rounds_count: int,
                                ai_provider: Any) -> dict:
        """对话结束后调（asyncio.create_task）。fail-soft:抽取/写库失败仅 log。

        Returns:
            观测 stats dict:
              {input_chars, duration_ms, new_user_facts, new_customer_facts,
               new_product_facts, dedup_skipped, skip_reason?}
            caller 用来打 [MEM-STAT] 日志做数据驱动调优。
        """
        if not self.should_extract(messages=messages, rounds_count=rounds_count):
            return {"skip_reason": "gate_failed", "input_chars": 0, "duration_ms": 0}

        started = time.monotonic()
        summary = build_extraction_input(messages)
        input_chars = len(summary)

        try:
            result = await ai_provider.parse_intent(
                text=EXTRACTION_PROMPT + "\n\n" + summary,
                schema={
                    "user_facts": [
                        {"fact": "string", "kind": "string", "confidence": "float"},
                    ],
                    "customer_facts": [
                        {"customer_id": "int", "fact": "string",
                         "kind": "string", "confidence": "float"},
                    ],
                    "product_facts": [
                        {"product_id": "int", "fact": "string",
                         "kind": "string", "confidence": "float"},
                    ],
                },
            )
        except Exception:
            logger.exception(
                "MemoryWriter.extract_and_write LLM 抽取失败 conv=%s", conversation_id,
            )
            return {
                "skip_reason": "llm_failed",
                "input_chars": input_chars,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }

        try:
            counts = await self._upsert_all(hub_user_id, conversation_id, result or {})
        except Exception:
            logger.exception(
                "MemoryWriter._upsert_all 写库失败 conv=%s（不阻塞业务，待下次抽取兜底）",
                conversation_id,
            )
            return {
                "skip_reason": "db_failed",
                "input_chars": input_chars,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }

        counts.update({
            "input_chars": input_chars,
            "duration_ms": int((time.monotonic() - started) * 1000),
        })
        return counts

    async def _upsert_all(self, hub_user_id: int, conversation_id: str,
                          extraction: dict) -> dict[str, int]:
        """三层分别 upsert,返回各层新增 fact 数量。"""
        # user_facts
        u_facts = [
            f for f in _safe_list(extraction.get("user_facts"))
            if isinstance(f, dict) and f.get("confidence", 0) >= 0.6
        ]
        for f in u_facts:
            f["source_conversation"] = conversation_id
        if u_facts:
            await self.user.upsert_facts(hub_user_id, new_facts=u_facts)

        # customer_facts — confidence 过滤 + kind 保留
        c_count = 0
        for f in _safe_list(extraction.get("customer_facts")):
            cid = f.get("customer_id")
            if isinstance(cid, int) and isinstance(f, dict) and f.get("confidence", 0) >= 0.6:
                await self.customer.upsert_facts(cid, new_facts=[
                    self._build_persisted_fact(f, conversation_id),
                ])
                c_count += 1

        # product_facts — confidence 过滤 + kind 保留
        p_count = 0
        for f in _safe_list(extraction.get("product_facts")):
            pid = f.get("product_id")
            if isinstance(pid, int) and isinstance(f, dict) and f.get("confidence", 0) >= 0.6:
                await self.product.upsert_facts(pid, new_facts=[
                    self._build_persisted_fact(f, conversation_id),
                ])
                p_count += 1

        # dedup_skipped: LLM 抽出但 confidence < 0.6 被丢的数量(供观测)
        all_extracted = (
            len(_safe_list(extraction.get("user_facts")))
            + len(_safe_list(extraction.get("customer_facts")))
            + len(_safe_list(extraction.get("product_facts")))
        )
        accepted = len(u_facts) + c_count + p_count
        return {
            "new_user_facts": len(u_facts),
            "new_customer_facts": c_count,
            "new_product_facts": p_count,
            "dedup_skipped": max(0, all_extracted - accepted),
        }

    @staticmethod
    def _build_persisted_fact(extracted: dict, conversation_id: str) -> dict:
        """从 LLM 抽取的 fact dict 构造入库 dict,保留 kind 字段。

        kind 缺失时默认 "reference"(保守:不会触发 ⚠️ 警示)。
        非 reference / decision 的非法值也归一到 reference。
        """
        kind = extracted.get("kind")
        if kind not in ("reference", "decision"):
            kind = "reference"
        return {
            "fact": extracted.get("fact"),
            "kind": kind,
            "confidence": extracted.get("confidence"),
            "source_conversation": conversation_id,
        }
