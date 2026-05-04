"""Plan 6 v9 Task 2.5：register_all_tools 注册完整性 + 子图分布测试。"""
from hub.agent.tools import register_all_tools
from hub.agent.tools.registry import ToolRegistry


def test_all_16_tools_registered():
    """16 schema 全部注册成功（strict mode 严格校验）。

    Plan v9 expected 17 (includes get_product_customer_prices) but that
    function does not exist as a separate tool — adapter via get_customer_history.
    Actual count: 11 read + 5 write = 16.
    """
    reg = ToolRegistry()
    register_all_tools(reg)
    # 总数：所有 _subgraphs 中至少出现一次的 schema
    all_schemas = set()
    for sg in ("query", "contract", "quote", "voucher", "adjust_price", "adjust_stock"):
        for s in reg.schemas_for_subgraph(sg):
            all_schemas.add(s["function"]["name"])
    assert len(all_schemas) == 16


def test_subgraph_distribution_matches_spec():
    """spec §5.1 表（plan v9 deviation: contract/adjust_price 4→3）：
    query 11, contract 3, quote 3, voucher 3, adjust_price 3, adjust_stock 3, chat 0.
    """
    reg = ToolRegistry()
    register_all_tools(reg)
    assert len(reg.schemas_for_subgraph("query")) == 11
    assert len(reg.schemas_for_subgraph("contract")) == 3
    assert len(reg.schemas_for_subgraph("quote")) == 3
    assert len(reg.schemas_for_subgraph("voucher")) == 3
    assert len(reg.schemas_for_subgraph("adjust_price")) == 3
    assert len(reg.schemas_for_subgraph("adjust_stock")) == 3
    assert len(reg.schemas_for_subgraph("chat")) == 0


def test_chat_subgraph_has_zero_tools():
    """chat 子图 0 tool — 重要架构保证（spec §5.1）。"""
    reg = ToolRegistry()
    register_all_tools(reg)
    assert reg.schemas_for_subgraph("chat") == []
