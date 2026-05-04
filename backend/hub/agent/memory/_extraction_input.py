"""MemoryWriter 抽事实的 prompt 常量 + 把 LangGraph messages 渲染成对话原文 helpers。

拆出本模块是为了让 writer.py 不超 250 行。本模块是 writer 的私有辅助,
只暴露 EXTRACTION_PROMPT / build_extraction_input / extract_tool_call_names /
parse_tool_message_content。
"""
from __future__ import annotations

import json
from typing import Any


EXTRACTION_PROMPT = """从下面的对话历史中抽取事实，写入三层 memory：

1. user_facts: 当前用户偏好 / 工作习惯（如"喜欢付款条款 30 天"）
2. customer_facts: 关于客户的事实（如"阿里巴巴最近三月平均月单 50 万"）
3. product_facts: 关于商品的事实（如"讯飞 X5 Pro 春节断货 2 周"）

每条 fact 必须含 kind 字段：
  - "reference"：参考性事实（习惯 / 倾向 / 过往现象），LLM 看到后可作背景理解
  - "decision"：决策性事实（具体价格 / 折扣率 / 信用额度等会直接影响报价的数字）

【严禁】抽取以下内容写入 fact（会污染 prompt，导致 LLM 跳过实时数据校验）：
  - 具体价格数字（"客户 A 的 X5 单价 ¥3950"） → 价格永远以查实时 ERP 为准
  - 具体折扣率（"客户 B 享 95 折"）         → 折扣以审批流为准，不能从历史对话固化
  - 具体信用额度 / 余额                      → 余额以 get_customer_balance 为准
  - 一次性的订单数量 / 金额                   → 订单数据以 ERP 实时数据为准

【鼓励】抽取参考性的习惯 / 模式 / 现象：
  ✅ "客户阿里付款节奏偏慢，平均 45 天"（参考性，提示 LLM 关注催款）
  ✅ "讯飞 X5 春节通常断货 2 周"（参考性，提示 LLM 主动告知）
  ✅ "用户偏好按历史价下单，下单前会问 get_customer_history"（用户工作习惯）

格式 JSON：
{
  "user_facts": [{"fact": "string", "kind": "reference|decision", "confidence": 0.0-1.0}],
  "customer_facts": [{"customer_id": int, "fact": "string", "kind": "reference|decision", "confidence": 0.0-1.0}],
  "product_facts": [{"product_id": int, "fact": "string", "kind": "reference|decision", "confidence": 0.0-1.0}]
}

只抽有商业价值的事实；闲聊 / 重复 / 无意义内容跳过。confidence < 0.6 不写。
默认 kind="reference"；只在事实是数字结论时才标 "decision"。
"""

MAX_MESSAGES_FOR_EXTRACTION = 30  # 上限：避免极端长会话拖慢 LLM 抽取


def msg_class_name(msg: Any) -> str:
    """获取 message class 名称(避免强制依赖 langchain import 路径做 isinstance)。"""
    return type(msg).__name__


def extract_tool_call_names(messages: list) -> list[str]:
    """从 AIMessage.tool_calls 收集所有调用过的 tool 名称。"""
    names: list[str] = []
    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in tool_calls:
            if isinstance(tc, dict):
                name = tc.get("name") or ""
            else:
                name = getattr(tc, "name", "") or ""
            if name:
                names.append(name)
    return names


def parse_tool_message_content(msg: Any) -> Any:
    """ToolMessage.content 可能是 dict / str(json) / str(任意),归一成 Python 对象。"""
    content = getattr(msg, "content", None)
    if isinstance(content, (dict, list)):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return content
    return content


def build_extraction_input(messages: list) -> str:
    """把 LangGraph BaseMessage 列表渲染成对话原文 + tool 调用摘要。

    - HumanMessage → "用户: {content}"
    - AIMessage    → "助手: {content}"  + 若有 tool_calls 加 "  → 调 {name}({args})"
    - ToolMessage  → "  ← {name} 返回: {content[:200]}"
    - SystemMessage / 其他 → 跳过
    - 多于 MAX_MESSAGES_FOR_EXTRACTION 时只取最后 N 条(对话尾部上下文最重要)
    """
    recent = messages[-MAX_MESSAGES_FOR_EXTRACTION:]
    lines: list[str] = []
    for msg in recent:
        cls = msg_class_name(msg)
        content = getattr(msg, "content", "") or ""
        if cls == "HumanMessage":
            lines.append(f"用户: {content}")
        elif cls == "AIMessage":
            if content:
                lines.append(f"助手: {content}")
            tool_calls = getattr(msg, "tool_calls", None) or []
            for tc in tool_calls:
                if isinstance(tc, dict):
                    name = tc.get("name", "")
                    args = tc.get("args", {})
                else:
                    name = getattr(tc, "name", "")
                    args = getattr(tc, "args", {})
                if name:
                    lines.append(f"  → 调 {name}({args})")
        elif cls == "ToolMessage":
            name = getattr(msg, "name", "tool")
            truncated = str(content)[:200]
            lines.append(f"  ← {name} 返回: {truncated}")
    return "\n".join(lines)
