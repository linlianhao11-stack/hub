"""HUB 后台：AI 提供商配置 API。

提供 5 个 endpoint：
- GET  /defaults      返默认值（base_url + model）方便前端预填
- POST /              创建（api_key 加密入库；同时只能有一个 active）
- GET  /              列表（不返明文 api_key）
- POST /{id}/test-chat  测试 chat：调一次 ping 验证 key/url/model 可用
- POST /{id}/set-active 设为 active（其他自动 disabled）

权限码：platform.apikeys.write。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from hub.auth.admin_perms import require_hub_perm

# provider 类必须提到模块顶层（不能在 test_chat 函数内 import），
# 否则 monkeypatch 这个模块下的 DeepSeekProvider/QwenProvider 不生效。
from hub.capabilities.deepseek import DeepSeekProvider
from hub.capabilities.qwen import QwenProvider
from hub.crypto import decrypt_secret, encrypt_secret
from hub.models import AIProvider, AuditLog

router = APIRouter(prefix="/hub/v1/admin/ai-providers", tags=["admin-ai"])


_AI_DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
}


class CreateAIRequest(BaseModel):
    # C 阶段仅支持 deepseek / qwen（与 capabilities/factory.py 注册一致）
    provider_type: str = Field(..., pattern="^(deepseek|qwen)$")
    name: str = ""
    api_key: str
    base_url: str | None = None
    model: str | None = None


@router.get("/defaults")
async def get_defaults():
    """前端创建表单初始值（含每个支持的 provider 的 base_url + 推荐 model）。"""
    return _AI_DEFAULTS


@router.post("", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def create_ai(request: Request, body: CreateAIRequest = Body(...)):
    # provider_type 已被 Pydantic pattern 限制为 deepseek/qwen
    d = _AI_DEFAULTS[body.provider_type]
    base_url = body.base_url or d["base_url"]
    model = body.model or d["model"]

    # 单 active 不变量：新建 active provider 前先把其他全部 disable，
    # Plan 4 capabilities/factory 取 active 时只会有一条，避免选取不确定。
    await AIProvider.exclude(status="disabled").update(status="disabled")

    rec = await AIProvider.create(
        provider_type=body.provider_type,
        name=body.name or f"{body.provider_type} 默认",
        encrypted_api_key=encrypt_secret(body.api_key, purpose="config_secrets"),
        base_url=base_url,
        model=model,
        config={},
        status="active",
    )
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id,
        action="create_ai_provider",
        target_type="ai_provider",
        target_id=str(rec.id),
        detail={"provider_type": body.provider_type},
    )
    return {"id": rec.id}


@router.get("", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def list_ai():
    items = await AIProvider.all().order_by("-id")
    return {"items": [
        {
            "id": a.id,
            "provider_type": a.provider_type,
            "name": a.name,
            "base_url": a.base_url,
            "model": a.model,
            "status": a.status,
        }
        for a in items
    ]}


@router.post("/{ai_id}/test-chat", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def test_chat(ai_id: int):
    """测试 chat：调一次 ping，确认 API key + base_url + model 都能用。"""
    rec = await AIProvider.filter(id=ai_id).first()
    if rec is None:
        raise HTTPException(404, "AI 提供商不存在")
    cls = {"deepseek": DeepSeekProvider, "qwen": QwenProvider}.get(rec.provider_type)
    if cls is None:
        raise HTTPException(400, f"不支持测试 {rec.provider_type}")
    api_key = decrypt_secret(rec.encrypted_api_key, purpose="config_secrets")
    p = cls(api_key=api_key, base_url=rec.base_url, model=rec.model)
    try:
        await p.chat(messages=[{"role": "user", "content": "ping"}])
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        await p.aclose()


@router.post("/{ai_id}/set-active", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def set_active(request: Request, ai_id: int):
    """同时只能有一个 active；切换时把其他设为 disabled。"""
    rec = await AIProvider.filter(id=ai_id).first()
    if rec is None:
        raise HTTPException(404, "AI 提供商不存在")
    await AIProvider.exclude(id=ai_id).update(status="disabled")
    rec.status = "active"
    await rec.save()
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id,
        action="set_active_ai_provider",
        target_type="ai_provider",
        target_id=str(ai_id),
    )
    return {"success": True}
