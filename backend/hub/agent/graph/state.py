"""GraphAgent state schemas — Pydantic typed，跨节点 / 跨子图共享。"""
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
    """所有子图共享的 state。

    P1-A v1.4 关键：跨轮选择字段（candidate_customers / candidate_products / customer / products / items）
    **必须**在父 AgentState 上声明 —— LangGraph StateGraph 用父 schema 做 checkpoint，
    子图返回的字段如果父 schema 没有 → 不会写入父 checkpoint → 上一轮的 candidate 会丢，
    pre_router 永远 peek 不到，"选 1" 就回不到 contract 子图。

    子图 (ContractState/QuoteState 等) 仍可继承加自己专属字段（如 draft_id），
    但**所有"下一轮可能用得上"的跨轮字段**都在 AgentState 上。
    """
    user_message: str
    hub_user_id: int
    conversation_id: str
    acting_as: int | None = None
    channel_userid: str | None = None

    intent: Intent | None = None
    final_response: str | None = None
    file_sent: bool = False
    errors: list[str] = Field(default_factory=list)

    # confirm 链路（v1.2 P1-A）
    confirmed_subgraph: str | None = None       # e.g. "adjust_price"
    confirmed_action_id: str | None = None      # 完整 32-hex
    confirmed_payload: dict | None = None       # canonical {tool_name, args}

    # P1-A v1.6：active_subgraph 持久化候选来源 — 不能用 intent 判，run() 每轮都把 intent reset 成 None。
    # 当 quote 流程留下 candidate_customers/products 时，下一轮"选 2"必须回 quote，不是兜底 contract。
    active_subgraph: str | None = None  # "contract" / "quote"，写候选时一并写；候选清空时一并清

    # 跨轮选择字段（v1.4 P1-A 提升 / v1.5 P1-C 把 shipping 也加进来）
    # 用户可能在第 1 轮就把地址给齐了，第 2 轮选候选客户/产品时父图 checkpoint
    # 必须保留 shipping，否则 validate_inputs 会重新问地址。
    extracted_hints: dict = Field(default_factory=dict)
    customer: CustomerInfo | None = None
    candidate_customers: list[CustomerInfo] = Field(default_factory=list)
    products: list[ProductInfo] = Field(default_factory=list)
    candidate_products: dict[str, list[ProductInfo]] = Field(default_factory=dict)
    items: list[ContractItem] = Field(default_factory=list)
    shipping: ShippingInfo = Field(default_factory=ShippingInfo)  # ← v1.5 P1-C 提升
    missing_fields: list[str] = Field(default_factory=list)

    # P2-A v1.8：draft_id / quote_id 也提升到 AgentState — 否则跑完合同/报价后
    # 父图 snapshot 拿不到 ID（同 v1.4 candidate_* 教训：父 schema 不含 → checkpoint 不存）。
    # eval driver / 端到端测试都从父图 snapshot 读 draft_id 验合同生成。
    draft_id: int | None = None
    quote_id: int | None = None


class ContractState(AgentState):
    """contract_subgraph state — 全部跨轮字段都在父类，子类无业务专属字段。
    保留这个类是为了类型签名清晰（contract 子图节点接收的是 ContractState 而非裸 AgentState）。"""
    pass


class QuoteState(AgentState):
    """quote_subgraph state — 同上，结构上和 ContractState 等价。"""
    pass


class AdjustPriceState(AgentState):
    """adjust_price_subgraph state."""
    extracted_hints: dict = Field(default_factory=dict)
    customer: CustomerInfo | None = None
    product: ProductInfo | None = None
    old_price: Decimal | None = None
    new_price: Decimal | None = None
    history_prices: list[Decimal] = Field(default_factory=list)
    pending_action_id: str | None = None


class AdjustStockState(AgentState):
    """adjust_stock_subgraph state."""
    extracted_hints: dict = Field(default_factory=dict)
    product: ProductInfo | None = None
    delta_qty: int | None = None
    reason: str | None = None
    pending_action_id: str | None = None


class VoucherState(AgentState):
    """voucher_subgraph state — 出库 / 入库凭证。

    P1-B v1.4：voucher_type 必须从用户消息 / extracted_hints 解析，**不能**硬编码 outbound。
    """
    order_id: int | None = None
    voucher_type: str | None = None  # "outbound" / "inbound"，必填（preview 之前一定要有值）
    voucher_id: int | None = None
    pending_action_id: str | None = None
