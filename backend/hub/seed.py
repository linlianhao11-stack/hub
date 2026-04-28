"""启动时跑预设角色 + 权限码种子（幂等）。"""
from __future__ import annotations
from hub.models import HubRole, HubPermission


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
    # channel.*
    ("channel.dingtalk.use", "channel", "dingtalk", "use", "使用钉钉接入",
     "允许通过钉钉机器人交互"),
]


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
        "permissions": ["platform.tasks.read", "platform.audit.read"],
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


async def run_seed():
    """幂等：已存在则跳过，新增则插入。"""
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

    # 2. 角色 + 权限关联
    for role_code, info in ROLES.items():
        role, _ = await HubRole.get_or_create(
            code=role_code,
            defaults={
                "name": info["name"], "description": info["description"],
                "is_builtin": True,
            },
        )
        # 同步权限关联（只增不减）
        existing_codes = {p.code async for p in role.permissions}
        for pcode in info["permissions"]:
            if pcode not in existing_codes:
                await role.permissions.add(perm_objs[pcode])
