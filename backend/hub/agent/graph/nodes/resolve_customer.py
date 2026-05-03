# backend/hub/agent/graph/nodes/resolve_customer.py
"""resolve_customer — 强制调 search_customers，把结果写入 state.customer。"""
from __future__ import annotations
import json
import re
from typing import Awaitable, Callable
from hub.agent.graph.state import ContractState, CustomerInfo
from hub.agent.llm_client import DeepSeekLLMClient, ToolClass, disable_thinking
from hub.agent.tools.erp_tools import SEARCH_CUSTOMERS_SCHEMA


RESOLVE_CUSTOMER_PROMPT = """根据用户消息找客户。强制调 search_customers，参数用提取到的客户名 / 关键词。
若用户没明确客户，留 query 字段为关键词候选。
"""


def _generate_fallback_hints(hint: str) -> list[str]:
    """ERP `customers.name ILIKE '%hint%'` 子串不匹配时的备选短 hint。

    场景：用户输 "广州得帆"，ERP 客户全名是 "广州市得帆计算机科技有限公司"，
    "广州得帆" 不是子串（中间隔了 "市"），但 "得帆" 是子串能命中。

    策略：仅对 ≥3 字的纯 CJK hint 生成 4 种切短候选（按命中概率从高到低）：
      1. 去前 2 字 "广州得帆" → "得帆"（去 2 字地名）
      2. 去前 3 字 "广州市得帆" → "得帆"（去 3 字地名）
      3. 后 3 字（保留主体后段）
      4. 后 2 字
    含数字/英文的 hint（如"X1 系列"）不切，避免误命中。
    """
    if not hint or len(hint) < 3:
        return []
    # 仅对纯 CJK 段处理
    if not re.fullmatch(r"[一-鿿]+", hint):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for cand in (hint[2:], hint[3:], hint[-3:], hint[-2:]):
        if cand and cand != hint and cand not in seen and len(cand) >= 2:
            seen.add(cand)
            out.append(cand)
    return out


async def _search_with_fallback(
    args: dict,
    tool_executor: Callable[[str, dict], Awaitable[object]],
) -> tuple[list, str]:
    """search_customers 0 命中时按 _generate_fallback_hints 重搜，返回 (results, used_hint)。"""
    results = await tool_executor("search_customers", args)
    if isinstance(results, dict):
        results = results.get("items", [])
    if results:
        return results, args.get("query", "")
    # 0 命中 → fallback
    for fb_hint in _generate_fallback_hints(args.get("query") or ""):
        fb_results = await tool_executor("search_customers", {"query": fb_hint})
        if isinstance(fb_results, dict):
            fb_results = fb_results.get("items", [])
        if fb_results:
            return fb_results, fb_hint
    return [], args.get("query", "")


def _try_consume_customer_selection(message: str, candidates: list) -> "CustomerInfo | None":
    """P2-C v1.2 / P1-B v1.5：识别"选 N" / "1" / "id=10" / 直接说客户名 → 消费 candidate。"""
    if not candidates:
        return None
    msg = message.strip()
    # 1. 编号选择 — 优先匹配"选 N"前缀，其次裸数字，再次"第几"
    import re
    m = (re.search(r"选\s*([1-9])", msg)
         or re.search(r"\b([1-9])\b", msg)
         or re.search(r"第\s*([一二三四五六七八九])", msg))
    if m:
        token = m.group(1)
        # P2-B v1.6：dict.get(key, default) 的 default 是**提前求值**的；用户回"第二个"时
        # int("二") 会先抛 ValueError 而非走 digit_map 分支。必须显式分支。
        digit_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                      "六": 6, "七": 7, "八": 8, "九": 9}
        if token.isdigit():
            num = int(token)
        else:
            num = digit_map.get(token, 0)
        if 1 <= num <= len(candidates):
            return candidates[num - 1]
    # 2. id 显式 "id=10" / "id 10"
    m = re.search(r"id\s*[=:：]?\s*(\d+)", msg, re.IGNORECASE)
    if m:
        target = int(m.group(1))
        for c in candidates:
            if c.id == target:
                return c
    # 3. 用户直接说候选里某个名字（精确包含匹配）
    for c in candidates:
        if c.name and c.name in msg:
            return c
    return None


async def resolve_customer_node(
    state: ContractState,
    *,
    llm: DeepSeekLLMClient,
    tool_executor: Callable[[str, dict], Awaitable[object]],
) -> ContractState:
    """三分支处理 — unique 写 state.customer / multi 写 candidate_customers / none 加 missing_fields。

    P2-C v1.2 候选选择闭环：上轮 checkpoint 留下了 candidate_customers，
    本轮 user_message 是"选 1"/"id=10" → 直接消费候选写 state.customer，不再调 search_customers。
    """
    if state.customer:  # 已经解析过（多轮场景）
        return state

    # P2-C：上轮多命中候选 + 当前消息是选择 → 直接消费
    if state.candidate_customers:
        chosen = _try_consume_customer_selection(state.user_message, state.candidate_customers)
        if chosen:
            state.customer = chosen
            state.candidate_customers = []  # 清空，避免下轮再触发
            state.missing_fields = [
                m for m in state.missing_fields if m != "customer_choice"
            ]
            return state
        # 候选还在但用户没说编号 → 留 candidate_customers，让 ask_user 再列一次
        return state

    resp = await llm.chat(
        messages=[
            {"role": "system", "content": RESOLVE_CUSTOMER_PROMPT},
            {"role": "user", "content": f"消息：{state.user_message}\nhint: {state.extracted_hints.get('customer_name', '')}"},
        ],
        tools=[SEARCH_CUSTOMERS_SCHEMA],
        tool_choice={"type": "function", "function": {"name": "search_customers"}},
        thinking=disable_thinking(),
        temperature=0.0,
        tool_class=ToolClass.READ,
    )
    if not resp.tool_calls:
        # 整字段替换 — 避免 LangGraph model_fields_set 陷阱
        state.errors = list(state.errors) + ["resolve_customer_no_tool_call"]
        state.missing_fields = list(state.missing_fields) + ["customer"]
        return state
    args = json.loads(resp.tool_calls[0]["function"]["arguments"])
    # 0 命中时自动按拆词 fallback 重搜（"广州得帆" → "得帆" 命中"广州市得帆..."）
    results, used_hint = await _search_with_fallback(args, tool_executor)

    # P1-B 三分支：合同/报价是对外文件，错客户比反问严重得多
    if len(results) == 0:
        # none — 让 ask_user 问"哪个客户"
        if "customer" not in state.missing_fields:
            state.missing_fields = list(state.missing_fields) + ["customer"]
        return state
    if len(results) == 1:
        # unique — 写 state.customer（字段赋值，model_fields_set 会记录）
        c = results[0]
        state.customer = CustomerInfo(
            id=c["id"], name=c["name"],
            address=c.get("address"), tax_id=c.get("tax_id"), phone=c.get("phone"),
        )
        return state
    # multi — 写候选列表 + missing_fields，让下游 ask_user 列出来
    # 整字段替换 — 避免 LangGraph model_fields_set 陷阱
    state.candidate_customers = [
        CustomerInfo(id=c["id"], name=c["name"], address=c.get("address"),
                      tax_id=c.get("tax_id"), phone=c.get("phone"))
        for c in results
    ]
    if "customer_choice" not in state.missing_fields:
        state.missing_fields = list(state.missing_fields) + ["customer_choice"]
    # P1-A v1.6：写候选时一并标记来源子图，pre_router 据此路由"选 N"回正确子图
    # contract 子图调本节点 → contract；quote 子图调本节点 → quote。
    # 该字段由调用方在传入 state 时已经设好（contract_subgraph 和 quote_subgraph 入口都先写 state.active_subgraph）。
    # 这里不写就是为了让 contract/quote 共用本节点。
    return state
