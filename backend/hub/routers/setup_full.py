"""初始化向导路由（步骤 2-6 完整业务）。

spec §16.2 步骤 2-6：
- 步骤 2：注册 ERP 系统连接 + 测试连接
- 步骤 3：创建第一个管理员（用 ERP 账号登录）
- 步骤 4：注册钉钉应用
- 步骤 5：注册 AI 提供商
- 步骤 6：完成（写 system_initialized=true，关闭所有 /setup/* 路由）

所有 endpoint 必须先校验 X-Setup-Session 头（值由 setup.py /verify-token 颁发）。
session 存在 `app.state.active_setup_sessions: dict[str, bool]`，与 setup.py 共享。

每步幂等：同 (channel_type, name) 已存在则更新，不重复创建。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Header, HTTPException, Request
from pydantic import BaseModel, Field

from hub.adapters.downstream.erp4 import (
    Erp4Adapter,
    ErpAdapterError,
    ErpPermissionError,
    ErpSystemError,
)

# ❗ 同 ai_providers.py：provider 类提到顶层，让测试能 monkeypatch
# hub.routers.setup_full.DeepSeekProvider/QwenProvider，不在函数体内 import。
from hub.capabilities.deepseek import DeepSeekProvider
from hub.capabilities.qwen import QwenProvider
from hub.crypto import encrypt_secret
from hub.models import (
    AIProvider,
    ChannelApp,
    DownstreamIdentity,
    DownstreamSystem,
    HubRole,
    HubUser,
    HubUserRole,
    SystemConfig,
)

router = APIRouter(prefix="/hub/v1/setup", tags=["setup"])


async def _is_initialized() -> bool:
    cfg = await SystemConfig.filter(key="system_initialized").first()
    return bool(cfg and cfg.value is True)


def _check_setup_session(request: Request, session_id: str | None) -> None:
    """所有步骤 2-6 都要校验 setup session。"""
    if session_id is None:
        raise HTTPException(401, "缺少 X-Setup-Session 头")
    sessions = getattr(request.app.state, "active_setup_sessions", {})
    if not sessions.get(session_id):
        raise HTTPException(401, "Setup session 无效或已过期")


# ========== 步骤 2：注册 ERP 系统连接 ==========


class ConnectErpRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    base_url: str = Field(..., pattern=r"^https?://")
    api_key: str = Field(..., min_length=8)
    apikey_scopes: list[str] = Field(..., min_length=1)


@router.post("/connect-erp")
async def connect_erp(
    request: Request,
    body: ConnectErpRequest = Body(...),
    x_setup_session: str | None = Header(default=None, alias="X-Setup-Session"),
):
    """步骤 2：注册 ERP 系统连接 + 测试连接 + 刷新 session_auth。"""
    if await _is_initialized():
        raise HTTPException(404, "HUB 已完成初始化")
    _check_setup_session(request, x_setup_session)

    # 测试连接（health check 不带 ApiKey）
    test_adapter = Erp4Adapter(base_url=body.base_url, api_key=body.api_key)
    try:
        ok = await test_adapter.health_check()
        if not ok:
            raise HTTPException(400, "ERP 连接测试失败：health 返回非 200")
    except (ErpSystemError, ErpAdapterError) as e:
        raise HTTPException(502, f"无法连接 ERP：{e}")
    finally:
        await test_adapter.aclose()

    # 幂等：已有同名 → 更新；否则创建
    existing = await DownstreamSystem.filter(
        downstream_type="erp", name=body.name,
    ).first()
    encrypted = encrypt_secret(body.api_key, purpose="config_secrets")
    if existing:
        existing.base_url = body.base_url
        existing.encrypted_apikey = encrypted
        existing.apikey_scopes = body.apikey_scopes
        existing.status = "active"
        await existing.save()
        ds_id = existing.id
    else:
        ds = await DownstreamSystem.create(
            downstream_type="erp",
            name=body.name,
            base_url=body.base_url,
            encrypted_apikey=encrypted,
            apikey_scopes=body.apikey_scopes,
            status="active",
        )
        ds_id = ds.id

    # 立刻刷新 app.state.session_auth（让步骤 3 的 admin login 能用）
    new_adapter = Erp4Adapter(base_url=body.base_url, api_key=body.api_key)
    from hub.auth.erp_session import ErpSessionAuth
    request.app.state.session_auth = ErpSessionAuth(erp_adapter=new_adapter)
    # 留引用，shutdown 时关闭
    old_adapter = getattr(request.app.state, "_session_erp_adapter", None)
    if old_adapter is not None:
        try:
            await old_adapter.aclose()
        except Exception:
            pass
    request.app.state._session_erp_adapter = new_adapter

    return {"id": ds_id, "ok": True}


# ========== 步骤 3：创建第一个 admin ==========


class CreateAdminRequest(BaseModel):
    erp_username: str
    erp_password: str


@router.post("/create-admin")
async def create_admin(
    request: Request,
    body: CreateAdminRequest = Body(...),
    x_setup_session: str | None = Header(default=None, alias="X-Setup-Session"),
):
    """步骤 3：用 ERP 账号登录 → 创建 hub_user + downstream_identity + 绑 platform_admin。"""
    if await _is_initialized():
        raise HTTPException(404, "HUB 已完成初始化")
    _check_setup_session(request, x_setup_session)

    auth = getattr(request.app.state, "session_auth", None)
    if auth is None:
        raise HTTPException(400, "请先完成步骤 2 注册 ERP 连接")

    # 用 ERP 凭据登录（同时确认 ApiKey 配置正确）
    try:
        login_resp = await auth.erp.login(
            username=body.erp_username, password=body.erp_password,
        )
    except ErpPermissionError:
        raise HTTPException(401, "ERP 用户名或密码错误")
    except (ErpSystemError, ErpAdapterError) as e:
        raise HTTPException(502, f"ERP 通信失败：{e}")

    erp_user = login_resp.get("user", {})
    erp_user_id = erp_user.get("id")
    erp_display = erp_user.get("display_name") or body.erp_username

    if erp_user_id is None:
        raise HTTPException(502, "ERP 登录响应缺少 user.id")

    # 幂等：已有同 erp_user_id 的 hub_user → 复用，仅追加 platform_admin 角色
    existing_di = await DownstreamIdentity.filter(
        downstream_type="erp", downstream_user_id=erp_user_id,
    ).first()
    if existing_di:
        hub_user = await HubUser.get(id=existing_di.hub_user_id)
    else:
        hub_user = await HubUser.create(display_name=erp_display)
        await DownstreamIdentity.create(
            hub_user=hub_user,
            downstream_type="erp",
            downstream_user_id=erp_user_id,
        )

    role = await HubRole.get(code="platform_admin")
    await HubUserRole.get_or_create(hub_user_id=hub_user.id, role_id=role.id)

    return {"hub_user_id": hub_user.id, "erp_user_id": erp_user_id}


# ========== 步骤 4：注册钉钉应用 ==========


class ConnectDingtalkRequest(BaseModel):
    name: str = Field(default="钉钉企业内部应用")
    app_key: str = Field(..., min_length=1)
    app_secret: str = Field(..., min_length=1)
    robot_id: str | None = None


@router.post("/connect-dingtalk")
async def connect_dingtalk(
    request: Request,
    body: ConnectDingtalkRequest = Body(...),
    x_setup_session: str | None = Header(default=None, alias="X-Setup-Session"),
):
    """步骤 4：注册钉钉企业内部应用（app_key / app_secret 加密入库）。

    写完后 set 钉钉 reload event，让 gateway 后台 task 立即重连 Stream。
    """
    if await _is_initialized():
        raise HTTPException(404, "HUB 已完成初始化")
    _check_setup_session(request, x_setup_session)

    # 幂等
    existing = await ChannelApp.filter(
        channel_type="dingtalk", name=body.name,
    ).first()
    enc_key = encrypt_secret(body.app_key, purpose="config_secrets")
    enc_secret = encrypt_secret(body.app_secret, purpose="config_secrets")
    if existing:
        existing.encrypted_app_key = enc_key
        existing.encrypted_app_secret = enc_secret
        existing.robot_id = body.robot_id
        existing.status = "active"
        await existing.save()
        ca_id = existing.id
    else:
        ca = await ChannelApp.create(
            channel_type="dingtalk",
            name=body.name,
            encrypted_app_key=enc_key,
            encrypted_app_secret=enc_secret,
            robot_id=body.robot_id,
            status="active",
        )
        ca_id = ca.id

    # 触发 gateway 钉钉 reload event：让后台 task 立即重连 Stream（已存在则更新）
    reload_event = getattr(request.app.state, "dingtalk_reload_event", None)
    if reload_event is not None:
        reload_event.set()

    return {"id": ca_id}


# ========== 步骤 5：注册 AI 提供商 ==========

# spec §19.2：DeepSeek + Qwen 默认配置
_AI_DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
}


class ConnectAIRequest(BaseModel):
    # **C 阶段仅支持 deepseek / qwen**——Plan 4 capabilities/factory 也只注册了这两类。
    # custom 类型留给 B 阶段或独立 spec 加 CustomOpenAIProvider + factory 注册时再开放。
    provider_type: str = Field(..., pattern="^(deepseek|qwen)$")
    name: str = Field(default="")
    api_key: str = Field(..., min_length=1)
    base_url: str | None = None  # 不填用默认（spec §19.2）
    model: str | None = None


@router.post("/connect-ai")
async def connect_ai(
    request: Request,
    body: ConnectAIRequest = Body(...),
    x_setup_session: str | None = Header(default=None, alias="X-Setup-Session"),
):
    """步骤 5：测试 chat 通过后写库，激活该 provider，其他 disable。"""
    if await _is_initialized():
        raise HTTPException(404, "HUB 已完成初始化")
    _check_setup_session(request, x_setup_session)

    # 已被 Pydantic pattern 限制为 deepseek/qwen，无需 else 兜底
    defaults = _AI_DEFAULTS[body.provider_type]
    base_url = body.base_url or defaults["base_url"]
    model = body.model or defaults["model"]

    name = body.name or f"{body.provider_type} 默认"

    # 测试 chat（provider 类已在模块顶层导入，方便测试 monkeypatch）
    cls = DeepSeekProvider if body.provider_type == "deepseek" else QwenProvider
    test = cls(api_key=body.api_key, base_url=base_url, model=model)
    try:
        try:
            await test.chat(messages=[{"role": "user", "content": "ping"}])
        except Exception as e:
            raise HTTPException(502, f"AI 测试连接失败：{e}")
    finally:
        await test.aclose()

    # 幂等 + 单 active 不变量：先把其他 provider 全部 disable
    existing = await AIProvider.filter(
        provider_type=body.provider_type, name=name,
    ).first()
    enc_key = encrypt_secret(body.api_key, purpose="config_secrets")
    if existing:
        await AIProvider.exclude(id=existing.id).update(status="disabled")
        existing.encrypted_api_key = enc_key
        existing.base_url = base_url
        existing.model = model
        existing.status = "active"
        await existing.save()
        return {"id": existing.id}
    else:
        await AIProvider.exclude(status="disabled").update(status="disabled")
        rec = await AIProvider.create(
            provider_type=body.provider_type,
            name=name,
            encrypted_api_key=enc_key,
            base_url=base_url,
            model=model,
            config={},
            status="active",
        )
        return {"id": rec.id}


# ========== 步骤 6：完成 ==========


@router.post("/complete")
async def setup_complete(
    request: Request,
    x_setup_session: str | None = Header(default=None, alias="X-Setup-Session"),
):
    """步骤 6：完成初始化。校验前置 + 写 system_initialized=true + 关闭 setup。"""
    if await _is_initialized():
        raise HTTPException(404, "HUB 已完成初始化")
    _check_setup_session(request, x_setup_session)

    # 校验前置：DownstreamSystem(erp) + admin（DownstreamIdentity）+ ChannelApp(dingtalk)
    erp_ds = await DownstreamSystem.filter(
        downstream_type="erp", status="active",
    ).first()
    if not erp_ds:
        raise HTTPException(400, "未完成步骤 2（注册 ERP）")

    admin_di = await DownstreamIdentity.filter(downstream_type="erp").first()
    if not admin_di:
        raise HTTPException(400, "未完成步骤 3（创建 admin）")

    dt_app = await ChannelApp.filter(
        channel_type="dingtalk", status="active",
    ).first()
    if not dt_app:
        raise HTTPException(400, "未完成步骤 4（注册钉钉）")

    # AI 步骤 5 是可选，跳过也允许 complete

    # 写入 system_initialized=true（同步关闭所有 /setup/* 路由）
    await SystemConfig.update_or_create(
        key="system_initialized", defaults={"value": True},
    )

    # 清理 setup session
    sessions = getattr(request.app.state, "active_setup_sessions", {})
    if x_setup_session in sessions:
        del sessions[x_setup_session]

    return {"success": True, "redirect_to": "/login"}
