from hub.agent.react.prompts import SYSTEM_PROMPT


def test_system_prompt_mentions_key_concepts():
    """system prompt 必须告诉 LLM 关键约定。"""
    assert "ERP" in SYSTEM_PROMPT or "erp" in SYSTEM_PROMPT.lower()
    # 写操作 plan-then-execute
    assert "confirm_action" in SYSTEM_PROMPT
    assert "pending_confirmation" in SYSTEM_PROMPT
    # 复用上份合同
    assert "get_recent_drafts" in SYSTEM_PROMPT
    # 钉钉文案约定
    assert "钉钉" in SYSTEM_PROMPT
    # 中文回复
    assert "中文" in SYSTEM_PROMPT


def test_system_prompt_does_not_teach_nonexistent_tool_args():
    """关键回归断言：prompt 不能教 LLM 传不存在的参数。

    `get_recent_drafts` 真实签名只有 `limit`（详见 read.py）。早期版本 prompt 写过
    `get_recent_drafts(draft_type="contract", limit=5)`,LLM 真按 prompt 传 draft_type
    会导致 tool schema 报错,直接打死"同样/上次/复用"核心场景。本断言锁住该回归。
    """
    assert "draft_type" not in SYSTEM_PROMPT, (
        "system prompt 不应该出现 `draft_type` —— get_recent_drafts 没这个参数"
    )


def test_system_prompt_size_reasonable():
    """token 控制 — DeepSeek 中文 tokenizer 约 1 char ≈ 1 token。
    控制在 6000 char 以内（约 6K token,DeepSeek prompt cache 友好）。
    """
    assert len(SYSTEM_PROMPT) < 6000, (
        f"SYSTEM_PROMPT 太长（{len(SYSTEM_PROMPT)} chars）— 精简或拆分"
    )
