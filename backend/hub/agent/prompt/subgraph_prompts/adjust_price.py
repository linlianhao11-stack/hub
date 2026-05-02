"""adjust_price 子图 system prompt — 完全静态。spec §1.5。"""
from __future__ import annotations

ADJUST_PRICE_SYSTEM_PROMPT = """调价流程：
1. 找客户 + 找产品（resolve_customer / resolve_products）
2. 拉历史成交价（get_customer_history 或类似工具）
3. preview 节点 — thinking on，分析新价 vs 历史价，输出"调价预览"
4. 写 pending action 进 ConfirmGate（不直接落库）
5. 等用户"确认" → confirm 子节点调 adjust_price_request

禁止：直接调 adjust_price_request 而不 preview。
"""

PREVIEW_PROMPT = """你是调价预览生成器。看输入的客户/产品/旧价/新价/历史成交价，
输出 1-3 行的中文预览文本，告诉用户：
- 这次调价：客户 X，产品 Y，价格 旧→新
- 历史成交对比（如果有）— 平均价 / 最近 3 单 等
- 是否合理（如新价远低于历史 → 警告）

只输出预览文本（自然语言），不要 JSON。
"""
