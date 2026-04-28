"""绑定/解绑业务编排。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from hub import messages
from hub.models import (
    ChannelUserBinding,
    ConsumedBindingToken,
    DownstreamIdentity,
    HubRole,
    HubUser,
    HubUserRole,
)


@dataclass
class BindingResult:
    success: bool
    reply_text: str
    hub_user_id: int | None = None
    note: str | None = None  # already_consumed / conflict / created / reactivated


class _AlreadyConsumedError(Exception):
    """内部异常：用于 confirm_final 事务内传递"已消费"信号。"""


class _ConflictError(Exception):
    """内部异常：用于 confirm_final 事务内传递冲突信号（事务回滚不消费 token）。"""

    def __init__(self, reply: str, code: str):
        self.reply = reply
        self.code = code


class BindingService:
    DEFAULT_ROLE_CODE = "bot_user_basic"

    def __init__(self, erp_adapter):
        self.erp = erp_adapter

    async def initiate_binding(self, dingtalk_userid: str, erp_username: str) -> BindingResult:
        """用户在钉钉发 /绑定 X → 校验 → 调 ERP 生成绑定码 → 返回回复文案。"""
        existing = await ChannelUserBinding.filter(
            channel_type="dingtalk", channel_userid=dingtalk_userid, status="active",
        ).first()
        if existing:
            return BindingResult(
                success=False, reply_text=messages.binding_already_bound(),
            )

        try:
            exists = await self.erp.user_exists(erp_username)
        except Exception as e:
            return BindingResult(success=False, reply_text=messages.system_error(str(e)))

        if not exists:
            return BindingResult(
                success=False, reply_text=messages.binding_user_not_found(erp_username),
            )

        try:
            result = await self.erp.generate_binding_code(
                erp_username=erp_username, dingtalk_userid=dingtalk_userid,
            )
        except Exception as e:
            return BindingResult(success=False, reply_text=messages.system_error(str(e)))

        return BindingResult(
            success=True,
            reply_text=messages.binding_code_reply(
                code=result["code"], ttl_minutes=result.get("expires_in", 300) // 60,
            ),
        )

    async def unbind_self(self, dingtalk_userid: str) -> BindingResult:
        """用户主动解绑。"""
        binding = await ChannelUserBinding.filter(
            channel_type="dingtalk", channel_userid=dingtalk_userid, status="active",
        ).first()

        if binding is None:
            return BindingResult(success=False, reply_text=messages.unbind_not_bound())

        binding.status = "revoked"
        binding.revoked_at = datetime.now(UTC)
        binding.revoked_reason = "self_unbind"
        await binding.save()

        return BindingResult(success=True, reply_text=messages.unbind_success())

    async def confirm_final(
        self, *, token_id: int, dingtalk_userid: str, erp_user_id: int,
        erp_username: str, erp_display_name: str,
    ) -> BindingResult:
        """ERP 反向通知 confirm-final → 原子地消费 token + 写绑定 + downstream + 默认角色。

        所有写操作在单一事务里。任何分支失败 → 整体回滚。
        """
        # 已消费 token 快路径检查（read-only，无副作用）
        existing_consumed = await ConsumedBindingToken.filter(erp_token_id=token_id).first()
        if existing_consumed:
            return BindingResult(
                success=True, reply_text="该绑定请求已处理",
                hub_user_id=existing_consumed.hub_user_id, note="already_consumed",
            )

        try:
            async with in_transaction():
                # 消费 token
                try:
                    consumed = await ConsumedBindingToken.create(
                        erp_token_id=token_id, hub_user_id=0,
                    )
                except IntegrityError as e:
                    raise _AlreadyConsumedError() from e

                # 冲突检查：dingtalk → 不同 ERP
                existing_binding = await ChannelUserBinding.filter(
                    channel_type="dingtalk", channel_userid=dingtalk_userid,
                ).first()
                if existing_binding and existing_binding.status == "active":
                    existing_di = await DownstreamIdentity.filter(
                        hub_user_id=existing_binding.hub_user_id, downstream_type="erp",
                    ).first()
                    if existing_di and existing_di.downstream_user_id != erp_user_id:
                        raise _ConflictError(
                            "该钉钉账号已绑定到其他 ERP 用户。如需换绑请先发送 /解绑。",
                            "conflict_dingtalk_already_bound",
                        )

                # 冲突检查：ERP → 不同 dingtalk
                other_di = await DownstreamIdentity.filter(
                    downstream_type="erp", downstream_user_id=erp_user_id,
                ).first()
                if other_di:
                    other_active = await ChannelUserBinding.filter(
                        hub_user_id=other_di.hub_user_id,
                        channel_type="dingtalk", status="active",
                    ).first()
                    if other_active and other_active.channel_userid != dingtalk_userid:
                        raise _ConflictError(
                            "该 ERP 用户已被另一个钉钉账号占用，请联系管理员解绑后再绑。",
                            "conflict_erp_user_owned",
                        )

                # 找/建 hub_user + binding
                if existing_binding and existing_binding.status == "active":
                    hub_user = await HubUser.get(id=existing_binding.hub_user_id)
                    note = "already_active"
                elif existing_binding and existing_binding.status == "revoked":
                    hub_user = await HubUser.get(id=existing_binding.hub_user_id)
                    existing_binding.status = "active"
                    existing_binding.bound_at = datetime.now(UTC)
                    existing_binding.revoked_at = None
                    existing_binding.revoked_reason = None
                    await existing_binding.save()
                    note = "reactivated"
                else:
                    hub_user = await HubUser.create(display_name=erp_display_name)
                    await ChannelUserBinding.create(
                        hub_user=hub_user, channel_type="dingtalk",
                        channel_userid=dingtalk_userid,
                        display_meta={
                            "erp_username": erp_username,
                            "erp_display_name": erp_display_name,
                        },
                        status="active",
                    )
                    note = "created"

                # 写/更新 downstream_identity（含 UNIQUE 兜底）
                di = await DownstreamIdentity.filter(
                    hub_user_id=hub_user.id, downstream_type="erp",
                ).first()
                if di is None:
                    try:
                        await DownstreamIdentity.create(
                            hub_user=hub_user, downstream_type="erp",
                            downstream_user_id=erp_user_id,
                        )
                    except IntegrityError as e:
                        raise _ConflictError(
                            "该 ERP 用户已被另一个钉钉账号占用，请联系管理员解绑后再绑。",
                            "conflict_erp_user_owned",
                        ) from e
                elif di.downstream_user_id != erp_user_id:
                    di.downstream_user_id = erp_user_id
                    try:
                        await di.save()
                    except IntegrityError as e:
                        raise _ConflictError(
                            "该 ERP 用户已被另一个钉钉账号占用，请联系管理员解绑后再绑。",
                            "conflict_erp_user_owned",
                        ) from e
                    if note == "reactivated":
                        note = "reactivated_with_new_erp"

                # 默认角色
                role = await HubRole.get(code=self.DEFAULT_ROLE_CODE)
                await HubUserRole.get_or_create(hub_user_id=hub_user.id, role_id=role.id)

                # 把 consumed token 的 hub_user_id 更新为真实值（同事务内）
                consumed.hub_user_id = hub_user.id
                await consumed.save()

            return BindingResult(
                success=True,
                reply_text=messages.binding_success(erp_display_name),
                hub_user_id=hub_user.id,
                note=note,
            )

        except _AlreadyConsumedError:
            existing = await ConsumedBindingToken.filter(erp_token_id=token_id).first()
            return BindingResult(
                success=True, reply_text="该绑定请求已处理",
                hub_user_id=existing.hub_user_id if existing else None,
                note="already_consumed",
            )
        except _ConflictError as e:
            return BindingResult(success=False, reply_text=e.reply, note=e.code)
