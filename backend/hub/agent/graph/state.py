"""HUB 业务数据类（v10：仅 react tools 内部用,无 LangGraph state schema）。

**v9 → v10 删除内容**：
  - AgentState 主 Pydantic 类（30+ 业务字段）
  - ContractState / QuoteState / VoucherState / AdjustPriceState / AdjustStockState /
    ChatState / QueryState 等 7 个子图 alias 类
  - 任何 model_validator / field_validator / 业务字段（resolved / candidates /
    extracted_hints / missing_fields / customer / shipping 等）

ReAct agent 用 LangGraph MessagesState（messages 即状态），不需要业务字段 schema。
保留的 5 个数据类只是 react tool 内部数据传递用的轻量 record。
"""
from __future__ import annotations
from decimal import Decimal
from enum import Enum
from pydantic import BaseModel


class Intent(str, Enum):
    """[v9 残留,本 v10 不再用 LLM router；保留 enum 给 audit / log 用]"""
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
