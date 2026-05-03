# backend/hub/agent/graph/nodes/ask_user.py
"""ask_user — 输出缺失字段或候选列表给用户。

P2-C v1.2 关键：candidate_customers / candidate_products 不能只输出 'customer_choice' 这种
内部字段名 — 必须列出候选项的编号 / id / 名称，让用户能精确选回来。
"""
from __future__ import annotations
from hub.agent.graph.state import ContractState

# 与 validate_inputs.VALID_MISSING_FIELDS_FIXED 保持同步，覆盖所有 fixed enum
FIELD_LABELS = {
    "customer": "客户",
    "items": "产品明细",
    "products": "产品",
    "shipping_address": "收货地址",
    "shipping_contact": "收货联系人",
    "shipping_phone": "收货电话",
    "contact": "收货联系人",  # 保留旧字段名兼容（向后）
    "phone": "收货电话",
    "template": "合同模板（请联系管理员到后台上传销售合同模板）",
}


async def ask_user_node(state: ContractState) -> ContractState:
    parts: list[str] = []

    # 1. 多候选客户 — 列编号 + id + 名称
    if state.candidate_customers:
        lines = ["请问哪个客户？回复编号或客户 ID："]
        for i, c in enumerate(state.candidate_customers, 1):
            lines.append(f"  {i}) [id={c.id}] {c.name}")
        parts.append("\n".join(lines))

    # 2. 多候选产品 — 按 hint 分组列
    multi_groups = len(state.candidate_products) > 1
    for hint, candidates in state.candidate_products.items():
        if multi_groups:
            lines = [f"产品「{hint}」找到多个，请用 id 精确选（如 id={candidates[0].id}）："]
        else:
            lines = [f"产品「{hint}」找到多个，请选一个（回复编号或产品 ID）："]
        for i, p in enumerate(candidates, 1):
            spec = f" {p.spec}" if p.spec else ""
            color = f" {p.color}" if p.color else ""
            lines.append(f"  {i}) [id={p.id}] {p.name}{color}{spec}")
        parts.append("\n".join(lines))
    if multi_groups:
        parts.append("（多个产品都有歧义时，请按 `id=N` 精确选每个，例如：H5 用 id=10，F1 用 id=22）")

    # 3. 一般缺失字段（非 _choice 类）
    plain = [mf for mf in state.missing_fields
              if not mf.startswith("customer_choice") and not mf.startswith("product_choice:")]
    if plain:
        labeled = []
        for mf in plain:
            if mf.startswith("item_qty:"):
                labeled.append(f"产品「{mf.split(':', 1)[1]}」的数量")
            elif mf.startswith("item_price:"):
                labeled.append(f"产品「{mf.split(':', 1)[1]}」的单价")
            elif mf.startswith("product_not_found:"):
                labeled.append(f"产品「{mf.split(':', 1)[1]}」找不到，请确认名称")
            else:
                labeled.append(FIELD_LABELS.get(mf, mf))
        parts.append("还差这些：" + "、".join(labeled) + "。")

    state.final_response = "\n\n".join(parts) if parts else "请补充信息后再试。"
    return state
