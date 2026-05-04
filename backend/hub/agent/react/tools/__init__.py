"""所有 ReAct agent tool 集中导出。"""
from hub.agent.react.tools.read import (
    search_customer, search_product,
    get_product_detail, check_inventory, get_customer_history,
    get_customer_balance, search_orders, get_order_detail, analyze_top_customers,
    get_recent_drafts,
)
from hub.agent.react.tools.write import (
    create_contract_draft, create_quote_draft, create_voucher_draft,
    request_price_adjustment, request_stock_adjustment,
)
from hub.agent.react.tools.confirm import confirm_action

ALL_TOOLS = [
    # read
    search_customer, search_product,
    get_product_detail, check_inventory, get_customer_history,
    get_customer_balance, search_orders, get_order_detail, analyze_top_customers,
    get_recent_drafts,
    # write (plan 阶段)
    create_contract_draft, create_quote_draft, create_voucher_draft,
    request_price_adjustment, request_stock_adjustment,
    # confirm
    confirm_action,
]
# 共 16 个
