import pytest


def test_channel_adapter_protocol_imports():
    from hub.ports import (
        ChannelAdapter, InboundMessage, OutboundMessage, OutboundMessageType
    )
    assert ChannelAdapter is not None
    msg = InboundMessage(
        channel_type="dingtalk", channel_userid="m1",
        conversation_id="c1", content="hi", content_type="text",
        timestamp=1700000000, raw_payload={},
    )
    assert msg.channel_type == "dingtalk"


def test_downstream_adapter_protocol_imports():
    from hub.ports import DownstreamAdapter
    assert DownstreamAdapter is not None


def test_capability_provider_protocol_imports():
    from hub.ports import CapabilityProvider, AICapability
    assert AICapability is not None


def test_intent_parser_imports():
    from hub.ports import IntentParser, ParsedIntent
    intent = ParsedIntent(intent_type="query_product", fields={"sku": "X"}, confidence=0.9)
    assert intent.confidence == 0.9


def test_task_runner_imports():
    from hub.ports import TaskRunner, TaskStatus
    assert TaskStatus.QUEUED.value == "queued"


def test_pricing_strategy_imports():
    from hub.ports import PricingStrategy, PriceInfo
    p = PriceInfo(unit_price="100.00", source="retail", customer_id=None)
    assert p.unit_price == "100.00"


def test_mock_implementation_satisfies_channel_adapter():
    """实例化一个 Mock 实现确认 Protocol 鸭子类型成立。"""
    from hub.ports import ChannelAdapter

    class MockChannel:
        channel_type = "mock"
        async def start(self): pass
        async def stop(self): pass
        async def send_message(self, channel_userid, message): pass
        def on_message(self, handler): pass

    m: ChannelAdapter = MockChannel()  # 类型注解兼容
    assert m.channel_type == "mock"
