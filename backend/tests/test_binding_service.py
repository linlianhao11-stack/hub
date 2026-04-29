import asyncio
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_initiate_binding_user_exists_generates_code():
    """ERP 用户存在 → 调 ERP 生成绑定码 → 返回回复文案。"""
    from hub.services.binding_service import BindingService

    erp = AsyncMock()
    erp.user_exists = AsyncMock(return_value=True)
    erp.generate_binding_code = AsyncMock(return_value={"code": "742815", "expires_in": 300})

    svc = BindingService(erp_adapter=erp)
    result = await svc.initiate_binding(dingtalk_userid="m1", erp_username="zhangsan")

    assert result.success is True
    assert "742815" in result.reply_text
    assert "5 分钟" in result.reply_text or "5分钟" in result.reply_text
    erp.generate_binding_code.assert_awaited_once_with(
        erp_username="zhangsan", dingtalk_userid="m1",
    )


@pytest.mark.asyncio
async def test_initiate_binding_user_not_exists():
    from hub.services.binding_service import BindingService

    erp = AsyncMock()
    erp.user_exists = AsyncMock(return_value=False)

    svc = BindingService(erp_adapter=erp)
    result = await svc.initiate_binding(dingtalk_userid="m1", erp_username="nobody")

    assert result.success is False
    assert "未找到" in result.reply_text
    erp.generate_binding_code.assert_not_called()


@pytest.mark.asyncio
async def test_already_bound_returns_friendly_message():
    """已经绑定过 → 提示先解绑。"""
    from hub.models import ChannelUserBinding, HubUser
    from hub.services.binding_service import BindingService

    user = await HubUser.create(display_name="A")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m1", status="active",
    )

    erp = AsyncMock()
    svc = BindingService(erp_adapter=erp)
    result = await svc.initiate_binding(dingtalk_userid="m1", erp_username="x")

    assert result.success is False
    assert "已经绑定" in result.reply_text
    erp.user_exists.assert_not_called()


@pytest.mark.asyncio
async def test_unbind_self():
    """用户主动解绑自己。"""
    from hub.models import ChannelUserBinding, HubUser
    from hub.services.binding_service import BindingService

    user = await HubUser.create(display_name="B")
    binding = await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m2", status="active",
    )

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.unbind_self(dingtalk_userid="m2")

    assert result.success is True
    assert "已解绑" in result.reply_text
    refreshed = await ChannelUserBinding.get(id=binding.id)
    assert refreshed.status == "revoked"


@pytest.mark.asyncio
async def test_unbind_when_not_bound():
    from hub.services.binding_service import BindingService

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.unbind_self(dingtalk_userid="never_bound")

    assert result.success is False
    assert "未绑定" in result.reply_text or "没有" in result.reply_text


@pytest.mark.asyncio
async def test_confirm_final_writes_binding():
    """ERP 反向通知 confirm-final → 写入 binding + downstream_identity + 默认角色。"""
    from hub.models import ChannelUserBinding, DownstreamIdentity, HubRole, HubUserRole
    from hub.seed import run_seed
    from hub.services.binding_service import BindingService

    await run_seed()

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.confirm_final(
        token_id=1,
        dingtalk_userid="m3", erp_user_id=99, erp_username="zhao",
        erp_display_name="赵三",
    )
    assert result.success is True
    assert result.hub_user_id is not None

    binding = await ChannelUserBinding.filter(channel_userid="m3").first()
    assert binding is not None
    assert binding.status == "active"

    di = await DownstreamIdentity.filter(
        hub_user_id=result.hub_user_id, downstream_type="erp",
    ).first()
    assert di.downstream_user_id == 99

    role = await HubRole.get(code="bot_user_basic")
    user_roles = await HubUserRole.filter(hub_user_id=result.hub_user_id, role_id=role.id)
    assert len(user_roles) == 1


@pytest.mark.asyncio
async def test_confirm_final_idempotent_by_token_id():
    """同一 token_id 重复 confirm 应直接返回已处理结果。"""
    from hub.models import ChannelUserBinding
    from hub.seed import run_seed
    from hub.services.binding_service import BindingService
    await run_seed()

    svc = BindingService(erp_adapter=AsyncMock())
    r1 = await svc.confirm_final(
        token_id=42,
        dingtalk_userid="m4", erp_user_id=100, erp_username="qian", erp_display_name="钱",
    )
    r2 = await svc.confirm_final(
        token_id=42,
        dingtalk_userid="m4", erp_user_id=100, erp_username="qian", erp_display_name="钱",
    )
    assert r1.success and r2.success
    assert r1.hub_user_id == r2.hub_user_id
    assert r2.note == "already_consumed"
    bindings = await ChannelUserBinding.filter(channel_userid="m4")
    assert len(bindings) == 1


@pytest.mark.asyncio
async def test_confirm_final_conflict_dingtalk_already_bound_to_other_erp():
    """同 dingtalk_userid 已绑到别的 ERP → 拒绝。"""
    from hub.models import ChannelUserBinding, DownstreamIdentity, HubUser
    from hub.seed import run_seed
    from hub.services.binding_service import BindingService
    await run_seed()

    user = await HubUser.create(display_name="A")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m_conflict", status="active",
    )
    await DownstreamIdentity.create(hub_user=user, downstream_type="erp", downstream_user_id=200)

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.confirm_final(
        token_id=50,
        dingtalk_userid="m_conflict",
        erp_user_id=999,
        erp_username="other", erp_display_name="他",
    )
    assert result.success is False
    assert "已绑定" in result.reply_text or "冲突" in result.reply_text


@pytest.mark.asyncio
async def test_confirm_final_conflict_erp_user_owned_by_other_hub_user():
    """同 erp_user_id 已被另一钉钉占用 → 拒绝。"""
    from hub.models import ChannelUserBinding, DownstreamIdentity, HubUser
    from hub.seed import run_seed
    from hub.services.binding_service import BindingService
    await run_seed()

    other = await HubUser.create(display_name="Other")
    await ChannelUserBinding.create(
        hub_user=other, channel_type="dingtalk", channel_userid="m_other", status="active",
    )
    await DownstreamIdentity.create(hub_user=other, downstream_type="erp", downstream_user_id=300)

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.confirm_final(
        token_id=51,
        dingtalk_userid="m_new",
        erp_user_id=300,
        erp_username="x", erp_display_name="X",
    )
    assert result.success is False
    assert "已被" in result.reply_text or "占用" in result.reply_text


@pytest.mark.asyncio
async def test_confirm_final_concurrent_same_erp_user_only_one_wins():
    """两个并发不同 token 但同 erp_user → DownstreamIdentity 唯一约束兜底。"""
    from hub.models import ChannelUserBinding, DownstreamIdentity
    from hub.seed import run_seed
    from hub.services.binding_service import BindingService
    await run_seed()

    svc = BindingService(erp_adapter=AsyncMock())

    async def attempt(token_id: int, dingtalk_userid: str):
        return await svc.confirm_final(
            token_id=token_id,
            dingtalk_userid=dingtalk_userid, erp_user_id=2024,
            erp_username="x", erp_display_name="X",
        )

    await asyncio.gather(
        attempt(token_id=8001, dingtalk_userid="m_concurrent_a"),
        attempt(token_id=8002, dingtalk_userid="m_concurrent_b"),
    )

    dis = await DownstreamIdentity.filter(
        downstream_type="erp", downstream_user_id=2024,
    ).all()
    assert len(dis) == 1, f"期望同 ERP 用户只 1 条 DownstreamIdentity，实际 {len(dis)}"

    winner_hub_user_id = dis[0].hub_user_id
    bindings = await ChannelUserBinding.filter(
        hub_user_id=winner_hub_user_id, status="active",
    ).all()
    assert len(bindings) == 1


@pytest.mark.asyncio
async def test_confirm_final_revoked_rebind_to_different_erp_updates_di():
    """revoked 复活换 ERP → di 应更新。"""
    from hub.models import ChannelUserBinding, DownstreamIdentity, HubUser
    from hub.seed import run_seed
    from hub.services.binding_service import BindingService
    await run_seed()

    user = await HubUser.create(display_name="R")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m_rebind_diff",
        status="revoked", revoked_reason="self_unbind",
    )
    await DownstreamIdentity.create(hub_user=user, downstream_type="erp", downstream_user_id=4001)

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.confirm_final(
        token_id=9001,
        dingtalk_userid="m_rebind_diff",
        erp_user_id=4002,
        erp_username="r2", erp_display_name="R2",
    )
    assert result.success is True

    di = await DownstreamIdentity.filter(
        hub_user_id=user.id, downstream_type="erp",
    ).first()
    assert di.downstream_user_id == 4002

    all_di = await DownstreamIdentity.filter(
        hub_user_id=user.id, downstream_type="erp",
    ).all()
    assert len(all_di) == 1


@pytest.mark.asyncio
async def test_confirm_final_concurrent_same_token_no_dirty_binding():
    """两个并发请求同 token_id，应只有一个赢，且失败方不留绑定副作用。"""
    from hub.models import ChannelUserBinding, ConsumedBindingToken
    from hub.seed import run_seed
    from hub.services.binding_service import BindingService
    await run_seed()

    svc = BindingService(erp_adapter=AsyncMock())

    async def attempt(dingtalk_userid: str, erp_user_id: int):
        return await svc.confirm_final(
            token_id=999,
            dingtalk_userid=dingtalk_userid, erp_user_id=erp_user_id,
            erp_username="x", erp_display_name="X",
        )

    await asyncio.gather(
        attempt("m_concurrent_1", 1001),
        attempt("m_concurrent_2", 1002),
        return_exceptions=False,
    )

    bindings = await ChannelUserBinding.all()
    winner_bindings = [b for b in bindings if b.channel_userid in ("m_concurrent_1", "m_concurrent_2")]
    assert len(winner_bindings) == 1, f"期望 1 个绑定，实际 {len(winner_bindings)}"

    consumed = await ConsumedBindingToken.filter(erp_token_id=999).all()
    assert len(consumed) == 1


@pytest.mark.asyncio
async def test_confirm_final_conflict_does_not_consume_token():
    """冲突场景应整体回滚，token 未被消费。"""
    from hub.models import (
        ChannelUserBinding,
        ConsumedBindingToken,
        DownstreamIdentity,
        HubUser,
    )
    from hub.seed import run_seed
    from hub.services.binding_service import BindingService
    await run_seed()

    user = await HubUser.create(display_name="占位")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m_taken", status="active",
    )
    await DownstreamIdentity.create(hub_user=user, downstream_type="erp", downstream_user_id=500)

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.confirm_final(
        token_id=777,
        dingtalk_userid="m_taken",
        erp_user_id=999,
        erp_username="y", erp_display_name="Y",
    )
    assert result.success is False

    consumed = await ConsumedBindingToken.filter(erp_token_id=777).first()
    assert consumed is None


@pytest.mark.asyncio
async def test_confirm_final_revoked_binding_can_rebind():
    """先前 revoke 的同 dingtalk + 同 erp → 可重新激活。"""
    from hub.models import ChannelUserBinding, DownstreamIdentity, HubUser
    from hub.seed import run_seed
    from hub.services.binding_service import BindingService
    await run_seed()

    user = await HubUser.create(display_name="R")
    await ChannelUserBinding.create(
        hub_user=user, channel_type="dingtalk", channel_userid="m_rebind",
        status="revoked", revoked_reason="self_unbind",
    )
    await DownstreamIdentity.create(hub_user=user, downstream_type="erp", downstream_user_id=400)

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.confirm_final(
        token_id=52,
        dingtalk_userid="m_rebind", erp_user_id=400,
        erp_username="r", erp_display_name="R",
    )
    assert result.success is True
    binding = await ChannelUserBinding.filter(channel_userid="m_rebind").first()
    assert binding.status == "active"


@pytest.mark.asyncio
async def test_confirm_final_attaches_to_existing_hub_user_with_erp_link_no_dingtalk():
    """ERP user 已有 hub_user（admin 后台/setup wizard 创建）但没绑钉钉 →
    confirm_final 应复用 hub_user 挂钉钉，不能创建新 hub_user 撞唯一约束 409。

    回归 setup wizard step 3 → 钉钉 /绑定 闭环场景。
    """
    from hub.models import ChannelUserBinding, DownstreamIdentity, HubUser
    from hub.seed import run_seed
    from hub.services.binding_service import BindingService
    await run_seed()

    # setup wizard 创建的 admin：hub_user + downstream_identity，但没钉钉绑定
    existing = await HubUser.create(display_name="setup-admin")
    await DownstreamIdentity.create(
        hub_user=existing, downstream_type="erp", downstream_user_id=2,
    )

    svc = BindingService(erp_adapter=AsyncMock())
    result = await svc.confirm_final(
        token_id=8888,
        dingtalk_userid="m_attach", erp_user_id=2,
        erp_username="1", erp_display_name="setup-admin",
    )

    assert result.success is True
    assert result.note == "attached"
    assert result.hub_user_id == existing.id

    # 没有创建新 hub_user，只是给已有 hub_user 挂上钉钉绑定
    users = await HubUser.all()
    assert len(users) == 1
    assert users[0].id == existing.id

    binding = await ChannelUserBinding.filter(channel_userid="m_attach").first()
    assert binding is not None
    assert binding.hub_user_id == existing.id
    assert binding.status == "active"

    # downstream_identity 复用已有，不重复
    dis = await DownstreamIdentity.filter(downstream_type="erp", downstream_user_id=2).all()
    assert len(dis) == 1
