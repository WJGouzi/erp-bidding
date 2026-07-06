# -*- coding: utf-8 -*-
"""
模板绑定器 — 检测章节是否有原文模板，并实现两阶段内容生成。

两阶段：
  阶段 A - template_binding：检查 format_requirements 中是否有本章节的模板
  阶段 B - content_filling：复制模板原文，仅填充空缺占位符

设计文档：openspec/changes/generation-fidelity-fix/design.md
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Placeholder:
    """模板中的占位符信息。"""
    marker: str            # 原文占位符，如 "XXX"
    field_name: str        # 标准字段名，如 "company_name"
    fill_type: str         # "subject" | "knowledge" | "product" | "unknown"
    fallback: str = ""     # 找不到时的兜底


@dataclass
class ContentBlock:
    """内容块，可以是段落或表格。"""
    type: str                       # "paragraph" | "table"
    # paragraph 字段：
    text: str = ""
    placeholders: list[Placeholder] = field(default_factory=list)
    # table 字段：
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    merge_cells: list[dict] = field(default_factory=list)
    column_widths: list = field(default_factory=list)
    per_cell: Optional[dict] = None

    def to_dict(self) -> dict:
        d = {"type": self.type}
        if self.type == "paragraph":
            d["text"] = self.text
            d["placeholders"] = [
                {"marker": p.marker, "field_name": p.field_name,
                 "fill_type": p.fill_type, "fallback": p.fallback}
                for p in self.placeholders
            ]
        elif self.type == "table":
            d["headers"] = self.headers
            d["rows"] = self.rows
            d["merge_cells"] = self.merge_cells
            d["column_widths"] = self.column_widths
            if self.per_cell:
                d["per_cell"] = self.per_cell
        return d

    @classmethod
    def paragraph(cls, text: str, placeholders: list = None) -> "ContentBlock":
        return cls(type="paragraph", text=text, placeholders=placeholders or [])

    @classmethod
    def table(cls, headers: list, rows: list, merge_cells: list = None, column_widths: list = None, per_cell: dict = None) -> "ContentBlock":
        return cls(type="table", headers=headers, rows=rows, merge_cells=merge_cells or [], column_widths=column_widths or [], per_cell=per_cell)


@dataclass
class TemplateBinding:
    """模板绑定结果。"""
    chapter_title: str
    has_template: bool
    template_blocks: list[ContentBlock] = field(default_factory=list)
    placeholders: list[Placeholder] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
# 占位符检测常量
# ═══════════════════════════════════════════════════════════════════

# 标准占位符模式（这些应当被当作"空缺"处理）
PLACEHOLDER_PATTERNS = [
    re.compile(r'^XXX$'),                          # 标准 XXX
    re.compile(r'^_{3,}$'),                        # ___
    re.compile(r'^\.{3,}$'),                       # ...
    re.compile(r'^—{1,3}$'),                       # —
    re.compile(r'^[-–]{1,3}$'),                    # - / –
    re.compile(r'^20\d{2}\s*年\s*月\s*日$'),      # 2025年 月 日
    re.compile(r'^\d{4}年\s*月\s*日$'),            # 年 月 日
    re.compile(r'^年\s*月\s*日$'),                  # 年 月 日
    re.compile(r'^XXX有限公司$'),                   # XXX有限公司
    re.compile(r'^XXX公司$'),                       # XXX公司
]

# 字段名映射规则: marker关键词 → field_name
MARKER_TO_FIELD = {
    "单位名称": "company_name",
    "供应商名称": "company_name",
    "投标人": "company_name",
    "法定代表人": "legal_person",
    "法人": "legal_person",
    "被授权人": "authorized_person",
    "项目名称": "project_name",
    "标的名称": "project_name",
    "项目编号": "project_no",
    "采购编号": "project_no",
    "采购项目名称": "project_name",
    "采购文件编号": "project_no",
    "投标日期": "bid_date",
    "日期": "bid_date",
}

# 字段名 → 数据源类型
FIELD_TO_SOURCE = {
    "company_name": "subject",
    "legal_person": "subject",
    "authorized_person": "subject",
    "project_name": "bidder_notice",
    "project_no": "bidder_notice",
    "bid_date": "system",
}


# ═══════════════════════════════════════════════════════════════════
# 核心函数
# ═══════════════════════════════════════════════════════════════════

def classify_content_state(text: str) -> str:
    """判断原文中一个字段/区域的状态。

    Returns:
        "EMPTY" | "PLACEHOLDER" | "FILLED"
    """
    if not text or not text.strip():
        return "EMPTY"
    stripped = text.strip()
    for pattern in PLACEHOLDER_PATTERNS:
        if pattern.match(stripped):
            return "PLACEHOLDER"
    return "FILLED"


def extract_placeholders(text: str) -> list[Placeholder]:
    """从模板文本中提取占位符。

    识别模式：
    - XXX（上下文关键词） → 根据关键词推断字段名
    - 裸 XXX → 标记为 unknown
    """
    placeholders = []
    # 匹配 XXX（可能带括号上下文）
    pattern = re.compile(r'XXX\s*（([^）]+)）|XXX')
    for match in pattern.finditer(text):
        marker = match.group(0)
        context = match.group(1)  # 括号内的内容
        # 推断 field_name
        field_name = "unknown"
        fill_type = "unknown"
        if context:
            for keyword, name in MARKER_TO_FIELD.items():
                if keyword in context:
                    field_name = name
                    fill_type = FIELD_TO_SOURCE.get(name, "unknown")
                    break
        else:
            # 裸 XXX，看前文关键词
            prefix = text[max(0, match.start() - 20):match.start()]
            for keyword, name in MARKER_TO_FIELD.items():
                if keyword in prefix:
                    field_name = name
                    fill_type = FIELD_TO_SOURCE.get(name, "unknown")
                    break
        placeholders.append(Placeholder(
            marker=marker,
            field_name=field_name,
            fill_type=fill_type,
        ))
    return placeholders


def bind_template(chapter_title: str, format_requirements: dict) -> TemplateBinding:
    """检测章节是否有原文模板。

    Args:
        chapter_title: 章节标题（如"法定代表人授权书"）
        format_requirements: analysis_data 中的格式要求

    Returns:
        TemplateBinding: has_template=True 表示有模板
    """
    if not format_requirements or not isinstance(format_requirements, dict):
        return TemplateBinding(chapter_title=chapter_title, has_template=False)

    required_sections = format_requirements.get("required_sections", [])
    if not required_sections:
        return TemplateBinding(chapter_title=chapter_title, has_template=False)

    # 对标题做去前缀匹配
    clean_title = _clean_title(chapter_title)
    for section in required_sections:
        sec_title = section.get("title", "").strip()
        sec_clean = _clean_title(sec_title)
        if _fuzzy_title_match(clean_title, sec_clean):
            # 找到了匹配章节，检查是否有模板内容
            # phase1_5_format.py 使用 template_content 字段，type 为 "text" 或 "table"
            template_content = section.get("template_content", [])
            if not template_content:
                # 降级兼容：尝试 content_blocks（旧字段名）
                template_content = section.get("content_blocks", [])
            if not template_content:
                return TemplateBinding(chapter_title=chapter_title, has_template=False)

            blocks = []
            all_placeholders = []
            for block_data in template_content:
                if not isinstance(block_data, dict):
                    continue
                block_type = block_data.get("type", "text")
                # 兼容 phase1_5_format 使用 "text" 而 design 用 "paragraph"
                if block_type in ("text", "paragraph"):
                    text = block_data.get("text", "")
                    phs = extract_placeholders(text)
                    blocks.append(ContentBlock.paragraph(text, phs))
                    all_placeholders.extend(phs)
                elif block_type == "table":
                    headers = block_data.get("headers", [])
                    rows = block_data.get("rows", [])
                    merge_cells = block_data.get("merge_cells", [])
                    # 提取表格中的占位符
                    for row in rows:
                        for cell in row:
                            all_placeholders.extend(extract_placeholders(cell))
                    per_cell = block_data.get("per_cell")
                    blocks.append(ContentBlock.table(headers, rows, merge_cells, block_data.get("column_widths", []), per_cell=per_cell))

            return TemplateBinding(
                chapter_title=chapter_title,
                has_template=True,
                template_blocks=blocks,
                placeholders=all_placeholders,
            )

    return TemplateBinding(chapter_title=chapter_title, has_template=False)


def fill_content(binding: TemplateBinding,
                 subject_context: dict = None,
                 knowledge_context: dict = None,
                 product_context: dict = None) -> list[ContentBlock]:
    """复制模板并填充占位符（段落+表格）。

    规则：
    - 有模板 → 复制模板原文，仅替换占位符
    - 无模板 → 返回空列表

    Args:
        binding: 模板绑定结果
        subject_context: 主体信息
        knowledge_context: 知识库信息
        product_context: 产品库信息

    Returns:
        list[ContentBlock]: 填充后的内容块
    """
    if not binding.has_template:
        return []

    all_context = {
        "subject": subject_context or {},
        "knowledge": knowledge_context or {},
        "product": product_context or {},
    }

    filled_blocks = []
    for block in binding.template_blocks:
        if block.type == "paragraph":
            filled_blocks.append(_fill_paragraph_block(block, all_context))
        elif block.type == "table":
            filled_blocks.append(_fill_table_block(block, all_context))

    return filled_blocks


# ═══════════════════════════════════════════════════════════════════
# 内部辅助函数
# ═══════════════════════════════════════════════════════════════════

def _clean_title(title: str) -> str:
    """去除标题的编号前缀和空白。"""
    t = title.strip()
    # 去除 "一、", "1.", "1、" 等前缀
    t = re.sub(r'^[一二三四五六七八九十]+[、．.]?\s*', '', t)
    t = re.sub(r'^\d+[、．.]\s*', '', t)
    return t.strip()




def _fuzzy_title_match(clean_title: str, sec_clean: str) -> bool:
    """多策略模糊标题匹配，提高模板绑定命中率。
    
    策略:
      1. 直接包含匹配（原逻辑）
      2. 去除特殊字符后包含匹配
      3. 去除共同前缀后关键部分匹配
    """
    if not clean_title or not sec_clean:
        return False
    
    # 策略1：直接包含（原逻辑）
    if clean_title in sec_clean or sec_clean in clean_title:
        return True
    
    # 策略2：去除空格和特殊字符后匹配
    _strip_re = lambda s: re.sub(r'[\s★◆●■▲➢※▪▶•·❤：:（）()（）\-—]', '', s)
    s1 = _strip_re(clean_title)
    s2 = _strip_re(sec_clean)
    if s1 and s2 and (s1 in s2 or s2 in s1):
        return True
    
    # 策略3：提取关键部分（去除常见前缀和编号）
    _key_re = lambda s: re.sub(r'^[一二三四五六七八九十零〇]+[、，,．.]*|[第.章节篇]+[一二三四五六七八九十零〇]+[章节篇]?|响应文件|格式|要求|模板', '', s).strip()
    k1 = _key_re(clean_title)
    k2 = _key_re(sec_clean)
    if k1 and k2 and len(k1) >= 2 and len(k2) >= 2 and (k1 in k2 or k2 in k1):
        return True
    
    return False

def _fill_paragraph_block(block: ContentBlock, all_context: dict) -> ContentBlock:
    """填充段落块中的占位符。"""
    text = block.text
    for ph in block.placeholders:
        value = _resolve_from_context(ph, all_context)
        if value:
            text = text.replace(ph.marker, value, 1)
    return ContentBlock.paragraph(text)


def _fill_table_block(block: ContentBlock, all_context: dict) -> ContentBlock:
    """填充表格块中的空缺单元格。"""
    new_rows = []
    for row in block.rows:
        new_row = []
        for cell in row:
            state = classify_content_state(cell)
            if state in ("EMPTY", "PLACEHOLDER"):
                # 尝试填充
                phs = extract_placeholders(cell)
                filled = cell
                for ph in phs:
                    value = _resolve_from_context(ph, all_context)
                    if value:
                        filled = filled.replace(ph.marker, value, 1)
                new_row.append(filled)
            else:
                # FILLED — 保留原文
                new_row.append(cell)
        new_rows.append(new_row)
    return ContentBlock.table(
        headers=list(block.headers),
        rows=new_rows,
        merge_cells=list(block.merge_cells),
        column_widths=list(block.column_widths),
        per_cell=block.per_cell,
    )


def _resolve_from_context(placeholder: Placeholder, all_context: dict) -> str:
    """从上下文中解析占位符的值。"""
    if placeholder.fill_type == "subject":
        ctx = all_context.get("subject", {})
        return ctx.get(placeholder.field_name, "")
    elif placeholder.fill_type == "bidder_notice":
        ctx = all_context.get("subject", {})
        return ctx.get(placeholder.field_name, "")
    elif placeholder.fill_type == "system":
        if placeholder.field_name == "bid_date":
            from datetime import datetime
            return datetime.now().strftime("%Y年%m月%d日")
    return ""
