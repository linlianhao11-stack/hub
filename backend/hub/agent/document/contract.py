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
import copy
import io
import logging
import re
from datetime import UTC, date, datetime

from docx import Document  # python-docx
from docx.table import _Row

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

        merged cell 防重复处理：merged 后多 row 共享同一个 XML element（cell._tc），
        用 element id 去重。**不能用 id(cell)** —— Python GC 会复用对象 id，
        导致正常的不同 cell 也被错认为"已处理"（v8 staging review #6 实测踩坑）。
        """
        # v4 加固（v8 staging review #7）：items_table 真正按列展开成 N 行表格，
        # 而不是把所有数据挤到第一个 cell。
        # 模板规则：含 `{{items_table}}` 的整行视为"商品行模板"，期望 8 列：
        #   序号 / 产品名称 / 规格 / 颜色 / 数量 / 单价 / 含税金额 / 备注
        # 渲染流程：在该 row 前插入 N 行（每个 item 一行），最后删除占位 row。
        # 兼容：cell 数 < 8 时按 cell 顺序填前 N 个字段，剩余字段忽略。

        # 第 1 阶段：找到所有"items_table 模板行"，记录 (table, tr_element) 待展开
        items_template_trs: list[tuple] = []
        for table in doc.tables:
            for row in table.rows:
                row_text = " ".join(c.text for c in row.cells)
                if "{{items_table}}" in row_text:
                    items_template_trs.append((table, row, row._tr))
                    break  # 同一 table 只取第一个 items_table 行

        # 第 2 阶段：按记录展开
        items = ctx.get("items") or []
        for table, row, tr in items_template_trs:
            ContractRenderer._expand_items_row(table, row, tr, items)

        # 第 3 阶段：扫所有表格 cell 做普通占位符替换（替换幂等，无需去重）
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
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

    @staticmethod
    def _expand_items_row(table, template_row, template_tr, items: list[dict]) -> None:
        """把 items_table 占位行展开成 N 行。

        - 复制模板行的 XML element 作为新行（保留边框 / 字号等格式）
        - 按列 0-7 填：序号 / 产品名称 / 规格 / 颜色 / 数量 / 单价 / 含税金额 / 备注
        - 在原 tr 前插入新行，最后删除原 tr
        """
        parent = template_tr.getparent()
        if parent is None:
            return  # 模板行已脱离 doc，啥都不做

        if not items:
            # 无 items：清空占位行内容（保留行结构）
            for cell in template_row.cells:
                ContractRenderer._set_cell_text(cell, "")
            return

        idx = list(parent).index(template_tr)
        # 8 列字段顺序（与模板对齐；cell 数少于 8 时按顺序填前 N 个）
        for i, item in enumerate(items):
            new_tr = copy.deepcopy(template_tr)
            new_row = _Row(new_tr, table)
            cells = new_row.cells

            qty = item.get("qty") or 0
            price = item.get("price") or 0
            subtotal = item.get("subtotal")
            if subtotal is None:
                try:
                    subtotal = float(price) * float(qty)
                except (TypeError, ValueError):
                    subtotal = ""

            col_values = [
                str(i + 1),                            # 序号
                str(item.get("name") or ""),           # 产品名称
                str(item.get("spec") or ""),           # 规格
                str(item.get("color") or ""),          # 颜色
                str(qty) if qty else "",               # 数量
                str(price) if price else "",           # 单价
                str(subtotal) if subtotal != "" else "",  # 含税金额
                str(item.get("remark") or ""),         # 备注
            ]
            for col_i, value in enumerate(col_values):
                if col_i >= len(cells):
                    break
                ContractRenderer._set_cell_text(cells[col_i], value)

            parent.insert(idx + i, new_tr)

        # 删除原占位行（在所有新行已插入后）
        parent.remove(template_tr)

    @staticmethod
    def _set_cell_text(cell, text: str) -> None:
        """清空 cell 所有 paragraph 的 run，把 text 写到第一个 paragraph 的第一个 run。
        保留 cell 原有格式（边框 / 字号 / 对齐）。"""
        # 清空所有 run text（不删 paragraph，保字号/对齐设置）
        for p in cell.paragraphs:
            for r in p.runs:
                r.text = ""
        if not cell.paragraphs:
            cell.add_paragraph(text)
            return
        p = cell.paragraphs[0]
        if p.runs:
            p.runs[0].text = text
        else:
            p.add_run(text)
