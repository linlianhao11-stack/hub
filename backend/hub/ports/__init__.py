"""6 个核心端口/策略 Protocol 聚合。"""
from hub.ports.capability_provider import AICapability, CapabilityProvider
from hub.ports.channel_adapter import (
    ChannelAdapter,
    InboundMessage,
    OutboundMessage,
    OutboundMessageType,
)
from hub.ports.downstream_adapter import DownstreamAdapter
from hub.ports.intent_parser import IntentParser, ParsedIntent
from hub.ports.pricing_strategy import PriceInfo, PricingStrategy
from hub.ports.task_runner import TaskInfo, TaskRunner, TaskStatus

__all__ = [
    "ChannelAdapter", "InboundMessage", "OutboundMessage", "OutboundMessageType",
    "DownstreamAdapter",
    "CapabilityProvider", "AICapability",
    "IntentParser", "ParsedIntent",
    "TaskRunner", "TaskStatus", "TaskInfo",
    "PricingStrategy", "PriceInfo",
]
