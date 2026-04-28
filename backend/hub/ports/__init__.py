"""6 个核心端口/策略 Protocol 聚合。"""
from hub.ports.channel_adapter import (
    ChannelAdapter, InboundMessage, OutboundMessage, OutboundMessageType,
)
from hub.ports.downstream_adapter import DownstreamAdapter
from hub.ports.capability_provider import CapabilityProvider, AICapability
from hub.ports.intent_parser import IntentParser, ParsedIntent
from hub.ports.task_runner import TaskRunner, TaskStatus, TaskInfo
from hub.ports.pricing_strategy import PricingStrategy, PriceInfo

__all__ = [
    "ChannelAdapter", "InboundMessage", "OutboundMessage", "OutboundMessageType",
    "DownstreamAdapter",
    "CapabilityProvider", "AICapability",
    "IntentParser", "ParsedIntent",
    "TaskRunner", "TaskStatus", "TaskInfo",
    "PricingStrategy", "PriceInfo",
]
