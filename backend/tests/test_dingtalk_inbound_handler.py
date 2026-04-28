from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_bind_command_routed_to_initiate():
    from hub.handlers.dingtalk_inbound import handle_inbound

    binding_svc = AsyncMock()
    binding_svc.initiate_binding = AsyncMock(
        return_value=AsyncMock(success=True, reply_text="reply"),
    )
    identity_svc = AsyncMock()
    sender = AsyncMock()

    payload = {
        "task_id": "t1", "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": "m1", "content": "/绑定 zhangsan",
            "conversation_id": "c1", "timestamp": 1700000000,
        },
    }
    await handle_inbound(
        payload, binding_service=binding_svc, identity_service=identity_svc, sender=sender,
    )

    binding_svc.initiate_binding.assert_awaited_once_with(
        dingtalk_userid="m1", erp_username="zhangsan",
    )
    sender.send_text.assert_awaited_once()
    identity_svc.resolve.assert_not_called()


@pytest.mark.asyncio
async def test_unbind_command_routed():
    from hub.handlers.dingtalk_inbound import handle_inbound

    binding_svc = AsyncMock()
    binding_svc.unbind_self = AsyncMock(
        return_value=AsyncMock(success=True, reply_text="已解绑"),
    )
    identity_svc = AsyncMock()
    sender = AsyncMock()

    payload = {
        "task_id": "t2", "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": "m1", "content": "/解绑",
            "conversation_id": "c1", "timestamp": 1700000000,
        },
    }
    await handle_inbound(
        payload, binding_service=binding_svc, identity_service=identity_svc, sender=sender,
    )
    binding_svc.unbind_self.assert_awaited_once_with(dingtalk_userid="m1")


@pytest.mark.asyncio
async def test_help_command_returns_help_message():
    from hub.handlers.dingtalk_inbound import handle_inbound

    sender = AsyncMock()
    payload = {
        "task_id": "t3", "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": "m1", "content": "帮助",
            "conversation_id": "c1", "timestamp": 1700000000,
        },
    }
    await handle_inbound(
        payload, binding_service=AsyncMock(), identity_service=AsyncMock(), sender=sender,
    )
    sender.send_text.assert_awaited_once()
    sent_text = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "帮助" in sent_text or "帮你做" in sent_text


@pytest.mark.asyncio
async def test_unknown_command_for_unbound_user_triggers_binding_hint():
    """未绑定用户发任意话 → 提示先绑定。"""
    from hub.handlers.dingtalk_inbound import handle_inbound
    from hub.services.identity_service import IdentityResolution

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(found=False, erp_active=False))

    binding_svc = AsyncMock()
    sender = AsyncMock()
    payload = {
        "task_id": "t4", "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": "m_unknown", "content": "查 SKU100",
            "conversation_id": "c1", "timestamp": 1700000000,
        },
    }
    await handle_inbound(
        payload, binding_service=binding_svc, identity_service=identity_svc, sender=sender,
    )

    sender.send_text.assert_awaited_once()
    sent_text = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "绑定" in sent_text


@pytest.mark.asyncio
async def test_disabled_erp_user_blocked():
    """已绑定但 ERP 用户被禁用 → 拒绝并提示。"""
    from hub.handlers.dingtalk_inbound import handle_inbound
    from hub.services.identity_service import IdentityResolution

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=False, hub_user_id=1, erp_user_id=99,
    ))
    binding_svc = AsyncMock()
    sender = AsyncMock()
    payload = {
        "task_id": "t5", "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": "m_disabled", "content": "查 SKU100",
            "conversation_id": "c1", "timestamp": 1700000000,
        },
    }
    await handle_inbound(
        payload, binding_service=binding_svc, identity_service=identity_svc, sender=sender,
    )

    sender.send_text.assert_awaited_once()
    sent_text = sender.send_text.call_args.kwargs.get("text") or sender.send_text.call_args.args[1]
    assert "停用" in sent_text or "禁用" in sent_text


@pytest.mark.asyncio
async def test_sender_failure_propagates_so_runtime_can_dead_letter():
    """sender.send_text 抛错时异常向上冒泡，让 WorkerRuntime 转死信。

    回归 P2：早期版本捕获并吞异常 → 钉钉短暂故障时用户收不到回复，
    任务也不会重试或进死信，问题被静默掩盖。
    """
    from hub.handlers.dingtalk_inbound import handle_inbound
    from hub.services.identity_service import IdentityResolution

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True, hub_user_id=1, erp_user_id=42,
    ))
    sender = AsyncMock()
    sender.send_text = AsyncMock(side_effect=RuntimeError("dingtalk down"))

    payload = {
        "task_id": "tX", "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": "m1", "content": "随便说点啥",
            "conversation_id": "c1", "timestamp": 1700000000,
        },
    }
    with pytest.raises(RuntimeError, match="dingtalk down"):
        await handle_inbound(
            payload, binding_service=AsyncMock(),
            identity_service=identity_svc, sender=sender,
        )


@pytest.mark.asyncio
async def test_active_user_unrecognized_command():
    """已绑定 + ERP 启用 → 未识别命令提示帮助。"""
    from hub.handlers.dingtalk_inbound import handle_inbound
    from hub.services.identity_service import IdentityResolution

    identity_svc = AsyncMock()
    identity_svc.resolve = AsyncMock(return_value=IdentityResolution(
        found=True, erp_active=True, hub_user_id=1, erp_user_id=42,
    ))
    sender = AsyncMock()
    payload = {
        "task_id": "t6", "task_type": "dingtalk_inbound",
        "payload": {
            "channel_userid": "m_active", "content": "随便说点啥",
            "conversation_id": "c1", "timestamp": 1700000000,
        },
    }
    await handle_inbound(
        payload, binding_service=AsyncMock(), identity_service=identity_svc, sender=sender,
    )
    sender.send_text.assert_awaited_once()
