"""analysis_v3 总入口 — 三层专家分析管线。

架构（标书撰写专家思维）：
  第1层：元数据提取 — 规则+表格，零LLM
  第2层：生死线扫描 — 法规固定清单 + 动态章节提取，零LLM
  第3层：章节逐项拆解（评分点） — 表格+文本规则，零LLM
  第3层：横向关联分析（策略） — 跨包/跨章节整合

调用流程：
  1. 解析文件 → StructuredDocument
  2. 第1层：元数据提取（表格+规则+LLM增强）
  3. 第2层：生死线扫描（法规固定清单 + 动态章节提取）
  4. 检测是否分包
  5. 第3层：逐包/整体执行评分拆解 + 包参数统计
  6. 第4层：跨包策略分析
  6. 组装 analysis_data JSON
  7. 生成核对项
"""

import json
import logging
import re

from .schemas import assemble_v3_analysis_data, analysis_data_to_json
from ....domain.analysis_schema import AnalysisSchema, ValidationGate
from .llm_extractor import (
    extract_metadata as llm_extract_metadata,
    extract_budget as llm_extract_budget,
    extract_scoring as llm_extract_scoring,
    extract_business as llm_extract_business,
    extract_technical as llm_extract_technical,
)
from ....infrastructure.document_parser import strip_heading_prefix as _strip_heading_prefix
from .llm_validator import merge_llm_into_metadata
from .phase1_metadata import extract_metadata, classify_document
from .phase2_eligibility import scan_eligibility
from .phase1_5_format import extract_format_requirements
from .phase2_extractor import scan_eligibility_v2
from .phase3_scoring import extract_scoring, extract_packages, cross_package_analysis
from .check_items import generate_check_items, assemble_check_items
from ....infrastructure.table_classifier import classify_all_tables
from ....infrastructure.document_parser import ContentBlock, Section
from ....infrastructure.table_parser import parse_all_tables

logger = logging.getLogger(__name__)


def _get_parser():
    """延迟导入 DocumentParser。"""
    from ....infrastructure.document_parser import DocumentParser
    return DocumentParser()


def _text_from_first_sections(doc, count=6, raw_text=""):
    """从 doc 的前 count 个 section 提取文本。提供 raw_text 时优先使用。"""
    if raw_text:
        lines = raw_text.split("\n")
        return "\n".join(lines[:80])  # 前80行通常覆盖封面+目录+第一章
    if not doc.sections:
        return ""
    texts = []
    for section in doc.sections[:count]:
        t = getattr(section, "title", "") or ""
        if t:
            texts.append(t)
        for block in getattr(section, "content", []):
            if getattr(block, "text", ""):
                texts.append(block.text)
    return "\n".join(texts)


def _text_from_all_sections(doc, raw_text=""):
    """从 doc 的所有 section 提取纯文本。提供 raw_text 时优先使用。"""
    if raw_text:
        return raw_text[:50000]
    if not doc.sections:
        return ""
    return doc.to_text()


def _detect_package_count(metadata, doc, raw_text=""):
    """检测分包数量。优先用 metadata 的 package_count，没有则从文档中找。"""
    pkg_count = metadata.get("package_count", 0)
    if pkg_count and pkg_count > 0:
        return list(range(1, int(pkg_count) + 1))

    # 扫描前几个章节识别分包
    doc_text = _text_from_first_sections(doc, count=6, raw_text=raw_text)

    # 1. 显式声明："本项目共X个包"
    m = re.search(r"(?:共计|分为|共)\s*(\d+)\s*个包", doc_text)
    if m:
        count = int(m.group(1))
        return list(range(1, count + 1))

    # 2. "第X包" 格式
    max_pkg = 0
    pkg_nums = re.findall(r"第(\d+)包", doc_text)
    for num in pkg_nums:
        n = int(num)
        if n > max_pkg:
            max_pkg = n

    # 3. "采购包X" 格式（最常见，覆盖公开招标/比选/竞争性谈判等）
    cg_nums = re.findall(r"采购包(\d+)", doc_text)
    for num in cg_nums:
        n = int(num)
        if n > max_pkg:
            max_pkg = n

    if max_pkg > 0:
        return list(range(1, max_pkg + 1))

    # 4. 文档有内容但没找到任何包声明 → 默认为单包项目
    #    检查文档是否有实际内容
    if doc_text and len(doc_text.strip()) > 200:
        return [1]

    return []


def _build_strategy_from_phases(metadata, eligibility, scoring, packages):
    """从前面三层的输出构建策略分析。

    每包独立策略分析（由 extract_packages 中的 analyze_package_strategy 完成）。
    此处进行包间关联分析，汇总撰写建议。
    """
    package_priorities = []
    writing_focus = []

    # ── 包优先级分析（从每包的 strategy 字段读取） ──
    if packages:
        for pkg in packages:
            pkg_no = pkg.get("package_no", 0)
            pkg_name = pkg.get("name", f"第{pkg_no}包")
            pkg_strat = pkg.get("strategy", {})

            # 综合优先级评分（预算 + 难度 + 评分维度）
            budget = pkg.get("budget", 0)
            priority_score = 0
            if budget:
                priority_score += min(budget / 10000, 10)

            params = pkg.get("parameters") or {}
            starred = params.get("starred_count", 0)
            if starred > 0:
                priority_score += starred * 2

            difficulty = pkg_strat.get("difficulty", "low")
            if difficulty == "high":
                priority_score += 2  # 高难度包需要更多关注
            elif difficulty == "medium":
                priority_score += 1

            priority_level = "high" if priority_score > 5 else ("medium" if priority_score > 2 else "low")

            risk_factors = []
            if pkg_strat.get("risk") and pkg_strat["risk"] != "无显著风险":
                risk_factors.append(pkg_strat["risk"])
            if starred > 3:
                risk_factors.append(f"★条款{starred}项")

            package_priorities.append({
                "package_no": pkg_no,
                "name": pkg_name,
                "priority": priority_level,
                "priority_score": round(priority_score, 1),
                "budget": budget,
                "starred_count": starred,
                "difficulty": difficulty,
                "competition": pkg_strat.get("competition", "medium"),
                "risk_factors": risk_factors,
            })

        # 按优先级排序
        package_priorities.sort(key=lambda x: x["priority_score"], reverse=True)

        # 包间关联分析
        cross = cross_package_analysis(packages)
        if cross:
            package_priorities.insert(0, {
                "_type": "cross_package",
                "highest_value": cross.get("highest_value", ""),
                "lowest_risk": cross.get("lowest_risk", ""),
                "recommendations": cross.get("recommendations", ""),
            })

    # ── 撰写重点 ──
    disqualifications = eligibility.get("disqualifications", [])
    starred_reqs = eligibility.get("starred_requirements", [])

    if disqualifications:
        for d in disqualifications[:3]:
            writing_focus.append({
                "focus": "废标条件注意",
                "detail": d.get("requirement", "")[:100],
                "priority": "critical",
            })

    for s in starred_reqs[:5]:
        writing_focus.append({
            "focus": "★实质性条款响应",
            "detail": s.get("requirement", "")[:100],
            "priority": "high",
        })

    dims = scoring.get("dimensions", [])
    for d in dims:
        if d.get("type") == "subjective":
            writing_focus.append({
                "focus": f"撰写方案：{d['name']}",
                "detail": f"需准备详细方案，分值 {d['score']} 分",
                "priority": "high",
            })

    return {
        "package_priorities": package_priorities,
        "writing_focus": writing_focus,
    }

# ── 商务/技术要求通用字段正则模式 ──
_BIZ_FIELD_PATTERNS = {
    "payment_terms": [r"付款[^\n。]{5,200}", r"付款方式[^\n。]{5,200}"],
    "delivery_location": [r"履约地点[^\n。]{5,200}", r"交货地点[^\n。]{5,200}"],
    "delivery_terms": [r"服务时间[^\n。]{5,200}", r"交货时间[^\n。]{5,200}"],
    "acceptance_standard": [r"验收[^\n。]{5,300}"],
    "warranty_period": [r"质保期[^\n。]{5,200}", r"到货有效期[^\n。]{5,200}"],
    "pricing_rule": [r"报价[^\n。]{5,200}", r"报价要求[^\n。]{5,200}"],
    "after_sale_service": [r"售后[^\n。]{5,200}"],
    "submission_location": [r"递交地点[^\n。]{5,100}"],
}

_TECH_FIELD_PATTERNS = {
    "specifications": [r"规格参数[^\n。]{10,500}"],
    "quality_standard": [r"质量标准[^\n。]{10,200}", r"技术标准[^\n。]{10,200}"],
}


def _extract_section_text(raw_text: str, start_markers: list, end_markers: list) -> str:
    """从 raw_text 中定位章节并提取原文（精确边界）。"""
    for marker in start_markers:
        idx = raw_text.find(marker)
        if idx >= 0:
            rest = raw_text[idx:]
            end = len(rest)
            for sep in end_markers:
                pos = rest.find(sep, 5)
                if 5 < pos < end:
                    end = pos
            text = rest[:end].strip()
            if len(text) > 50:
                return text
    return ""


def _rule_extract_business(section_text: str) -> dict:
    """从商务章节文本中用规则提取已知字段。"""
    result = {}
    for field, patterns in _BIZ_FIELD_PATTERNS.items():
        for p in patterns:
            m = __import__("re").search(p, section_text)
            if m:
                result[field] = m.group(0)
                break
    return result


def _rule_extract_technical(section_text: str) -> list:
    """从技术章节文本中用规则提取要点。"""
    result = []
    for field, patterns in _TECH_FIELD_PATTERNS.items():
        for p in patterns:
            m = __import__("re").search(p, section_text)
            if m:
                result.append(f"{field}: {m.group(0)[:200]}")
    return result


def _find_business_section_text(sections, raw_text="", max_chars=0) -> str:
    """定位商务章节并提取原文（规则优先，LLM 兜底）。
    
    策略:
      1. 精确搜索 ★二、商务要求 定位章节边界 → 截取原文
      2. 用规则从截取的原文中提取已知字段（付款方式、交货时间等）
      3. 只有规则提取不到时才返回原文给 LLM（此时传小段即可）
    
    Returns:
        规则提取成功 → 空字符串（数据已存入 metadata.extra）
        规则提取失败 → 小段原文（给 LLM 兜底用，最多 1000 字符）
    """
    if not raw_text:
        return ""
    
    section_text = _extract_section_text(
        raw_text,
        start_markers=["★二、商务要求", "★2、商务要求", "商务要求", "商务条款"],
        end_markers=["\n★", "\n第", "\n四、", "\n五、", "\n六、", "\n七、"],
    )
    if not section_text:
        return ""
    
    # 规则优先：从截取的章节文本中提取字段
    rule_result = _rule_extract_business(section_text)
    if rule_result:
        # 规则提取成功，直接存入全局 metadata（通过 side effect 传递）
        _biz_rule_cache.clear()
        _biz_rule_cache.update(rule_result)
        logger.info("[analysis_v3] 规则提取商务要求完成: %d fields %s", len(rule_result), list(rule_result.keys()))
        return ""  # 不需要 LLM 了
    
    # 规则提取失败，返回小段原文给 LLM 兜底（最多 1000 字符）
    logger.info("[analysis_v3] 规则未提取到商务字段，返回原文给 LLM (len=%d)", len(section_text))
    return section_text[:1000]


# 全局缓存：规则提取结果传给调用方
_biz_rule_cache = {}
_tech_rule_cache = {}

def _get_biz_rule_cache():
    global _biz_rule_cache
    result = dict(_biz_rule_cache)
    _biz_rule_cache = {}
    return result


def _find_technical_section_text(sections, raw_text="", max_chars=0) -> str:
    """定位技术章节并提取原文（规则优先，LLM 兜底）。"""
    if not raw_text:
        return ""
    
    section_text = _extract_section_text(
        raw_text,
        start_markers=["三、技术、服务要求", "技术、服务要求", "★三、技术", "技术要求", "技术参数"],
        end_markers=["\n★", "\n第", "\n四、", "\n五、", "\n六、", "\n七、", "\n八、", "\n第六章", "\n第七章"],
    )
    if not section_text:
        return ""
    
    # 规则优先
    rule_result = _rule_extract_technical(section_text)
    if rule_result:
        _tech_rule_cache.clear()
        _tech_rule_cache["items"] = rule_result
        logger.info("[analysis_v3] 规则提取技术要求完成: %d items", len(rule_result))
        return ""
    
    return section_text[:1000]




def _section_to_text(section) -> str:
    """提取章节的纯文本内容（段落 + 子章节）。"""
    parts = []
    for block in getattr(section, "content", []):
        if getattr(block, "type", "") in ("paragraph", "heading", "list"):
            text = getattr(block, "text", "") or ""
            if text:
                parts.append(text)
        elif getattr(block, "type", "") == "table":
            headers = getattr(block, "headers", []) or []
            rows = getattr(block, "rows", []) or []
            if headers:
                parts.append(" | ".join(headers))
            for row in rows:
                parts.append(" | ".join(row))
    for child in getattr(section, "children", []):
        child_text = _section_to_text(child)
        if child_text:
            parts.append(child_text)
    return "\n".join(parts)




def start_analyze_v3(task, source_texts, adapter=None):
    """三层分析管线总入口 — 零LLM依赖。

    Args:
        task: BiddingTask 实例
        source_texts: 包含 raw_text 和 effective_text 的 dict
        adapter: 保留参数，不再使用

    Returns:
        dict: {"analysis_data": dict, "check_items": list}
              失败时返回 None
    """
    logger.info("[analysis_v3] 开始三层分析 task=%s", task.id)

    parser = _get_parser()

    # 1. 解析文件
    doc = None
    try:
        from ....domain import BiddingSharedResource, FileStorage
        shared_resource = BiddingSharedResource.query.filter_by(
            id=task.shared_resource_id).first()
        if not shared_resource:
            logger.warning(
                "[analysis_v3] shared_resource 不存在: task_id=%s", task.id)
        elif not shared_resource.tender_file_id:
            logger.warning(
                "[analysis_v3] tender_file_id 为空: shared_resource_id=%s",
                shared_resource.id)
        else:
            file_record = FileStorage.query.get(shared_resource.tender_file_id)
            if not file_record:
                logger.warning(
                    "[analysis_v3] FileStorage 不存在: file_id=%s",
                    shared_resource.tender_file_id)
            elif file_record.storage_provider in ("CHROMA", "CHROMA_MANAGED"):
                # CHROMA 存储：直接从解析缓存重建文档
                doc = _get_structured_doc_from_cache(file_record)
                if not doc:
                    logger.warning(
                        "[analysis_v3] 缓存重建失败: file_id=%s", file_record.id)
            else:
                # MINIO/LOCAL：读取文件原始字节后解析
                payload = _get_file_payload(file_record)
                if not payload:
                    logger.warning(
                        "[analysis_v3] 无法读取文件内容: file_id=%s, storage=%s, local=%s",
                        file_record.id, file_record.storage_provider,
                        file_record.local_path)
                else:
                    doc = parser.parse_structured(
                        file_record.file_name, payload)
    except Exception as exc:
        logger.exception("[analysis_v3] 文档解析异常")

    if not doc:
        logger.warning("[analysis_v3] 无法获取结构化文档，降级")
        return None
    # ════════════════════════════════════════════
    #  第1层：元数据提取 + 生死线扫描（并行）
    # ════════════════════════════════════════════

    logger.info("[analysis_v3] 第1层: 预设清单扫描（元数据+生死线）")

    # 获取 raw_text 作为章节回退
    raw_text = source_texts.get("raw_text", "")
    
    # 获取文件名（用于文档分类）
    file_name = ""
    try:
        from ....domain import BiddingSharedResource, FileStorage
        shared_resource = BiddingSharedResource.query.filter_by(
            id=task.shared_resource_id).first()
        if shared_resource:
            file_record = FileStorage.query.get(shared_resource.tender_file_id)
            if file_record:
                file_name = file_record.file_name or ""
    except Exception:
        pass

    # Phase 1: 表格解析 + 元数据提取（纯规则）
    meta_text = _text_from_first_sections(doc, count=6, raw_text=raw_text)
    
    # 表格解析
    table_results = None
    try:
        table_results = parse_all_tables(getattr(doc, 'tables', []) or [])
    except Exception as exc:
        logger.warning("[analysis_v3] 表格解析异常: %s", exc)
    
    # 表格矩阵分类（补充分类维度，兼容已有 table_results）
    try:
        doc_tables = getattr(doc, 'tables', []) or []
        if doc_tables:
            classification = classify_all_tables(doc_tables)
            if table_results is None:
                table_results = {}
            table_results["_classification"] = classification
    except Exception as exc:
        logger.warning("[analysis_v3] 表格分类异常: %s", exc)
    
    # 元数据提取（含文档分类 + 表格融合）
    metadata = extract_metadata(meta_text, file_name=file_name, table_results=table_results, sections=doc.sections)

    # ── LLM 增强：补全规则无法提取的元数据 ──
    try:
        llm_meta = llm_extract_metadata(raw_text[:8000])
        if llm_meta:
            # LLM提取预算
            table_kv_text = ''
            if table_results:
                cls = table_results.get('_classification', {})
                prelim = cls.get('preliminary', {}) if cls else {}
                kv = prelim.get('kv_pairs', {}) if prelim else {}
                if kv:
                    table_kv_text = '\n'.join([f'{k} → {v}' for k, v in kv.items()])
            if table_kv_text:
                llm_budget = llm_extract_budget(table_kv_text)
                if llm_budget:
                    llm_meta['budget'] = llm_budget
            # 合并到规则结果
            metadata = merge_llm_into_metadata(metadata, llm_meta)
            logger.info('[analysis_v3] LLM元数据增强完成: purchaser=%s, agent=%s, budget=%s',
                        llm_meta.get('purchaser_name', ''),
                        llm_meta.get('agent_name', ''),
                        llm_meta.get('budget', {}).get('budget_total', 0) if isinstance(llm_meta.get('budget'), dict) else '')
    except Exception as exc:
        logger.warning('[analysis_v3] LLM元数据增强异常: %s', exc)

    # 检测分包
    package_nos = _detect_package_count(metadata, doc, raw_text=raw_text)
    logger.info("[analysis_v3] 分包检测: package_nos=%s", package_nos)

    # 章节为空时（如旧缓存），用 raw_text 构建临时章节
    sections = doc.sections
    if not sections and raw_text:
        temp_section = Section(title="全文", level=1)
        temp_section.content.append(ContentBlock(ContentBlock.TYPE_PARAGRAPH, raw_text[:50000]))
        sections = [temp_section]
        logger.info("[analysis_v3] sections 为空，使用 raw_text 构建临时章节")

    # ════════════════════════════════════════════
    #  第1.5层：格式要求提取（比选申请文件格式等）
    # ════════════════════════════════════════════

    # ── LLM 增强：商务要求（规则未提取到时补充） ──
    try:
        extra = metadata.get("extra", {}) if isinstance(metadata, dict) else {}
        # 检查规则是否提取到商务字段
        biz_field_keys = ["payment_terms", "delivery_location", "service_period", 
                          "acceptance_standard", "after_sale_service", "warranty_period",
                          "business_terms_raw"]
        has_rule_biz = any(extra.get(k) for k in biz_field_keys)
        
        if not has_rule_biz:
            biz_section_text = _find_business_section_text(sections, raw_text)
            # 先检查规则提取缓存（_find_business_section_text 内部已尝试规则）
            rule_biz = _get_biz_rule_cache()
            if rule_biz:
                # 规则提取成功，写入 metadata.extra
                if isinstance(metadata, dict):
                    if "extra" not in metadata:
                        metadata["extra"] = {}
                    metadata["extra"].update(rule_biz)
                    biz_texts = [f"{k}: {v}" for k, v in rule_biz.items()]
                    metadata["extra"]["business_terms_raw"] = "\n".join(biz_texts)
                logger.info("[analysis_v3] 规则提取商务要求完成: %d fields", len(rule_biz))
            elif biz_section_text:
                # 规则提取失败，启用 LLM 兜底（仅传小段文本）
                logger.info("[analysis_v3] 规则未提取到商务要求，启用 LLM 兜底 (text_len=%d)", len(biz_section_text))
                llm_biz = llm_extract_business(biz_section_text)
                if llm_biz and isinstance(llm_biz, list) and len(llm_biz) > 0:
                    biz_texts = []
                    for item in llm_biz:
                        name = item.get("name", "")
                        requirement = item.get("requirement", "")
                        if name and requirement:
                            biz_texts.append(f"{name}: {requirement}")
                    if biz_texts:
                        if isinstance(metadata, dict):
                            if "extra" not in metadata:
                                metadata["extra"] = {}
                            metadata["extra"]["llm_business_raw"] = "\n".join(biz_texts)
                            metadata["extra"]["business_terms_raw"] = "\n".join(biz_texts)
                        logger.info("[analysis_v3] LLM商务提取完成: %d items", len(llm_biz))
    except Exception as exc:
        logger.warning("[analysis_v3] LLM商务增强异常(非阻断): %s", exc)

    # ── LLM 增强：技术要求（规则未提取到时补充） ──
    try:
        if not has_rule_biz or True:  # 技术要求一直尝试 LLM 补充
            tech_section_text = _find_technical_section_text(sections, raw_text)
            if tech_section_text:
                logger.info("[analysis_v3] 尝试 LLM 技术要求提取 (text_len=%d)", len(tech_section_text))
                llm_tech = llm_extract_technical(tech_section_text)
                if llm_tech and isinstance(llm_tech, list) and len(llm_tech) > 0:
                    tech_texts = []
                    for item in llm_tech:
                        name = item.get("name", "")
                        requirement = item.get("requirement", "")
                        if name and requirement:
                            tech_texts.append(f"{name}: {requirement}")
                    if tech_texts and isinstance(metadata, dict):
                        if "extra" not in metadata:
                            metadata["extra"] = {}
                        metadata["extra"]["llm_technical_raw"] = "\n".join(tech_texts)
                        logger.info("[analysis_v3] LLM技术提取完成: %d items", len(llm_tech))
    except Exception as exc:
        logger.warning("[analysis_v3] LLM技术增强异常(非阻断): %s", exc)

    format_requirements = None
    try:
        format_requirements = extract_format_requirements(sections)
        if format_requirements:
            logger.info("[analysis_v3] 格式要求提取完成: chapter='%s', %d sections",
                        format_requirements["chapter_title"],
                        len(format_requirements.get("required_sections", [])))
    except Exception as exc:
        logger.warning("[analysis_v3] 格式要求提取异常(非阻断): %s", exc)

    # Phase 2: 专家级生死线扫描（零bid_type/doc_type依赖）
    # 使用新的章节定位v2：评分机制 + 法规固定清单 + 动态提取
    eligibility = scan_eligibility_v2(sections)

    # ════════════════════════════════════════════
    #  第2层：章节逐项拆解（评分点）
    # ════════════════════════════════════════════

    logger.info("[analysis_v3] 第2层: 章节逐项拆解（评分+分包）")

    # 评分（规则提取 + LLM增强）
    scoring = extract_scoring(sections)

    # ── LLM 增强：评分表结构化（规则未提取到时补充） ──
    if not scoring.get('dimensions') or len(scoring['dimensions']) == 0:
        try:
            # 从 raw_text 中找评分表区域
            scoring_text = _find_scoring_section(raw_text)
            if scoring_text:
                llm_scoring = llm_extract_scoring(scoring_text)
                if llm_scoring and llm_scoring.get('dimensions'):
                    if not scoring.get('dimensions'):
                        scoring['dimensions'] = llm_scoring['dimensions']
                    if not scoring.get('method') and llm_scoring.get('method'):
                        scoring['method'] = llm_scoring['method']
                    if not scoring.get('total_score') and llm_scoring.get('total_score'):
                        scoring['total_score'] = llm_scoring['total_score']
                    logger.info('[analysis_v3] LLM评分增强完成: method=%s, dims=%d',
                                llm_scoring.get('method'), len(llm_scoring['dimensions']))
        except Exception as exc:
            logger.warning('[analysis_v3] LLM评分增强异常: %s', exc)

    # 提取包名（从 raw_text 的"第X包：名称"模式）
    pkg_name_map = _extract_package_names(raw_text, package_nos)

    # 分包参数统计（纯规则，零LLM）
    packages = extract_packages(
        sections, package_nos, pkg_name_map=pkg_name_map,
        metadata_budget=metadata.get("budget"),
        table_results=table_results,
    )

    # ════════════════════════════════════════════
    #  第3层：横向关联分析（策略）
    # ════════════════════════════════════════════

    logger.info("[analysis_v3] 第3层: 横向关联分析（策略）")

    strategy = _build_strategy_from_phases(metadata, eligibility, scoring, packages)

    # ════════════════════════════════════════════
    #  组装输出
    # ════════════════════════════════════════════

    # 组装 analysis_data
    # 获取表格分类结果（用于补充商务/技术要求等字段）
    table_classification = None
    if table_results and isinstance(table_results, dict):
        table_classification = table_results.get("_classification")
    
    # ── 收集文档章节标题（用于后续目录生成） ──
    chapter_titles = []
    seen_titles = set()
    for sec in sections:
        title = _strip_heading_prefix(getattr(sec, "title", "") or "")
        # 只收集一级章节（第一章、一、等）
        if title and len(title) < 50 and title not in seen_titles:
            level = getattr(sec, "level", 0)
            if level <= 2:  # 一级或二级标题
                chapter_titles.append(getattr(sec, "title", "") or "")  # 保留原文
                seen_titles.add(title)
    
    analysis_data = assemble_v3_analysis_data(
        metadata=metadata,
        eligibility=eligibility,
        scoring=scoring,
        packages=packages,
        strategy=strategy,
        table_classification=table_classification,
    )
    # 注入章节标题到 analysis_data
    analysis_data["document_chapters"] = chapter_titles

    # ── Validation Gate：校验数据质量（非阻断，仅日志） ──
    try:
        schema = AnalysisSchema.from_analysis_data(analysis_data)
        gate = ValidationGate()
        issues = gate.validate(schema)
        if issues:
            logger.warning("[analysis_v3] 数据校验发现 %d 个问题:", len(issues))
            for issue in issues:
                logger.warning("  [validation] %s", issue)
        analysis_data["_validation"] = {"issues": issues, "passed": len(issues) == 0}
    except Exception as exc:
        logger.warning("[analysis_v3] 数据校验异常(非阻断): %s", exc)

    # 生成核对项
    check_items = generate_check_items(eligibility, scoring, packages)

    logger.info(
        "[analysis_v3] 分析完成: metadata=%d fields, eligibility=%d items, "
        "scoring=%d dims, packages=%d, strategy=%d items, check_items=%d",
        len(metadata),
        eligibility["summary"]["total_items"] if eligibility.get("summary") else 0,
        len(scoring["dimensions"]),
        len(packages),
        len(strategy["writing_focus"]),
        len(check_items),
    )

    return {
        "analysis_data": analysis_data,
        "analysis_data_json": analysis_data_to_json(analysis_data),
        "check_items": check_items,
        "format_requirements": format_requirements,
        "effective_text": source_texts.get("effective_text", "") or _text_from_all_sections(doc)[:50000],
    }


def _find_scoring_section(raw_text):
    """从 raw_text 中定位评分表区域。"""
    if not raw_text:
        return None
    lines = raw_text.split('\\n')
    score_lines = []
    in_score = False
    for i, line in enumerate(lines):
        if re.search(r'[（(]?评标[）)]?|[（(]?评分[）)]?', line) and re.search(r'分值|分数|权重|得分|评审', line):
            in_score = True
        if in_score:
            score_lines.append(line)
            if len(score_lines) >= 80:
                break
    if score_lines:
        return '\n'.join(score_lines)
    return None


def _extract_package_names(raw_text, package_nos):
    """从 raw_text 中提取各包的名称。"""
    if not package_nos or not raw_text:
        return {}
    # 匹配 "第X包：名称" 或 "第X包:名称" 模式
    pkg_names = {}
    for m in re.finditer(r"第(\d+)包[：:]\s*([^；;。\n]+)", raw_text):
        pkg_no = int(m.group(1))
        name = m.group(2).strip()
        if pkg_no in package_nos:
            pkg_names[pkg_no] = name
    return pkg_names


def _get_file_payload(file_record):
    """读取文件原始二进制内容（MINIO / LOCAL）。
    
    CHROMA 存储的文档请用 _get_structured_doc_from_cache()。
    """
    from flask import current_app
    from pathlib import Path

    if not file_record:
        return None

    if file_record.storage_provider == "MINIO":
        from ....infrastructure.integrations import MinioAdapter
        endpoint = current_app.config.get("MINIO_ENDPOINT")
        access_key = current_app.config.get("MINIO_ACCESS_KEY")
        secret_key = current_app.config.get("MINIO_SECRET_KEY")
        bucket_name = current_app.config.get("MINIO_BUCKET_NAME")
        secure = current_app.config.get("MINIO_SECURE", False)
        adapter = MinioAdapter(endpoint, access_key, secret_key, bucket_name, secure)
        return adapter.download_bytes(file_record.minio_object_name)

    if file_record.local_path and Path(file_record.local_path).exists():
        return Path(file_record.local_path).read_bytes()

    return None


def _get_structured_doc_from_cache(file_record):
    """从 doc_parse_cache 中重建结构化文档。
    
    适用于 storage_provider == CHROMA 的场景。
    """
    if not file_record:
        return None
    from ....domain import DocParseCache
    from ....infrastructure.document_parser import StructuredDocument
    import json

    cached = DocParseCache.query.filter_by(file_id=file_record.id).first()
    if cached and cached.parsed_json:
        try:
            data = json.loads(cached.parsed_json.decode("utf-8"))
            return StructuredDocument.from_dict(data)
        except Exception as exc:
            logger.warning("[analysis_v3] 缓存重建失败: %s", exc)
    return None

