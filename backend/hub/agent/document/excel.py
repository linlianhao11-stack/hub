"""Plan 6 Task 7：Excel 表格导出（openpyxl）。"""
from __future__ import annotations

import io
import json
import logging

from openpyxl import Workbook

logger = logging.getLogger("hub.agent.document.excel")


def _sanitize_cell(v: object) -> object:
    """openpyxl 单元格值清理：dict/list/tuple/set 转 JSON 字符串；其他原样。"""
    if v is None:
        return ""
    if isinstance(v, (dict, list, tuple, set)):
        try:
            return json.dumps(v, ensure_ascii=False, default=str)
        except Exception:
            return str(v)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, str)):
        return v
    # 其他类型（datetime / Decimal 等）转 str
    return str(v)


class ExcelExporter:
    """openpyxl 把 list[dict] 写成 .xlsx 字节流。"""

    async def export(
        self,
        *,
        table_data: list[dict],
        sheet_name: str = "Sheet1",
    ) -> bytes:
        """导出 Excel。

        Args:
            table_data: list of dict，第一行为表头（取所有 keys 的并集，保留首行顺序）；
                       空 list 返空 Excel
            sheet_name: 工作表名（Excel 限制 31 字符）

        Returns:
            .xlsx 字节流
        """
        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name[:31]  # Excel 上限 31 字符

        if not table_data:
            output = io.BytesIO()
            wb.save(output)
            return output.getvalue()

        # 表头 = 所有行 key 的并集（保留首行顺序）
        headers: list[str] = []
        seen: set[str] = set()
        for row in table_data:
            if isinstance(row, dict):
                for k in row.keys():
                    if k not in seen:
                        headers.append(k)
                        seen.add(k)

        if not headers:
            output = io.BytesIO()
            wb.save(output)
            return output.getvalue()

        ws.append(headers)
        for row in table_data:
            if isinstance(row, dict):
                ws.append([_sanitize_cell(row.get(h)) for h in headers])

        output = io.BytesIO()
        wb.save(output)
        return output.getvalue()
