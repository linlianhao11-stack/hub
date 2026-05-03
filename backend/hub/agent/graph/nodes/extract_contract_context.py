"""extract_contract_context — 子图入口节点，一次抽完用户原文里所有合同结构化信息。

v1.8 P1-A+B：
- v1.7 把 parse_contract_shipping 放在 parse_items 之后；多候选 → ask_user 时跳过这两节点 → 第一轮信息丢
- resolve_products 依赖 extracted_hints.product_hints 但没人写 → 多商品兜底失败
本节点放 set_origin 后第一个，**任何 ask_user 之前**抽完所有 hints 写 state，跨轮安全。

跨轮规则：
  - state.extracted_hints 已有值 + 本轮消息短（≤ 8 字）→ 跳过 LLM
  - 抽到的字段为 null 时**不**覆盖 state 已有值（保护跨轮信息）

v1.14 状态更新规则（LangGraph model_fields_set 陷阱）：
  - LangGraph 0.2.x 用 model_fields_set 判断哪些字段被更新，只传播被"赋值"的字段。
  - 在 Pydantic 模型上对 dict 字段做 in-place 修改（state.extracted_hints["k"] = v）
    或对嵌套模型做属性设置（state.shipping.address = ...）**不会**把字段加入 model_fields_set，
    导致修改被 LangGraph 丢弃，下一节点看到的还是旧值。
  - 修复方案：用 **整字段替换（field reassignment）** 代替原地修改：
      state.extracted_hints = {**state.extracted_hints, "k": v}
      state.shipping = ShippingInfo(address=..., ...)
"""
from __future__ import annotations
import json

from hub.agent.graph.state import ContractState, ShippingInfo
from hub.agent.llm_client import DeepSeekLLMClient, disable_thinking


EXTRACT_CONTEXT_PROMPT = """你是合同请求抽取器。从用户原文一次抽 4 类信息，输出严格 JSON：

{
  "customer_name": <str 或 null>,           // 用户提到的客户名 / 关键词
  "product_hints": [<str>, ...],            // 用户提到的产品名 / 编号列表（如 ["H5", "F1", "K5"]）
  "items_raw": [
    {"hint": <str>, "qty": <int 或 null>, "price": <number 或 null>}, ...
  ],                                          // 每个产品的原始数量 / 价格；用户没明说传 null
  "shipping": {
    "address": <str 或 null>,                // 详细地址；只有"北京"太模糊算 null
    "contact": <str 或 null>,                // 联系人姓名
    "phone": <str 或 null>                   // 11 位电话
  }
}

规则：
- 只抽**当前消息**里明确出现的内容；不要补全 / 不要根据上下文猜
- 数量 / 价格用户没明说就传 null（不要默认 1 / 不要默认 list_price）
- product_hints 顺序与 items_raw 一致
- 只输出 JSON，不要解释
"""


def _looks_like_pure_selection(message: str) -> bool:
    """P1-B v1.9 / v1.12：只有"明确选择/确认类"消息才跳过抽取 — 短消息但是补地址/联系人不能跳过。

    命中：
      - 纯数字 "1" / "2"（含中文""一""二""…"）
      - "选 N" / "第 N 个"
      - "id=N" / "id N"
      - **多 id**："id=11 id=21" / "id=11, id=21" / "id=11、id=21"（v1.12 P1-B 新加）
      - 业务 action_id 前缀（adj-/vch-/stk-/...）
      - 确认词（"是" / "确认" / "好的" / "OK"）
    不命中（仍走 LLM 抽取）：
      - "北京海淀" / "张三" / "13800001111" 等补字段消息
    """
    import re
    msg = message.strip()
    if not msg:
        return False
    if re.fullmatch(r"\s*[1-9一二三四五六七八九]\s*", msg):
        return True
    if re.search(r"^选\s*[1-9]$", msg) or re.search(r"^第\s*[一二三四五六七八九1-9]\s*个?$", msg):
        return True
    # 单 id 或多 id（id=N 重复，可空格 / 逗号 / 顿号 / 中文分隔）
    if re.fullmatch(r"\s*(?:id\s*[=:：]?\s*\d+[\s,，、]*)+\s*", msg, re.IGNORECASE):
        return True
    if re.fullmatch(r"(adj|vch|stk|act|qte|cnt)-[0-9a-f]{8,}", msg, re.IGNORECASE):
        return True
    if msg in {"是", "确认", "好的", "OK", "ok", "yes", "嗯"}:
        return True
    return False


def _looks_like_candidate_id_reference(message: str, candidate_products: dict) -> bool:
    """P1 v1.13：消息里出现至少一个 id=N，且 N 是当前候选里某个 product.id → 算 selection。

    覆盖 ask_user 文案诱导的写法："H5 用 id=10，F1 用 id=22" / "H5: id=10, F1=22" / 等。
    这类消息按 _looks_like_pure_selection 的纯 id 正则不命中（中间有 hint 名 / 中文连接词），
    但只要含至少一个有效候选 id，就可视为选择消息 — extract_context 跳过 LLM 不覆盖第一轮 hints。

    安全：用户回"13800001111"（电话号）没 `id=` 前缀 → 不命中；不会把电话号误当 selection。
    """
    import re
    if not candidate_products:
        return False
    ids_in_msg = {int(x) for x in re.findall(r"id\s*[=:：]?\s*(\d+)", message, re.IGNORECASE)}
    if not ids_in_msg:
        return False
    valid_ids = {p.id for candidates in candidate_products.values() for p in candidates}
    return bool(ids_in_msg & valid_ids)


async def extract_contract_context_node(state: ContractState, *, llm: DeepSeekLLMClient) -> ContractState:
    # P1-B v1.9 / v1.13 跨轮跳过规则：
    # 1. 明确纯选择消息（"选 2"/"是"/单或多 id=N/action_id 等）→ 跳过
    # 2. v1.13 P1：上轮留了 candidate_products + 本轮含至少一个有效候选 id → 也跳过
    #    （覆盖"H5 用 id=10，F1 用 id=22"等带 hint 的选择回复 — 按 ask_user 文案诱导的写法）
    # 3. 其他短消息（"北京海淀"/"张三"）仍走 LLM，靠 only-write-non-null 保护原值
    if _looks_like_pure_selection(state.user_message):
        return state
    if _looks_like_candidate_id_reference(state.user_message, state.candidate_products):
        return state

    resp = await llm.chat(
        messages=[
            {"role": "system", "content": EXTRACT_CONTEXT_PROMPT},
            {"role": "user", "content": state.user_message},
        ],
        thinking=disable_thinking(),
        temperature=0.0,
        max_tokens=1000,
    )
    try:
        parsed = json.loads(resp.text)
    except json.JSONDecodeError:
        state.errors.append("extract_context_json_decode_failed")
        return state

    # 钉钉实测 hotfix（客户切换 e2e 测试 task=switch）：用户上一轮 contract 没走完
    # cleanup（停在 ask_user 等用户补字段），state.customer 残留。这一轮用户改主意
    # 给**新客户**做合同时,resolve_customer 看 state.customer 有值 early return,
    # bot 用旧客户 + 上轮 items 生成合同 → 合同发错客户（严重 bug）。
    #
    # 切换检测：parsed.customer_name 跟 state.customer.name 互不为子串 → 视为新合同,
    # 整体重置当前合同工作 state，让 resolve_customer 重新搜，items/products/shipping
    # 也由本轮重新解析。
    new_customer_name = parsed.get("customer_name") or ""
    if new_customer_name and state.customer and state.customer.name:
        existing_name = state.customer.name
        if (new_customer_name not in existing_name
                and existing_name not in new_customer_name):
            # 切换客户 — 整体重置当前合同工作 state（保留 active_subgraph,留 contract 流程继续）
            state.customer = None
            state.candidate_customers = []
            state.products = []
            state.candidate_products = {}
            state.items = []
            state.shipping = ShippingInfo()
            state.extracted_hints = {}

    # extracted_hints — 整字段替换（field reassignment），避免 LangGraph model_fields_set 陷阱
    # 只在抽到非 null/empty 时合并，避免覆盖跨轮已有值
    new_hints = dict(state.extracted_hints)
    if parsed.get("customer_name"):
        new_hints["customer_name"] = parsed["customer_name"]
    if parsed.get("product_hints"):
        new_hints["product_hints"] = parsed["product_hints"]
    if parsed.get("items_raw"):
        new_hints["items_raw"] = parsed["items_raw"]
    state.extracted_hints = new_hints

    # shipping — 同样整字段替换（先读旧值，只覆盖有新值的字段）
    shipping = parsed.get("shipping") or {}
    new_address = shipping.get("address") or state.shipping.address
    new_contact = shipping.get("contact") or state.shipping.contact
    new_phone = shipping.get("phone") or state.shipping.phone
    state.shipping = ShippingInfo(address=new_address, contact=new_contact, phone=new_phone)
    return state
