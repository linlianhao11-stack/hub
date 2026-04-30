"""Plan 6 Task 6：ChainAgent 公共类型。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


# ===== LLM 响应 =====

@dataclass
class ToolCall:
    """LLM 请求调用的单个 tool。"""
    id: str  # OpenAI tool_call.id
    name: str
    args: dict[str, Any]


@dataclass
class AgentLLMResponse:
    """从 OpenAI chat completions 解析后的统一响应。"""
    text: str | None = None  # assistant message content
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage_prompt_tokens: int = 0
    usage_completion_tokens: int = 0
    raw_message: dict | None = None  # 原始 message dict（assistant role + tool_calls，用于回灌 history）

    @property
    def is_tool_call(self) -> bool:
        return bool(self.tool_calls)

    @property
    def is_clarification(self) -> bool:
        """判断是否是反问澄清（启发式）。

        v2 加固（review M-1）：仅当满足以下全部条件才视作 clarification：
        1. text 非空
        2. text 末尾含 ? 或 ？
        3. 长度 < 200（短反问；防长复述含问号被误判）
        4. 不含 tool_calls（带 tool_call 的不算 clarification）
        """
        if not self.text or self.tool_calls:
            return False
        if len(self.text) >= 200:
            return False
        return self.text.rstrip().endswith(("?", "？"))


# ===== Agent 最终结果 =====

@dataclass
class AgentResult:
    kind: str  # "text" / "clarification" / "error"
    text: str | None = None
    error: str | None = None

    @classmethod
    def text_result(cls, text: str) -> "AgentResult":
        return cls(kind="text", text=text)

    @classmethod
    def clarification(cls, text: str) -> "AgentResult":
        return cls(kind="clarification", text=text)

    @classmethod
    def error_result(cls, error: str) -> "AgentResult":
        return cls(kind="error", error=error)


# ===== 错误类 =====

class AgentMaxRoundsExceeded(Exception):
    """5 round 后还要调 tool → 抛此错。"""

class PromptTooLargeError(Exception):
    """ContextBuilder 必保上下文已超 budget；外层应降级 RuleParser。"""

