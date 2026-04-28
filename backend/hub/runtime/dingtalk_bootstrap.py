"""从数据库加载已加密的钉钉/ERP 配置 → 解密 → 装配 adapter / sender / service。

gateway 与 worker 共用这套装配代码，避免两边逻辑漂移。

设计要点：
- 配置缺失时返回 None，调用方决定是 fail-fast 还是 warn-and-skip
- 所有 secret 解密走 hub.crypto.decrypt_secret（master_key + HKDF）
- 装配产物自带 close()：方便 lifespan 退出时释放 httpx 连接
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from hub.adapters.channel.dingtalk_sender import DingTalkSender
from hub.adapters.channel.dingtalk_stream import DingTalkStreamAdapter
from hub.adapters.downstream.erp4 import Erp4Adapter
from hub.crypto import decrypt_secret
from hub.models import ChannelApp, DownstreamSystem
from hub.services.binding_service import BindingService
from hub.services.erp_active_cache import ErpActiveCache
from hub.services.identity_service import IdentityService

logger = logging.getLogger("hub.runtime.dingtalk_bootstrap")


@dataclass
class DingtalkConfig:
    """钉钉接入配置（已解密明文）。"""
    app_key: str
    app_secret: str
    robot_code: str | None  # robot_id 同时作 robotCode 用于 oToMessages.batchSend


@dataclass
class Erp4Config:
    """ERP-4 下游配置（已解密明文）。"""
    base_url: str
    api_key: str


@dataclass
class BootstrappedClients:
    """gateway/worker 装配后的客户端集合。"""
    erp_adapter: Erp4Adapter | None = None
    erp_cache: ErpActiveCache | None = None
    identity_service: IdentityService | None = None
    binding_service: BindingService | None = None
    dingtalk_sender: DingTalkSender | None = None
    dingtalk_stream: DingTalkStreamAdapter | None = None

    async def aclose(self) -> None:
        """释放所有 httpx 连接 + Stream 长连接。"""
        if self.dingtalk_stream is not None:
            try:
                await self.dingtalk_stream.stop()
            except Exception:
                logger.exception("DingTalkStreamAdapter 关闭异常")
        if self.dingtalk_sender is not None:
            try:
                await self.dingtalk_sender.aclose()
            except Exception:
                logger.exception("DingTalkSender 关闭异常")
        if self.erp_adapter is not None:
            try:
                await self.erp_adapter.aclose()
            except Exception:
                logger.exception("Erp4Adapter 关闭异常")


async def load_active_dingtalk_app() -> DingtalkConfig | None:
    """读 channel_app 表里 status=active 的钉钉应用配置（取最新一条）。"""
    app = await ChannelApp.filter(channel_type="dingtalk", status="active").order_by("-id").first()
    if app is None:
        return None
    try:
        app_key = decrypt_secret(app.encrypted_app_key, purpose="config_secrets")
        app_secret = decrypt_secret(app.encrypted_app_secret, purpose="config_secrets")
    except Exception:
        logger.exception("钉钉 app secret 解密失败 channel_app.id=%s", app.id)
        return None
    return DingtalkConfig(
        app_key=app_key,
        app_secret=app_secret,
        robot_code=app.robot_id,
    )


async def load_active_erp_system() -> Erp4Config | None:
    """读 downstream_system 表里 status=active 的 ERP 配置（取最新一条）。"""
    sys = await DownstreamSystem.filter(
        downstream_type="erp", status="active",
    ).order_by("-id").first()
    if sys is None:
        return None
    try:
        api_key = decrypt_secret(sys.encrypted_apikey, purpose="config_secrets")
    except Exception:
        logger.exception("ERP api key 解密失败 downstream_system.id=%s", sys.id)
        return None
    return Erp4Config(base_url=sys.base_url, api_key=api_key)


async def bootstrap_dingtalk_clients(*, with_stream: bool) -> BootstrappedClients:
    """读取 DB 配置 → 装配全套客户端。

    Args:
        with_stream: gateway 传 True（启 Stream 长连接）；worker 传 False（仅 sender）。

    返回：
        BootstrappedClients——任何字段为 None 表示对应配置缺失。
        gateway / worker 各自决定缺失时是 warn-and-skip 还是 fail-fast。
    """
    erp_cfg = await load_active_erp_system()
    dt_cfg = await load_active_dingtalk_app()

    out = BootstrappedClients()

    if erp_cfg is not None:
        out.erp_adapter = Erp4Adapter(base_url=erp_cfg.base_url, api_key=erp_cfg.api_key)
        out.erp_cache = ErpActiveCache(out.erp_adapter)
        out.identity_service = IdentityService(out.erp_cache)
        out.binding_service = BindingService(out.erp_adapter)
    else:
        logger.warning("downstream_system(erp/active) 未配置，ERP 相关服务跳过装配")

    if dt_cfg is not None:
        if not dt_cfg.robot_code:
            logger.warning(
                "channel_app(dingtalk).robot_id 缺失，DingTalkSender 无法主动 push 消息",
            )
        else:
            out.dingtalk_sender = DingTalkSender(
                app_key=dt_cfg.app_key,
                app_secret=dt_cfg.app_secret,
                robot_code=dt_cfg.robot_code,
            )
        if with_stream:
            out.dingtalk_stream = DingTalkStreamAdapter(
                app_key=dt_cfg.app_key,
                app_secret=dt_cfg.app_secret,
                robot_id=dt_cfg.robot_code,
            )
    else:
        logger.warning("channel_app(dingtalk/active) 未配置，钉钉接入跳过装配")

    return out
