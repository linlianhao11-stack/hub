# hub/agent/tools/__init__.py
"""Plan 6 v9 Task 2.5：注册全部 16 个 tool 到 registry。

按 spec §5.1 子图分布：
- query: 11
- contract: 3 (deviation from plan v9: get_product_customer_prices 不存在为独立 tool)
- quote: 3
- voucher: 3
- adjust_price: 3 (deviation from plan v9: 同上)
- adjust_stock: 3
- chat: 0
"""
from hub.agent.tools.erp_tools import (
    SEARCH_CUSTOMERS_SCHEMA, SEARCH_PRODUCTS_SCHEMA, SEARCH_ORDERS_SCHEMA,
    GET_CUSTOMER_HISTORY_SCHEMA, GET_ORDER_DETAIL_SCHEMA,
    GET_CUSTOMER_BALANCE_SCHEMA, GET_INVENTORY_AGING_SCHEMA,
    GET_PRODUCT_DETAIL_SCHEMA, CHECK_INVENTORY_SCHEMA,
)
from hub.agent.tools.analyze_tools import (
    ANALYZE_TOP_CUSTOMERS_SCHEMA, ANALYZE_SLOW_MOVING_PRODUCTS_SCHEMA,
)
from hub.agent.tools.generate_tools import (
    GENERATE_CONTRACT_DRAFT_SCHEMA, GENERATE_PRICE_QUOTE_SCHEMA,
)
from hub.agent.tools.draft_tools import (
    CREATE_VOUCHER_DRAFT_SCHEMA,
    CREATE_PRICE_ADJUSTMENT_REQUEST_SCHEMA,
    CREATE_STOCK_ADJUSTMENT_REQUEST_SCHEMA,
)


def register_all_tools(registry):
    """按 spec §5.1 子图分布注册 16 个 tool 到 registry。

    使用 enforce_strict=True 确保所有 schema 都符合 strict mode 要求。
    """
    schemas = (
        # Read tools (11)
        SEARCH_CUSTOMERS_SCHEMA, SEARCH_PRODUCTS_SCHEMA, SEARCH_ORDERS_SCHEMA,
        GET_CUSTOMER_HISTORY_SCHEMA, GET_ORDER_DETAIL_SCHEMA,
        GET_CUSTOMER_BALANCE_SCHEMA, GET_INVENTORY_AGING_SCHEMA,
        GET_PRODUCT_DETAIL_SCHEMA, CHECK_INVENTORY_SCHEMA,
        ANALYZE_TOP_CUSTOMERS_SCHEMA, ANALYZE_SLOW_MOVING_PRODUCTS_SCHEMA,
        # Write tools (5)
        GENERATE_CONTRACT_DRAFT_SCHEMA, GENERATE_PRICE_QUOTE_SCHEMA,
        CREATE_VOUCHER_DRAFT_SCHEMA,
        CREATE_PRICE_ADJUSTMENT_REQUEST_SCHEMA,
        CREATE_STOCK_ADJUSTMENT_REQUEST_SCHEMA,
    )
    for schema in schemas:
        registry.register(schema, enforce_strict=True)
