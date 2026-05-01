"""Tool calling few-shot 示例（system prompt 注入）。

每个范例展示用户输入 + 预期 tool call 链，给 LLM 学样。
覆盖：基础查询 / 多步推理 / 写操作（必须 confirm）/ 长尾说法。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FewShot:
    user: str  # 用户输入消息
    expected_calls: list[dict] = field(default_factory=list)  # 预期 tool call 链
    expected_text: str | None = None  # 应直接回的 text（不调 tool）
    note: str | None = None  # 给 LLM 的额外说明


DEFAULT_FEW_SHOTS: list[FewShot] = [
    # ===== 基础查询 =====
    FewShot(
        user="查讯飞x5 的库存",
        expected_calls=[
            {"tool": "search_products", "args": {"query": "讯飞x5"}},
            {"tool": "check_inventory", "args": {"product_id": "<上一步返的 product_id>"}},
        ],
        note="先 search 拿 ID，再查库存",
    ),
    FewShot(
        user="阿里巴巴的回款情况",
        expected_calls=[
            {"tool": "search_customers", "args": {"query": "阿里巴巴"}},
            {"tool": "get_customer_balance", "args": {"customer_id": "<上一步返的 id>"}},
        ],
        note="客户先 search 找 ID，再查应收",
    ),

    # ===== 多步推理（合同生成）=====
    FewShot(
        user="给阿里写讯飞x5 50 台合同 按上次价",
        expected_calls=[
            {"tool": "search_customers", "args": {"query": "阿里"}},
            {"tool": "search_products", "args": {"query": "讯飞x5"}},
            {"tool": "get_customer_history",
             "args": {"customer_id": "<阿里 id>", "product_id": "<讯飞x5 id>"}},
            {"tool": "check_inventory", "args": {"product_id": "<讯飞x5 id>"}},
            {"tool": "generate_contract_draft",
             "args": {"customer_id": "<id>", "items": [{"product_id": "<id>", "qty": 50, "price": "<历史价>"}]}},
        ],
        note="销售场景：先备齐客户 + 商品 + 历史价 + 库存，最后生成合同 docx",
    ),

    # ===== 写操作（必须 confirm 后才真调）=====
    FewShot(
        user="把这周差旅做凭证",
        expected_calls=[
            {"tool": "search_orders",
             "args": {"type": "expense_reimburse", "since_days": 7}},
            # 拿到差旅订单后，agent 应输出 text 预览给用户确认，
            # **不要直接调** create_voucher_draft（写门禁会拦截）
        ],
        expected_text=(
            "我准备创建 12 张凭证（差旅总额 ¥X）。"
            "回复\"是\"确认提交。"
        ),
        note="写类 tool 必须先 text 预览，等用户回\"是\"后再带 confirmation_action_id+token 调用",
    ),
    FewShot(
        user="是",
        expected_calls=[
            {"tool": "create_voucher_draft",
             "args": {
                 "voucher_data": "<...>",
                 "rule_matched": "差旅",
                 "confirmation_action_id": "<ChainAgent 注入>",
                 "confirmation_token": "<ChainAgent 注入>",
             }},
        ],
        # M6: 加 prior_context 说明，警示无上下文时不应贸然调写 tool
        note=(
            "上下文依赖：本例假定上一轮 history 中存在差旅凭证预览（如例 4 输出）。"
            "用户单独说'是'但无对应预览时，应反问澄清而非贸然调写 tool。"
        ),
    ),

    # ===== 长尾说法 =====
    FewShot(
        user="阿里最近半年总共下了多少单",
        expected_calls=[
            {"tool": "search_customers", "args": {"query": "阿里"}},
            {"tool": "search_orders",
             "args": {"customer_id": "<阿里 id>", "since_days": 180}},
        ],
        note="半年=180 天；agent 应汇总 search_orders 返回的订单数 + 总额给用户",
    ),
    FewShot(
        user="哪些商品压货了",
        expected_calls=[
            {"tool": "get_inventory_aging", "args": {"threshold_days": 90}},
        ],
        note="压货 → inventory_aging（业务词典已映射）",
    ),

    # ===== 不确定先反问 =====
    FewShot(
        user="把那个客户的价格改一下",
        expected_calls=[],  # 不直接调
        expected_text="请问是哪个客户？哪个商品？要调到什么价格（或几折）？",
        note="信息不全应该反问澄清，不要瞎调 tool",
    ),
]


def render_few_shots(shots: list[FewShot] | None = None) -> str:
    """渲染 few-shots 为 system prompt 文本。"""
    shots = shots if shots is not None else DEFAULT_FEW_SHOTS
    if not shots:
        return ""
    lines = []
    for i, s in enumerate(shots, 1):
        # M4: 去掉前置 \n（builder 侧已在 f"[Few-shot 例子]\n" 里加好了）
        # 例子之间用空行分隔
        if i > 1:
            lines.append("")
        lines.append(f"例 {i}：")
        lines.append(f"  用户: {s.user}")
        if s.expected_calls:
            lines.append("  应调用:")
            for c in s.expected_calls:
                args_repr = c.get("args")
                lines.append(f"    - {c['tool']}({args_repr})")
        if s.expected_text:
            lines.append(f"  应回复: {s.expected_text}")
        if s.note:
            lines.append(f"  说明: {s.note}")
    return "\n".join(lines)
