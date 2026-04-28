from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_uses_recent_customer_price():
    from hub.strategy.pricing import DefaultPricingStrategy
    erp = AsyncMock()
    erp.get_product_customer_prices = AsyncMock(return_value={
        "records": [
            {"unit_price": "98.50", "order_no": "O1", "order_date": "2026-04-01T00:00:00Z"},
        ],
    })
    erp.search_products = AsyncMock(return_value={
        "items": [{"id": 1, "retail_price": "120.00"}],
    })

    strat = DefaultPricingStrategy(erp_adapter=erp)
    info = await strat.get_price(product_id=1, customer_id=10, acting_as=42)
    assert info.unit_price == "98.50"
    assert info.source == "customer_history"
    assert info.customer_id == 10


@pytest.mark.asyncio
async def test_no_customer_uses_fallback_retail_when_provided():
    """调用方传入 fallback_retail_price → 直接用，不调 ERP。"""
    from hub.strategy.pricing import DefaultPricingStrategy
    erp = AsyncMock()
    strat = DefaultPricingStrategy(erp_adapter=erp)
    info = await strat.get_price(
        product_id=1, customer_id=None, acting_as=42,
        fallback_retail_price="120.00",
    )
    assert info.unit_price == "120.00"
    assert info.source == "retail"
    erp.get_product.assert_not_called()


@pytest.mark.asyncio
async def test_no_customer_no_fallback_uses_get_product():
    """无 fallback → 用 get_product(product_id) 精确反查。"""
    from hub.strategy.pricing import DefaultPricingStrategy
    erp = AsyncMock()
    erp.get_product = AsyncMock(return_value={"id": 1, "retail_price": "120.00"})
    strat = DefaultPricingStrategy(erp_adapter=erp)
    info = await strat.get_price(product_id=1, customer_id=None, acting_as=42)
    assert info.unit_price == "120.00"
    assert info.source == "retail"
    erp.get_product.assert_awaited_once_with(product_id=1, acting_as_user_id=42)


@pytest.mark.asyncio
async def test_empty_history_uses_fallback_first():
    from hub.strategy.pricing import DefaultPricingStrategy
    erp = AsyncMock()
    erp.get_product_customer_prices = AsyncMock(return_value={"records": []})
    strat = DefaultPricingStrategy(erp_adapter=erp)
    info = await strat.get_price(
        product_id=1, customer_id=10, acting_as=42,
        fallback_retail_price="120.00",
    )
    assert info.source == "retail"
    assert info.unit_price == "120.00"


@pytest.mark.asyncio
async def test_history_query_failure_falls_back():
    """ERP 历史价查询异常 → 降级到 fallback_retail_price。"""
    from hub.adapters.downstream.erp4 import ErpSystemError
    from hub.strategy.pricing import DefaultPricingStrategy
    erp = AsyncMock()
    erp.get_product_customer_prices = AsyncMock(side_effect=ErpSystemError("timeout"))
    strat = DefaultPricingStrategy(erp_adapter=erp)
    info = await strat.get_price(
        product_id=1, customer_id=10, acting_as=42,
        fallback_retail_price="120.00",
    )
    assert info.source == "retail"
    assert info.unit_price == "120.00"
    assert info.notes is not None


@pytest.mark.asyncio
async def test_history_403_raises_not_falls_back():
    """历史价 403 → ErpPermissionError 向上抛，不降级零售价。"""
    from hub.adapters.downstream.erp4 import ErpPermissionError
    from hub.strategy.pricing import DefaultPricingStrategy
    erp = AsyncMock()
    erp.get_product_customer_prices = AsyncMock(side_effect=ErpPermissionError("403"))
    strat = DefaultPricingStrategy(erp_adapter=erp)
    with pytest.raises(ErpPermissionError):
        await strat.get_price(
            product_id=1, customer_id=10, acting_as=42,
            fallback_retail_price="120.00",
        )


@pytest.mark.asyncio
async def test_decimal_string_preserved():
    """价格保持 Decimal 字符串，不转 float。"""
    from hub.strategy.pricing import DefaultPricingStrategy
    erp = AsyncMock()
    erp.get_product_customer_prices = AsyncMock(return_value={
        "records": [{"unit_price": "98.500000", "order_no": "x", "order_date": "x"}],
    })
    erp.search_products = AsyncMock(return_value={"items": []})

    strat = DefaultPricingStrategy(erp_adapter=erp)
    info = await strat.get_price(product_id=1, customer_id=10, acting_as=42)
    assert isinstance(info.unit_price, str)
    assert info.unit_price in ("98.500000", "98.50")
