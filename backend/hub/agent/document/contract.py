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
from datetime import date, datetime, UTC

from docx import Document  # python-docx

from hub.models.contract import ContractTemplate

logger = logging.getLogger("hub.agent.document.contract")


class TemplateNotFoundError(Exception):
    """合同模板不存在或未启用。"""


class TemplateRenderError(Exception):
    """模板渲染失败（占位符缺失 / 数据格式错 / file_storage_key 格式异常）。"""


class ContractRenderer:
    """合同模板渲染：从 ContractTemplate 加载 docx → 替换占位符 → 返 bytes。"""

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
        """组装占位符上下文。"""
        ctx: dict = {
            "customer_name": customer.get("name", ""),
            "customer_address": customer.get("address", ""),
            "customer_id": str(customer.get("id", "")),
            "today": date.today().isoformat(),
            "now": datetime.now(UTC).isoformat(),
            "items": items,
            "total_amount": str(
                sum(
                    float(i.get("subtotal") or float(i.get("price", 0)) * float(i.get("qty", 0)))
                    for i in items
                )
            ),
            **{k: str(v) if not isinstance(v, (list, dict)) else v for k, v in extras.items()},
        }
        # 校验 required placeholders
        for ph in (template.placeholders or []):
            if not isinstance(ph, dict):
                continue
            name = ph.get("name")
            if ph.get("required") and name and name not in ctx:
                raise TemplateRenderError(f"必填占位符 {name} 缺数据")
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
        """表格级 {{items_table}} 占位符 → 展开成多行；其余 cell 内占位符替换。"""
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
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
