"""所有 ReAct agent tool 集中导出。"""
from hub.agent.react.tools.read import (
    search_customer, search_product,
    get_product_detail, check_inventory, get_customer_history,
    get_customer_balance, search_orders, get_order_detail, analyze_top_customers,
    get_recent_drafts,
)
from hub.agent.react.tools.write import create_contract_draft

ALL_TOOLS = [
    # read
    search_customer, search_product,
    get_product_detail, check_inventory, get_customer_history,
    get_customer_balance, search_orders, get_order_detail, analyze_top_customers,
    get_recent_drafts,
    # write
    create_contract_draft,
]
