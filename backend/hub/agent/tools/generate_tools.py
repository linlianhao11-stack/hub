"""Plan 6 Task 7：生成型 tool（合同 / 报价 / Excel）。

特点：
- ToolType.GENERATE（不需 confirmation_action_id；register-time 不强制）
- 输出对请求人本人（生成 docx/xlsx + 钉钉发文件）
- 重复调用允许（每次新 draft；不强制幂等）
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date

from hub.adapters.channel.dingtalk_sender import DingTalkSender, DingTalkSendError
from hub.adapters.downstream.erp4 import Erp4Adapter, ErpAdapterError, ErpNotFoundError
from hub.agent.document.contract import (
    ContractRenderer,
    TemplateNotFoundError,
    TemplateRenderError,
)
from hub.agent.document.excel import ExcelExporter
from hub.agent.tools.registry import ToolRegistry
from hub.agent.tools.types import ToolType
from hub.models.contract import ContractDraft, ContractTemplate
from hub.models.identity import ChannelUserBinding


def _normalize_for_fingerprint(value):
    """v8 review #17：用于 fingerprint 的稳定 normalize（dict 按 key 排序，list 保序）。"""
    if isinstance(value, dict):
        return {k: _normalize_for_fingerprint(value[k]) for k in sorted(value.keys())}
    if isinstance(value, list):
        return [_normalize_for_fingerprint(v) for v in value]
    return value


def _compute_contract_fingerprint(
    *, template_id: int, customer_id: int, items: list, extras: dict | None,
) -> str:
    """v8 review #17：合同幂等 fingerprint，覆盖 items + extras。

    用 sha256 摘要稳定 JSON 序列化结果，64 字符 hex（与 model 字段长度对齐）。
    items / extras 的 dict 按 key 排序保稳定（同样输入永远同样 fingerprint）。
    """
    payload = {
        "template_id": template_id,
        "customer_id": customer_id,
        "items": _normalize_for_fingerprint(items or []),
        "extras": _normalize_for_fingerprint(extras or {}),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

logger = logging.getLogger("hub.agent.tools.generate_tools")


# 模块单例（与 erp_tools 同模式）
_dingtalk_sender: DingTalkSender | None = None
_erp_adapter: Erp4Adapter | None = None


def set_dependencies(
    *,
    sender: DingTalkSender | None,
    erp: Erp4Adapter | None,
) -> None:
    """app startup 调；测试 fixture 注入 mock。"""
    global _dingtalk_sender, _erp_adapter
    _dingtalk_sender = sender
    _erp_adapter = erp


def current_sender() -> DingTalkSender:
    if _dingtalk_sender is None:
        raise RuntimeError(
            "DingTalkSender 未初始化（startup 必须先调 set_dependencies）"
        )
    return _dingtalk_sender


def current_erp_adapter() -> Erp4Adapter:
    if _erp_adapter is None:
        raise RuntimeError("Erp4Adapter 未初始化（startup 必须先调 set_dependencies）")
    return _erp_adapter


# ===== 3 个 tool =====


async def generate_contract_draft(
    template_id: int,
    customer_id: int,
    items: list[dict],
    extras: dict | None = None,
    *,
    hub_user_id: int,
    conversation_id: str,
    acting_as_user_id: int,
) -> dict:
    """生成销售合同草稿 docx 并发到钉钉。

    Args:
        template_id: ContractTemplate.id
        customer_id: ERP 客户 ID
        items: [{"product_id", "name", "qty", "price"}]
        extras: 额外占位符字段（合同号 / 付款条款等）；
               值类型应为 str/int/float/bool；嵌套 dict/list 可能让模板渲染丢失。
               注意：extras 字段会出现在 LLM 看到的 schema 中（GENERATE 类不含
               confirmation_action_id 等内部字段，但其他业务参数全部对 LLM 可见）。

    Returns:
        {"draft_id", "file_sent", "file_name"}

    幂等保护（v8 staging review #15 → #17）：
    用 fingerprint = sha256(template_id|customer_id|items|extras) 做幂等键。
    - 入参完全相同（含 extras）→ fingerprint 一致 → 复用 draft 跳过 create，
      worker 重试时只重发文件不重复入库
    - 改了 items / extras 任一字段 → fingerprint 变 → 创建新 draft，
      admin 审计 metadata 与实际 docx 内容始终一致
    - 极端 race（同 conv 并发 generate 同 fingerprint）可能漏拦——
      用 fingerprint 列加 partial index 加速查询，DB 唯一约束等 follow-up
    """
    # 1. 拉客户信息（v8 staging review #12：删宽容 fallback）
    # 旧逻辑：失败时用 "客户N" 假占位 → LLM 编错 customer_id 时合同上写"客户102"
    # 用户察觉前文件已发出，无法回滚。
    # 新逻辑：客户找不到直接返 error 让 LLM 看到，重新调 search_customers 取对的 ID。
    erp = current_erp_adapter()
    try:
        customer = await erp.get_customer(
            customer_id=customer_id, acting_as_user_id=acting_as_user_id,
        )
    except (ErpNotFoundError, ErpAdapterError) as e:
        logger.warning(
            "get_customer %s 失败 → 返 error 让 agent 重调 conv=%s err=%s",
            customer_id, conversation_id, e,
        )
        return {
            "draft_id": None,
            "file_sent": False,
            "error": (
                f"客户 ID {customer_id} 在 ERP 不存在或查询失败。"
                f"**请重新调 search_customers 拿到正确的客户 ID 后再调本 tool**，"
                f"不要凭印象编造 ID。原始错误: {str(e)[:100]}"
            ),
        }

    # 1.5. 自动拉账套信息注入甲方（seller_xxx）— v2 staging review #5
    # LLM 不知道账套字段（business_dict 没教），不强求 LLM 传 extras。
    # 当前固定取 account_set_id=1（启领），后续按 channel_user_binding.default_account_set_id 走。
    seller_extras: dict = {}
    try:
        account_set = await erp.get_account_set(
            set_id=1, acting_as_user_id=acting_as_user_id,
        )
        seller_extras = {
            "seller_name": account_set.get("company_name") or account_set.get("name") or "",
            "seller_bank_name": account_set.get("bank_name") or "",
            "seller_bank_account": account_set.get("bank_account") or "",
            "seller_tax_id": account_set.get("tax_id") or "",
            "seller_account_set_name": account_set.get("name") or "",
        }
    except (ErpNotFoundError, ErpAdapterError) as e:
        logger.warning(
            "get_account_set 1 失败（甲方字段留空）conv=%s err=%s",
            conversation_id, e,
        )

    # 1.6. enrich items：LLM 没传 name 时自动从 ERP 拉 name/spec/color
    # v8 staging review #12：商品 ID 不存在直接返 error，不再 fallback 留空
    enriched_items: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        enriched = dict(item)
        product_id = enriched.get("product_id")
        if product_id:
            try:
                product = await erp.get_product(
                    product_id=int(product_id),
                    acting_as_user_id=acting_as_user_id,
                )
                # 用 ERP 真实数据覆盖（防 LLM 自己瞎编的 name 也填进去）
                enriched["name"] = product.get("name") or enriched.get("name") or ""
                if not enriched.get("spec"):
                    enriched["spec"] = product.get("spec") or product.get("specification") or ""
                if not enriched.get("color"):
                    enriched["color"] = product.get("color") or ""
                if not enriched.get("unit"):
                    enriched["unit"] = product.get("unit") or ""
            except (ErpNotFoundError, ErpAdapterError) as e:
                logger.warning(
                    "get_product %s 失败 → 返 error 让 agent 重调 conv=%s err=%s",
                    product_id, conversation_id, e,
                )
                return {
                    "draft_id": None,
                    "file_sent": False,
                    "error": (
                        f"商品 ID {product_id} 在 ERP 不存在或查询失败。"
                        f"**请重新调 search_products 拿到正确的商品 ID 后再调本 tool**，"
                        f"不要凭印象编造 ID。原始错误: {str(e)[:100]}"
                    ),
                }
        enriched_items.append(enriched)

    # 1.7. customer.name 必须非空（防 ERP 返了客户但 name 字段是空的边界情况）
    if not customer.get("name"):
        return {
            "draft_id": None, "file_sent": False,
            "error": f"客户 ID {customer_id} 数据异常：name 字段为空。请联系管理员核对 ERP。",
        }

    # extras 类型加固（LLM 偶尔传 string 而不是 dict）
    safe_extras = extras if isinstance(extras, dict) else {}
    # 合并 seller_extras → 用户 extras 可覆盖（如指定不同账套时）
    merged_extras = {**seller_extras, **safe_extras}

    # 2. 渲染 docx（可能抛 TemplateNotFoundError / TemplateRenderError）
    renderer = ContractRenderer()
    try:
        docx_bytes = await renderer.render(
            template_id=template_id,
            customer=customer,
            items=enriched_items,
            extras=merged_extras,
        )
    except TemplateNotFoundError:
        logger.warning("合同模板 %s 不存在 conv=%s", template_id, conversation_id)
        return {
            "draft_id": None,
            "file_sent": False,
            "error": f"合同模板 {template_id} 不存在或未启用，请联系管理员",
        }
    except TemplateRenderError as e:
        logger.exception("合同模板 %s 渲染失败 conv=%s", template_id, conversation_id)
        return {
            "draft_id": None,
            "file_sent": False,
            "error": f"合同模板渲染失败: {e}（可能是 items 数据缺字段）",
        }

    # 3. 持久化 ContractDraft（metadata 审计用），幂等保护
    # v8 review #15 → #17：用 fingerprint 替代多字段比对。
    # fingerprint = sha256(template_id | customer_id | items 排序 | extras 排序)
    # 改 extras 时 fingerprint 变 → 创建新 draft（admin 审计准确）；
    # 重试相同入参 → fingerprint 一致 → 复用 draft（worker 重试不爆复制）。
    fingerprint = _compute_contract_fingerprint(
        template_id=template_id,
        customer_id=customer_id,
        items=items,
        extras=merged_extras,
    )
    draft = await ContractDraft.filter(
        conversation_id=conversation_id,
        requester_hub_user_id=hub_user_id,
        fingerprint=fingerprint,
    ).first()
    if draft is not None:
        logger.info(
            "ContractDraft 幂等命中（fingerprint）复用 draft_id=%s conv=%s",
            draft.id, conversation_id,
        )
    else:
        draft = await ContractDraft.create(
            template_id=template_id,
            requester_hub_user_id=hub_user_id,
            customer_id=customer_id,
            items=items,
            extras=merged_extras,  # v8 review #17：审计用，与 fingerprint 配套落库
            fingerprint=fingerprint,
            rendered_file_storage_key=None,  # 第一版不存文件 bytes
            conversation_id=conversation_id,
        )

    # 4. 发钉钉
    sender = current_sender()
    binding = await ChannelUserBinding.filter(
        hub_user_id=hub_user_id,
        channel_type="dingtalk",
        status="active",
    ).first()
    if not binding:
        logger.warning("hub_user %s 无 active 钉钉绑定，跳过 send_file", hub_user_id)
        return {
            "draft_id": draft.id,
            "file_sent": False,
            "file_name": None,
            "warning": "用户未绑定钉钉，文件未发送",
        }

    file_name = (
        f"销售合同_{customer.get('name')}_{date.today().isoformat()}.docx"
    )
    try:
        await sender.send_file(
            dingtalk_userid=binding.channel_userid,
            file_bytes=docx_bytes,
            file_name=file_name,
            file_type="docx",
        )
    except DingTalkSendError:
        # 草稿已持久化，send_file 失败：让 worker 转死信重试
        logger.exception("send_file 失败 draft_id=%s", draft.id)
        raise

    draft.status = "sent"
    await draft.save(update_fields=["status"])

    return {
        "draft_id": draft.id,
        "file_sent": True,
        "file_name": file_name,
    }


async def generate_price_quote(
    customer_id: int,
    items: list[dict],
    extras: dict | None = None,
    *,
    hub_user_id: int,
    conversation_id: str,
    acting_as_user_id: int,
) -> dict:
    """生成报价单 docx（同 generate_contract_draft 模式但用报价模板）。

    简化第一版：自动找第一个 active 的 quote 类型模板。
    """
    template = await ContractTemplate.filter(
        template_type="quote",
        is_active=True,
    ).first()
    if not template:
        logger.warning(
            "用户 hub_user_id=%s 调 generate_price_quote 但无 quote 模板 conv=%s",
            hub_user_id, conversation_id,
        )
        return {
            "draft_id": None,
            "file_sent": False,
            "error": "未配置报价模板，请先在管理后台创建 template_type=quote 的模板",
        }
    # 复用 generate_contract_draft 实现
    return await generate_contract_draft(
        template_id=template.id,
        customer_id=customer_id,
        items=items,
        extras=extras,
        hub_user_id=hub_user_id,
        conversation_id=conversation_id,
        acting_as_user_id=acting_as_user_id,
    )


async def export_to_excel(
    table_data: list[dict],
    file_name: str,
    *,
    hub_user_id: int,
    conversation_id: str,
    acting_as_user_id: int,
) -> dict:
    """把 list[dict] 导出 .xlsx 并发到钉钉。

    Args:
        table_data: 表格数据（key = 列名）
        file_name: 文件名（自动补 .xlsx 后缀）
    """
    if not file_name.endswith(".xlsx"):
        file_name += ".xlsx"

    exporter = ExcelExporter()
    xlsx_bytes = await exporter.export(table_data=table_data)

    sender = current_sender()
    binding = await ChannelUserBinding.filter(
        hub_user_id=hub_user_id,
        channel_type="dingtalk",
        status="active",
    ).first()
    if not binding:
        return {
            "file_sent": False,
            "warning": "用户未绑定钉钉，文件未发送",
        }

    try:
        await sender.send_file(
            dingtalk_userid=binding.channel_userid,
            file_bytes=xlsx_bytes,
            file_name=file_name,
            file_type="xlsx",
        )
    except DingTalkSendError:
        logger.exception("export_to_excel send_file 失败 file=%s", file_name)
        raise

    return {
        "file_sent": True,
        "file_name": file_name,
        "rows_count": len(table_data),
    }


# ===== register =====


def register_all(registry: ToolRegistry) -> None:
    """3 个 GENERATE 类 tool 注册。

    GENERATE 类不强制 confirmation_action_id（register fail-fast 不触发）。
    """
    registry.register(
        "generate_contract_draft",
        generate_contract_draft,
        perm="usecase.generate_contract.use",
        tool_type=ToolType.GENERATE,
        description="生成销售合同草稿 docx 并发到钉钉",
    )
    registry.register(
        "generate_price_quote",
        generate_price_quote,
        perm="usecase.generate_quote.use",
        tool_type=ToolType.GENERATE,
        description="生成客户报价单 docx 并发到钉钉",
    )
    registry.register(
        "export_to_excel",
        export_to_excel,
        perm="usecase.export.use",
        tool_type=ToolType.GENERATE,
        description="把表格数据导出成 .xlsx 发到钉钉",
    )
