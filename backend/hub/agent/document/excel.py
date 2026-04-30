"""Plan 6 Task 7：Excel 表格导出（openpyxl）。"""
from __future__ import annotations

import io
import logging

from openpyxl import Workbook

logger = logging.getLogger("hub.agent.document.excel")


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
                ws.append([row.get(h, "") for h in headers])

        output = io.BytesIO()
        wb.save(output)
        return output.getvalue()
