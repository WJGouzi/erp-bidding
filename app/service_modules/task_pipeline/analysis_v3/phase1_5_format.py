"""Phase 1.5: 格式要求提取 — 从文档中提取响应文件的格式要求。

定位：
  Phase 1 (元数据) → Phase 1.5 (格式要求) → Phase 2 (资格) → Phase 3 (评分)

提取内容：
  - 格式要求章节（如"第三章 比选申请文件格式"）
  - 必选章节清单（响应函、报价一览表、授权书等）
  - 模板表格（固定的表格结构，如报价表模板）
  - 固定文本（必须出现的文字，如响应函声明文字）

使用方式：
    from .phase1_5_format import extract_format_requirements
    fmt_req = extract_format_requirements(doc.sections)
"""

import logging
import re
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  格式章节标题关键词（按优先级排序）
# ═══════════════════════════════════════════════════════════════

FORMAT_CHAPTER_KEYWORDS = [
    "比选申请文件格式",
    "投标文件格式",
    "响应文件格式",
    "申请文件格式",
    "比选申请文件",
    "投标文件",
    "响应文件格式要求",
    "文件格式",
]

# 格式章节中常见的必选文件标题（用于识别子章节）
REQUIRED_SECTION_PATTERNS = [
    # (关键词, 文件类型标识)
    (r"响应函|投标函|报价函", "response_letter"),
    (r"报价一览表|报价表|报价单|分项报价", "price_list"),
    (r"法定代表人授权|法人授权|授权委托", "authorization"),
    (r"资格证明|资质证明|资格文件", "qualification"),
    (r"实质性要求|★|实质性要求响应", "compliance"),
    (r"技术参数|技术响应|技术规格|技术要求响应", "technical"),
    (r"商务要求响应|商务条款", "business"),
    (r"评分标准|评分响应|综合评分", "scoring_response"),
    (r"售后服务|培训方案|服务方案", "service"),
    (r"项目业绩|类似项目|业绩证明", "performance"),
    (r"其他材料|其他文件|补充材料", "other"),
]


def _find_format_chapter(sections) -> Optional[object]:
    """在文档章节树中定位格式要求章节。

    策略：
      1. 按 FORMAT_CHAPTER_KEYWORDS 标题匹配
      2. 匹配后检查子章节数量（至少 3 个才视为有效格式章节）
    """
    best = None
    best_kw = ""

    def _search(section_list):
        nonlocal best, best_kw
        for section in section_list:
            title = getattr(section, "title", "") or ""
            for kw in FORMAT_CHAPTER_KEYWORDS:
                if kw in title:
                    children = getattr(section, "children", [])
                    if len(children) >= 2 or kw == best_kw:
                        best = section
                        best_kw = kw
                    break
            children = getattr(section, "children", [])
            if children:
                _search(children)

    _search(sections if isinstance(sections, list) else [])
    return best


def _extract_required_sections(section) -> List[Dict]:
    """从格式章节中提取必选文件清单。

    Returns:
        List[Dict]: [{"title": ..., "required": True, "has_template": False, "order": 1}]
    """
    required = []
    children = getattr(section, "children", [])

    for idx, child in enumerate(children):
        title = getattr(child, "title", "") or ""
        if not title:
            continue

        # 检查是否有模板表格
        has_template = False
        for block in getattr(child, "content", []):
            if getattr(block, "type", "") == "table":
                has_template = True
                break
        # 递归检查子章节
        if not has_template:
            for sub in getattr(child, "children", []):
                for block in getattr(sub, "content", []):
                    if getattr(block, "type", "") == "table":
                        has_template = True
                        break

        # 识别文件类型
        file_type = "unknown"
        for pattern, ftype in REQUIRED_SECTION_PATTERNS:
            if re.search(pattern, title):
                file_type = ftype
                break

        required.append({
            "title": title,
            "order": idx + 1,
            "required": True,
            "has_template": has_template,
            "template_tables": _extract_template_tables(child),
            "file_type": file_type,
        })

    return required


def _extract_template_tables(section) -> List[Dict]:
    """从章节中提取模板表格。

    搜索当前章节及其子章节的内容块，收集 table 类型块。
    """
    tables = []

    def _collect(node):
        for block in getattr(node, "content", []):
            if getattr(block, "type", "") == "table":
                headers = getattr(block, "headers", []) or []
                rows = getattr(block, "rows", []) or []
                if headers or rows:
                    tables.append({
                        "headers": headers[:10],
                        "rows": rows[:20],
                    })
        for child in getattr(node, "children", []):
            _collect(child)

    _collect(section)
    return tables


def _extract_fixed_texts(section) -> List[Dict]:
    """从章节中提取固定文本要求。

    一些格式章节会指定必须出现在响应文件中的文字。
    如：响应函声明文字、报价有效期承诺等。
    """
    fixed_texts = []
    section_title = getattr(section, "title", "") or ""

    for block in getattr(section, "content", []):
        text = getattr(block, "text", "") or ""
        text = text.strip()
        # 识别固定文本：较长的段落（>30字），不含表格
        if len(text) >= 30 and getattr(block, "type", "") != "table":
            fixed_texts.append({
                "section_ref": section_title,
                "text": text[:500],
                "position": "start",
            })

    return fixed_texts


def extract_format_requirements(sections) -> Optional[Dict]:
    """从文档章节树中提取格式要求。

    Args:
        sections: 文档章节树（list of Section）

    Returns:
        dict or None: {
            "chapter_title": "第三章 比选申请文件格式",
            "required_sections": [...],
            "template_tables": [...],
            "fixed_texts": [...],
            "confidence": 0.85,
        }
    """
    chapter = _find_format_chapter(sections)
    if not chapter:
        logger.info("[phase1.5] 未找到格式要求章节")
        return None

    chapter_title = getattr(chapter, "title", "") or ""

    required_sections = _extract_required_sections(chapter)
    if not required_sections:
        logger.info("[phase1.5] 格式章节 '%s' 无子章节", chapter_title)
        return None

    # 收集所有模板表格
    all_tables = []
    for rs in required_sections:
        all_tables.extend(rs.get("template_tables", []))

    # 收集固定文本
    fixed_texts = _extract_fixed_texts(chapter)

    # 置信度计算
    confidence = 0.5
    if len(required_sections) >= 3:
        confidence = 0.7 + min(len(required_sections) / 20, 0.2)
    if any(rs["has_template"] for rs in required_sections):
        confidence = min(confidence + 0.1, 0.95)

    logger.info(
        "[phase1.5] 格式要求提取完成: chapter='%s', sections=%d, tables=%d, fixed=%d, confidence=%.2f",
        chapter_title, len(required_sections), len(all_tables), len(fixed_texts), confidence,
    )

    return {
        "chapter_title": chapter_title,
        "required_sections": required_sections,
        "template_tables": all_tables,
        "fixed_texts": fixed_texts,
        "confidence": confidence,
    }
