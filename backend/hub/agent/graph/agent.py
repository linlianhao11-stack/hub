# backend/hub/agent/graph/agent.py
"""GraphAgent — 顶层入口，将所有子图和 router 串联成主图。

spec §2.1 thread_id 复合 key + §3 主图。

主图结构：
  START → pre_router → (条件) → router | [直接跳至 chat/query/contract/quote/voucher/
                                          adjust_price/adjust_stock/confirm]
  router → (条件边) → 8 个分支
  confirm → (条件) → commit_adjust_price | commit_adjust_stock | commit_voucher | END
  commit_* → END

P1-A v1.3 pre_router：
  - 有 pending actions + "确认"类消息 → Intent.CONFIRM
  - 有 candidate_customers/products + "选 N"/"id=N" 等 → active_subgraph 对应 intent
  - 否则 → None（走 LLM router）
"""
from __future__ import annotations

import re
from typing import Callable, Awaitable

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from hub.agent.graph.state import AgentState, Intent
from hub.agent.graph.config import build_langgraph_config
from hub.agent.graph.nodes.confirm import confirm_node
from hub.agent.graph.router import router_node
from hub.agent.graph.subgraphs.chat import chat_subgraph
from hub.agent.graph.subgraphs.query import query_subgraph
from hub.agent.graph.subgraphs.contract import build_contract_subgraph
from hub.agent.graph.subgraphs.quote import build_quote_subgraph
from hub.agent.graph.subgraphs.voucher import build_voucher_subgraph, commit_voucher_node
from hub.agent.graph.subgraphs.adjust_price import (
    build_adjust_price_subgraph,
    commit_adjust_price_node,
)
from hub.agent.graph.subgraphs.adjust_stock import (
    build_adjust_stock_subgraph,
    commit_adjust_stock_node,
)
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools.confirm_gate import ConfirmGate


# ─────────────────────────── helpers ───────────────────────────

_CONFIRM_KEYWORDS = {"确认", "是", "好的", "ok", "yes", "OK", "嗯"}

_ACTION_ID_RE = re.compile(r"(adj|vch|stk|act|qte|cnt)-[0-9a-f]{8,}", re.IGNORECASE)


def _is_confirm_message(msg: str) -> bool:
    """是确认消息：含"确认"词 / action_id 前缀 / 单个数字（从 pending list 选）。"""
    msg = msg.strip()
    if msg in _CONFIRM_KEYWORDS:
        return True
    if _ACTION_ID_RE.search(msg):
        return True
    return False


def _is_selection_message(msg: str) -> bool:
    """是候选选择消息："选 N" / 单数字 / id=N 等 — 沿用 extract_contract_context 的规则。"""
    from hub.agent.graph.nodes.extract_contract_context import _looks_like_pure_selection
    return _looks_like_pure_selection(msg)


def _subgraph_to_intent(active: str | None) -> Intent | None:
    """active_subgraph 名称 → Intent enum。"""
    _MAP = {
        "contract": Intent.CONTRACT,
        "quote": Intent.QUOTE,
        "voucher": Intent.VOUCHER,
        "adjust_price": Intent.ADJUST_PRICE,
        "adjust_stock": Intent.ADJUST_STOCK,
        "chat": Intent.CHAT,
        "query": Intent.QUERY,
    }
    return _MAP.get(active or "")


# ─────────────────────────── GraphAgent ───────────────────────────

class GraphAgent:
    """顶层 Agent 类 — 封装主 StateGraph + checkpointer + run() 接口。

    用法::

        agent = GraphAgent(llm=llm, registry=registry, confirm_gate=gate,
                           session_memory=mem, tool_executor=executor)
        response = await agent.run(
            user_message="帮我做一份合同",
            hub_user_id=42,
            conversation_id="conv-1",
        )

    也可以传入已编译好的 compiled_graph（供测试注入 fake）::

        agent = GraphAgent(compiled_graph=FakeGraph(), llm=..., ...)
    """

    def __init__(
        self,
        *,
        llm=None,
        registry: ToolRegistry | None = None,
        confirm_gate: ConfirmGate | None = None,
        session_memory=None,
        tool_executor: Callable[[str, dict], Awaitable[object]] | None = None,
        compiled_graph=None,
    ):
        self.llm = llm
        self.registry = registry
        self.confirm_gate = confirm_gate
        self.session_memory = session_memory
        self.tool_executor = tool_executor

        if compiled_graph is not None:
            self.compiled_graph = compiled_graph
        else:
            self.compiled_graph = self._build()

    # ───────────────────── graph construction ─────────────────────

    def _build(self):
        """构建主 StateGraph 并编译返回。"""
        llm = self.llm
        gate = self.confirm_gate
        registry = self.registry
        tool_executor = self.tool_executor

        # ── 子图（compiled runnables）──
        contract_sub = build_contract_subgraph(llm=llm, tool_executor=tool_executor)
        quote_sub = build_quote_subgraph(llm=llm, tool_executor=tool_executor)
        voucher_sub = build_voucher_subgraph(llm=llm, gate=gate, tool_executor=tool_executor)
        adjust_price_sub = build_adjust_price_subgraph(
            llm=llm, gate=gate, tool_executor=tool_executor
        )
        adjust_stock_sub = build_adjust_stock_subgraph(
            llm=llm, gate=gate, tool_executor=tool_executor
        )

        # ── commit_* wrappers（直接 import 函数，compiled graph 没有 get_commit_node()）──
        async def _commit_adjust_price(s: AgentState) -> AgentState:
            return await commit_adjust_price_node(s, tool_executor=tool_executor)

        async def _commit_adjust_stock(s: AgentState) -> AgentState:
            return await commit_adjust_stock_node(s, tool_executor=tool_executor)

        async def _commit_voucher(s: AgentState) -> AgentState:
            return await commit_voucher_node(s, tool_executor=tool_executor)

        # ── node wrappers（绑定依赖）──
        async def _pre_router(s: AgentState) -> AgentState:
            return await _pre_router_node(s, gate=gate)

        async def _router(s: AgentState) -> AgentState:
            return await router_node(s, llm=llm)

        async def _chat(s: AgentState) -> AgentState:
            return await chat_subgraph(s, llm=llm)

        async def _query(s: AgentState) -> AgentState:
            return await query_subgraph(
                s, llm=llm, registry=registry, tool_executor=tool_executor
            )

        async def _confirm(s: AgentState) -> AgentState:
            return await confirm_node(s, gate=gate)

        # ── StateGraph 构建 ──
        g: StateGraph = StateGraph(AgentState)

        # 节点注册
        g.add_node("pre_router", _pre_router)
        g.add_node("router", _router)
        g.add_node("chat", _chat)
        g.add_node("query", _query)
        g.add_node("contract", contract_sub)
        g.add_node("quote", quote_sub)
        g.add_node("voucher", voucher_sub)
        g.add_node("adjust_price", adjust_price_sub)
        g.add_node("adjust_stock", adjust_stock_sub)
        g.add_node("confirm", _confirm)
        g.add_node("commit_adjust_price", _commit_adjust_price)
        g.add_node("commit_adjust_stock", _commit_adjust_stock)
        g.add_node("commit_voucher", _commit_voucher)

        # 入口 → pre_router
        g.add_edge(START, "pre_router")

        # pre_router → (条件)：已确定 intent 跳子图；未确定 → router
        g.add_conditional_edges(
            "pre_router",
            _pre_router_route,
            {
                "router": "router",
                "chat": "chat",
                "query": "query",
                "contract": "contract",
                "quote": "quote",
                "voucher": "voucher",
                "adjust_price": "adjust_price",
                "adjust_stock": "adjust_stock",
                "confirm": "confirm",
            },
        )

        # router → 8 个目标（条件边）
        g.add_conditional_edges(
            "router",
            _router_route,
            {
                "chat": "chat",
                "query": "query",
                "contract": "contract",
                "quote": "quote",
                "voucher": "voucher",
                "adjust_price": "adjust_price",
                "adjust_stock": "adjust_stock",
                "confirm": "confirm",
            },
        )

        # 子图 → END
        for node in ("chat", "query", "contract", "quote", "voucher",
                     "adjust_price", "adjust_stock"):
            g.add_edge(node, END)

        # confirm → commit 分支 / END
        g.add_conditional_edges(
            "confirm",
            _confirm_route,
            {
                "commit_adjust_price": "commit_adjust_price",
                "commit_adjust_stock": "commit_adjust_stock",
                "commit_voucher": "commit_voucher",
                END: END,
            },
        )

        # commit → END
        g.add_edge("commit_adjust_price", END)
        g.add_edge("commit_adjust_stock", END)
        g.add_edge("commit_voucher", END)

        checkpointer = MemorySaver()
        return g.compile(checkpointer=checkpointer)

    # ───────────────────── public interface ─────────────────────

    async def run(
        self,
        *,
        user_message: str,
        hub_user_id: int,
        conversation_id: str,
        acting_as: int | None = None,
        channel_userid: str | None = None,
    ) -> str | None:
        """执行一轮对话。返回 state.final_response（或 None）。

        spec §2.1：LangGraph config thread_id = f"{conversation_id}:{hub_user_id}"
        """
        # P1-A v1.5 update_payload：只传本轮新字段，让 LangGraph checkpoint
        # 从上一轮 hydrate 跨轮状态（不强写 [] / {} 默认值覆盖）。
        # review issue 4：上一轮的输出字段（intent / final_response / file_sent /
        # confirmed_* / errors）必须显式 reset，避免跨轮 hydrate 污染本轮判定，
        # 比如普通聊天显示 file_sent=True、上轮 confirmed_payload 残留导致路由误判。
        update_payload: dict = {
            "user_message": user_message,
            "hub_user_id": hub_user_id,
            "conversation_id": conversation_id,
            "intent": None,
            "final_response": None,
            "errors": [],
            "file_sent": False,
            "confirmed_subgraph": None,
            "confirmed_action_id": None,
            "confirmed_payload": None,
        }
        if acting_as is not None:
            update_payload["acting_as"] = acting_as
        if channel_userid is not None:
            update_payload["channel_userid"] = channel_userid

        config = build_langgraph_config(
            conversation_id=conversation_id,
            hub_user_id=hub_user_id,
        )
        result = await self.compiled_graph.ainvoke(update_payload, config=config)
        if isinstance(result, dict):
            return result.get("final_response")
        # Pydantic model（LangGraph 有时返回 state schema 实例）
        return getattr(result, "final_response", None)


# ─────────────────────────── routing functions ───────────────────────────

async def _pre_router_node(state: AgentState, *, gate: ConfirmGate | None) -> AgentState:
    """P1-A v1.3 pre_router：基于上下文 + 消息类型直接判 intent，跳过 LLM。

    优先级（review issue 1 修正：pending 命中时纯数字 / 选 N / 第 N 个 / id=N 也算 CONFIRM）：
    1. 有 pending actions + 确认/选择类消息（"确认" / 数字 / "选 N" / "第 N 个" / id=N / action_id 前缀）
       → Intent.CONFIRM。confirm_node 内部会按编号匹配 pending list。
    2. 候选 + 选择词（仅当无 pending 时；pending 优先）
       → 上轮活跃子图的 intent
    3. 否则 intent = None → 交给 LLM router
    """
    msg = state.user_message

    # 1. pending + 确认/选择类消息：pending 优先于 candidate 路由
    #    （review issue 1：用户回 "1" 选第 1 个 pending 必须能进 confirm_node）
    if gate is not None and (_is_confirm_message(msg) or _is_selection_message(msg)):
        try:
            pendings = await gate.list_pending_for_context(
                conversation_id=state.conversation_id,
                hub_user_id=state.hub_user_id,
            )
            if pendings:
                state.intent = Intent.CONFIRM
                return state
        except Exception:
            pass  # 查 pending 失败 → 兜底走下一档（candidate / LLM）

    # 2. 候选 + 选择消息（pending 已检过；走到这里说明无 pending）
    if _is_selection_message(msg):
        if state.candidate_customers or state.candidate_products:
            intent = _subgraph_to_intent(state.active_subgraph)
            if intent is not None:
                state.intent = intent
                return state

    # 3. intent = None → 走 LLM router
    state.intent = None
    return state


def _pre_router_route(state: AgentState) -> str:
    """pre_router 出口路由：intent 已知 → 直跳子图；None → router。"""
    if state.intent is None:
        return "router"
    return _intent_to_node(state.intent)


def _router_route(state: AgentState) -> str:
    """LLM router 出口路由。"""
    return _intent_to_node(state.intent or Intent.CHAT)


def _confirm_route(state: AgentState) -> str:
    """confirm 节点出口：据 confirmed_subgraph 路由到对应 commit；否则 END。"""
    if state.confirmed_subgraph == "adjust_price":
        return "commit_adjust_price"
    if state.confirmed_subgraph == "adjust_stock":
        return "commit_adjust_stock"
    if state.confirmed_subgraph == "voucher":
        return "commit_voucher"
    return END


def _intent_to_node(intent: Intent) -> str:
    """Intent → 节点名映射。"""
    _MAP = {
        Intent.CHAT: "chat",
        Intent.QUERY: "query",
        Intent.CONTRACT: "contract",
        Intent.QUOTE: "quote",
        Intent.VOUCHER: "voucher",
        Intent.ADJUST_PRICE: "adjust_price",
        Intent.ADJUST_STOCK: "adjust_stock",
        Intent.CONFIRM: "confirm",
        Intent.UNKNOWN: "chat",
    }
    return _MAP.get(intent, "chat")
