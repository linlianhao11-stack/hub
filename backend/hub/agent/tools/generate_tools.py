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


# ===== Plan 6 v9 Task 2.2：strict tool schema（spec §1.3 / §5.2）=====

GENERATE_CONTRACT_DRAFT_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "generate_contract_draft",
        "strict": True,
        "description": "生成销售合同草稿 docx 并发到钉钉",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "template_id",
                "customer_id",
                "items",
                "shipping_address",
                "shipping_contact",
                "shipping_phone",
                "contract_no",
                "payment_terms",
                "tax_rate",
                "extras",
            ],
            "properties": {
                "template_id": {
                    "type": "integer",
                    "description": "ContractTemplate.id（销售合同模板一般是 1）",
                },
                "customer_id": {
                    "type": "integer",
                    "description": "ERP 客户 ID，必须用 search_customers 真返过的 id",
                },
                "items": {
                    "type": "array",
                    "description": "商品列表，每项含 product_id/name/qty/price；如无传 []",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["product_id", "qty", "price"],
                        "properties": {
                            "product_id": {
                                "type": "integer",
                                "description": "ERP 商品 ID（必须用 search_products 真返过的 id）",
                            },
                            "qty": {
                                "type": "number",
                                "description": "数量（必须大于 0）",
                            },
                            "price": {
                                "type": "number",
                                "description": "单价（必须大于 0）",
                            },
                        },
                    },
                },
                "shipping_address": {
                    "type": "string",
                    "description": "收货地址，如'广州市天河区华穗路406号'；如无传 ''",
                },
                "shipping_contact": {
                    "type": "string",
                    "description": "收货人姓名，如'林炼豪'；如无传 ''",
                },
                "shipping_phone": {
                    "type": "string",
                    "description": "收货人电话，如'13692977880'；如无传 ''",
                },
                "contract_no": {
                    "type": "string",
                    "description": "合同编号（admin 后台审批时再补）；如无传 ''",
                },
                "payment_terms": {
                    "type": "string",
                    "description": "付款方式，默认'乙方预付 100% 货款'；如无传 ''",
                },
                "tax_rate": {
                    "type": "string",
                    "description": "增值税税率字符串，如'13%'；如无传 ''",
                },
                "extras": {
                    "type": "object",
                    "description": "模板自定义占位符（极少用），如无传 {}",
                    "additionalProperties": True,
                },
            },
        },
    },
    "_subgraphs": ["contract"],
}

GENERATE_PRICE_QUOTE_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "generate_price_quote",
        "strict": True,
        "description": "生成客户报价单 docx 并发到钉钉",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "customer_id",
                "items",
                "extras",
            ],
            "properties": {
                "customer_id": {
                    "type": "integer",
                    "description": "ERP 客户 ID，必须用 search_customers 真返过的 id",
                },
                "items": {
                    "type": "array",
                    "description": "商品列表，每项含 product_id/name/qty/price；如无传 []",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["product_id", "qty", "price"],
                        "properties": {
                            "product_id": {
                                "type": "integer",
                                "description": "ERP 商品 ID（必须用 search_products 真返过的 id）",
                            },
                            "qty": {
                                "type": "number",
                                "description": "数量（必须大于 0）",
                            },
                            "price": {
                                "type": "number",
                                "description": "单价（必须大于 0）",
                            },
                        },
                    },
                },
                "extras": {
                    "type": "object",
                    "description": "模板自定义占位符（极少用），如无传 {}",
                    "additionalProperties": True,
                },
            },
        },
    },
    "_subgraphs": ["quote"],
}


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
    shipping_address: str | None = None,
    shipping_contact: str | None = None,
    shipping_phone: str | None = None,
    contract_no: str | None = None,
    payment_terms: str | None = None,
    tax_rate: str | None = None,
    extras: dict | None = None,
    *,
    hub_user_id: int,
    conversation_id: str,
    acting_as_user_id: int,
) -> dict:
    """生成销售合同草稿 docx 并发到钉钉。

    Args:
        template_id: ContractTemplate.id（销售合同模板一般是 1）
        customer_id: ERP 客户 ID（必须用 search_customers 真返过的 id）
        items: [{"product_id", "name", "qty", "price"}]
            product_id 必须用 search_products 真返过的 id；不要凭印象编造 ID
        shipping_address: 收货地址（用户提供时务必传），如"广州市天河区华穗路406号"
        shipping_contact: 收货人姓名，如"林炼豪"
        shipping_phone: 收货人电话，如"13692977880"
        contract_no: 合同编号（admin 后台审批时再补）
        payment_terms: 付款方式，默认"乙方预付 100% 货款"
        tax_rate: 增值税税率字符串，如"13%"，默认 13%
        extras: **极少需要**传——上面 5 个常用字段已抽到顶层。仅当模板有自定义
                占位符时才用这个 dict。**类型必须是 dict，不能是字符串**。

    Returns:
        {"draft_id", "file_sent", "file_name"}

    幂等保护（v8 staging review #15 → #17 → #18）：
    fingerprint = sha256(template_id|customer_id|items|extras_normalized) +
    (conversation_id, requester_hub_user_id, fingerprint) partial UNIQUE index +
    IntegrityError 回查复用，确保并发场景也不重复创建 draft。
    """
    # sentinel 归一化（spec §1.3 v3.4）：LLM 传 "" 当 optional → 归一化成 None
    shipping_address = shipping_address or None
    shipping_contact = shipping_contact or None
    shipping_phone = shipping_phone or None
    contract_no = contract_no or None
    payment_terms = payment_terms or None
    tax_rate = tax_rate or None
    extras = extras or {}

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

    # v8 staging review #21：把顶层参数 shipping/contract_no/payment_terms 优先注入 extras
    # （LLM 直接传顶层字段而不需要构造 dict——schema 明确字段名 LLM 不会瞎猜）
    top_level_extras: dict[str, str] = {}
    if shipping_address:
        top_level_extras["shipping_address"] = str(shipping_address)
    if shipping_contact:
        top_level_extras["shipping_contact"] = str(shipping_contact)
    if shipping_phone:
        top_level_extras["shipping_phone"] = str(shipping_phone)
    if contract_no:
        top_level_extras["contract_no"] = str(contract_no)
    if payment_terms:
        top_level_extras["payment_terms"] = str(payment_terms)
    if tax_rate:
        top_level_extras["tax_rate"] = str(tax_rate)

    # extras 类型加固（LLM 偶尔传 string 而不是 dict——这种情况现在罕见，
    # 因为常用字段已抽到顶层；剩下的兜底）
    safe_extras = extras if isinstance(extras, dict) else {}
    if extras and not isinstance(extras, dict):
        logger.warning(
            "generate_contract_draft 收到非 dict extras（已忽略，请用顶层 shipping_xxx 等参数）"
            " conv=%s type=%s value=%r",
            conversation_id, type(extras).__name__, str(extras)[:100],
        )
    # 合并：seller_extras（甲方账套）→ top_level_extras（用户传的收货等）→ safe_extras（兜底）
    merged_extras = {**seller_extras, **top_level_extras, **safe_extras}

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
            "ContractDraft 幂等命中（fingerprint）复用 draft_id=%s status=%s conv=%s",
            draft.id, draft.status, conversation_id,
        )
        # v8 review #23：已成功发过钉钉的 draft 不重发文件
        # 防 LLM 把用户的"是"理解成"再生成一次"，导致同一文件发到钉钉 2 次
        # 仅 status="sent" 才跳过；status="generated"（render 成功但 send 失败）继续走 send 路径
        if draft.status == "sent":
            file_name = f"销售合同_{customer.get('name')}_{date.today().isoformat()}.docx"
            return {
                "draft_id": draft.id,
                "file_sent": True,
                "file_name": file_name,
                "note": "该合同已生成并发送过钉钉，未重复发送（如需重发请联系管理员）",
            }
    else:
        # v8 review #18：DB 已加 partial UNIQUE index 防 race；
        # create 抛 IntegrityError 时回查 first() 拿对端 race winner 的 draft 复用
        from tortoise.exceptions import IntegrityError
        try:
            draft = await ContractDraft.create(
                template_id=template_id,
                requester_hub_user_id=hub_user_id,
                customer_id=customer_id,
                items=items,
                extras=merged_extras,  # v8 review #17：审计用
                fingerprint=fingerprint,
                rendered_file_storage_key=None,  # 第一版不存文件 bytes
                conversation_id=conversation_id,
            )
        except IntegrityError:
            # 并发 race：另一个请求已经 create 成功 → 我们回查复用它的 draft
            logger.warning(
                "ContractDraft fingerprint 并发 race，回查复用 conv=%s fp=%s",
                conversation_id, fingerprint[:16],
            )
            draft = await ContractDraft.filter(
                conversation_id=conversation_id,
                requester_hub_user_id=hub_user_id,
                fingerprint=fingerprint,
            ).first()
            if draft is None:
                # 极端：DB UNIQUE 拒绝但回查又没拿到（非常罕见，可能 DB 索引未生效）
                logger.exception(
                    "ContractDraft IntegrityError 但回查无果 conv=%s fp=%s",
                    conversation_id, fingerprint[:16],
                )
                raise

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
    # sentinel 归一化（spec §1.3 v3.4）：LLM 传 {} 当 optional → 归一化成 {} 已 ok；extras={} 保持
    extras = extras or {}

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
