"""Plan 6 Task 6：ContextBuilder — 调用前 token 估算 + 裁剪。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from hub.agent.memory.types import Memory
from hub.agent.prompt.builder import PromptBuilder
from hub.agent.types import PromptTooLargeError

logger = logging.getLogger("hub.agent.context_builder")

_ENCODER = None
_ENCODER_FAILED = False


def _get_encoder():
    """懒加载 tiktoken encoder（与 memory.loader 模式一致）。"""
    global _ENCODER, _ENCODER_FAILED
    if _ENCODER is not None or _ENCODER_FAILED:
        return _ENCODER
    try:
        import tiktoken
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    except ImportError:
        _ENCODER_FAILED = True
        logger.warning("tiktoken 未装，token 估算 fallback（中文偏低 3x）")
    return _ENCODER


def _estimate_tokens(text: str) -> int:
    """与 memory.loader 一致的中文优化 fallback。"""
    enc = _get_encoder()
    if enc is None:
        cjk = sum(1 for c in text if ord(c) >= 0x3000)
        ascii_count = len(text) - cjk
        return int(cjk / 1.5 + ascii_count / 4)
    return len(enc.encode(text))


@dataclass
class Section:
    name: str
    content: Any
    tokens: int


class ContextBuilder:
    """每 round 调 LLM 前估算 + 裁剪上下文。"""

    DEFAULT_BUDGET = 18_000

    def __init__(self, prompt_builder: PromptBuilder | None = None,
                 budget_token: int = DEFAULT_BUDGET):
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.budget = budget_token

    async def build_round(self, *,
                          round_idx: int,
                          base_memory: Memory,
                          tools_schema: list[dict],
                          conversation_history: list[dict],
                          latest_user_message: str | None,
                          confirm_state_hint: str | None = None,
                          round_state: dict | None = None,
                          budget_token: int | None = None) -> list[dict]:
        """组装本 round 的 OpenAI messages（已裁剪到 budget 内）。

        Plan §1944 列 priority 7 的 entity_memory（customers + products）section，
        本实现简化为：customer/product memory 由 PromptBuilder._render_memory()
        在 build system_prompt 阶段已经渲染进 system 段，本层不再单独装填。
        避免重复注入 prompt。
        """
        budget = budget_token if budget_token is not None else self.budget

        # ❶ MUST_KEEP
        must_keep: list[Section] = []

        # system_prompt（含业务词典 + 同义词 + few-shots + memory + 行为准则）
        sp = self.prompt_builder.build(memory=base_memory, tools_schema=tools_schema)
        must_keep.append(self._mk_section("system_prompt", sp))

        # v2 加固（review I-2）：tools_schema 也计入 budget
        # OpenAI chat completions 把 tools= 字段当 input token 算
        if tools_schema:
            tools_json = json.dumps(tools_schema, ensure_ascii=False)
            must_keep.append(self._mk_section("tools_schema_estimate", tools_json))

        if latest_user_message:
            must_keep.append(self._mk_section("user_msg", latest_user_message))

        # 最近 1 round（边界感知切片：确保 OpenAI 协议完整）
        recent = self._slice_recent_round(conversation_history)
        if recent:
            must_keep.append(self._mk_section("recent_round", recent))

        if confirm_state_hint:
            must_keep.append(self._mk_section("confirm_hint", confirm_state_hint))

        # v8 staging review #13：跨轮 entity state 摘要
        # 上一轮 ChainAgent 写入 Redis 的 round_state（含 customers_seen / products_seen
        # / last_intent），这一轮注入 must_keep 让 LLM 看到上轮已确认的 ID + 价格 + 数量，
        # 避免"按之前要求做"时 LLM 重新搜 / 失忆。
        if round_state:
            state_text = self._format_round_state(round_state)
            if state_text:
                must_keep.append(self._mk_section("round_state", state_text))

        must_tokens = sum(s.tokens for s in must_keep)
        if must_tokens > budget:
            raise PromptTooLargeError(
                f"必保上下文 {must_tokens} token 已超 budget {budget}；"
                "可能是 system_prompt + tool schema 太大或 confirm_hint 太长。"
                "建议减少 tool 数量或裁剪 user_msg。"
            )

        # ❷ CAN_TRUNCATE（按优先级降序装填）
        remaining = budget - must_tokens
        candidates: list[tuple[int, Section]] = []

        # 优先级 5：3 round 之前的 tool result 摘要
        old_results = self._summarize_old_tool_results(conversation_history[:-2])
        if old_results:
            candidates.append((5, self._mk_section("old_results_summary", old_results)))

        # 优先级 2：4 round 之前对话历史压缩
        old_history = self._summarize_old_history(conversation_history[:-4])
        if old_history:
            candidates.append((2, self._mk_section("old_history_summary", old_history)))

        kept: list[Section] = []
        for _, sec in sorted(candidates, key=lambda x: -x[0]):
            if sec.tokens <= remaining:
                kept.append(sec)
                remaining -= sec.tokens

        return self._compose_messages(must_keep + kept)

    @staticmethod
    def _mk_section(name: str, content: Any) -> Section:
        return Section(name=name, content=content, tokens=ContextBuilder._count_tokens(content))

    @staticmethod
    def _count_tokens(content: Any) -> int:
        if isinstance(content, str):
            return _estimate_tokens(content)
        if isinstance(content, list):
            total = 0
            for m in content:
                if isinstance(m, dict):
                    # v2 加固（review M-3）：assistant tool_calls 消息 content=None，
                    # 但 tool_calls 字段含 function name + args 占 token
                    content_str = str(m.get("content") or "")
                    tool_calls_str = json.dumps(m.get("tool_calls") or [], ensure_ascii=False)
                    total += _estimate_tokens(content_str) + _estimate_tokens(tool_calls_str)
                else:
                    total += _estimate_tokens(str(m))
            return total
        return _estimate_tokens(str(content))

    @staticmethod
    def _slice_recent_round(history: list[dict]) -> list[dict]:
        """从 history 末尾向前找最近一个 user/assistant 边界，含进去。

        v2 加固（review M-2）：确保 OpenAI 协议完整：
        tool 消息必须紧跟 assistant.tool_calls 消息。
        多 tool_calls round 时，原 [-2:] 切片可能截到孤儿 tool 消息。
        本方法从末尾反向扫，遇到 user 停止，保证完整 round 边界。

        v3 加固（v8 staging review）：单 tool 消息 > 4000 token 时摘要化。
        防 must_keep 因为前一轮某个 tool 返了 14K 数据就直接撞 budget——
        老 round 已经摘要了，最近 round 也得摘要（保 keys + 数量，不保整 list）。

        v4 加固（v8 staging review #3）：assistant 消息 > 3000 token 时也截断。
        实际场景：用户连续聊了 30 条，前面几条是 markdown 表格 14K-token 巨响应，
        累积在 SessionMemory 里——下一轮哪怕用户只输入"查最新订单"，recent_round
        把整个上一轮 assistant 含进来 → 直接撞 32K budget。
        截断到首 600 char + "[...截断]" 提示，让 LLM 知道有过去回复但不展开。
        """
        if not history:
            return []
        out: list[dict] = []
        for msg in reversed(history):
            if not isinstance(msg, dict):
                out.insert(0, msg)
                continue
            out.insert(0, msg)
            role = msg.get("role")
            if role == "user":
                break

        # 摘要 / 截断巨大消息（防 must_keep 撞 budget）
        for i, msg in enumerate(out):
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content", "") or ""
            tokens = _estimate_tokens(str(content))

            if role == "tool" and tokens > 4000:
                # tool 结果用 summarize_dict 保留 keys + 数量
                summary = ContextBuilder._summarize_dict(content)
                tool_name = msg.get("name") or msg.get("tool_name", "?")
                new_content = (
                    f"[本 tool 返回过大已自动摘要：{tool_name}: {summary}。"
                    f"如需完整数据请重新调用 tool 并加 limit / 过滤参数缩小范围]"
                )
                out[i] = {**msg, "content": new_content}
            elif role == "assistant" and tokens > 3000:
                # assistant 文本截断到首 600 char（保对话连贯性，不保细节）
                content_str = content if isinstance(content, str) else str(content)
                head = content_str[:600]
                new_content = (
                    f"{head}…[此回复较长已截断 {tokens} token → 600 字]"
                )
                out[i] = {**msg, "content": new_content}
        return out

    @staticmethod
    def _summarize_old_tool_results(history: list[dict]) -> str:
        lines = []
        for msg in history:
            if not isinstance(msg, dict) or msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            tokens = _estimate_tokens(str(content))
            tool_name = msg.get("name") or msg.get("tool_name", "?")
            if tokens > 500:
                summary = ContextBuilder._summarize_dict(content)
                lines.append(f"[round-{msg.get('round_idx', '?')}] {tool_name}: {summary}")
            else:
                content_str = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                lines.append(f"[round-{msg.get('round_idx', '?')}] {tool_name}: {content_str[:200]}")
        return "\n".join(lines)

    @staticmethod
    def _summarize_dict(content: Any) -> str:
        try:
            data = json.loads(content) if isinstance(content, str) else content
            if isinstance(data, dict) and "items" in data:
                items = data.get("items") or []
                if items and isinstance(items, list):
                    keys = list(items[0].keys()) if isinstance(items[0], dict) else []
                    return f"{len(items)} items, fields={keys[:6]}"
            return f"{type(data).__name__}, len={len(data) if hasattr(data, '__len__') else '?'}"
        except Exception:
            return "(摘要失败)"

    @staticmethod
    def _summarize_old_history(history: list[dict]) -> str:
        lines = []
        for msg in history:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "?")
            tool_name = msg.get("tool_name") or msg.get("name")
            if role == "tool" and tool_name:
                lines.append(f"调了 {tool_name}")
            elif role == "user":
                content = msg.get("content", "")
                lines.append(f"用户: {str(content)[:50]}")
        return " → ".join(lines)

    @staticmethod
    def _compose_messages(sections: list[Section]) -> list[dict]:
        messages: list[dict] = []
        for s in sections:
            if s.name == "tools_schema_estimate":
                # v2 加固（review I-2）：仅用于 budget 估算，tools 真正透传给 LLM 走 chat(tools=...) 参数
                continue
            elif s.name in ("system_prompt", "confirm_hint"):
                messages.append({"role": "system", "content": str(s.content)})
            elif s.name == "user_msg":
                messages.append({"role": "user", "content": str(s.content)})
            elif s.name == "recent_round" and isinstance(s.content, list):
                messages.extend(s.content)
            elif s.name in ("old_results_summary", "old_history_summary", "round_state"):
                if s.content:
                    messages.append({
                        "role": "system",
                        "content": f"[{s.name}]\n{s.content}",
                    })
            else:
                messages.append({"role": "system", "content": f"[{s.name}]\n{s.content}"})
        return messages

    @staticmethod
    def _format_round_state(state: dict) -> str:
        """v8 staging review #13：把 round_state dict 格式化成 LLM 可读文本。

        例：
          [上一轮已确认实体]
          客户: 北京翼蓝科技发展有限公司 (id=7)
          已搜到的商品（请用这些 ID，不要重新搜也不要编造）:
            - 科大讯飞智能办公本X5 Pro经典黑 (id=5030, sku=SKU50139)
            - 讯飞 AI 翻译耳机 AIH-2541 (id=5032)
          上轮已准备的写操作（用户已大致确认）:
            tool=generate_contract_draft
            customer_id=7, items=[(5030, qty=20, price=3900), (5032, qty=6, price=2000)]
        """
        if not isinstance(state, dict) or not state:
            return ""

        lines: list[str] = []
        custs = state.get("customers_seen") or []
        prods = state.get("products_seen") or []
        intent = state.get("last_intent") or {}

        if custs:
            lines.append(
                "上轮 search_customers 搜到的客户（仅当用户**当前消息提到**这个客户时"
                "用其 id；用户没提到的不要凭印象套用）:"
            )
            for c in custs:
                if not isinstance(c, dict):
                    continue
                line = f"  - {c.get('name', '')} (id={c.get('id')}"
                if c.get("phone"):
                    line += f", phone={c['phone']}"
                line += ")"
                lines.append(line)

        if prods:
            lines.append(
                "上轮 search_products 搜到的商品（**仅当用户当前消息提到这个商品**"
                "时才用其 id；不要把下面所有商品都加进 generate/create tool 调用——"
                "这是参考清单不是任务清单）:"
            )
            for p in prods:
                if not isinstance(p, dict):
                    continue
                line = f"  - {p.get('name', '')} (id={p.get('id')}"
                if p.get("sku"):
                    line += f", sku={p['sku']}"
                if p.get("color"):
                    line += f", color={p['color']}"
                line += ")"
                lines.append(line)

        if intent and isinstance(intent, dict):
            tool = intent.get("tool", "")
            args = intent.get("args") or {}
            if tool and args:
                lines.append("上轮已发起的写操作意图（用户基本确认中）:")
                lines.append(f"  tool: {tool}")
                cid = args.get("customer_id")
                if cid:
                    lines.append(f"  customer_id: {cid}")
                items = args.get("items") or []
                if items and isinstance(items, list):
                    lines.append("  items:")
                    for it in items:
                        if isinstance(it, dict):
                            pid = it.get("product_id")
                            qty = it.get("qty")
                            price = it.get("price")
                            lines.append(f"    - product_id={pid}, qty={qty}, price={price}")

        if not lines:
            return ""
        return "\n".join(lines)
