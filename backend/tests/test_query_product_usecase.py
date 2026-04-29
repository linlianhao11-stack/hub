from unittest.mock import AsyncMock

import pytest


def _price(unit_price="120.00", source="retail", customer_id=None, notes=None):
    return type("P", (), {
        "unit_price": unit_price, "source": source,
        "customer_id": customer_id, "notes": notes,
    })()


@pytest.mark.asyncio
async def test_unique_product_returns_card():
    from hub.usecases.query_product import QueryProductUseCase
    erp = AsyncMock()
    erp.search_products = AsyncMock(return_value={
        "items": [{"id": 1, "sku": "SKU100", "name": "鼠标", "retail_price": "120.00"}],
    })

    pricing = AsyncMock()
    pricing.get_price = AsyncMock(return_value=_price())

    sender = AsyncMock()
    state = AsyncMock()

    uc = QueryProductUseCase(erp=erp, pricing=pricing, sender=sender, state=state)
    await uc.execute(
        sku_or_keyword="SKU100", dingtalk_userid="m1", acting_as=42,
    )

    sender.send_text.assert_awaited_once()
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "鼠标" in sent
    assert "120.00" in sent


@pytest.mark.asyncio
async def test_multi_match_saves_state_and_sends_choice_card():
    from hub.usecases.query_product import QueryProductUseCase
    erp = AsyncMock()
    erp.search_products = AsyncMock(return_value={
        "items": [
            {"id": 1, "sku": "SKU100", "name": "鼠标 A", "retail_price": "100"},
            {"id": 2, "sku": "SKU101", "name": "鼠标 B", "retail_price": "110"},
        ],
    })

    sender = AsyncMock()
    state = AsyncMock()

    uc = QueryProductUseCase(erp=erp, pricing=AsyncMock(), sender=sender, state=state)
    await uc.execute(
        sku_or_keyword="鼠标", dingtalk_userid="m1", acting_as=42,
    )

    sender.send_text.assert_awaited_once()
    state.save.assert_awaited_once()
    saved = state.save.call_args.args[1]
    assert saved["resource"] == "商品"
    assert len(saved["candidates"]) == 2


@pytest.mark.asyncio
async def test_no_match_returns_friendly():
    from hub.usecases.query_product import QueryProductUseCase
    erp = AsyncMock()
    erp.search_products = AsyncMock(return_value={"items": []})

    sender = AsyncMock()
    uc = QueryProductUseCase(erp=erp, pricing=AsyncMock(), sender=sender, state=AsyncMock())
    await uc.execute(sku_or_keyword="zzz", dingtalk_userid="m1", acting_as=42)

    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "未找到" in sent or "没找到" in sent


@pytest.mark.asyncio
async def test_erp_permission_denied_translates_to_user_msg():
    from hub.adapters.downstream.erp4 import ErpPermissionError
    from hub.usecases.query_product import QueryProductUseCase

    erp = AsyncMock()
    erp.search_products = AsyncMock(side_effect=ErpPermissionError("403"))

    sender = AsyncMock()
    uc = QueryProductUseCase(erp=erp, pricing=AsyncMock(), sender=sender, state=AsyncMock())
    await uc.execute(sku_or_keyword="X", dingtalk_userid="m1", acting_as=42)

    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "权限" in sent


@pytest.mark.asyncio
async def test_circuit_open_returns_friendly_message():
    from hub.circuit_breaker import CircuitOpenError
    from hub.usecases.query_product import QueryProductUseCase

    erp = AsyncMock()
    erp.search_products = AsyncMock(side_effect=CircuitOpenError("熔断"))

    sender = AsyncMock()
    uc = QueryProductUseCase(erp=erp, pricing=AsyncMock(), sender=sender, state=AsyncMock())
    await uc.execute(sku_or_keyword="X", dingtalk_userid="m1", acting_as=42)

    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "暂时不可用" in sent or "稍后" in sent


@pytest.mark.asyncio
async def test_execute_selected_renders_card_with_name_and_stock():
    """编号选择后 execute_selected 用候选项 dict 直接渲染：必含 name + 库存。"""
    from hub.usecases.query_product import QueryProductUseCase
    pricing = AsyncMock()
    pricing.get_price = AsyncMock(return_value=_price())
    sender = AsyncMock()
    uc = QueryProductUseCase(erp=AsyncMock(), pricing=pricing, sender=sender, state=AsyncMock())

    selected = {
        "id": 1, "sku": "SKU100", "name": "鼠标 X",
        "retail_price": "120.00", "stock": 50,
    }
    await uc.execute_selected(product=selected, dingtalk_userid="m1", acting_as=42)

    sender.send_text.assert_awaited_once()
    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "鼠标 X" in sent
    assert "120.00" in sent
    assert "50" in sent


@pytest.mark.asyncio
async def test_execute_selected_passes_fallback_retail_to_pricing():
    """execute_selected 必须把候选项的 retail_price 传给 PricingStrategy 当 fallback。"""
    from hub.usecases.query_product import QueryProductUseCase
    pricing = AsyncMock()
    pricing.get_price = AsyncMock(return_value=_price())
    uc = QueryProductUseCase(erp=AsyncMock(), pricing=pricing, sender=AsyncMock(), state=AsyncMock())
    await uc.execute_selected(
        product={"id": 1, "name": "X", "retail_price": "99.99"},
        dingtalk_userid="m1", acting_as=42,
    )
    args = pricing.get_price.call_args.kwargs
    assert args["fallback_retail_price"] == "99.99"


@pytest.mark.asyncio
async def test_sender_failure_propagates_for_unique_render():
    """sender.send_text 抛错（钉钉短暂故障）时，异常向上冒泡让 WorkerRuntime 转死信。

    回归 P2：早期 _send/_send_message 吞 sender 异常 → 用户收不到卡片但任务被 ACK，
    问题被掩盖。修复后任何发送失败都让上层转死信。
    """
    from hub.usecases.query_product import QueryProductUseCase

    erp = AsyncMock()
    erp.search_products = AsyncMock(return_value={
        "items": [{"id": 1, "sku": "SKU100", "name": "鼠标", "retail_price": "120"}],
    })
    pricing = AsyncMock()
    pricing.get_price = AsyncMock(return_value=_price())

    sender = AsyncMock()
    sender.send_text = AsyncMock(side_effect=RuntimeError("dingtalk down"))

    uc = QueryProductUseCase(erp=erp, pricing=pricing, sender=sender, state=AsyncMock())

    with pytest.raises(RuntimeError, match="dingtalk down"):
        await uc.execute(sku_or_keyword="SKU100", dingtalk_userid="m1", acting_as=42)


@pytest.mark.asyncio
async def test_sender_failure_propagates_for_error_path():
    """错误路径（PERM/CIRCUIT/ERP_TIMEOUT 文案）的 send_text 失败也要上抛。"""
    from hub.adapters.downstream.erp4 import ErpPermissionError
    from hub.usecases.query_product import QueryProductUseCase

    erp = AsyncMock()
    erp.search_products = AsyncMock(side_effect=ErpPermissionError("403"))
    sender = AsyncMock()
    sender.send_text = AsyncMock(side_effect=RuntimeError("dingtalk down"))

    uc = QueryProductUseCase(erp=erp, pricing=AsyncMock(), sender=sender, state=AsyncMock())
    with pytest.raises(RuntimeError, match="dingtalk down"):
        await uc.execute(sku_or_keyword="X", dingtalk_userid="m1", acting_as=42)


@pytest.mark.asyncio
async def test_erp_5xx_uses_retry_friendly():
    from hub.adapters.downstream.erp4 import ErpSystemError
    from hub.usecases.query_product import QueryProductUseCase

    erp = AsyncMock()
    erp.search_products = AsyncMock(side_effect=ErpSystemError("503"))

    sender = AsyncMock()
    uc = QueryProductUseCase(erp=erp, pricing=AsyncMock(), sender=sender, state=AsyncMock())
    await uc.execute(sku_or_keyword="X", dingtalk_userid="m1", acting_as=42)

    sent = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "繁忙" in sent or "稍后" in sent
