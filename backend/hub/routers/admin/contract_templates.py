"""HUB 后台合同模板管理（Plan 6 Task 11）。

提供 5 类 endpoint：
  - POST   /admin/contract-templates           上传 docx 模板（自动解析占位符）
  - GET    /admin/contract-templates           列表查询
  - GET    /admin/contract-templates/{id}/placeholders  获取已存占位符
  - PUT    /admin/contract-templates/{id}      更新元信息（不重传文件）
  - POST   /admin/contract-templates/{id}/disable     停用
  - POST   /admin/contract-templates/{id}/enable      启用
"""
from __future__ import annotations

import base64
import io
import logging
import re
from typing import Optional

from docx import Document
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from hub.auth.admin_perms import require_hub_perm
from hub.models.contract import ContractTemplate

logger = logging.getLogger("hub.routers.admin.contract_templates")

router = APIRouter(prefix="/hub/v1/admin/contract-templates", tags=["admin", "contract_templates"])

# 合同模板管理权限码（Plan 6 Task 11 新增，见 seed.py）
_PERM_WRITE = "usecase.contract_templates.write"

# 允许的模板类型
_VALID_TYPES = {"sales", "purchase", "framework", "quote", "other"}

# 文件大小上限：5MB
_MAX_FILE_SIZE = 5 * 1024 * 1024


class ContractTemplateRow(BaseModel):
    id: int
    name: str
    template_type: str
    description: Optional[str] = None
    placeholders: list[dict]
    is_active: bool
    created_by_hub_user_id: Optional[int] = None
    created_at: str


class ContractTemplateUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    template_type: Optional[str] = Field(default=None, max_length=50)
    description: Optional[str] = Field(default=None, max_length=1000)


def _extract_placeholders(docx_bytes: bytes) -> list[dict]:
    """从 docx 文件字节流解析所有 {{name}} 形式的占位符。

    - 扫描所有段落（paragraphs）和表格单元格（tables）
    - 返回 list[{"name": str, "type": "string", "required": True}]（第一版默认 type=string）
    - 同名占位符只保留一条（去重）
    """
    try:
        doc = Document(io.BytesIO(docx_bytes))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"docx 文件解析失败：{exc}") from exc

    found: dict[str, dict] = {}
    pattern = re.compile(r"\{\{(\w+)\}\}")

    def _scan_para(para_text: str) -> None:
        for m in pattern.finditer(para_text):
            name = m.group(1)
            if name not in found:
                found[name] = {"name": name, "type": "string", "required": True}

    # 顶层段落
    for para in doc.paragraphs:
        _scan_para(para.text)

    # 表格内段落
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    _scan_para(para.text)

    return list(found.values())


@router.post(
    "",
    dependencies=[Depends(require_hub_perm(_PERM_WRITE))],
    summary="上传合同模板",
)
async def upload_template(
    request: Request,
    name: str = Form(..., max_length=200),
    template_type: str = Form(..., max_length=50),
    description: str = Form("", max_length=1000),
    file: UploadFile = File(...),
):
    """上传合同模板 docx 文件。

    - 文件大小上限 5MB，格式必须为 .docx
    - 自动解析 {{xxx}} 占位符，第一版默认 type=string + required=True
    - file_storage_key 第一版用 base64 编码存储（与 Task 7 ContractRenderer 对齐）
    """
    # 1. 基础校验
    if not name.strip():
        raise HTTPException(status_code=400, detail="模板名称不能为空")
    if template_type not in _VALID_TYPES:
        raise HTTPException(status_code=400, detail="模板类型必须是 sales/purchase/framework/quote/other")
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="仅支持 .docx 格式的文件")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空，请重新上传")
    if len(content) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="文件超过 5MB 上限，请压缩后重试")

    # 2. 自动解析占位符
    placeholders = _extract_placeholders(content)

    # 3. 持久化
    actor = request.state.hub_user
    file_storage_key = base64.b64encode(content).decode("ascii")

    template = await ContractTemplate.create(
        name=name.strip(),
        template_type=template_type,
        file_storage_key=file_storage_key,
        placeholders=placeholders,
        description=description.strip() or None,
        is_active=True,
        created_by_hub_user_id=actor.id,
    )

    logger.info("合同模板上传成功 id=%d name=%s placeholders=%d", template.id, template.name, len(placeholders))
    return {
        "id": template.id,
        "name": template.name,
        "template_type": template.template_type,
        "placeholders": placeholders,
        "is_active": True,
        "message": f"上传成功，共识别 {len(placeholders)} 个占位符",
    }


@router.get(
    "",
    summary="合同模板列表",
)
async def list_templates(
    template_type: Optional[str] = None,
    is_active: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
):
    """列出所有合同模板（管理员可见全部）。

    支持按模板类型和启用状态筛选。
    """
    qs = ContractTemplate.all().order_by("-created_at")
    if template_type:
        qs = qs.filter(template_type=template_type)
    if is_active is not None:
        qs = qs.filter(is_active=is_active)

    total = await qs.count()
    rows = await qs.offset(offset).limit(limit).all()

    return {
        "items": [
            ContractTemplateRow(
                id=r.id,
                name=r.name,
                template_type=r.template_type,
                description=r.description,
                placeholders=r.placeholders or [],
                is_active=r.is_active,
                created_by_hub_user_id=r.created_by_hub_user_id,
                created_at=r.created_at.isoformat(),
            ).model_dump()
            for r in rows
        ],
        "total": total,
    }


@router.get(
    "/{template_id}/placeholders",
    summary="查看模板占位符",
)
async def get_placeholders(template_id: int):
    """获取指定模板的占位符列表（直接从数据库读取，不重新解析文件）。"""
    template = await ContractTemplate.filter(id=template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail=f"模板 {template_id} 不存在")
    return {"placeholders": template.placeholders or []}


@router.put(
    "/{template_id}",
    dependencies=[Depends(require_hub_perm(_PERM_WRITE))],
    summary="更新模板元信息",
)
async def update_template(template_id: int, body: ContractTemplateUpdate):
    """更新模板名称、类型或描述（不支持重新上传文件，不更新占位符）。"""
    template = await ContractTemplate.filter(id=template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail=f"模板 {template_id} 不存在")

    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(status_code=400, detail="模板名称不能为空")
        template.name = body.name.strip()
    if body.template_type is not None:
        if body.template_type not in _VALID_TYPES:
            raise HTTPException(status_code=400, detail="模板类型不合法")
        template.template_type = body.template_type
    if body.description is not None:
        template.description = body.description.strip() or None

    await template.save()
    return {"id": template.id, "message": "模板信息已更新"}


@router.post(
    "/{template_id}/disable",
    dependencies=[Depends(require_hub_perm(_PERM_WRITE))],
    summary="停用模板",
)
async def disable_template(template_id: int):
    """将模板标记为禁用（is_active=False）。已禁用时幂等返回。"""
    template = await ContractTemplate.filter(id=template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail=f"模板 {template_id} 不存在")
    if not template.is_active:
        return {"id": template.id, "message": "该模板已是禁用状态"}
    template.is_active = False
    await template.save()
    return {"id": template.id, "message": "已禁用"}


@router.post(
    "/{template_id}/enable",
    dependencies=[Depends(require_hub_perm(_PERM_WRITE))],
    summary="启用模板",
)
async def enable_template(template_id: int):
    """将模板标记为启用（is_active=True）。已启用时幂等返回。"""
    template = await ContractTemplate.filter(id=template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail=f"模板 {template_id} 不存在")
    if template.is_active:
        return {"id": template.id, "message": "该模板已是启用状态"}
    template.is_active = True
    await template.save()
    return {"id": template.id, "message": "已启用"}
