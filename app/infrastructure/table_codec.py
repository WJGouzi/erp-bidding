# -*- coding: utf-8 -*-
"""Per-Cell 自描述表格数据模型与编解码器。

统一「识别→存储→组装」三阶段的表格表示。

核心数据类:
  - TableCell: 单个格, 自带 colSpan/rowSpan/格式
  - TableRow: 行
  - TableData: 完整表格

编解码:
  - to_per_cell(headers, rows, merges, column_widths) → TableData
  - write_table_from_data(doc, table_data) → None  (XML 写入)
  - from_dict(d) → TableData
  - to_dict(td) → dict
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from lxml import etree as ET
from app.service_modules.task_pipeline.helpers import _strip_xml_control_chars

logger = logging.getLogger(__name__)

NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


# ═══════════════════════════════════════════════════════════════════
#  数据类
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TableCell:
    """单个单元格，携带全部自描述属性。"""
    text: str = ""
    col_span: int = 1
    row_span: int = 1
    hidden: bool = False
    bold: bool = False
    font_name: str = ""
    font_size_half_pt: int = 0
    align: str = ""
    v_align: str = ""


@dataclass
class TableRow:
    """表格的一行。"""
    cells: List[TableCell] = field(default_factory=list)
    height: int = 0


@dataclass
class TableData:
    """完整表格数据。"""
    grid_cols: List[int] = field(default_factory=list)
    table_width: int = 9072
    rows: List[TableRow] = field(default_factory=list)
    borders: bool = True
    row_heights: List[dict] = field(default_factory=list)

    def row_count(self) -> int:
        return len(self.rows)

    def col_count(self) -> int:
        if self.grid_cols:
            return len(self.grid_cols)
        if self.rows:
            return max(len(r.cells) for r in self.rows)
        return 0

# ═══════════════════════════════════════════════════════════════════
#  序列化 / 反序列化
# ═══════════════════════════════════════════════════════════════════

def _cell_to_dict(c: TableCell) -> dict:
    d: Dict[str, Any] = {"text": c.text}
    if c.col_span != 1:
        d["colSpan"] = c.col_span
    if c.row_span != 1:
        d["rowSpan"] = c.row_span
    if c.hidden:
        d["hidden"] = True
    if c.bold:
        d["bold"] = True
    if c.font_name:
        d["fontName"] = c.font_name
    if c.font_size_half_pt:
        d["fontSizeHalfPt"] = c.font_size_half_pt
    if c.align:
        d["align"] = c.align
    if c.v_align:
        d["vAlign"] = c.v_align
    return d


def _cell_from_dict(d: dict) -> TableCell:
    return TableCell(
        text=d.get("text", ""),
        col_span=d.get("colSpan", 1),
        row_span=d.get("rowSpan", 1),
        hidden=d.get("hidden", False),
        bold=d.get("bold", False),
        font_name=d.get("fontName", ""),
        font_size_half_pt=d.get("fontSizeHalfPt", 0),
        align=d.get("align", ""),
        v_align=d.get("vAlign", ""),
    )


def to_dict(td: TableData) -> dict:
    """TableData → JSON-ready dict"""
    return {
        "gridCols": td.grid_cols,
        "tableWidth": td.table_width,
        "borders": td.borders,
        "rowHeights": td.row_heights,
        "rows": [
            {
                "cells": [_cell_to_dict(c) for c in row.cells],
                "height": row.height,
            }
            for row in td.rows
        ],
        "merge_cells": _rebuild_merge_cells(td),
    }


def _rebuild_merge_cells(td):
    """从 TableData.rows 重建 merge_cells 列表。"""
    _mc = []
    ncols = td.col_count()
    for ri, row in enumerate(td.rows):
        ci = 0
        cells_list = row.cells if hasattr(row, 'cells') else []
        while ci < ncols:
            cell = cells_list[ci] if ci < len(cells_list) else __import__('app.infrastructure.table_codec', fromlist=['TableCell']).TableCell()
            if cell.hidden:
                ci += 1
                continue
            if cell.col_span > 1:
                _mc.append({"type": "horizontal", "row": ri, "col": ci, "span": cell.col_span})
            if cell.row_span > 1:
                _mc.append({"type": "vertical", "row": ri, "col": ci, "span": cell.row_span})
            ci += cell.col_span
    return _mc


def from_dict(d: dict) -> TableData:
    """JSON-ready dict → TableData"""
    return TableData(
        grid_cols=d.get("gridCols", []),
        table_width=d.get("tableWidth", 9072),
        borders=d.get("borders", True),
        row_heights=list(d.get("rowHeights", [])),
        rows=[
            TableRow(
                cells=[_cell_from_dict(c) for c in row.get("cells", [])],
                height=row.get("height", 0),
            )
            for row in d.get("rows", [])
        ],
    )


# ═══════════════════════════════════════════════════════════════════
#  转换：{headers, rows, merges} → TableData
# ═══════════════════════════════════════════════════════════════════

def to_per_cell(
    headers: List[str],
    rows: List[List[str]],
    merges: List[dict],
    column_widths: Optional[List[int]] = None,
    row_heights: Optional[List[dict]] = None,
) -> TableData:
    """将当前三角格式 {headers, rows, merges} 转换为 Per-Cell TableData。

    Args:
        headers: 表头行文本列表
        rows: 数据行文本列表（二维）
        merges: 合并信息列表 [{"type":"horizontal"/"vertical", "row":int, "col":int, "span":int}, ...]
        column_widths: 可选的各列宽度

    Returns:
        TableData 对象
    """
    # 计算网格尺寸
    ncols = max(len(headers), max((len(r) for r in rows), default=0))
    # 注意: _extract_raw_table() 的 rows 已包含 header 行在 rows[0]
    # 所以 nrows = len(rows)
    nrows = len(rows)

    # 构建全部行的 cells 占位，rows[0] 即 header 行
    all_cells: List[List[Optional[TableCell]]] = [
        [None] * ncols for _ in range(nrows)
    ]

    # 构建水平合并查找表: (row, col) → span
    h_map: Dict[tuple, int] = {}
    for m in merges:
        if m["type"] == "horizontal":
            h_map[(m["row"], m["col"])] = m["span"]

    # 构建垂直合并查找表: col → [(start_row, span, col_span), ...]
    v_map: Dict[int, List[tuple]] = {}
    for m in merges:
        if m["type"] == "vertical":
            vrow = m["row"]
            vcol = m["col"]
            vspan = m["span"]
            vhspan = h_map.get((vrow, vcol), 1)
            v_map.setdefault(vcol, []).append((vrow, vspan, vhspan))

    def _vm_covered_colspan(r: int, c: int) -> int:
        """返回垂直合并覆盖的 col_span，0 = 未被覆盖"""
        for sr, sp, sh in v_map.get(c, []):
            if sr < r < sr + sp:
                return sh
        return 0

    def _v_span_at(r: int, c: int) -> int:
        """获取当前格子如果是垂直合并起始则返回 span"""
        for sr, sp, _sh in v_map.get(c, []):
            if r == sr:
                return sp
        return 1

    for ri in range(nrows):
        # rows[ri] 即当前行原始数据（rows[0] 是 header）
        src_row = rows[ri]
        ci = 0
        while ci < ncols:
            # 获取该单元格的文本
            cell_text = src_row[ci] if ci < len(src_row) else ""

            # 检查水平合并
            h_span = h_map.get((ri, ci), 1)

            # 检查垂直合并
            v_span = _v_span_at(ri, ci)

            is_hidden = False

            # 如果在垂直合并覆盖范围（非起始格），标记 hidden
            vc_span = _vm_covered_colspan(ri, ci)
            if vc_span:
                cell = TableCell(text="", hidden=True, col_span=vc_span)
                all_cells[ri][ci] = cell
                # 跳过被 col_span 覆盖的后续列
                for hc in range(ci + 1, min(ci + vc_span, ncols)):
                    if all_cells[ri][hc] is None:
                        all_cells[ri][hc] = TableCell(text="", hidden=True)
                ci += vc_span
                continue

            # 创建单元格
            cell = TableCell(
                text=cell_text,
                col_span=h_span,
                row_span=v_span,
                hidden=False,
            )
            all_cells[ri][ci] = cell

            # 填充被水平合并覆盖的虚拟单元格
            for hc in range(ci + 1, min(ci + h_span, ncols)):
                if all_cells[ri][hc] is None:
                    all_cells[ri][hc] = TableCell(text="", hidden=True)

            # 垂直合并由 _vm_covered_colspan 在后续行的循环中处理
            # 这里不需要预填充，_vm_covered_colspan 会在遍历到对应行时创建 hidden 格

            ci += h_span

        # 补齐未填充的单元格
        for ci2 in range(ncols):
            if all_cells[ri][ci2] is None:
                all_cells[ri][ci2] = TableCell(text="", hidden=False)

    # 构建行对象
    table_rows = [
        TableRow(cells=[all_cells[ri][ci] for ci in range(ncols)])
        for ri in range(nrows)
    ]

    # 注入行高
    if row_heights:
        for ri in range(min(nrows, len(row_heights))):
            rh_val = row_heights[ri].get("val", 0) if isinstance(row_heights[ri], dict) else int(row_heights[ri] or 0)
            table_rows[ri].height = rh_val

    # 列宽
    cols = list(column_widths) if column_widths else []

    return TableData(grid_cols=cols, rows=table_rows, row_heights=list(row_heights) if row_heights else [])


# ═══════════════════════════════════════════════════════════════════
#  按列宽计算
# ═══════════════════════════════════════════════════════════════════

def _calc_col_width(total_width: int, ncols: int) -> int:
    if ncols <= 0:
        return 0
    return total_width // ncols


# ═══════════════════════════════════════════════════════════════════
#  XML 写入：TableData → docx
# ═══════════════════════════════════════════════════════════════════

def _build_merge_key(merges: List[dict], row: int, col: int) -> Optional[dict]:
    """在 merge 列表中查找指定行列的合并信息。"""
    for m in merges:
        if m.get("row") == row and m.get("col") == col:
            return m
    return None


def write_table_from_data(doc, table_data: TableData, insert_after=None):
    """将 TableData 写入 python-docx Document 的 XML 层。

    Args:
        doc: python-docx Document
        table_data: Per-Cell 表格数据
        insert_after: 可选，在此 XML 元素之后插入表格。未提供时追加到 body 末尾。
    """
    ncols = table_data.col_count()
    if ncols <= 0:
        return
    nrows = table_data.row_count()
    if nrows <= 0:
        return

    # 列宽：如果没指定，均分
    col_widths = list(table_data.grid_cols) if table_data.grid_cols else []
    if not col_widths:
        cw = table_data.table_width // ncols
        col_widths = [cw] * ncols

    _ns = NS

    # 验证 insert_after 是 body 的直接子代，否则回溯到最近的 body 子代
    _body = doc.element.body
    if insert_after is not None:
        _parent = insert_after.getparent() if hasattr(insert_after, 'getparent') else None
        while _parent is not None and _parent is not _body:
            insert_after = _parent
            _parent = insert_after.getparent()
        # 如果 insert_after 不是 body 子代或其本身为 None，回退到 append
        if _parent is not _body or insert_after is None:
            insert_after = None

    # 创建表格 XML 根
    if insert_after is not None:
        tbl = ET.SubElement(doc.element.body, _ns + 'tbl')
        # 将 tbl 移动到 insert_after 之后
        insert_after.addnext(tbl)
    else:
        tbl = ET.SubElement(doc.element.body, _ns + 'tbl')

    # tblPr
    tblPr = ET.SubElement(tbl, _ns + 'tblPr')
    tblW = ET.SubElement(tblPr, _ns + 'tblW')
    tblW.set(_ns + 'w', str(table_data.table_width))
    tblW.set(_ns + 'type', 'dxa')
    tblStyle = ET.SubElement(tblPr, _ns + 'tblStyle')
    tblStyle.set(_ns + 'val', 'Table Grid')

    # 黑色实线边框
    tblBorders = ET.SubElement(tblPr, _ns + 'tblBorders')
    for _edge in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
        _el = ET.SubElement(tblBorders, _ns + _edge)
        _el.set(_ns + 'val', 'single')
        _el.set(_ns + 'sz', '4')
        _el.set(_ns + 'space', '0')
        _el.set(_ns + 'color', '000000')

    # tblGrid — 列定义
    tblGrid = ET.SubElement(tbl, _ns + 'tblGrid')
    for cw in col_widths:
        gc = ET.SubElement(tblGrid, _ns + 'gridCol')
        gc.set(_ns + 'w', str(cw))

    # 预先收集所有垂直合并信息：col -> [(start_row, span)]
    col_vm: Dict[int, List[tuple]] = {}
    for ri, row in enumerate(table_data.rows):
        ci = 0
        cells_list = row.cells if hasattr(row, 'cells') else []
        while ci < ncols:
            cell = cells_list[ci] if ci < len(cells_list) else TableCell()
            if cell.hidden:
                ci += 1
                continue
            if cell.row_span > 1:
                col_vm.setdefault(ci, []).append((ri, cell.row_span))
            ci += cell.col_span

    def _is_vm_continue(ri: int, ci: int) -> bool:
        """检查当前格子是否是垂直合并的延续格（需要写 vMerge=continue）"""
        for sr, sp in col_vm.get(ci, []):
            if sr < ri < sr + sp:
                return True
        return False

    # 逐行
    for ri, row in enumerate(table_data.rows):
        tr = ET.SubElement(tbl, _ns + 'tr')
        # 写行高
        if row.height > 0:
            trPr = ET.SubElement(tr, _ns + 'trPr')
            trHeight = ET.SubElement(trPr, _ns + 'trHeight')
            trHeight.set(_ns + 'val', str(row.height))
            trHeight.set(_ns + 'rule', 'atLeast')
        cells_list = row.cells if hasattr(row, 'cells') else []

        ci = 0
        while ci < ncols:
            cell = cells_list[ci] if ci < len(cells_list) else TableCell()

            # 处理垂直合并延续格：即使 hidden=true 也要创建 tc 带 vMerge=continue
            v_continue = _is_vm_continue(ri, ci)

            if cell.hidden and not v_continue:
                # 水平合并的虚拟格（无 vMerge 延续）→ 跳过
                ci += 1
                continue

            col_span = cell.col_span if (not cell.hidden or v_continue) else 1
            # 对于 vMerge continue 格子，文本清空
            text = cell.text if not cell.hidden and not v_continue else ""

            # 计算合并占用的总宽度
            span_width = sum(col_widths[ci : min(ci + col_span, ncols)])

            tc = ET.SubElement(tr, _ns + 'tc')
            tcPr = ET.SubElement(tc, _ns + 'tcPr')

            # 单元格宽度
            tcW = ET.SubElement(tcPr, _ns + 'tcW')
            tcW.set(_ns + 'w', str(span_width))
            tcW.set(_ns + 'type', 'dxa')

            # 水平合并
            if col_span > 1:
                gs = ET.SubElement(tcPr, _ns + 'gridSpan')
                gs.set(_ns + 'val', str(col_span))

            # 垂直合并 restart
            if not cell.hidden and cell.row_span > 1:
                vm = ET.SubElement(tcPr, _ns + 'vMerge')
                vm.set(_ns + 'val', 'restart')

            # 垂直合并 continue
            if v_continue:
                vm = ET.SubElement(tcPr, _ns + 'vMerge')
                vm.set(_ns + 'val', 'continue')

            # 垂直对齐
            if not cell.hidden and cell.v_align:
                va = ET.SubElement(tcPr, _ns + 'vAlign')
                va.set(_ns + 'val', cell.v_align)

            # 文本内容
            if text:
                p = ET.SubElement(tc, _ns + 'p')

                # 段落对齐
                if not cell.hidden and cell.align:
                    pPr = ET.SubElement(p, _ns + 'pPr')
                    jc = ET.SubElement(pPr, _ns + 'jc')
                    jc.set(_ns + 'val', cell.align)

                r_elem = ET.SubElement(p, _ns + 'r')
                rPr = ET.SubElement(r_elem, _ns + 'rPr')

                # 字体
                font = cell.font_name or '\u4eff\u5b8b'
                rFonts = ET.SubElement(rPr, _ns + 'rFonts')
                rFonts.set(_ns + 'ascii', font)
                rFonts.set(_ns + 'hAnsi', font)
                rFonts.set(_ns + 'eastAsia', font)

                # 字号
                sz_val = str(cell.font_size_half_pt) if cell.font_size_half_pt else '24'
                sz = ET.SubElement(rPr, _ns + 'sz')
                sz.set(_ns + 'val', sz_val)
                szCs = ET.SubElement(rPr, _ns + 'szCs')
                szCs.set(_ns + 'val', sz_val)

                # 加粗
                if not cell.hidden and cell.bold:
                    ET.SubElement(rPr, _ns + 'b')

                t_elem = ET.SubElement(r_elem, _ns + 't')
                t_elem.text = _strip_xml_control_chars(text)
                t_elem.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')

            ci += col_span

    return tbl


def _patch_vmerge_continue(tbl: ET.Element, td: TableData, _ns: str) -> None:
    """遍历所有行，为被垂直合并覆盖的非起始行补上 vMerge continue。"""
    nrows = td.row_count()
    ncols = td.col_count()

    # 收集每列的垂直合并信息
    col_vm: Dict[int, List[tuple]] = {}
    for ri, row in enumerate(td.rows):
        ci = 0
        while ci < ncols:
            cell = row.cells[ci] if ci < len(row.cells) else TableCell()
            if cell.hidden:
                ci += 1
                continue
            if cell.row_span > 1:
                col_vm.setdefault(ci, []).append((ri, cell.row_span))
            ci += cell.col_span

    # 找到所有 tr 元素
    tr_list = list(tbl.findall(f'{_ns}tr'))

    # 对每列，标记被垂直合并覆盖的行
    for ci, vm_entries in col_vm.items():
        for start_row, span in vm_entries:
            for vr in range(start_row + 1, min(start_row + span, nrows)):
                if vr < len(tr_list):
                    tr_elem = tr_list[vr]
                    # 找到该行对应的 tc
                    # 由于可能存在水平合并，需要定位到正确的 tc
                    tcs = list(tr_elem.findall(f'{_ns}tc'))
                    tc_idx = 0
                    scan_ci = 0
                    while scan_ci < ci and tc_idx < len(tcs):
                        # 获取这个 tc 的 gridSpan
                        tc_elem = tcs[tc_idx]
                        tcPr = tc_elem.find(f'{_ns}tcPr')
                        gs = tcPr.find(f'{_ns}gridSpan') if tcPr is not None else None
                        span_v = int(gs.get(f'{_ns}val')) if gs is not None else 1
                        scan_ci += span_v
                        tc_idx += 1
                    if tc_idx < len(tcs):
                        tc_elem = tcs[tc_idx]
                        tcPr = tc_elem.find(f'{_ns}tcPr')
                        if tcPr is None:
                            tcPr = ET.SubElement(tc_elem, _ns + 'tcPr')
                        # 检查是否已经有 vMerge
                        existing_vm = tcPr.find(f'{_ns}vMerge')
                        if existing_vm is None:
                            vm = ET.SubElement(tcPr, _ns + 'vMerge')
                            vm.set(_ns + 'val', 'continue')
                        elif existing_vm.get(f'{_ns}val') != 'continue':
                            existing_vm.set(_ns + 'val', 'continue')
