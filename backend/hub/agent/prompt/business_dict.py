"""业务术语 → tool 映射（system prompt 注入用）。

LLM 看到用户说"压货"应该理解成"查 inventory_aging"；
看到"上次价"应该理解成"查 get_customer_history"。
"""
from __future__ import annotations

DEFAULT_DICT: dict[str, str] = {
    # ===== 库存相关 =====
    "压货": "库龄高的滞销商品（用 get_inventory_aging tool）",
    "积压": "同压货（库龄高的滞销商品）",
    "断货": "库存为 0（用 check_inventory）",
    "周转": "商品周转率（订单 / 库存）",
    "周转天数": "库存平均周转天数（库存量 / 日均出库）",
    "库龄": "商品入库到当前的时长（用 get_inventory_aging）",
    "盘点": "库存调整（用 create_stock_adjustment_request 提请审批）",
    "盘盈": "实盘大于账面（正向调整）",
    "盘亏": "实盘小于账面（负向调整）",

    # ===== 客户/订单相关 =====
    "回款": "客户应付未付的款项（用 get_customer_balance）",
    "应收": "未收款金额（同回款）",
    "对账": "客户余额对账单（用 get_customer_balance）",
    "上次价格": "客户最近一次该商品成交价（用 get_customer_history）",
    "上次价": "同上次价格",
    "历史价": "客户历史成交价（用 get_customer_history）",
    "成交价": "客户实际成交价（区别于挂牌价）",
    "底价": "成本价或最低售价（不直接查；让用户澄清）",
    "挂牌价": "商品标准售价（用 get_product_detail）",
    "客户专属价": "客户专属定价规则（涉及 create_price_adjustment_request）",
    "调价": "调整客户专属定价（用 create_price_adjustment_request 提请审批）",
    "议价": "与客户协商价格（先 get_customer_history 看历史）",

    # ===== 凭证/财务相关 =====
    "凭证": "会计凭证（用 create_voucher_draft 提请审批）",
    "做凭证": "生成凭证草稿（用 create_voucher_draft）",
    "做账": "同做凭证",
    "差旅": "差旅费报销（凭证模板：借管理费用-差旅 / 贷库存现金）",
    "报销": "员工费用报销（凭证模板按费用类型匹配）",
    "费用": "公司各类费用（差旅/招待/办公等）",

    # ===== 合同/销售相关 =====
    "合同": "销售合同（用 generate_contract_draft 生成 docx）",
    "写合同": "生成合同草稿（用 generate_contract_draft）",
    "报价": "客户报价单（用 generate_price_quote 生成 PDF/docx）",
    "下单": "新建销售订单（暂未支持，提示用户去 ERP 操作）",

    # ===== 时间/数据范围 =====
    "上月": "上一个完整自然月",
    "本月": "本月 1 日至今",
    "上周": "上一个完整周（周一至周日）",
    "本周": "本周一至今",
    "近N天": "今天往前 N 个自然日",
    "去年": "去年同期",

    # ===== 报表/分析相关 =====
    "TOP": "排名前 N（用 analyze_top_customers / analyze_slow_moving_products）",
    "排行": "同 TOP",
    "滞销": "销量低且库龄长的商品（用 analyze_slow_moving_products）",
    "畅销": "销量高的商品（与滞销相反）",
    "导出": "导出 Excel（用 export_to_excel tool）",
}


def render_dict(d: dict[str, str] | None = None) -> str:
    """渲染成 system prompt 文本片段。"""
    d = d or DEFAULT_DICT
    if not d:
        return ""
    lines = [f"- {k}: {v}" for k, v in d.items()]
    return "\n".join(lines)
