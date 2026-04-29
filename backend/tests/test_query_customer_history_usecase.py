from unittest.mock import AsyncMock

import pytest


def _price(unit_price="98.00", source="customer_history", customer_id=9, notes=None):
    return type("P", (), {
        "unit_price": unit_price, "source": source,
        "customer_id": customer_id, "notes": notes,
    })()


def _make_uc(erp, sender, state, pricing=None):
    from hub.usecases.query_customer_history import QueryCustomerHistoryUseCase
    return QueryCustomerHistoryUseCase(
        erp=erp,
        pricing=pricing or AsyncMock(),
        sender=sender, state=state,
    )


@pytest.mark.asyncio
async def test_unique_customer_unique_product_renders_history():
    erp = AsyncMock()
    erp.search_customers = AsyncMock(return_value={
        "items": [{"id": 9, "name": "阿里巴巴集团"}],
    })
    erp.search_products = AsyncMock(return_value={
        "items": [{"id": 1, "sku": "SKU100", "name": "鼠标", "retail_price": "120"}],
    })
    erp.get_product_customer_prices = AsyncMock(return_value={
        "records": [
            {"unit_price": "98.00", "order_no": "O1", "order_date": "2026-04-01T00:00:00Z"},
            {"unit_price": "99.00", "order_no": "O2", "order_date": "2026-03-01T00:00:00Z"},
        ],
    })

    pricing = AsyncMock()
    pricing.get_price = AsyncMock(return_value=_price())

    sender = AsyncMock()
    state = AsyncMock()
    uc = _make_uc(erp, sender, state, pricing)
    await uc.execute(
        sku_or_keyword="SKU100", customer_keyword="阿里",
        dingtalk_userid="m1", acting_as=42,
    )
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "阿里巴巴集团" in sent
    assert "98.00" in sent or "99.00" in sent


@pytest.mark.asyncio
async def test_multi_customer_saves_state():
    erp = AsyncMock()
    erp.search_customers = AsyncMock(return_value={
        "items": [
            {"id": 9, "name": "阿里巴巴"}, {"id": 10, "name": "阿里云"},
        ],
    })

    sender = AsyncMock()
    state = AsyncMock()
    uc = _make_uc(erp, sender, state)
    await uc.execute(
        sku_or_keyword="SKU100", customer_keyword="阿里",
        dingtalk_userid="m1", acting_as=42,
    )

    state.save.assert_awaited_once()
    saved = state.save.call_args.args[1]
    assert saved["resource"] == "客户"
    assert saved.get("sku_or_keyword") == "SKU100"


@pytest.mark.asyncio
async def test_multi_product_saves_state_with_resolved_customer():
    """客户唯一但商品多命中 → 保存状态时记下 customer_id。"""
    erp = AsyncMock()
    erp.search_customers = AsyncMock(return_value={
        "items": [{"id": 9, "name": "阿里"}],
    })
    erp.search_products = AsyncMock(return_value={
        "items": [
            {"id": 1, "sku": "SKU100A", "name": "鼠标 A"},
            {"id": 2, "sku": "SKU100B", "name": "鼠标 B"},
        ],
    })

    state = AsyncMock()
    sender = AsyncMock()
    uc = _make_uc(erp, sender, state)
    await uc.execute(
        sku_or_keyword="SKU100", customer_keyword="阿里",
        dingtalk_userid="m1", acting_as=42,
    )
    state.save.assert_awaited_once()
    saved = state.save.call_args.args[1]
    assert saved["resource"] == "商品"
    assert saved["customer_id"] == 9


@pytest.mark.asyncio
async def test_customer_not_found():
    erp = AsyncMock()
    erp.search_customers = AsyncMock(return_value={"items": []})

    sender = AsyncMock()
    uc = _make_uc(erp, sender, AsyncMock())
    await uc.execute(
        sku_or_keyword="X", customer_keyword="不存在的客户",
        dingtalk_userid="m1", acting_as=42,
    )

    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "不存在的客户" in sent or "客户" in sent
    assert "未找到" in sent or "没找到" in sent


@pytest.mark.asyncio
async def test_empty_history_still_renders_with_retail():
    erp = AsyncMock()
    erp.search_customers = AsyncMock(return_value={
        "items": [{"id": 9, "name": "阿里"}],
    })
    erp.search_products = AsyncMock(return_value={
        "items": [{"id": 1, "sku": "SKU100", "name": "鼠标", "retail_price": "120"}],
    })
    erp.get_product_customer_prices = AsyncMock(return_value={"records": []})

    pricing = AsyncMock()
    pricing.get_price = AsyncMock(return_value=_price(unit_price="120.00", source="retail"))

    sender = AsyncMock()
    uc = _make_uc(erp, sender, AsyncMock(), pricing)
    await uc.execute(
        sku_or_keyword="SKU100", customer_keyword="阿里",
        dingtalk_userid="m1", acting_as=42,
    )
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "120" in sent


@pytest.mark.asyncio
async def test_erp_permission_denied():
    from hub.adapters.downstream.erp4 import ErpPermissionError
    erp = AsyncMock()
    erp.search_customers = AsyncMock(side_effect=ErpPermissionError("403"))

    sender = AsyncMock()
    uc = _make_uc(erp, sender, AsyncMock())
    await uc.execute(
        sku_or_keyword="X", customer_keyword="X",
        dingtalk_userid="m1", acting_as=42,
    )
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "权限" in sent


@pytest.mark.asyncio
async def test_history_price_403_translates_to_perm_message():
    """历史价 403 → 用户看到无权限提示，不是降级的零售价/空历史。"""
    from hub.adapters.downstream.erp4 import ErpPermissionError
    erp = AsyncMock()
    erp.search_customers = AsyncMock(return_value={
        "items": [{"id": 9, "name": "阿里"}],
    })
    erp.search_products = AsyncMock(return_value={
        "items": [{"id": 1, "sku": "SKU100", "name": "鼠标", "retail_price": "120"}],
    })
    erp.get_product_customer_prices = AsyncMock(side_effect=ErpPermissionError("403"))

    pricing = AsyncMock()
    pricing.get_price = AsyncMock(side_effect=ErpPermissionError("403"))

    sender = AsyncMock()
    uc = _make_uc(erp, sender, AsyncMock(), pricing)
    await uc.execute(
        sku_or_keyword="SKU100", customer_keyword="阿里",
        dingtalk_userid="m1", acting_as=42,
    )
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "权限" in sent or "PERM" not in sent


@pytest.mark.asyncio
async def test_sender_failure_propagates_for_history_render():
    """历史价渲染 send_text 失败时异常向上冒泡，不静默 ACK。

    回归 P2：早期 _send/_send_message 吞 sender 异常 → 用户收不到回复但任务被 ACK。
    """
    erp = AsyncMock()
    erp.search_customers = AsyncMock(return_value={
        "items": [{"id": 9, "name": "阿里"}],
    })
    erp.search_products = AsyncMock(return_value={
        "items": [{"id": 1, "sku": "SKU100", "name": "鼠标", "retail_price": "120"}],
    })
    erp.get_product_customer_prices = AsyncMock(return_value={"records": []})

    pricing = AsyncMock()
    pricing.get_price = AsyncMock(return_value=_price(unit_price="120.00", source="retail"))

    sender = AsyncMock()
    sender.send_text = AsyncMock(side_effect=RuntimeError("dingtalk down"))

    uc = _make_uc(erp, sender, AsyncMock(), pricing)
    with pytest.raises(RuntimeError, match="dingtalk down"):
        await uc.execute(
            sku_or_keyword="SKU100", customer_keyword="阿里",
            dingtalk_userid="m1", acting_as=42,
        )


@pytest.mark.asyncio
async def test_sender_failure_propagates_for_error_path():
    """错误路径（PERM/CIRCUIT/MATCH_NOT_FOUND 文案）的 send_text 失败也要上抛。"""
    from hub.adapters.downstream.erp4 import ErpPermissionError

    erp = AsyncMock()
    erp.search_customers = AsyncMock(side_effect=ErpPermissionError("403"))
    sender = AsyncMock()
    sender.send_text = AsyncMock(side_effect=RuntimeError("dingtalk down"))

    uc = _make_uc(erp, sender, AsyncMock())
    with pytest.raises(RuntimeError, match="dingtalk down"):
        await uc.execute(
            sku_or_keyword="X", customer_keyword="X",
            dingtalk_userid="m1", acting_as=42,
        )


@pytest.mark.asyncio
async def test_circuit_open_returns_friendly():
    from hub.circuit_breaker import CircuitOpenError
    erp = AsyncMock()
    erp.search_customers = AsyncMock(side_effect=CircuitOpenError("open"))

    sender = AsyncMock()
    uc = _make_uc(erp, sender, AsyncMock())
    await uc.execute(
        sku_or_keyword="X", customer_keyword="X",
        dingtalk_userid="m1", acting_as=42,
    )
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "暂时不可用" in sent or "稍后" in sent
