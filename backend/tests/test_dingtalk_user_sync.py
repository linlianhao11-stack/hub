from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_daily_audit_revokes_offboarded_users():
    """钉钉返回的现役员工列表中没有的，已绑定的标记 revoked。"""
    from hub.cron.dingtalk_user_sync import daily_employee_audit
    from hub.models import ChannelUserBinding, HubUser

    user1 = await HubUser.create(display_name="A")
    user2 = await HubUser.create(display_name="B")
    await ChannelUserBinding.create(
        hub_user=user1, channel_type="dingtalk", channel_userid="m_active",
        status="active",
    )
    await ChannelUserBinding.create(
        hub_user=user2, channel_type="dingtalk", channel_userid="m_offboarded",
        status="active",
    )

    dingtalk_client = AsyncMock()
    dingtalk_client.fetch_active_userids = AsyncMock(return_value={"m_active"})

    await daily_employee_audit(dingtalk_client)

    b1 = await ChannelUserBinding.filter(channel_userid="m_active").first()
    b2 = await ChannelUserBinding.filter(channel_userid="m_offboarded").first()
    assert b1.status == "active"
    assert b2.status == "revoked"
    assert b2.revoked_reason == "daily_audit"


@pytest.mark.asyncio
async def test_daily_audit_skips_already_revoked():
    from hub.cron.dingtalk_user_sync import daily_employee_audit
    from hub.models import ChannelUserBinding, HubUser

    user = await HubUser.create(display_name="C")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m_old",
        status="revoked", revoked_reason="self_unbind",
        revoked_at=datetime.now(UTC),
    )

    dingtalk_client = AsyncMock()
    dingtalk_client.fetch_active_userids = AsyncMock(return_value=set())

    await daily_employee_audit(dingtalk_client)

    b = await ChannelUserBinding.filter(channel_userid="m_old").first()
    assert b.revoked_reason == "self_unbind"
