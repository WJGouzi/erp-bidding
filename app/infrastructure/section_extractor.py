"""章节提取器：基于文档章节树结构的商务/技术字段提取。

替代 regex 全文搜索，通过章节导航定位子章节并读取内容。
与 phase2_extractor.py 的设计思路一致。

使用方式：
    from app.infrastructure.section_extractor import extract_business_from_sections
    extra = extract_business_from_sections(doc.sections)
"""

import logging

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  章节标题 → extra 字段映射表
#  按优先级排序，高优先级优先匹配
# ═══════════════════════════════════════════════════════════════

SECTION_TO_EXTRA = [
    # (标题关键词列表, extra字段名, 优先级)
    (["付款", "支付", "结算"], "payment_terms", 1),
    (["交货时间", "供货期", "合同履行期限", "履约时间", "交货期"], "service_period", 1),
    (["服务期限", "服务期", "合同期限", "服务时间"], "service_period", 2),
    (["交货地点", "交付地点", "供货地点", "服务地点", "履约地点", "配送地点"], "delivery_location", 1),
    (["售后", "维修", "质保", "保修", "服务响应"], "after_sale_service", 1),
    (["质量", "验收标准", "验收方式", "质量要求"], "acceptance_standard", 1),
    (["包装", "运输", "配送"], "packaging_transport", 1),
    (["保险"], "insurance", 1),
    (["报价", "报价方式", "价格", "费用", "定价"], "pricing_rule", 1),
    (["特别说明", "其他要求", "其他", "声明"], "special_declaration", 1),
    (["代理服务费", "代理费", "服务费", "招标代理"], "agency_fee", 1),
    (["交货", "交付", "供货", "配送"], "delivery_terms", 2),
]


def find_section_by_title(sections, keyword):
    """递归在章节树中查找标题含关键词的章节。

    策略：
      1. 优先匹配：标题以关键词结尾或关键词作为标题主要部分
      2. 其次匹配：标题含关键词（且不是父章节的复合标题）
      3. 递归子章节搜索，子章节匹配优先于父章节

    Args:
        sections: list[Section] 或 list 结构（有 title/children 属性即可）
        keyword: 标题关键词（如 "商务要求"）

    Returns:
        Section or None
    """
    best = None
    best_score = -1

    def _search(section_list, depth=0):
        nonlocal best, best_score
        for section in section_list:
            title = getattr(section, "title", "") or ""
            if keyword in title:
                # 评分：标题长度越短（关键词占比越高），得分越高
                score = len(keyword) / max(len(title), 1) * 10
                # 以关键词结尾加分
                if title.strip().endswith(keyword):
                    score += 5
                # 子章节加分（更精确）
                score += depth * 2
                if score > best_score:
                    best_score = score
                    best = section
            children = getattr(section, "children", [])
            if children:
                _search(children, depth + 1)

    _search(sections)
    return best


def section_content_to_text(section):
    """提取章节的内容为文本（段落+表格）。

    Args:
        section: Section 对象

    Returns:
        str: 章节内容文本
    """
    parts = []
    for block in getattr(section, "content", []):
        if getattr(block, "type", "") in ("paragraph", "heading", "list"):
            text = getattr(block, "text", "") or ""
            if text:
                parts.append(text)
        elif getattr(block, "type", "") == "table":
            headers = getattr(block, "headers", []) or []
            rows = getattr(block, "rows", []) or []
            table_lines = []
            if headers:
                table_lines.append(" | ".join(headers))
            for row in rows:
                table_lines.append(" | ".join(row))
            if table_lines:
                parts.append(" | ".join(table_lines))
    # 包含子章节内容
    for child in getattr(section, "children", []):
        child_text = section_content_to_text(child)
        if child_text:
            parts.append(child_text)
    return "\n".join(parts)



def _fallback_search_business_fields(sections):
    """回退方案：直接在章节树中搜索商务相关的子章节。
    
    当找不到明确的"商务要求"章节时：
      1. 先找含"商务"关键词的父章节（如"第五章 采购项目...及商务要求"）
      2. 在父章节的子章节中按字段关键词匹配
      3. 仍找不到时，在全部子章节中搜索
    """
    result = {}

    # 策略1：找含"商务"的章节，在其子章节中搜索
    biz_parent = find_section_by_title(sections, "商务")
    if biz_parent:
        children = getattr(biz_parent, "children", [])
        for child in children:
            child_title = getattr(child, "title", "") or ""
            for keywords, field, priority in SECTION_TO_EXTRA:
                if field in result:
                    continue
                if any(kw in child_title for kw in keywords):
                    content_text = _direct_content_text(child)
                    if content_text.strip() and len(content_text.strip()) > 5:
                        result[field] = content_text.strip()[:500]
                        break
        if result:
            return result

    # 策略2：在全部章节的直接子章节中搜索
    def _search_direct_children(section_list):
        for section in section_list:
            children = getattr(section, "children", [])
            for child in children:
                child_title = getattr(child, "title", "") or ""
                for keywords, field, priority in SECTION_TO_EXTRA:
                    if field in result:
                        continue
                    if any(kw in child_title for kw in keywords):
                        content_text = _direct_content_text(child)
                        if content_text.strip() and len(content_text.strip()) > 5:
                            result[field] = content_text.strip()[:500]
                            break
            # 递归更深层
            if children:
                _search_direct_children(children)
    
    _search_direct_children(sections)
    
    if not result:
        logger.info("[section_extractor] 回退搜索也未找到商务字段")
    
    return result


def _direct_content_text(section):
    """提取章节的直接内容（段落+表格），不递归子章节。"""
    parts = []
    for block in getattr(section, "content", []):
        if getattr(block, "type", "") in ("paragraph", "heading", "list"):
            text = getattr(block, "text", "") or ""
            if text:
                parts.append(text)
        elif getattr(block, "type", "") == "table":
            headers = getattr(block, "headers", []) or []
            rows = getattr(block, "rows", []) or []
            table_lines = []
            if headers:
                table_lines.append(" | ".join(headers))
            for row in rows:
                table_lines.append(" | ".join(row))
            if table_lines:
                parts.append(" | ".join(table_lines))
    return "\n".join(parts)


def extract_business_from_sections(sections):
    """从文档章节树中提取商务要求字段。

    策略：
      1. 查找标题含"商务要求"的章节
      2. 遍历其子章节，按标题关键词归类到 extra 字段
      3. 按优先级匹配，每字段只取最匹配的一项

    Args:
        sections: list[Section]

    Returns:
        dict: {field_name: value_text, ...}
    """
    result = {}

    # 查找商务要求章节
    biz_section = find_section_by_title(sections, "商务要求")
    if not biz_section:
        for alt_name in ["商务需求", "商务条款", "供应商商务要求"]:
            biz_section = find_section_by_title(sections, alt_name)
            if biz_section:
                break
    if not biz_section:
        logger.info("[section_extractor] 未找到商务要求章节，回退到直接搜索商务关键词")
        return _fallback_search_business_fields(sections)

    children = getattr(biz_section, "children", [])
    if not children:
        # 没有子章节时，将整章内容返回
        content_text = section_content_to_text(biz_section)
        if content_text.strip():
            result["business_terms_raw"] = content_text.strip()[:500]
        return result

    # 按优先级分组处理
    used_fields = set()
    priority_groups = {}
    for keywords, field, priority in SECTION_TO_EXTRA:
        priority_groups.setdefault(priority, []).append((keywords, field))

    for priority in sorted(priority_groups.keys()):
        for keywords, field in priority_groups[priority]:
            if field in used_fields:
                continue
            for child in children:
                title = getattr(child, "title", "") or ""
                if any(kw in title for kw in keywords):
                    content_text = section_content_to_text(child)
                    if content_text.strip():
                        result[field] = content_text.strip()[:500]
                        used_fields.add(field)
                        break

    logger.info(
        "[section_extractor] 商务章节提取完成: 子章节=%d, 提取字段=%d",
        len(children), len(result),
    )
    return result


def extract_technical_from_sections(sections):
    """从文档章节树中提取技术要求。

    策略：
      1. 查找标题含"技术要求"或"技术参数"的章节
      2. 优先读子章节（分级技术参数）
      3. 提取表格结构

    Args:
        sections: list[Section]

    Returns:
        dict or None: {"technical_requirements": str, "tech_tables": list}
    """
    tech_section = None
    for keyword in ["技术要求", "技术参数", "技术规格", "技术标准", "采购项目技术"]:
        tech_section = find_section_by_title(sections, keyword)
        if tech_section:
            break

    if not tech_section:
        return None

    result = {"technical_requirements": "", "tech_tables": []}

    children = getattr(tech_section, "children", [])
    if children:
        parts = []
        for child in children:
            title = getattr(child, "title", "") or ""
            content_text = section_content_to_text(child)
            if title and content_text:
                parts.append(title + "\n" + content_text)
            elif content_text:
                parts.append(content_text)
            # 提取表格
            for block in getattr(child, "content", []):
                if getattr(block, "type", "") == "table":
                    headers = getattr(block, "headers", []) or []
                    rows = getattr(block, "rows", []) or []
                    if headers or rows:
                        result["tech_tables"].append({
                            "headers": headers,
                            "rows": rows[:50],
                        })
        result["technical_requirements"] = "\n---\n".join(parts)
    else:
        content_text = section_content_to_text(tech_section)
        result["technical_requirements"] = content_text
        # 提取表格
        for block in getattr(tech_section, "content", []):
            if getattr(block, "type", "") == "table":
                headers = getattr(block, "headers", []) or []
                rows = getattr(block, "rows", []) or []
                if headers or rows:
                    result["tech_tables"].append({
                        "headers": headers,
                        "rows": rows[:50],
                    })

    logger.info(
        "[section_extractor] 技术章节提取完成: 子章节=%d, 内容长度=%d, 表格=%d",
        len(children), len(result["technical_requirements"]),
        len(result["tech_tables"]),
    )
    return result
