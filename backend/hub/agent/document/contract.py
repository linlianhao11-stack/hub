"""Plan 6 Task 7：合同模板 docx 渲染。

ContractTemplate.placeholders 形如：
[
  {"name": "customer_name", "type": "string", "required": true},
  {"name": "items_table", "type": "table", "required": true},
  {"name": "total_amount", "type": "decimal", "required": true},
]

模板 docx 中用 {{customer_name}} 标记文本占位；
表格用整行 {{items_table}}（找到所在 cell 后展开 list[dict]）。
"""
from __future__ import annotations

import base64
import io
import logging
import re
from datetime import UTC, date, datetime

from docx import Document  # python-docx

from hub.models.contract import ContractTemplate

logger = logging.getLogger("hub.agent.document.contract")


class TemplateNotFoundError(Exception):
    """合同模板不存在或未启用。"""


class TemplateRenderError(Exception):
    """模板渲染失败（占位符缺失 / 数据格式错 / file_storage_key 格式异常）。"""


class ContractRenderer:
    """合同模板渲染：从 ContractTemplate 加载 docx → 替换占位符 → 返 bytes。

    模板设计规范（Plan 11 admin 上传模板时遵守）：
    - 占位符 {{xxx}} 必须独立成段落或独立成 cell，不要与其它格式 run 混用
    - 例：✅ 单独段落 "客户：{{customer_name}}"
    - 例：❌ 同一段落 "客户：{{customer_name}}（**重要**）" — bold 格式会丢失
    第一版段落级 + 表格级两级替换；多 run 格式合并问题待 follow-up
    （需"找连续含 {{ 的 run 串、合并文本、保留首 run 格式"逻辑）。
    """

    async def render(
        self,
        *,
        template_id: int,
        customer: dict,
        items: list[dict],
        extras: dict | None = None,
    ) -> bytes:
        """渲染合同 docx。

        Args:
            template_id: ContractTemplate.id
            customer: {"id", "name", "address", ...}（ERP 客户字段）
            items: [{"product_id", "name", "qty", "price", "subtotal"}, ...]
            extras: 额外字段如 contract_no / payment_terms

        Returns:
            docx 字节流（未加密；调用方决定是否存储加密）

        Raises:
            TemplateNotFoundError: template_id 不存在或未启用
            TemplateRenderError: 占位符缺失或数据格式错
        """
        template = await ContractTemplate.filter(
            id=template_id, is_active=True,
        ).first()
        if not template:
            raise TemplateNotFoundError(f"合同模板 {template_id} 不存在或未启用")

        if not template.file_storage_key:
            raise TemplateRenderError(f"模板 {template_id} 缺 file_storage_key")

        template_bytes = await self._load_template_bytes(template)

        try:
            doc = Document(io.BytesIO(template_bytes))
            ctx = self._build_context(template, customer, items, extras or {})
            self._replace_paragraphs(doc, ctx)
            self._replace_tables(doc, ctx)
            # 扫描渲染后仍含 {{xxx}} 的占位符（模板 typo 或数据缺字段）
            unknown_placeholders: set[str] = set()
            for para in doc.paragraphs:
                for m in re.finditer(r"\{\{(\w+)\}\}", para.text):
                    unknown_placeholders.add(m.group(1))
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for m in re.finditer(r"\{\{(\w+)\}\}", cell.text):
                            unknown_placeholders.add(m.group(1))
            if unknown_placeholders:
                logger.warning(
                    "合同模板 %s 渲染后仍含未知占位符: %s（可能是模板 typo）",
                    template.id, sorted(unknown_placeholders),
                )
            output = io.BytesIO()
            doc.save(output)
            return output.getvalue()
        except (TemplateRenderError, TemplateNotFoundError):
            raise
        except KeyError as e:
            raise TemplateRenderError(f"模板缺占位符 {e}") from e
        except Exception as e:
            raise TemplateRenderError(f"渲染失败: {e}") from e

    @staticmethod
    async def _load_template_bytes(template: ContractTemplate) -> bytes:
        """加载模板 docx 字节流。

        第一版：template.file_storage_key 存的是 base64 编码的 docx bytes。
        生产可换成 OSS URL / 文件路径。
        """
        try:
            return base64.b64decode(template.file_storage_key)
        except Exception as e:
            raise TemplateRenderError(f"模板 file_storage_key 格式异常: {e}") from e

    @staticmethod
    def _build_context(
        template: ContractTemplate,
        customer: dict,
        items: list[dict],
        extras: dict,
    ) -> dict:
        """组装占位符上下文。

        v2 staging review #5：placeholders 自动识别全部 required=True 太严苛——
        模板里有 18 个占位符（甲方/乙方/订单/收货全套），LLM 不可能每个都传齐。
        修：缺数据时**注入空字符串**而不是抛错，让 docx 渲染产物里
        留白让用户后期手填，至少合同主体（客户/商品/总额）能正确生成。
        """
        # extras 类型加固（防 LLM 传 string 等错型）
        if not isinstance(extras, dict):
            extras = {}

        ctx: dict = {
            # 乙方（来自 ERP customer 详情）
            "customer_name": customer.get("name") or "",
            "customer_address": customer.get("address") or "",
            "customer_id": str(customer.get("id") or ""),
            "customer_phone": customer.get("phone") or "",
            "customer_tax_id": customer.get("tax_id") or "",
            "customer_bank_name": customer.get("bank_name") or "",
            "customer_bank_account": customer.get("bank_account") or "",
            "customer_contact_person": customer.get("contact_person") or "",
            # 订单
            "today": date.today().isoformat(),
            "now": datetime.now(UTC).isoformat(),
            "items": items,
            "total_amount": str(
                sum(
                    float(i.get("subtotal") or float(i.get("price", 0)) * float(i.get("qty", 0)))
                    for i in items
                )
            ),
            # extras 覆盖前面（甲方账套字段 / shipping / tax_rate 等都从这里来）
            **{k: str(v) if not isinstance(v, (list, dict)) else v for k, v in extras.items()},
        }

        # required 字段缺数据时注入空字符串而不是抛错
        # 模板里残留的 {{xxx}} 占位符会被替换为空，docx 主体仍可生成（客户/商品/总额都对）
        # 后期 admin / 销售可在 docx 里手填空白字段
        missing_required: list[str] = []
        for ph in (template.placeholders or []):
            if not isinstance(ph, dict):
                continue
            name = ph.get("name")
            if not name:
                continue
            if ph.get("required") and name not in ctx:
                ctx[name] = ""  # 空串占位
                missing_required.append(name)
        if missing_required:
            import logging
            logging.getLogger(__name__).warning(
                "合同模板 %s 渲染时缺数据字段 %s（已用空串兜底，docx 留白）",
                template.id, missing_required,
            )

        return ctx

    @staticmethod
    def _replace_paragraphs(doc: Document, ctx: dict) -> None:
        """段落级 {{key}} 替换。"""
        for para in doc.paragraphs:
            full_text = para.text
            if "{{" not in full_text:
                continue
            new_text = full_text
            for key, value in ctx.items():
                if isinstance(value, (list, dict)):
                    continue  # 列表/字典占位符走 _replace_tables
                placeholder = f"{{{{{key}}}}}"
                if placeholder in new_text:
                    new_text = new_text.replace(placeholder, str(value))
            if new_text != full_text:
                # 清空所有 run，只用第一个 run 写文本
                for run in para.runs:
                    run.text = ""
                if para.runs:
                    para.runs[0].text = new_text
                else:
                    para.add_run(new_text)

    @staticmethod
    def _replace_tables(doc: Document, ctx: dict) -> None:
        """表格级 {{items_table}} 占位符 → 展开成多行；其余 cell 内占位符替换。

        merged cell 防重复处理：python-docx 对 merged cell 会多次返回同一 cell 对象，
        用 seen 集合按 id() 跳过已处理 cell。
        """
        seen: set[int] = set()
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if id(cell) in seen:
                        continue
                    seen.add(id(cell))
                    cell_text = cell.text
                    if "{{items_table}}" in cell_text:
                        items = ctx.get("items") or []
                        # 清掉占位内容
                        for para in cell.paragraphs:
                            for run in para.runs:
                                run.text = ""
                        # 简化版：把 items 拼成多行文本写进当前 cell
                        for item in items:
                            subtotal = item.get("subtotal") or (
                                float(item.get("price", 0)) * float(item.get("qty", 0))
                            )
                            line = (
                                f"{item.get('name', '')} × {item.get('qty', 0)} "
                                f"@ {item.get('price', 0)} = {subtotal}"
                            )
                            cell.add_paragraph(line)
                        continue
                    # 非 items_table：做普通占位符替换
                    for para in cell.paragraphs:
                        para_text = para.text
                        if "{{" not in para_text:
                            continue
                        new_text = para_text
                        for key, value in ctx.items():
                            if isinstance(value, (list, dict)):
                                continue
                            placeholder = f"{{{{{key}}}}}"
                            if placeholder in new_text:
                                new_text = new_text.replace(placeholder, str(value))
                        if new_text != para_text:
                            for run in para.runs:
                                run.text = ""
                            if para.runs:
                                para.runs[0].text = new_text
                            else:
                                para.add_run(new_text)
