"""Plan 6 Task 16：LLM Eval 框架 + gold set 集成 CI 阈值断言。"""
from __future__ import annotations
import pytest
from pathlib import Path
from collections import Counter
from unittest.mock import MagicMock

from hub.agent.eval import EvalRunner, EvalReport, load_gold_set
from hub.agent.eval.runner import CaseResult, _build_mock_llm_responses
from hub.agent.types import AgentLLMResponse, AgentResult


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def gold_set():
    return load_gold_set()


# ===========================================================================
# gold_set.yaml 结构验证（5 case）
# ===========================================================================

def test_load_gold_set_yaml_30_cases(gold_set):
    """gold_set.yaml 含 30 条标注样本。"""
    assert len(gold_set) == 30


def test_gold_set_categories_distribution(gold_set):
    """各 category 数量符合设计（plan §10.2）。"""
    cats = Counter(c["category"] for c in gold_set)
    assert cats["query"] == 12
    assert cats["multi_step"] == 6
    assert cats["write"] == 5
    assert cats["business_dict"] == 4
    assert cats["error"] == 3


def test_gold_set_required_fields(gold_set):
    """每条 case 必有 id / category / user_input / mock_llm_responses。"""
    for case in gold_set:
        assert case.get("id"), f"case 缺 id: {case}"
        assert case.get("category"), f"{case['id']} 缺 category"
        assert case.get("user_input"), f"{case['id']} 缺 user_input"
        assert "mock_llm_responses" in case, f"{case['id']} 缺 mock_llm_responses"


def test_gold_set_unique_ids(gold_set):
    """所有 case id 唯一。"""
    ids = [c["id"] for c in gold_set]
    assert len(ids) == len(set(ids)), (
        f"重复 id: {[i for i in ids if ids.count(i) > 1]}"
    )


def test_gold_set_mock_llm_responses_not_empty(gold_set):
    """每条 case 的 mock_llm_responses 非空。"""
    for case in gold_set:
        resps = case.get("mock_llm_responses", [])
        assert len(resps) >= 1, f"{case['id']} mock_llm_responses 为空"


# ===========================================================================
# EvalReport 数据结构（3 case）
# ===========================================================================

def test_eval_report_satisfaction_pct():
    """EvalReport.satisfaction_pct 计算正确。"""
    r = EvalReport(total=10, passed=8, failed=2)
    assert r.satisfaction_pct == 80.0

    r_zero = EvalReport(total=0, passed=0, failed=0)
    assert r_zero.satisfaction_pct == 0.0

    r_full = EvalReport(total=5, passed=5, failed=0)
    assert r_full.satisfaction_pct == 100.0


def test_eval_report_by_category():
    """EvalReport.by_category 聚合通过率正确。"""
    r = EvalReport(
        total=4, passed=3, failed=1,
        case_results=[
            CaseResult(case_id="a", category="query", passed=True),
            CaseResult(case_id="b", category="query", passed=True),
            CaseResult(case_id="c", category="write", passed=True),
            CaseResult(case_id="d", category="write", passed=False),
        ],
    )
    by_cat = r.by_category()
    assert by_cat["query"]["total"] == 2
    assert by_cat["query"]["passed"] == 2
    assert by_cat["query"]["satisfaction_pct"] == 100.0
    assert by_cat["write"]["total"] == 2
    assert by_cat["write"]["passed"] == 1
    assert by_cat["write"]["satisfaction_pct"] == 50.0


def test_eval_report_by_category_empty():
    """EvalReport.by_category 无结果时返空 dict。"""
    r = EvalReport(total=0, passed=0, failed=0)
    by_cat = r.by_category()
    assert by_cat == {}


# ===========================================================================
# _build_mock_llm_responses 转换（3 case）
# ===========================================================================

def test_build_mock_llm_responses_text_only():
    """text-only mock 转换。"""
    case = {
        "id": "test-text-only",
        "mock_llm_responses": [{"text": "hello world"}],
    }
    resps = _build_mock_llm_responses(case)
    assert len(resps) == 1
    assert resps[0].text == "hello world"
    assert resps[0].tool_calls == []
    assert resps[0].usage_prompt_tokens == 100
    assert resps[0].usage_completion_tokens == 50


def test_build_mock_llm_responses_with_tool_calls():
    """tool_calls mock 转换成 AgentLLMResponse。"""
    case = {
        "id": "test-tool-calls",
        "mock_llm_responses": [
            {"tool_calls": [{"name": "search_products", "args": {"query": "讯飞x5"}}]},
            {"text": "查询完成"},
        ],
    }
    resps = _build_mock_llm_responses(case)
    assert len(resps) == 2
    assert resps[0].tool_calls[0].name == "search_products"
    assert resps[0].tool_calls[0].args == {"query": "讯飞x5"}
    assert resps[0].text is None
    assert resps[1].text == "查询完成"
    assert resps[1].tool_calls == []


def test_build_mock_llm_responses_multiple_tool_calls():
    """单 response 含多个 tool_call。"""
    case = {
        "id": "test-multi-calls",
        "mock_llm_responses": [
            {
                "tool_calls": [
                    {"name": "search_customers", "args": {"query": "阿里"}},
                    {"name": "search_products", "args": {"query": "x5"}},
                ]
            },
        ],
    }
    resps = _build_mock_llm_responses(case)
    assert len(resps) == 1
    assert len(resps[0].tool_calls) == 2
    assert resps[0].tool_calls[0].name == "search_customers"
    assert resps[0].tool_calls[1].name == "search_products"
    # tool_call id 唯一
    ids = [tc.id for tc in resps[0].tool_calls]
    assert len(ids) == len(set(ids))


# ===========================================================================
# EvalRunner 集成：轻量 stub agent（不依赖真 LLM/ERP/DB）
# ===========================================================================

class _StubLLMClient:
    """轻量 LLM stub，chat 方法会被 EvalRunner 临时替换。"""

    async def chat(self, messages, *args, **kwargs):
        raise NotImplementedError("应被 EvalRunner mock 替换")


class _StubRegistry:
    """轻量 ToolRegistry stub，call 方法会被 EvalRunner 临时替换。"""

    async def call(self, name, args, **kwargs):
        raise NotImplementedError("应被 EvalRunner mock 替换")

    async def schema_for_user(self, hub_user_id):
        return []


class _StubAgent:
    """轻量 Agent stub：llm.chat 和 registry.call 会被 EvalRunner 临时 mock，
    run() 自己按 mock_llm_responses 序列驱动 tool 调用，
    正确模拟 ChainAgent 主循环逻辑（tool_call → registry.call → 再调 llm）。
    """

    MAX_ROUNDS = 6

    def __init__(self):
        self.llm = _StubLLMClient()
        self.registry = _StubRegistry()

    async def run(self, user_message: str, *,
                  hub_user_id: int,
                  conversation_id: str,
                  acting_as: int,
                  channel_userid: str = "",
                  user_just_confirmed: bool = False) -> AgentResult:
        """简化版主循环：与 ChainAgent.run 结构对齐，足以驱动 EvalRunner mock。"""
        history: list[dict] = [{"role": "user", "content": user_message}]

        for _ in range(self.MAX_ROUNDS):
            llm_resp: AgentLLMResponse = await self.llm.chat(history)

            if llm_resp.tool_calls:
                # 调 registry（被 EvalRunner 替换为 fake_tool_call）
                for tc in llm_resp.tool_calls:
                    tool_result = await self.registry.call(
                        tc.name, tc.args,
                        hub_user_id=hub_user_id,
                        acting_as=acting_as,
                        conversation_id=conversation_id,
                        round_idx=0,
                    )
                    history.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": str(tool_result),
                    })
                continue  # 下一 round

            # 终态：text 或 clarification
            final_text = llm_resp.text or "（无回复）"
            if llm_resp.is_clarification:
                return AgentResult.clarification(final_text)
            return AgentResult.text_result(final_text)

        return AgentResult.error_result("超出最大 round")


@pytest.mark.asyncio
async def test_eval_runner_single_case_query():
    """EvalRunner 跑单条 query case，评估 tool 序列 + 文本。"""
    case = {
        "id": "test_single_query",
        "category": "query",
        "user_input": "查讯飞x5 的库存",
        "expected_tools": ["search_products", "check_inventory"],
        "expected_clarification": False,
        "expected_text_contains": ["库存"],
        "mock_llm_responses": [
            {"tool_calls": [{"name": "search_products", "args": {"query": "讯飞x5"}}]},
            {"tool_calls": [{"name": "check_inventory", "args": {"product_id": 1}}]},
            {"text": "讯飞 X5 库存 49 台"},
        ],
    }

    agent = _StubAgent()
    runner = EvalRunner(agent=agent)
    report = await runner.run([case])

    assert report.total == 1
    assert report.passed == 1
    assert report.failed == 0
    assert report.satisfaction_pct == 100.0
    assert report.case_results[0].actual_tools == ["search_products", "check_inventory"]


@pytest.mark.asyncio
async def test_eval_runner_single_case_clarification():
    """EvalRunner 跑 clarification case（LLM 直接文本反问）。"""
    case = {
        "id": "test_single_clarif",
        "category": "query",
        "user_input": "查客户应收",
        "expected_tools": [],
        "expected_clarification": True,
        "expected_clarification_contains": ["哪个客户"],
        "mock_llm_responses": [
            {"text": "请问查哪个客户的应收？"},
        ],
    }

    agent = _StubAgent()
    runner = EvalRunner(agent=agent)
    report = await runner.run([case])

    assert report.total == 1
    assert report.passed == 1
    cr = report.case_results[0]
    assert cr.actual_tools == []
    assert "哪个客户" in (cr.actual_text or "")


@pytest.mark.asyncio
async def test_eval_runner_fail_missing_tool():
    """EvalRunner 检测到缺 expected tool → case 失败。"""
    case = {
        "id": "test_missing_tool",
        "category": "query",
        "user_input": "查库存",
        "expected_tools": ["search_products", "check_inventory"],
        "expected_clarification": False,
        "mock_llm_responses": [
            # 只调了 search_products，漏了 check_inventory
            {"tool_calls": [{"name": "search_products", "args": {"query": "x5"}}]},
            {"text": "找到了"},
        ],
    }

    agent = _StubAgent()
    runner = EvalRunner(agent=agent)
    report = await runner.run([case])

    assert report.total == 1
    assert report.failed == 1
    cr = report.case_results[0]
    assert not cr.passed
    assert any("check_inventory" in r for r in cr.reasons)


@pytest.mark.asyncio
async def test_eval_runner_fail_missing_keyword():
    """EvalRunner 检测到输出缺关键词 → case 失败。"""
    case = {
        "id": "test_missing_keyword",
        "category": "query",
        "user_input": "查库存",
        "expected_tools": ["check_inventory"],
        "expected_clarification": False,
        "expected_text_contains": ["库存", "台数"],  # "台数"不在输出
        "mock_llm_responses": [
            {"tool_calls": [{"name": "check_inventory", "args": {"product_id": 1}}]},
            {"text": "库存正常"},  # 含"库存"但无"台数"
        ],
    }

    agent = _StubAgent()
    runner = EvalRunner(agent=agent)
    report = await runner.run([case])

    assert report.failed == 1
    cr = report.case_results[0]
    assert any("台数" in r for r in cr.reasons)


@pytest.mark.asyncio
async def test_eval_runner_runs_gold_set_no_error(gold_set):
    """EvalRunner 跑全部 30 条 gold set 不抛异常；report 结构完整。"""
    agent = _StubAgent()
    runner = EvalRunner(agent=agent)
    report = await runner.run(gold_set)

    assert report.total == 30
    assert report.passed + report.failed == 30
    assert 0.0 <= report.satisfaction_pct <= 100.0
    # 每条 case 都有对应结果
    assert len(report.case_results) == 30
    # by_category 包含 5 个 category
    by_cat = report.by_category()
    assert set(by_cat.keys()) == {"query", "multi_step", "write", "business_dict", "error"}


@pytest.mark.asyncio
async def test_eval_runner_meets_80pct_threshold(gold_set):
    """CI 阈值断言：使用 stub agent 跑完整 gold set，满意度 ≥ 80%。

    stub agent 忠实回放 mock_llm_responses → expected_tools/text 均满足。
    80% 是 plan §3236 的 CI 阈值，低于此值阻止 merge。
    """
    agent = _StubAgent()
    runner = EvalRunner(agent=agent)
    report = await runner.run(gold_set)

    # 输出失败 case 细节方便 CI 调试
    failed_cases = [(r.case_id, r.reasons) for r in report.case_results if not r.passed]
    assert report.satisfaction_pct >= 80.0, (
        f"满意度 {report.satisfaction_pct}% < 80%，失败 case：{failed_cases}"
    )
