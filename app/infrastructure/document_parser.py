"""版面感知的文档解析器。

支持 DOCX（标题层级 + 表格结构）和 PDF（fitz 文本页 + PaddleOCR 扫描页）的
结构化解析，输出统一的结构化文档模型 JSON。
"""

import json
import logging
import tempfile
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Optional
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import fitz

logger = logging.getLogger(__name__)


class StructuredDocument:
    """结构化文档模型，统一表示 DOCX/PDF 的解析结果。"""

    def __init__(self, file_name="", file_sha256="", parse_version="1.0"):
        self.file_name = file_name
        self.file_sha256 = file_sha256
        self.parse_version = parse_version
        self.sections = []  # list[Section]
        self.tables = []  # list of python-docx Table objects

    def to_dict(self) -> dict:
        return {
            "file_name": self.file_name,
            "file_sha256": self.file_sha256,
            "parse_version": self.parse_version,
            "sections": [s.to_dict() for s in self.sections],
            "tables": [
                {
                    "headers": (
                        # python-docx Table: 表头在第一行
                        [cell.text.strip() for cell in t.rows[0].cells]
                        if hasattr(t, "rows") and not hasattr(t, "headers")
                        # TableStub: headers 属性直接可用
                        else list(t.headers)
                    ),
                    "rows": (
                        # python-docx Table: 从第二行开始取数据
                        [[cell.text.strip() for cell in row.cells] for row in list(t.rows)[1:]]
                        if hasattr(t, "rows") and not hasattr(t, "headers")
                        # TableStub: rows 属性直接可用
                        else [list(row) for row in t.rows]
                    ),
                }
                for t in self.tables
            ] if self.tables else [],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def to_text(self) -> str:
        """提取纯文本内容，递归遍历章节和子章节。"""
        texts = []
        for section in self.sections:
            texts.extend(self._section_to_texts(section))
        return "\n".join(texts)

    @staticmethod
    def _section_to_texts(section) -> "list[str]":
        result = []
        if section.title:
            result.append(section.title)
        for block in section.content:
            if block.type in (ContentBlock.TYPE_PARAGRAPH, ContentBlock.TYPE_HEADING, ContentBlock.TYPE_LIST):
                if block.text:
                    result.append(block.text)
            elif block.type == ContentBlock.TYPE_TABLE:
                parts = []
                if block.headers:
                    parts.append(" | ".join(block.headers))
                for row in block.rows:
                    parts.append(" | ".join(row))
                if parts:
                    result.append("\n".join(parts))
        for child in section.children:
            result.extend(StructuredDocument._section_to_texts(child))
        return result


    @classmethod
    def from_dict(cls, data: dict) -> "StructuredDocument":
        doc = cls(data.get("file_name", ""), data.get("file_sha256", ""), data.get("parse_version", "1.0"))
        for s_data in data.get("sections", []):
            section = Section.from_dict(s_data)
            doc.sections.append(section)
        # 表格数据以纯文本形式缓存（不可序列化 python-docx 原生对象）
        # 缓存的表格数据在 to_dict 中已转为 headers/rows 格式
        table_data = data.get("tables", [])
        if table_data:
            from collections import namedtuple
            TableStub = namedtuple("TableStub", ["headers", "rows"])
            doc.tables = [
                TableStub(
                    headers=t.get("headers", []),
                    rows=t.get("rows", []),
                )
                for t in table_data
            ]
        return doc


class Section:
    """文档中的一个章节或区块。"""

    def __init__(self, title="", level=1, page_range=None):
        self.title = title
        self.level = level  # 1=一级标题, 2=二级标题, ...
        self.content = []   # list[ContentBlock]
        self.children = []  # list[Section]（子章节）
        self.page_range = page_range or []  # [start_page, end_page]

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "level": self.level,
            "content": [c.to_dict() for c in self.content],
            "children": [c.to_dict() for c in self.children],
            "page_range": self.page_range,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Section":
        s = cls(data.get("title", ""), data.get("level", 1), data.get("page_range", []))
        for c_data in data.get("content", []):
            s.content.append(ContentBlock.from_dict(c_data))
        for c_data in data.get("children", []):
            s.children.append(cls.from_dict(c_data))
        return s


class ContentBlock:
    """文档内容块（段落、表格等）。"""

    TYPE_PARAGRAPH = "paragraph"
    TYPE_TABLE = "table"
    TYPE_HEADING = "heading"
    TYPE_LIST = "list"

    def __init__(self, type_="paragraph", text="", level=0):
        self.type = type_
        self.text = text
        self.level = level  # 列表缩进层级
        # 表格专用字段
        self.headers = []
        self.rows = []

    def to_dict(self) -> dict:
        d = {"type": self.type}
        if self.type in (self.TYPE_PARAGRAPH, self.TYPE_HEADING, self.TYPE_LIST):
            d["text"] = self.text
            if self.level:
                d["level"] = self.level
        elif self.type == self.TYPE_TABLE:
            d["headers"] = self.headers
            d["rows"] = self.rows
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ContentBlock":
        cb = cls(data.get("type", "paragraph"), data.get("text", ""), data.get("level", 0))
        cb.headers = data.get("headers", [])
        cb.rows = data.get("rows", [])
        return cb


class DocumentParser:
    """版面感知的文档解析器。"""

    PARSE_VERSION = "1.0"

    def __init__(self, ocr_client=None):
        self.ocr_client = ocr_client

    # ========== 统一入口 ==========

    def parse_structured(self, filename: str, payload: bytes, file_sha256: str = "") -> StructuredDocument:
        """统一入口：按扩展名选择解析器，返回结构化文档。

        Args:
            filename: 文件名（用于判断扩展名）
            payload: 文件二进制内容
            file_sha256: 文件 SHA256（可选）

        Returns:
            StructuredDocument 结构化文档
        """
        doc = StructuredDocument(file_name=filename, file_sha256=file_sha256, parse_version=self.PARSE_VERSION)
        ext = Path(filename).suffix.lower().lstrip(".") if filename else ""

        if ext == "docx":
            self._parse_docx_structured(payload, doc)
        elif ext in ("pdf",):
            self._parse_pdf_structured(payload, doc)
        elif ext in ("doc",):
            self._parse_doc_structured(payload, doc)
        elif ext in ("xlsx", "xls"):
            self._parse_spreadsheet_structured(payload, doc, ext)
        else:
            # 纯文本兜底
            text = payload.decode("utf-8", errors="replace")
            section = Section(title="全文", level=1)
            section.content.append(ContentBlock(ContentBlock.TYPE_PARAGRAPH, text))
            doc.sections.append(section)

        return doc

    # ========== DOCX 结构化解析 ==========

    def _parse_docx_structured(self, payload: bytes, doc: StructuredDocument):
        """解析 DOCX，保留标题层级和表格结构。"""
        try:
            from docx import Document as DocxDocument
        except ImportError:
            logger.warning("[parser] python-docx 未安装，使用降级解析")
            self._parse_docx_fallback(payload, doc)
            return

        try:
            document = DocxDocument(BytesIO(payload))
        except Exception as exc:
            logger.warning("[parser] python-docx 解析失败，使用降级解析: %s", exc)
            self._parse_docx_fallback(payload, doc)
            return

        # Heading 样式映射（含 toc 样式）
        heading_map = {}
        for i in range(1, 10):
            style_name = f"Heading {i}"
            try:
                heading_map[style_name] = i
            except Exception:
                pass


        # 文本内容级别的标题检测模式
        text_heading_patterns = [
            (1, r'^第[一二三四五六七八九十零〇百千万亿]+[章节篇部]'),      # 第一章
            (2, r'^[一二三四五六七八九十零〇]+[、，,．.]'),              # 一、
            (2, r'^\d+[、，,．.]'),                                   # 1.
            (2, r'^\d+\.\d+\s'),                                   # 1.1
            (3, r'^（[一二三四五六七八九十零〇]+）'),                    # （一）
            (3, r'^\d+\.\d+\.\d+\s'),                            # 1.1.1
        ]

        stack = [Section(title="__root__", level=0)]
        current_section = stack[-1]

        for para in document.paragraphs:
            text = para.text.strip()
            if not text:
                continue

            style_name = para.style.name if para.style else ""
            heading_level = heading_map.get(style_name, 0)

            if heading_level > 0:
                # 创建新章节
                new_section = Section(title=text, level=heading_level)
                # 弹出比当前层级深或相等的章节
                while stack and stack[-1].level >= heading_level:
                    stack.pop()
                # 仅剩 __root__ 时直接加到 doc.sections（修复章节消失 bug）
                if len(stack) == 1 and stack[0].level == 0:
                    doc.sections.append(new_section)
                elif stack:
                    stack[-1].children.append(new_section)
                else:
                    doc.sections.append(new_section)
                stack.append(new_section)
                current_section = new_section
            else:
                # 文本内容级标题检测（当样式为 Normal 但内容像标题时）
                text_heading = 0
                for level, pattern in text_heading_patterns:
                    if re.match(pattern, text):
                        text_heading = level
                        break
                
                if text_heading > 0 and heading_level == 0:
                    # 跳过目录项（含 tab 或纯数字页码的短标题）
                    if "\t" in text:
                        # 来自 TOC 目录的条目，不作为章节
                        block = ContentBlock(ContentBlock.TYPE_PARAGRAPH, text)
                        current_section.content.append(block)
                        continue
                    new_section = Section(title=text, level=text_heading)
                    while stack and stack[-1].level >= text_heading:
                        stack.pop()
                    # 仅剩 __root__ 时直接加到 doc.sections（修复章节消失 bug）
                    if len(stack) == 1 and stack[0].level == 0:
                        doc.sections.append(new_section)
                    elif stack:
                        stack[-1].children.append(new_section)
                    else:
                        doc.sections.append(new_section)
                    stack.append(new_section)
                    current_section = new_section
                else:
                    block = ContentBlock(ContentBlock.TYPE_PARAGRAPH, text)
                    # 尝试判断列表
                    num_prefix = re.match(r'^[\d一二三四五六七八九十]+[、.．\s]', text)
                    bullet_prefix = re.match(r'^[-\u2022\u25cf\u25cb\u25a0]\s', text)
                    if num_prefix or bullet_prefix:
                        block.type = ContentBlock.TYPE_LIST
                        block.level = 0
                    current_section.content.append(block)

        # 在解析表格前，先保存栈中的段落内容
        if stack:
            root_section = stack[0]
            # 将 root section 中的非标题内容保存为一个前言章节
            if root_section.content:
                from_title = root_section.content[0].text[:30] if root_section.content[0].text else ""
                preamble = Section(title=from_title or "前言", level=1)
                preamble.content = list(root_section.content)
                root_section.content = []
                doc.sections.insert(0, preamble)
            # 将 root 下的子章节（文本检测到的标题）转移到 doc.sections
            if root_section.children:
                for child in root_section.children:
                    child.level = 1  # 提升到顶级
                    doc.sections.append(child)
                root_section.children = []

        # 解析表格（带位置感知）
        for table_idx, table in enumerate(document.tables):
            self._parse_table(table, doc, table_index=table_idx, docx_document=document)

        # 保存原始表格对象供 table_parser 使用
        doc.tables = list(document.tables)

        # 清理空的根章节
        doc.sections = [s for s in doc.sections if s.title != "__root__"]
        
        # 如果没有检测到任何标题层级（纯文本），把内容放到一个根章节下
        if not doc.sections:
            root_content = []
            if stack and stack[0].children:
                doc.sections = stack[0].children
            elif stack:
                root = Section(title="全文", level=1)
                for s in stack:
                    root.content.extend(s.content)
                if root.content or root.children:
                    doc.sections = [root]

    def _parse_table(self, table, doc: StructuredDocument, table_index: int = 0, docx_document=None):
        """从 python-docx Table 对象提取结构化表格，并尝试分配到正确的章节。

        Args:
            table: python-docx Table 对象
            doc: StructuredDocument 目标文档
            table_index: 表格在 document.tables 中的索引（用于定位章节）
            docx_document: 可选的 python-docx Document，用于定位表格在正文中的位置
        """
        block = ContentBlock(ContentBlock.TYPE_TABLE)
        rows_data = []
        for row_idx, row in enumerate(table.rows):
            cells = [cell.text.strip() for cell in row.cells]
            if row_idx == 0:
                block.headers = cells
            else:
                rows_data.append(cells)
        block.rows = rows_data

        if not doc.sections:
            s = Section(title="表格", level=1)
            s.content.append(block)
            doc.sections.append(s)
            return

        # 尝试定位表格在文档体中的位置
        target_section = doc.sections[-1]  # 默认：最后章节
        if docx_document and hasattr(docx_document, "element") and hasattr(docx_document.element, "body"):
            try:
                body = docx_document.element.body
                # 遍历 body 子元素，找到所有表格的索引
                table_elements = []
                for child in body:
                    tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if tag == "tbl":
                        table_elements.append(child)
                
                # 找到当前表格前面的段落文本
                if table_index < len(table_elements):
                    tbl_elem = table_elements[table_index]
                    prev_text = ""
                    # 找表格前最近的段落文本
                    for child in body:
                        if child is tbl_elem:
                            break
                        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if tag in ("p", "pPr"):
                            # 提取段落文本
                            texts = child.itertext() if hasattr(child, "itertext") else []
                            for t in texts:
                                if t.strip():
                                    prev_text = t.strip()
                        elif tag == "tbl":
                            prev_text = ""  # 表格后的内容
                    
                    if prev_text:
                        # 按标题定位
                        target_section = self._find_section_by_text(doc, prev_text)
            except Exception as exc:
                pass

        # 添加到目标章节
        target = target_section
        while target.children:
            target = target.children[-1]
        target.content.append(block)

    def _find_section_by_text(self, doc, text: str):
        """根据文本片段找到包含它的章节。"""
        if not text:
            return doc.sections[-1] if doc.sections else None

        best_section = None
        best_match_len = 0

        def _search(node, depth=0):
            nonlocal best_section, best_match_len
            node_text = getattr(node, "title", "") or ""
            if node_text and text in node_text and len(text) > best_match_len:
                best_section = node
                best_match_len = len(text)
            for block in getattr(node, "content", []):
                block_text = getattr(block, "text", "") or ""
                if block_text and text in block_text and len(text) > best_match_len:
                    best_section = node
                    best_match_len = len(text)
            for child in getattr(node, "children", []):
                _search(child, depth + 1)

        for section in doc.sections:
            _search(section)

        return best_section or (doc.sections[-1] if doc.sections else None)

    def _parse_docx_fallback(self, payload: bytes, doc: StructuredDocument):
        """DOCX 降级解析：使用 docx2python 提取纯文本。"""
        try:
            from docx2python import docx2python
            with docx2python(BytesIO(payload)) as result:
                text = (result.text or "").strip()
                if text:
                    section = Section(title="全文", level=1)
                    section.content.append(ContentBlock(ContentBlock.TYPE_PARAGRAPH, text))
                    doc.sections.append(section)
        except Exception as exc:
            logger.warning("[parser] docx2python 也失败: %s", exc)
            text = payload.decode("utf-8", errors="replace")
            section = Section(title="全文", level=1)
            section.content.append(ContentBlock(ContentBlock.TYPE_PARAGRAPH, text))
            doc.sections.append(section)

    # ========== PDF 结构化解析 ==========

    def _parse_pdf_structured(self, payload: bytes, doc: StructuredDocument):
        """解析 PDF：fitz 逐页判断类型，混合策略提取。"""
        try:
            pdf_doc = fitz.open(stream=payload, filetype="pdf")
        except Exception as exc:
            logger.error("[parser] fitz 打开 PDF 失败: %s", exc)
            text = payload.decode("utf-8", errors="replace")
            section = Section(title="全文", level=1)
            section.content.append(ContentBlock(ContentBlock.TYPE_PARAGRAPH, text))
            doc.sections.append(section)
            return

        total_pages = len(pdf_doc)
        text_pages = []       # list of (page_text, page_no)
        ocr_pages = []        # list of (image_bytes, page_no)

        # 第一步：逐页判断类型
        for page_num in range(total_pages):
            page = pdf_doc[page_num]
            page_text = page.get_text().strip()
            images = page.get_images()

            is_scan = (len(page_text) < 50 and len(images) > 0) or (len(page_text) < 20)
            is_mixed = len(page_text) < 200 and len(images) > 0

            # 表格检测（对所有类型的页面都尝试）
            page_tables = self._detect_tables_in_pdf_page(page)
            
            if is_scan and self.ocr_client:
                # 有OCR：渲染为图片后 OCR
                pix = page.get_pixmap(dpi=200)
                img_bytes = pix.tobytes("png")
                ocr_pages.append((img_bytes, page_num + 1))
                if page_text:
                    text_pages.append((page_text, page_num + 1))
                if page_tables:
                    text_pages.append(("", page_num + 1))
            elif is_scan:
                # 无OCR：使用已有文本，不足则标记
                text = page_text or f"【第{page_num + 1}页为扫描页，无可用文本】"
                text_pages.append((text, page_num + 1))
                if page_tables:
                    for t in page_tables:
                        text_pages.append((t, page_num + 1))
            elif is_mixed and self.ocr_client:
                # 混合页：文本 + OCR 补充
                pix = page.get_pixmap(dpi=200)
                img_bytes = pix.tobytes("png")
                ocr_pages.append((img_bytes, page_num + 1))
                if page_text:
                    text_pages.append((page_text, page_num + 1))
            else:
                # 纯文本页
                if page_tables:
                    # 有表格时：用表格文本替换纯文本
                    table_text_parts = []
                    for t in page_tables:
                        h = " | ".join(t.headers) if t.headers else ""
                        rows = [" | ".join(r) for r in t.rows]
                        table_text_parts.append(h + "\n" + "\n".join(rows))
                    combined_table_text = "\n".join(table_text_parts)
                    if page_text:
                        combined_table_text = page_text + "\n" + combined_table_text
                    text_pages.append((combined_table_text, page_num + 1))
                else:
                    text_pages.append((page_text, page_num + 1))

        pdf_doc.close()

        # 第二步：OCR 识别
        ocr_results = {}  # page_no -> list of (text, box)
        if ocr_pages and self.ocr_client:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            images = [img for img, _ in ocr_pages]
            results = loop.run_until_complete(self.ocr_client.recognize_images_batch(images))
            for (_, page_no), page_items in zip(ocr_pages, results):
                ocr_results[page_no] = [(item["text"], item.get("box")) for item in page_items]

        # 第三步：按页码合并，重建版面
        all_pages_content = {}  # page_no -> list[ContentBlock]
        for text, page_no in text_pages:
            blocks = self._parse_text_page(text)
            all_pages_content[page_no] = blocks
        for page_no, items in ocr_results.items():
            texts = [t for t, _ in items]
            combined = "\n".join(texts)
            if page_no in all_pages_content:
                existing = all_pages_content[page_no]
                existing.append(ContentBlock(ContentBlock.TYPE_PARAGRAPH, combined))
            else:
                all_pages_content[page_no] = [ContentBlock(ContentBlock.TYPE_PARAGRAPH, combined)]

        # 第四步：按页码排序，合并为章节
        sorted_pages = sorted(all_pages_content.items())
        full_text = []
        for page_no, blocks in sorted_pages:
            for b in blocks:
                full_text.append(b.text)

        combined = "\n".join(full_text)
        self._build_sections_from_text(combined, doc)


    def _detect_table_in_text(self, text_block: str) -> "Optional[ContentBlock]":
        """启发式检测文本中是否包含表格结构，尝试重建为 ContentBlock(type=table)。
        
        检测条件：
        1. 连续 >=3 行，每行有相同的列数（按 3+空格 / | / \t 分割）
        2. 首行可能为表头
        
        返回 ContentBlock(type=table) 或 None
        """
        if not text_block or not isinstance(text_block, str):
            return None
        
        lines = [l.strip() for l in text_block.split("\n") if l.strip()]
        if len(lines) < 3:
            return None
        
        # 尝试多种分隔符
        separators = [
            lambda x: [c.strip() for c in re.split(r"\s{3,}", x) if c.strip()],  # 3+空格
            lambda x: [c.strip() for c in x.split("\t") if c.strip()],           # tab
            lambda x: [c.strip() for c in x.split("|") if c.strip()],             # pipe
        ]
        
        for sep in separators:
            split_lines = [sep(line) for line in lines]
            col_counts = [len(sl) for sl in split_lines]
            
            # 检查是否有 >=3 行有相同的列数 >=2
            from collections import Counter
            count_counter = Counter(col_counts)
            most_common_count, occurrences = count_counter.most_common(1)[0]
            
            if occurrences >= 3 and most_common_count >= 2:
                # 判定为表格
                table_lines = [sl for sl in split_lines if len(sl) == most_common_count]
                if len(table_lines) < 3:
                    continue
                
                block = ContentBlock(ContentBlock.TYPE_TABLE)
                block.headers = table_lines[0]
                block.rows = table_lines[1:]
                return block
        
        return None

    def _detect_tables_in_pdf_page(self, page) -> "list[ContentBlock]":
        """用 fitz 内置表格检测提取 PDF 页面的表格。
        
        优先用 find_tables()（检测网格线），
        失败则对页面文本用启发式检测。
        """
        tables = []
        
        # 方法1：fitz find_tables() - 检测有网格线的表格
        try:
            found = page.find_tables()
            if found and found.tables:
                for ft in found.tables:
                    data = ft.extract()
                    if not data or len(data) < 2:
                        continue
                    block = ContentBlock(ContentBlock.TYPE_TABLE)
                    block.headers = [str(c).strip() for c in data[0]]
                    block.rows = [[str(c).strip() for c in row] for row in data[1:]]
                    tables.append(block)
                if tables:
                    return tables
        except Exception as exc:
            logger.debug("[parser] fitz 表格检测异常: %s", exc)
        
        # 方法2：启发式 - 从页面文本中检测
        try:
            page_text = page.get_text().strip()
            if page_text:
                block = self._detect_table_in_text(page_text)
                if block:
                    tables.append(block)
        except Exception as exc:
            logger.debug("[parser] 启发式表格检测异常: %s", exc)
        
        return tables


    def _parse_text_page(self, text: str) -> list:
        """从纯文本中提取内容块。"""
        blocks = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            block_type = ContentBlock.TYPE_PARAGRAPH
            # 尝试识别标题
            heading_match = re.match(r'^(第[一二三四五六七八九十]+[章节篇]|[\d]+[.、]\s*|（[\d一二三四五六七八九十]+）)', line)
            if heading_match:
                block_type = ContentBlock.TYPE_HEADING
            blocks.append(ContentBlock(block_type, line))
        return blocks

    def _build_sections_from_text(self, text: str, doc: StructuredDocument):
        """从合并文本中重建章节结构。"""
        lines = text.split("\n")
        stack = [Section(title="__root__", level=0)]

        for line in lines:
            line = line.strip()
            if not line:
                continue

            heading_level = self._detect_heading_level(line)
            if heading_level > 0:
                section = Section(title=line, level=heading_level)
                while stack and stack[-1].level >= heading_level:
                    stack.pop()
                if stack:
                    stack[-1].children.append(section)
                else:
                    doc.sections.append(section)
                stack.append(section)
            else:
                if stack:
                    stack[-1].content.append(ContentBlock(ContentBlock.TYPE_PARAGRAPH, line))

        # 清理空根节点
        doc.sections = [s for s in doc.sections if s.title != "__root__"]
        # 如果没有章节结构，创建一个
        if not doc.sections and stack and stack[0].children:
            doc.sections = stack[0].children

    def _detect_heading_level(self, text: str) -> int:
        """检测文本是否是标题，返回标题层级（0=不是标题）。"""
        # 一级标题：第X章/X篇
        if re.match(r'^第[一二三四五六七八九十零〇百千万亿]+[章节篇部]', text):
            return 1
        # 二级标题：一、 二、 或 1. 2. 或 1.1
        if re.match(r'^[一二三四五六七八九十零〇]+[、，,．.]', text):
            return 2
        if re.match(r'^\d+[、，,．.]', text):
            return 2
        if re.match(r'^\d+\.\d+\s', text):
            return 2
        # 三级标题：（一）（二）或 1.1.1
        if re.match(r'^（[一二三四五六七八九十零〇]+）', text):
            return 3
        if re.match(r'^\d+\.\d+\.\d+\s', text):
            return 3
        return 0


    def _detect_text_tables_in_text(self, text):
        """从纯文本中检测表格，返回 ContentBlock(type=table) 列表。
        
        用于 PDF 文本页和扫描页 OCR 结果中的非原生表格文本。
        """
        tables = []
        lines = text.split("\n")
        current_table = []
        in_table = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if in_table and len(current_table) >= 3:
                    table = self._parse_text_table(current_table)
                    if table:
                        tables.append(table)
                current_table = []
                in_table = False
                continue

            is_table_line = False
            if "|" in stripped or "\u2502" in stripped:
                is_table_line = True
            elif "\t" in stripped:
                is_table_line = True
            elif stripped and stripped[0] in "\u250c\u2510\u2514\u2518\u251c\u2524\u252c\u2534\u253c\u2550\u2500\u2502\u2503\u2554\u2557\u255a\u255d\u2560\u2563\u2566\u2569\u256c":
                is_table_line = True

            if is_table_line:
                if not all(c in "\u250c\u2510\u2514\u2518\u251c\u2524\u252c\u2534\u253c\u2550\u2500\u2502\u2503\u2554\u2557\u255a\u255d\u2560\u2563\u2566\u2569\u256c " for c in stripped):
                    current_table.append(stripped)
                    in_table = True
            else:
                if in_table and len(current_table) >= 3:
                    table = self._parse_text_table(current_table)
                    if table:
                        tables.append(table)
                current_table = []
                in_table = False

        if in_table and len(current_table) >= 3:
            table = self._parse_text_table(current_table)
            if table:
                tables.append(table)

        return tables

    def _parse_text_table(self, lines):
        """从文本表格行解析为 ContentBlock(type=table)。"""
        if not lines:
            return None

        pipe_lines = [l for l in lines if "|" in l or "\u2502" in l]
        if pipe_lines:
            import re
            data_lines = [l for l in pipe_lines if not re.match(r"^[\s\|\u2502\-\u2501\u2550\u2500\+]+$", l)]
            if len(data_lines) < 2:
                return None
            parsed_rows = []
            for line in data_lines:
                cells = [c.strip() for c in re.split(r"[\||\u2502]", line) if c.strip()]
                if cells:
                    parsed_rows.append(cells)
            if len(parsed_rows) >= 2:
                block = ContentBlock(ContentBlock.TYPE_TABLE)
                block.headers = parsed_rows[0]
                block.rows = parsed_rows[1:]
                return block

        tab_lines = [l for l in lines if "\t" in l]
        if tab_lines and len(tab_lines) >= 2:
            parsed_rows = []
            for line in tab_lines:
                cells = [c.strip() for c in line.split("\t") if c.strip()]
                if cells:
                    parsed_rows.append(cells)
            if len(parsed_rows) >= 2:
                block = ContentBlock(ContentBlock.TYPE_TABLE)
                block.headers = parsed_rows[0]
                block.rows = parsed_rows[1:]
                return block

        return None


    # ========== 语义切片 ==========

    CHUNK_MIN_CHARS = 200
    CHUNK_MAX_CHARS = 1500

    def semantic_chunk(self, doc: StructuredDocument) -> list[dict]:
        """按标题/表格自然边界切片。

        Args:
            doc: 结构化文档

        Returns:
            list[dict]: 每个元素包含 text, section_path, content_type, page_range, metadata
        """
        chunks = []
        self._chunk_sections(doc.sections, [], chunks)
        return chunks

    def _chunk_sections(self, sections: list, parent_path: list, chunks: list):
        """递归遍历章节，生成切片。"""
        for section in sections:
            path = parent_path + [section.title] if section.title else parent_path
            section_path = " > ".join(path) if path else ""

            # 如果该章节有独立内容，作为一个 chunk
            if section.content:
                texts = []
                content_types = set()
                for block in section.content:
                    if block.type == ContentBlock.TYPE_TABLE:
                        texts.append(self._table_to_text(block))
                    elif block.text:
                        texts.append(block.text)
                    content_types.add(block.type)

                combined = "\n".join(texts)
                if combined and len(combined) >= self.CHUNK_MIN_CHARS:
                    chunks.append({
                        "text": combined,
                        "section_path": section_path,
                        "content_type": "mixed" if len(content_types) > 1 else (content_types.pop() if content_types else "paragraph"),
                        "page_range": section.page_range,
                        "metadata": {"section_level": section.level},
                    })
                elif combined and len(combined) < self.CHUNK_MIN_CHARS and path:
                    # 短内容合并到前一个 chunk
                    if chunks and chunks[-1].get("section_path", "").startswith(section_path.rsplit(" > ", 1)[0] if " > " in section_path else ""):
                        chunks[-1]["text"] += "\n" + combined
                    else:
                        chunks.append({
                            "text": combined,
                            "section_path": section_path,
                            "content_type": "paragraph",
                            "page_range": section.page_range,
                            "metadata": {"section_level": section.level},
                        })

            # 递归子章节
            if section.children:
                self._chunk_sections(section.children, path, chunks)

    def _table_to_text(self, block: ContentBlock) -> str:
        """将表格块转为结构化文本。"""
        lines = []
        if block.headers:
            lines.append(" | ".join(block.headers))
            lines.append(" | ".join(["---"] * len(block.headers)))
        for row in block.rows:
            lines.append(" | ".join(row))
        return "\n".join(lines)

    # ========== 旧版兼容 ==========

    def parse_bytes(self, filename: str, payload: bytes) -> str:
        """兼容旧接口：返回纯文本。"""
        doc = self.parse_structured(filename, payload)
        texts = []
        for chunk in self.semantic_chunk(doc):
            texts.append(chunk["text"])
        return "\n".join(texts)

    def split_text_chunks(self, text: str, max_length=1200, overlap=120):
        """兼容旧接口。"""
        return self.semantic_chunk(self._text_to_doc(text))

    def _text_to_doc(self, text: str) -> StructuredDocument:
        doc = StructuredDocument()
        self._build_sections_from_text(text, doc)
        return doc

    # ========== 辅助方法 ==========

    def _parse_doc_structured(self, payload: bytes, doc: StructuredDocument):
        """解析旧版 DOC 格式。"""
        import subprocess
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as tmp:
            tmp.write(payload)
            tmp_path = tmp.name

        try:
            result = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", tmp_path],
                capture_output=True, text=True, check=False, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                text = result.stdout
                # 尝试检测表格
                table_block = self._detect_table_in_text(text)
                if table_block:
                    # 有表格：把表格信息嵌入文本后再建章节
                    table_text = "表格内容：\n"
                    if table_block.headers:
                        table_text += " | ".join(table_block.headers) + "\n"
                    for row in table_block.rows:
                        table_text += " | ".join(row) + "\n"
                    text = text + "\n" + table_text
                self._build_sections_from_text(text, doc)
            else:
                text = payload.decode("utf-8", errors="replace")
                section = Section(title="全文", level=1)
                section.content.append(ContentBlock(ContentBlock.TYPE_PARAGRAPH, text))
                doc.sections.append(section)
        except Exception as exc:
            text = payload.decode("utf-8", errors="replace")
            section = Section(title="全文", level=1)
            section.content.append(ContentBlock(ContentBlock.TYPE_PARAGRAPH, text))
            doc.sections.append(section)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _parse_spreadsheet_structured(self, payload: bytes, doc: StructuredDocument, ext: str):
        """解析电子表格。"""
        namespace = {
            "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        }
        try:
            with ZipFile(BytesIO(payload)) as z:
                # 读取共享字符串
                shared_strings = []
                if "xl/sharedStrings.xml" in z.namelist():
                    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
                    for si in root.findall(".//main:si", namespace):
                        parts = [t.text or "" for t in si.findall(".//main:t", namespace)]
                        shared_strings.append("".join(parts))

                # 读取第一个 sheet
                if "xl/worksheets/sheet1.xml" in z.namelist():
                    sheet_root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
                    rows = sheet_root.findall(".//main:row", namespace)
                    for row in rows:
                        cells = []
                        for cell in row.findall("main:c", namespace):
                            cell_ref = cell.get("r", "")
                            cell_type = cell.get("t", "")
                            value_elem = cell.find("main:v", namespace)
                            raw_value = value_elem.text if value_elem is not None else ""
                            if cell_type == "s" and raw_value.isdigit() and int(raw_value) < len(shared_strings):
                                cells.append(shared_strings[int(raw_value)])
                            else:
                                cells.append(raw_value)
                        if cells:
                            section = Section(title=f"行 {row.get('r', '')}", level=1)
                            block = ContentBlock(ContentBlock.TYPE_PARAGRAPH, " | ".join(cells))
                            section.content.append(block)
                            doc.sections.append(section)
        except Exception as exc:
            logger.warning("[parser] 电子表格解析失败: %s", exc)

    def _normalize_text(self, text: str) -> str:
        """清理文本。"""
        if not text:
            return ""
        lines = []
        for raw_line in str(text).replace("\x00", "").splitlines():
            line = " ".join(raw_line.split())
            if line:
                lines.append(line)
        return "\n".join(lines).strip()
