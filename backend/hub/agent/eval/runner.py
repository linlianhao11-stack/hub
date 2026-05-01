"""Plan 6 Task 16：LLM Eval 框架。

agent 是非确定性系统；EvalRunner 用 mock LLM 跑 gold set 验证 tool 调用 + 输出文本符合期望。
设计：mock LLM 按 case.mock_llm_responses 顺序回放，避免 CI 跑真 LLM 烧钱。
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hub.agent.types import AgentLLMResponse, AgentResult, ToolCall

logger = logging.getLogger("hub.agent.eval.runner")


@dataclass
class CaseResult:
    case_id: str
    category: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    actual_tools: list[str] = field(default_factory=list)
    actual_text: str | None = None


@dataclass
class EvalReport:
    total: int
    passed: int
    failed: int
    case_results: list[CaseResult] = field(default_factory=list)

    @property
    def satisfaction_pct(self) -> float:
        if self.total == 0:
            return 0.0
        return round(self.passed / self.total * 100.0, 2)

    def by_category(self) -> dict[str, dict]:
        """按 category 聚合通过率。"""
        groups: dict[str, dict] = {}
        for r in self.case_results:
            cat = r.category or "unknown"
            if cat not in groups:
                groups[cat] = {"total": 0, "passed": 0}
            groups[cat]["total"] += 1
            if r.passed:
                groups[cat]["passed"] += 1
        for cat, g in groups.items():
            g["satisfaction_pct"] = (
                round(g["passed"] / g["total"] * 100.0, 2)
                if g["total"] > 0
                else 0.0
            )
        return groups


def load_gold_set(path: str | Path | None = None) -> list[dict]:
    """从 yaml 文件加载 gold set。默认路径 hub/agent/eval/gold_set.yaml。"""
    if path is None:
        path = Path(__file__).parent / "gold_set.yaml"
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    if not isinstance(data, list):
        raise ValueError(f"gold_set 应是 list，得到 {type(data)}")
    return data


def _build_mock_llm_responses(case: dict) -> list[AgentLLMResponse]:
    """把 case.mock_llm_responses 转成 AgentLLMResponse 序列。"""
    out: list[AgentLLMResponse] = []
    for resp in case.get("mock_llm_responses", []):
        tool_calls: list[ToolCall] = []
        for i, tc in enumerate(resp.get("tool_calls") or []):
            name = tc.get("name")
            if not name:
                raise ValueError(
                    f"case {case['id']} 第 {len(out)+1} round 第 {i+1} tool_call 缺 name 字段"
                )
            tool_calls.append(ToolCall(
                id=f"call_{case['id']}_{len(out)}_{i}",
                name=name,
                args=tc.get("args", {}),
            ))
        out.append(AgentLLMResponse(
            text=resp.get("text"),
            tool_calls=tool_calls,
            usage_prompt_tokens=100,
            usage_completion_tokens=50,
            raw_message={
                "role": "assistant",
                "content": resp.get("text"),
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.args, ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ] if tool_calls else None,
            },
        ))
    return out


class EvalRunner:
    """跑 gold set 评估。"""

    def __init__(self, agent: Any):
        self.agent = agent

    async def run(self, gold_set: list[dict]) -> EvalReport:
        """跑全 gold set；返回 EvalReport。

        Plan 6 Task 16 第一版串行跑（mock 模式 ~10s 不是瓶颈）。
        follow-up（Task 19 真 LLM）：加 max_concurrency 用 asyncio.Semaphore；
        串行 30 × 10s = 5min，并发 5 路降到 ~1min。
        """
        results: list[CaseResult] = []
        for case in gold_set:
            result = await self._run_one(case)
            results.append(result)

        passed = sum(1 for r in results if r.passed)
        return EvalReport(
            total=len(results),
            passed=passed,
            failed=len(results) - passed,
            case_results=results,
        )

    async def _run_one(self, case: dict) -> CaseResult:
        """跑单条 case。

        策略：
        1. 把 self.agent.llm 临时换成 mock（按 mock_llm_responses 序列回放）
        2. 调 agent.run（用最小 mock 的 hub_user_id / conversation_id）
        3. 从 registry.call 拦截历史 + result.text 提取 actual_tools / actual_text
        4. evaluate vs case.expected_*
        """
        if "user_input" not in case:
            return CaseResult(
                case_id=case.get("id", "(no-id)"),
                category=case.get("category", "unknown"),
                passed=False,
                reasons=["case 缺 user_input 字段"],
            )

        actual_tools: list[str] = []
        actual_text: str | None = None
        reasons: list[str] = []

        # mock LLM 回放
        responses = _build_mock_llm_responses(case)
        original_chat = self.agent.llm.chat
        call_idx = {"i": 0}

        async def fake_chat(messages: list[dict], *args: Any, **kwargs: Any) -> AgentLLMResponse:
            i = call_idx["i"]
            if i >= len(responses):
                raise RuntimeError(
                    f"case {case['id']} mock_llm_responses 用完（{len(responses)}）"
                    f"但 LLM 又被调了第 {i + 1} 次"
                )
            call_idx["i"] += 1
            return responses[i]

        self.agent.llm.chat = fake_chat

        # mock registry.call 拦截 tool_calls 序列（不真调 ERP）
        original_call = self.agent.registry.call

        async def fake_tool_call(name: str, args: dict, **kwargs: Any) -> dict:
            actual_tools.append(name)
            # 简单 fake 返回
            return {"items": [], "_eval_mocked": True}

        self.agent.registry.call = fake_tool_call

        try:
            result: AgentResult = await self.agent.run(
                user_message=case["user_input"],
                hub_user_id=999,  # eval 专用
                conversation_id=f"eval-{case['id']}",
                acting_as=999,
            )
            if result.kind == "error":
                actual_text = result.error
            elif result.kind in ("text", "clarification"):
                actual_text = result.text
            else:
                actual_text = result.text or result.error  # fallback
        except Exception as e:
            reasons.append(f"agent.run 抛错: {e}")
            return CaseResult(
                case_id=case["id"],
                category=case.get("category", "unknown"),
                passed=False,
                reasons=reasons,
                actual_tools=actual_tools,
                actual_text=actual_text,
            )
        finally:
            self.agent.llm.chat = original_chat
            self.agent.registry.call = original_call

        # ===== 评估 =====

        # 1. expected_tools：actual_tools 应包含 expected_tools 的所有项（顺序不强制）
        for tool in case.get("expected_tools") or []:
            if tool not in actual_tools:
                reasons.append(f"缺少期望 tool {tool}")

        # 2. expected_clarification
        expected_clarification = case.get("expected_clarification", False)
        actual_is_clarif = result.kind == "clarification"
        clarif_mismatch = False
        if expected_clarification and not actual_is_clarif:
            reasons.append("期望 clarification 但实际不是")
            clarif_mismatch = True
        if not expected_clarification and actual_is_clarif:
            reasons.append("不应 clarification 但实际是")
            clarif_mismatch = True

        # 3. expected_text_contains / expected_clarification_contains
        # clarification 类型不符时跳过 keyword 检查（避免冗余 reason）
        if not clarif_mismatch:
            text_to_check = actual_text or ""
            contains_keys = (
                case.get("expected_clarification_contains")
                if expected_clarification
                else case.get("expected_text_contains")
            )
            for keyword in contains_keys or []:
                if keyword not in text_to_check:
                    reasons.append(f"输出不含期望关键词 '{keyword}'")

        return CaseResult(
            case_id=case["id"],
            category=case.get("category", "unknown"),
            passed=len(reasons) == 0,
            reasons=reasons,
            actual_tools=actual_tools,
            actual_text=actual_text,
        )
