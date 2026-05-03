"""GraphAgent state schemas — Pydantic typed，跨节点 / 跨子图共享。

# 设计：单一 root state schema（v9 重构）

历史问题：以前每个子图有自己的 State 类（ContractState/QuoteState/AdjustPriceState/...），
父图用 AgentState。LangGraph 0.2.x 嵌套 compiled subgraph 时,父子 Pydantic schema
不同会触发 channel filtering — Optional 嵌套字段（如 `customer: CustomerInfo | None = None`）
在 model_validate 时走默认值 None,**导致父图刚 hydrate 的 customer 进子图就丢了**。
钉钉实测看到："上一轮明明问到客户,这一轮要么 bot 失忆问'还差客户',要么走错 intent"。

修复：所有"子图 State 类"都是 `AgentState` 的 type alias —— ContractState IS AgentState
(同一个 class object)。父图 StateGraph(AgentState) 和子图 StateGraph(ContractState)
拿的是同一个 schema class,LangGraph 内部不做 schema 边界 filtering,字段贯通。

子图业务专属字段（adjust_price 的 new_price / adjust_stock 的 delta_qty / voucher
的 order_id 等）也都提到 AgentState ——多几个 None 字段不增加多少认知成本,
换来 LangGraph state 一致性。

PostgresSaver checkpoint 表也是按 channel 名存,字段在父 schema 声明就一定能存,
跨 worker 重启 hydrate 也保住。
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class Intent(str, Enum):
    """Router 意图分类。value 必须 lowercase — router_node 用 Intent(value) 解析。"""
    CHAT = "chat"
    QUERY = "query"
    CONTRACT = "contract"
    QUOTE = "quote"
    VOUCHER = "voucher"
    ADJUST_PRICE = "adjust_price"
    ADJUST_STOCK = "adjust_stock"
    CONFIRM = "confirm"
    UNKNOWN = "unknown"


class CustomerInfo(BaseModel):
    id: int
    name: str
    address: str | None = None
    tax_id: str | None = None
    phone: str | None = None


class ProductInfo(BaseModel):
    id: int
    name: str
    sku: str | None = None
    color: str | None = None
    spec: str | None = None
    list_price: Decimal | None = None


class ContractItem(BaseModel):
    product_id: int
    name: str
    qty: int
    price: Decimal


class ShippingInfo(BaseModel):
    address: str | None = None
    contact: str | None = None
    phone: str | None = None


class AgentState(BaseModel):
    """所有节点 / 子图共享的唯一 state schema（v9 单一 root state）。

    LangGraph 嵌套子图 schema 边界问题：当父图 StateGraph(AgentState) 和子图
    StateGraph(ContractState) 用不同 Pydantic 类时,channel filtering 会让
    `customer: CustomerInfo | None = None` 等 Optional 嵌套字段在子图入口
    走默认值 None,父图 hydrate 的值传不到子图。把所有字段塞同一个 class,
    全部子类降级为 AgentState 的 alias —— 父子图 schema 完全一致,LangGraph
    不再做边界过滤。
    """
    # ───── 入口 / 元数据 ─────
    user_message: str
    hub_user_id: int
    conversation_id: str
    acting_as: int | None = None
    channel_userid: str | None = None

    # ───── 路由结果 / 出口 ─────
    intent: Intent | None = None
    final_response: str | None = None
    file_sent: bool = False
    errors: list[str] = Field(default_factory=list)

    # ───── confirm 链路（v1.2 P1-A）─────
    confirmed_subgraph: str | None = None       # e.g. "adjust_price"
    confirmed_action_id: str | None = None      # 完整 32-hex
    confirmed_payload: dict | None = None       # canonical {tool_name, args}

    # active_subgraph：候选/缺字段时记当前子图,pre_router 路由"选 N"用。
    # 写候选时一并写,候选清空时一并清。
    active_subgraph: str | None = None  # "contract" / "quote" / "voucher" / ...

    # ───── 跨轮工作字段（contract / quote / adjust_* / voucher 共用）─────
    extracted_hints: dict = Field(default_factory=dict)
    customer: CustomerInfo | None = None
    candidate_customers: list[CustomerInfo] = Field(default_factory=list)
    products: list[ProductInfo] = Field(default_factory=list)
    candidate_products: dict[str, list[ProductInfo]] = Field(default_factory=dict)
    items: list[ContractItem] = Field(default_factory=list)
    shipping: ShippingInfo = Field(default_factory=ShippingInfo)
    missing_fields: list[str] = Field(default_factory=list)

    # ───── 子图业务字段（v9 全部提到父类）─────
    # adjust_price / adjust_stock 用 product 而非 products[](单值)
    product: ProductInfo | None = None
    # adjust_price
    old_price: Decimal | None = None
    new_price: Decimal | None = None
    history_prices: list[Decimal] = Field(default_factory=list)
    # adjust_stock
    delta_qty: int | None = None
    reason: str | None = None  # 库存调整原因（仅 adjust_stock 用）
    # voucher
    order_id: int | None = None
    voucher_type: str | None = None  # "outbound" / "inbound"
    voucher_id: int | None = None
    # confirm 链路通用 — adjust_price/adjust_stock/voucher 都用 pending_action_id 跟 ConfirmGate 关联
    pending_action_id: str | None = None
    # 合同 / 报价 生成结果（output id）
    draft_id: int | None = None
    quote_id: int | None = None


# ─────────────────────────── alias（v9 单一 root state）───────────────────────────
# 所有"子图 State 类"都是 AgentState 同一个 class。原 import / 类型签名 / StateGraph(...)
# 一行都不用改 —— 看起来还像独立类型,实际背后是同一个 Pydantic schema,父子图共享
# 不再触发 LangGraph schema 边界过滤。
ContractState = AgentState
QuoteState = AgentState
AdjustPriceState = AgentState
AdjustStockState = AgentState
VoucherState = AgentState
