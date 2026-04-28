"""IdentityService：渠道身份 → HUB 身份 → 检查下游启用状态。

inbound handler 必须在进入业务前调 resolve()，禁用用户不能用机器人。
"""
from __future__ import annotations

from dataclasses import dataclass

from hub.models import ChannelUserBinding, DownstreamIdentity


@dataclass
class IdentityResolution:
    found: bool
    erp_active: bool
    hub_user_id: int | None = None
    erp_user_id: int | None = None
    binding: ChannelUserBinding | None = None


class IdentityService:
    def __init__(self, erp_active_cache):
        self.erp_cache = erp_active_cache

    async def resolve(self, dingtalk_userid: str) -> IdentityResolution:
        """钉钉 userid → HUB 身份 + ERP 启用状态。"""
        binding = await ChannelUserBinding.filter(
            channel_type="dingtalk", channel_userid=dingtalk_userid, status="active",
        ).select_related("hub_user").first()

        if binding is None:
            return IdentityResolution(found=False, erp_active=False)

        di = await DownstreamIdentity.filter(
            hub_user_id=binding.hub_user_id, downstream_type="erp",
        ).first()
        if di is None:
            # 绑定了但没 ERP 身份（异常）；视为已找到 HUB 身份但 ERP 不可用
            return IdentityResolution(
                found=True, erp_active=False,
                hub_user_id=binding.hub_user_id, erp_user_id=None, binding=binding,
            )

        active = await self.erp_cache.is_active(
            hub_user=binding.hub_user, erp_user_id=di.downstream_user_id,
        )
        return IdentityResolution(
            found=True, erp_active=active,
            hub_user_id=binding.hub_user_id, erp_user_id=di.downstream_user_id,
            binding=binding,
        )
