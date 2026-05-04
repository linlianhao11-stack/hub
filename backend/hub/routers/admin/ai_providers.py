"""HUB 后台：AI 提供商配置 API。

提供 7 个 endpoint：
- GET  /defaults      返默认值（base_url + model）方便前端预填
- POST /              创建（api_key 加密入库；同时只能有一个 active）
- GET  /              列表（不返明文 api_key）
- PUT  /{id}          编辑（name / base_url / model / api_key,api_key 留空则不改）
- POST /{id}/test-chat  测试 chat：调一次 ping 验证 key/url/model 可用
- POST /{id}/set-active 设为 active（其他自动 disabled）
- POST /{id}/disable    停用（status='disabled'；不影响其它 provider）

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


class UpdateAIRequest(BaseModel):
    """编辑 — 任一字段为 None / 空表示不改（仅 api_key 单独走"留空不改"约定）。"""
    name: str | None = None
    base_url: str | None = None
    model: str | None = None
    # api_key 单独处理：None 或空字符串 → 不改;有值 → 加密重写
    api_key: str | None = None


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


@router.put("/{ai_id}", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def update_ai(request: Request, ai_id: int, body: UpdateAIRequest = Body(...)):
    """编辑现有 provider。

    semantics:
    - name / base_url / model 任一非 None 都会更新（空字符串等同 None,不改;
      防止前端误传 "" 把 base_url 清空导致后续调用挂）。
    - api_key None 或空字符串 → 不改（前端 password 字段留空表示"沿用旧 key"）。
      有值 → 重新加密入库。
    - provider_type 不允许改（要换种类直接新建,因为 base_url + model 默认值 + 测试 client
      class 都跟 provider_type 绑定,改了语义复杂）。
    - status 不在本接口改;启用走 set-active,停用走 /disable。
    """
    rec = await AIProvider.filter(id=ai_id).first()
    if rec is None:
        raise HTTPException(404, "AI 提供商不存在")

    changed = []
    if body.name and body.name.strip():
        rec.name = body.name.strip()
        changed.append("name")
    if body.base_url and body.base_url.strip():
        rec.base_url = body.base_url.strip()
        changed.append("base_url")
    if body.model and body.model.strip():
        rec.model = body.model.strip()
        changed.append("model")
    if body.api_key and body.api_key.strip():
        rec.encrypted_api_key = encrypt_secret(body.api_key.strip(), purpose="config_secrets")
        changed.append("api_key")

    if not changed:
        return {"success": True, "changed": []}  # 没东西可改,不写 audit

    await rec.save()
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id,
        action="update_ai_provider",
        target_type="ai_provider",
        target_id=str(ai_id),
        detail={"changed": changed},  # 记录哪些字段变了,**不**记 api_key 明文
    )
    return {"success": True, "changed": changed}


@router.post("/{ai_id}/disable", dependencies=[Depends(require_hub_perm("platform.apikeys.write"))])
async def disable_ai(request: Request, ai_id: int):
    """停用某个 provider（status='disabled'）。

    跟 set-active 不同：disable 只动这一条,不影响其他 provider 状态。
    场景：API key 暴露要紧急停用,但还没准备好别的 active。停用后 worker 启动会
    走 RuleParser fallback（chat 降级,LLM agent 路径不可用）—— 这是预期行为。
    """
    rec = await AIProvider.filter(id=ai_id).first()
    if rec is None:
        raise HTTPException(404, "AI 提供商不存在")
    if rec.status == "disabled":
        return {"success": True, "already_disabled": True}
    rec.status = "disabled"
    await rec.save()
    actor = request.state.hub_user
    await AuditLog.create(
        who_hub_user_id=actor.id,
        action="disable_ai_provider",
        target_type="ai_provider",
        target_id=str(ai_id),
    )
    return {"success": True}


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
