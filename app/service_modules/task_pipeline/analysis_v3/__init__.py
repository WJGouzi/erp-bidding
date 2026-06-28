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
from .llm_extractor import (
    extract_metadata as llm_extract_metadata,
    extract_budget as llm_extract_budget,
    extract_scoring as llm_extract_scoring,
    extract_business as llm_extract_business,
    extract_technical as llm_extract_technical,
)
from .llm_validator import merge_llm_into_metadata
from .phase1_metadata import extract_metadata, classify_document
from .phase2_eligibility import scan_eligibility
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
        llm_meta = llm_extract_metadata(raw_text[:5000])
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
    
    analysis_data = assemble_v3_analysis_data(
        metadata=metadata,
        eligibility=eligibility,
        scoring=scoring,
        packages=packages,
        strategy=strategy,
        table_classification=table_classification,
    )

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

