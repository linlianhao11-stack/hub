"""启动时跑预设角色 + 权限码 + 业务词典种子（幂等）。"""
from __future__ import annotations

from hub.models import HubPermission, HubRole

# 全部权限码（spec §7.4）
PERMISSIONS = [
    # platform.*
    ("platform.tasks.read", "platform", "tasks", "read", "查看任务记录",
     "可以在后台看到每次机器人调用的详细执行记录"),
    ("platform.flags.write", "platform", "flags", "write", "调整功能开关",
     "可以打开或关闭系统的某些功能模块"),
    ("platform.users.write", "platform", "users", "write", "管理后台用户",
     "可以在后台添加用户、分配角色"),
    ("platform.alerts.write", "platform", "alerts", "write", "配置告警接收人",
     "可以设置出问题时通知谁"),
    ("platform.audit.read", "platform", "audit", "read", "查看操作日志",
     "可以看到管理员们的操作历史"),
    ("platform.audit.system_read", "platform", "audit", "system_read", "查看系统级审计",
     "可以看到 '谁查看了用户对话' 等敏感审计"),
    ("platform.conversation.monitor", "platform", "conversation", "monitor", "对话监控",
     "可以查看用户与机器人的实时对话和历史对话内容"),
    ("platform.apikeys.write", "platform", "apikeys", "write", "管理 API 密钥",
     "可以创建、吊销、查看下游系统对接密钥"),
    # downstream.*
    ("downstream.erp.use", "downstream", "erp", "use", "使用 ERP 数据",
     "允许机器人访问 ERP 系统的客户、商品、订单等数据"),
    # usecase.*
    ("usecase.query_product.use", "usecase", "query_product", "use", "商品查询",
     "允许在钉钉用机器人查询商品信息"),
    ("usecase.query_customer_history.use", "usecase", "query_customer_history", "use",
     "客户历史价查询", "允许查询某客户的历史成交价"),
    ("usecase.generate_contract.use", "usecase", "generate_contract", "use", "合同生成",
     "允许在钉钉用机器人自动生成销售合同（B 阶段启用）"),
    ("usecase.create_voucher.use", "usecase", "create_voucher", "use", "凭证生成",
     "允许审批通过的报销/付款自动生成会计凭证（D 阶段启用）"),
    ("usecase.contract_templates.write", "usecase", "contract_templates", "write", "管理合同模板",
     "可以在后台上传、编辑、启用/禁用合同模板"),
    # v2 加固（review C1）：read 权限独立，方便 viewer 角色查看不修改
    ("usecase.contract_templates.read", "usecase", "contract_templates", "read", "查看合同模板",
     "可以查看合同模板列表及占位符，不能上传或修改"),
    # channel.*
    ("channel.dingtalk.use", "channel", "dingtalk", "use", "使用钉钉接入",
     "允许通过钉钉机器人交互"),
]

# === Plan 6 Task 17 新加 13 条权限码 ===
PERMISSIONS.extend([
    ("usecase.query_customer.use", "usecase", "query_customer", "use",
     "客户查询", "允许搜索客户列表与查看客户详情"),
    ("usecase.query_inventory.use", "usecase", "query_inventory", "use",
     "库存查询", "允许查询商品库存数量与所在仓库"),
    ("usecase.query_orders.use", "usecase", "query_orders", "use",
     "订单查询", "允许搜索订单列表与查看订单详情"),
    ("usecase.query_customer_balance.use", "usecase", "query_customer_balance", "use",
     "客户余额查询", "允许查询客户应收 / 已付 / 未付汇总"),
    ("usecase.query_inventory_aging.use", "usecase", "query_inventory_aging", "use",
     "库龄查询", "允许查询超 N 天滞销商品列表"),
    ("usecase.analyze.use", "usecase", "analyze", "use",
     "数据分析", "允许使用 TOP 客户 / 滞销 / 周转等聚合分析"),
    ("usecase.generate_quote.use", "usecase", "generate_quote", "use",
     "报价单生成", "允许 agent 生成报价 docx 并发到钉钉"),
    ("usecase.export.use", "usecase", "export", "use",
     "Excel 导出", "允许把查询结果导出 .xlsx 并发到钉钉"),
    ("usecase.adjust_price.use", "usecase", "adjust_price", "use",
     "提交调价请求", "允许销售给客户提交特价请求草稿"),
    ("usecase.adjust_price.approve", "usecase", "adjust_price", "approve",
     "审批调价请求", "允许销售主管审批调价请求"),
    ("usecase.adjust_stock.use", "usecase", "adjust_stock", "use",
     "提交库存调整", "允许提交盘点 / 调拨草稿"),
    ("usecase.adjust_stock.approve", "usecase", "adjust_stock", "approve",
     "审批库存调整", "允许仓管审批库存调整草稿"),
    ("usecase.create_voucher.approve", "usecase", "create_voucher", "approve",
     "审批凭证", "允许会计批量审凭证草稿"),
])


# 6 预设角色 + 权限映射
ROLES = {
    "platform_admin": {
        "name": "HUB 系统管理员",
        "description": "拥有所有功能权限，可以管理用户、角色、系统配置",
        "permissions": [p[0] for p in PERMISSIONS],  # 全部
    },
    "platform_ops": {
        "name": "运维人员",
        "description": "可以查看任务记录、调整系统开关、配置告警接收人，但不能管理用户",
        "permissions": [
            "platform.tasks.read", "platform.flags.write",
            "platform.alerts.write", "platform.audit.read",
        ],
    },
    "platform_viewer": {
        "name": "只读观察员",
        "description": "只能查看任务记录和操作日志，不能做任何修改",
        "permissions": [
            "platform.tasks.read", "platform.audit.read",
            # v2 加固（review C1）：viewer 可查看合同模板但不能修改
            "usecase.contract_templates.read",
        ],
    },
    "bot_user_basic": {
        "name": "机器人 - 基础查询",
        "description": "可以在钉钉里让机器人查商品、查客户、查报价",
        "permissions": [
            "channel.dingtalk.use",
            "downstream.erp.use",
            "usecase.query_product.use",
            "usecase.query_customer_history.use",
        ],
    },
    "bot_user_sales": {
        "name": "机器人 - 销售（B 阶段启用）",
        "description": "在 '基础查询' 之上，还可以让机器人生成销售合同",
        "permissions": [
            "channel.dingtalk.use",
            "downstream.erp.use",
            "usecase.query_product.use",
            "usecase.query_customer_history.use",
            "usecase.generate_contract.use",
        ],
    },
    "bot_user_finance": {
        "name": "机器人 - 财务（D 阶段启用）",
        "description": "在 '基础查询' 之上，还可以让机器人自动生成报销/付款凭证",
        "permissions": [
            "channel.dingtalk.use",
            "downstream.erp.use",
            "usecase.query_product.use",
            "usecase.create_voucher.use",
        ],
    },
}

# === 既有角色升级（追加 Plan 6 新权限码） ===
# bot_user_basic 加：query_customer / query_inventory / query_orders
ROLES["bot_user_basic"]["permissions"].extend([
    "usecase.query_customer.use",
    "usecase.query_inventory.use",
    "usecase.query_orders.use",
])

# bot_user_sales 加：query_customer / query_customer_balance / query_inventory /
#                  query_orders / generate_quote / export / adjust_price.use
ROLES["bot_user_sales"]["permissions"].extend([
    "usecase.query_customer.use",
    "usecase.query_customer_balance.use",
    "usecase.query_inventory.use",
    "usecase.query_orders.use",
    "usecase.generate_quote.use",
    "usecase.export.use",
    "usecase.adjust_price.use",
])

# bot_user_finance 加：query_customer / query_customer_balance / query_orders /
#                    adjust_stock.use / export
ROLES["bot_user_finance"]["permissions"].extend([
    "usecase.query_customer.use",
    "usecase.query_customer_balance.use",
    "usecase.query_orders.use",
    "usecase.adjust_stock.use",
    "usecase.export.use",
])

# === Plan 6 Task 17 新加 2 个 lead 角色 ===
ROLES["bot_user_sales_lead"] = {
    "name": "机器人 - 销售主管",
    "description": "继承销售权限 + 调价审批权限",
    "permissions": [
        "channel.dingtalk.use",
        "downstream.erp.use",
        "usecase.query_product.use",
        "usecase.query_customer.use",
        "usecase.query_customer_history.use",
        "usecase.query_customer_balance.use",
        "usecase.query_inventory.use",
        "usecase.query_orders.use",
        "usecase.analyze.use",
        "usecase.generate_contract.use",
        "usecase.generate_quote.use",
        "usecase.export.use",
        "usecase.adjust_price.use",
        "usecase.adjust_price.approve",  # 主管才有的审批权
    ],
}

ROLES["bot_user_finance_lead"] = {
    "name": "机器人 - 会计主管",
    "description": "继承会计权限 + 凭证 / 库存调整审批权限",
    "permissions": [
        "channel.dingtalk.use",
        "downstream.erp.use",
        "usecase.query_product.use",
        "usecase.query_customer.use",
        "usecase.query_customer_balance.use",
        "usecase.query_orders.use",
        "usecase.create_voucher.use",
        "usecase.create_voucher.approve",   # 主管才有的审批权
        "usecase.adjust_stock.use",
        "usecase.adjust_stock.approve",     # 主管才有的审批权
        "usecase.export.use",
    ],
}


# === Plan 6 Task 17：业务词典默认 seed（写入 SystemConfig.business_dict 让 admin 后台可编辑）===
DEFAULT_BUSINESS_DICT_SEED = {
    "压货": "库龄高的商品（调 get_inventory_aging）",
    "周转": "商品周转率（调 analyze_slow_moving_products）",
    "回款": "客户应付未付的款项（调 get_customer_balance）",
    "上次价格": "客户最近一次该商品成交价（调 get_customer_history limit=1）",
    "上次价": "同 上次价格",
    "之前的价": "同 上次价格",
    "差旅": "差旅费用报销，对应凭证科目 借管理费用-差旅 / 贷库存现金",
    "报销": "员工费用报销，对应凭证按费用类型匹配科目",
    "套餐": "多 SKU 打包销售，每个 SKU 独立列在合同条款中",
    "组合": "同 套餐",
    "TOP": "排名前 N（调 analyze_top_customers / analyze_slow_moving_products）",
    "排行": "同 TOP",
    "滞销": "销量低 + 库龄长的商品（调 analyze_slow_moving_products）",
    "畅销": "销量高的商品（与 滞销 相反）",
    "出货": "订单出库（用 search_orders 看交付状态）",
    "进货": "采购入库（暂未涵盖；提示用户去 ERP 操作）",
    "对账": "客户余额对账单（调 get_customer_balance）",
    "应收": "客户欠款总额（同 回款）",
    "底价": "成本价或最低售价（业务概念，不直接查；让用户澄清）",
    "挂牌价": "商品标准售价（调 search_products / get_product_detail）",
    "议价": "客户协商价（调 get_customer_history 看历史）",
    "调价": "调整客户专属定价（调 create_price_adjustment_request 提请审批）",
    "盘点": "库存调整（调 create_stock_adjustment_request 提请审批）",
    "盘盈": "实盘大于账面（正向调整）",
    "盘亏": "实盘小于账面（负向调整）",
    "凭证": "会计凭证（调 create_voucher_draft 提请审批）",
    "做凭证": "生成凭证草稿（同 凭证）",
    "做账": "同 做凭证",
    "合同": "销售合同（调 generate_contract_draft 生成 docx）",
    "报价": "客户报价单（调 generate_price_quote 生成 docx）",
    "导出": "Excel 导出（调 export_to_excel）",
    "上月": "上一个完整自然月",
    "本月": "本月 1 日至今",
    "上周": "上一个完整周（周一至周日）",
    "本周": "本周一至今",
    "近一周": "今天往前 7 天",
    "近一月": "今天往前 30 天",
    "近三月": "今天往前 90 天",
    "今年": "本年 1 月 1 日至今",
    "去年": "上一个完整自然年",
}


async def _seed_business_dict() -> None:
    """业务词典 seed 写到 SystemConfig.business_dict（key 唯一）。

    幂等：已存在则跳过（不覆盖管理员手动编辑过的内容）；不存在才写默认。
    """
    from hub.models import SystemConfig

    rec = await SystemConfig.filter(key="business_dict").first()
    if rec:
        return  # 已存在不覆盖
    await SystemConfig.create(
        key="business_dict",
        value=DEFAULT_BUSINESS_DICT_SEED,
    )


async def run_seed():
    """启动时跑预设角色 + 权限码 + 业务词典种子（幂等）。"""
    # 1. 权限码
    perm_objs = {}
    for code, resource, sub, action, name, desc in PERMISSIONS:
        perm, _ = await HubPermission.get_or_create(
            code=code,
            defaults={
                "resource": resource, "sub_resource": sub, "action": action,
                "name": name, "description": desc,
            },
        )
        perm_objs[code] = perm

    # 2. 角色 + 权限关联（只增不减）
    for role_code, info in ROLES.items():
        role, _ = await HubRole.get_or_create(
            code=role_code,
            defaults={
                "name": info["name"], "description": info["description"],
                "is_builtin": True,
            },
        )
        existing_codes = {p.code async for p in role.permissions}
        for pcode in info["permissions"]:
            if pcode not in existing_codes:
                if pcode not in perm_objs:
                    # 防御：role 引用了未定义的 perm code（数据一致性）
                    import logging
                    logging.getLogger("hub.seed").warning(
                        "角色 %s 引用未定义权限码 %s，跳过", role_code, pcode,
                    )
                    continue
                await role.permissions.add(perm_objs[pcode])

    # 3. v2 加固（Plan 6 Task 17）：业务词典默认 seed
    await _seed_business_dict()
