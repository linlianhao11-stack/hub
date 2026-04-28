import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_hub_user_create():
    from hub.models import HubUser
    u = await HubUser.create(display_name="测试")
    assert u.id is not None
    assert u.status == "active"


@pytest.mark.asyncio
async def test_channel_user_binding_unique():
    from hub.models import HubUser, ChannelUserBinding
    u = await HubUser.create(display_name="x")
    await ChannelUserBinding.create(
        hub_user=u, channel_type="dingtalk", channel_userid="m1",
    )
    from tortoise.exceptions import IntegrityError
    with pytest.raises(IntegrityError):
        await ChannelUserBinding.create(
            hub_user=u, channel_type="dingtalk", channel_userid="m1",
        )


@pytest.mark.asyncio
async def test_downstream_identity_unique_per_downstream():
    from hub.models import HubUser, DownstreamIdentity
    u = await HubUser.create(display_name="y")
    await DownstreamIdentity.create(hub_user=u, downstream_type="erp", downstream_user_id=42)
    from tortoise.exceptions import IntegrityError
    with pytest.raises(IntegrityError):
        await DownstreamIdentity.create(hub_user=u, downstream_type="erp", downstream_user_id=999)


@pytest.mark.asyncio
async def test_hub_role_permission_many_to_many():
    from hub.models import HubRole, HubPermission
    role = await HubRole.create(code="r1", name="角色 1", is_builtin=False)
    perm = await HubPermission.create(
        code="p1", resource="platform", sub_resource="x", action="read",
        name="测试权限",
    )
    await role.permissions.add(perm)
    fetched = await HubRole.get(id=role.id).prefetch_related("permissions")
    perms = [p async for p in fetched.permissions]
    assert len(perms) == 1
    assert perms[0].code == "p1"


@pytest.mark.asyncio
async def test_task_log_and_payload_relationship():
    from hub.models import TaskLog, TaskPayload
    t = await TaskLog.create(
        task_id="abc-123", task_type="query_product",
        channel_type="dingtalk", channel_userid="m1", status="queued",
    )
    p = await TaskPayload.create(
        task_log=t,
        encrypted_request=b"\x00" * 32,
        encrypted_response=b"\x00" * 32,
        expires_at=datetime.now(timezone.utc),
    )
    assert p.task_log_id == t.id


@pytest.mark.asyncio
async def test_downstream_system_encrypted_apikey_field():
    from hub.models import DownstreamSystem
    ds = await DownstreamSystem.create(
        downstream_type="erp", name="ERP 测试",
        base_url="http://localhost:8090", encrypted_apikey=b"\x00" * 32,
        apikey_scopes=["act_as_user", "system_calls"],
    )
    assert ds.id is not None
    assert ds.apikey_scopes == ["act_as_user", "system_calls"]
