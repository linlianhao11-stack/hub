"""Task 5: PromptBuilder + 业务词典 + 同义词 + few-shots 测试（12 case）。"""
import pytest
from hub.agent.prompt.builder import PromptBuilder
from hub.agent.prompt.business_dict import DEFAULT_DICT, render_dict
from hub.agent.prompt.synonyms import DEFAULT_SYNONYMS, render_synonyms, normalize
from hub.agent.prompt.few_shots import DEFAULT_FEW_SHOTS, FewShot, render_few_shots
from hub.agent.memory.types import Memory, ConversationHistory


def test_business_dict_has_required_keys():
    """plan 列出的关键术语都在字典里。"""
    required = {"压货", "周转", "回款", "上次价格"}
    assert required.issubset(DEFAULT_DICT.keys())
    assert len(DEFAULT_DICT) >= 30  # plan 要求 ≥30 条


def test_synonyms_has_required_canonicals():
    """plan 列出的关键 canonical 都在。"""
    required = {"客户", "商品", "销售额"}
    assert required.issubset(DEFAULT_SYNONYMS.keys())
    assert len(DEFAULT_SYNONYMS) >= 20


def test_synonyms_normalize_replaces_alternatives():
    """normalize 把 alternative 形式替换成 canonical。"""
    text = "查一下顾客的总销售"
    result = normalize(text)
    assert "顾客" not in result
    assert "客户" in result
    assert "总销售" not in result  # 已被替换为"销售额"
    assert "销售额" in result


def test_few_shots_default_count_min():
    """plan 要求 ≥6 个 few-shot。"""
    assert len(DEFAULT_FEW_SHOTS) >= 6


def test_few_shots_includes_write_confirm_pattern():
    """必须有写 tool 必须先 confirm 的范例。"""
    has_write_with_confirm = any(
        s.expected_text and ("是" in s.expected_text or "确认" in s.expected_text)
        for s in DEFAULT_FEW_SHOTS
    )
    assert has_write_with_confirm


def test_render_dict_format():
    """render_dict 输出每行 `- key: value`。"""
    out = render_dict({"压货": "库龄高商品", "周转": "周转率"})
    assert "- 压货: 库龄高商品" in out
    assert "- 周转: 周转率" in out


def test_builder_includes_all_sections():
    """build() 输出包含所有规定段落。"""
    builder = PromptBuilder()
    prompt = builder.build()
    assert "[业务词典]" in prompt
    assert "[同义词]" in prompt
    assert "[Few-shot 例子]" in prompt
    assert "[行为准则]" in prompt
    # header 必须在最前
    assert prompt.startswith("你是 HUB 业务 Agent")


def test_builder_renders_user_memory():
    """memory.user 含 facts → prompt 含[当前用户偏好]段。"""
    builder = PromptBuilder()
    memory = Memory(
        session=ConversationHistory(conversation_id="c1"),
        user={"facts": [{"fact": "用户偏好分期付款"}], "preferences": {"lang": "zh"}},
        customers={},
        products={},
    )
    prompt = builder.build(memory=memory)
    assert "[当前用户偏好]" in prompt
    assert "用户偏好分期付款" in prompt
    assert "lang" in prompt  # preferences 也要渲染


def test_builder_renders_customer_and_product_memory():
    """memory.customers/products 渲染。"""
    builder = PromptBuilder()
    memory = Memory(
        session=ConversationHistory(conversation_id="c1"),
        user={},
        customers={9: {"facts": [{"fact": "阿里巴巴月单 50 万"}]}},
        products={42: {"facts": [{"fact": "讯飞 X5 春节断货"}]}},
    )
    prompt = builder.build(memory=memory)
    assert "[当前对话提及的客户]" in prompt
    assert "客户 9" in prompt
    assert "阿里巴巴月单 50 万" in prompt
    assert "[当前对话提及的商品]" in prompt
    assert "商品 42" in prompt
    assert "讯飞 X5 春节断货" in prompt


def test_builder_omits_memory_section_when_empty():
    """memory.user/customers/products 全空 → 不渲染 memory 段。"""
    builder = PromptBuilder()
    memory = Memory(
        session=ConversationHistory(conversation_id="c1"),
        user={},
        customers={},
        products={},
    )
    prompt = builder.build(memory=memory)
    assert "[当前用户偏好]" not in prompt
    assert "[当前对话提及的客户]" not in prompt


def test_builder_includes_tools_schema_summary():
    """tools_schema 传入时 prompt 含 tool 名列表。"""
    builder = PromptBuilder()
    schema = [
        {"function": {"name": "search_products", "description": "..."}},
        {"function": {"name": "check_inventory", "description": "..."}},
    ]
    prompt = builder.build(tools_schema=schema)
    assert "[当前可用 tool" in prompt
    assert "search_products" in prompt
    assert "check_inventory" in prompt


def test_builder_custom_dict_overrides_default():
    """传入 custom business_dict 覆盖默认。"""
    custom = {"测试词": "测试值"}
    builder = PromptBuilder(business_dict=custom)
    prompt = builder.build()
    assert "测试词: 测试值" in prompt
    # 业务词典段落里不含默认词"- 压货:"（few-shots 段可能还有"压货"字面，但 dict 段不含）
    dict_section_start = prompt.index("[业务词典]")
    dict_section_end = prompt.index("[同义词]")
    dict_section = prompt[dict_section_start:dict_section_end]
    assert "- 压货:" not in dict_section  # 默认词典已被自定义词典替换
