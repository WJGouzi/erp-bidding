# -*- coding: utf-8 -*-
import json
import logging
import re
import time
from io import BytesIO
from itertools import zip_longest
from pathlib import Path

import docx
from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn
from flask import current_app, send_file
import numpy as _np
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.utils import secure_filename

from ...core.extensions import db
from ...core.time_utils import utc_now
from ...domain import (
    BiddingAnalysisResult,
    BiddingCatalog,
    BiddingCheckItem,
    BiddingSharedResource,
    BiddingTenderAttachment,
    BiddingTask,
    BiddingTaskChapter,
    BiddingTaskExecution,
    FileStorage,
    KnowledgeBase,
    KnowledgeBaseFile,
    SubjectCompany,
    SubjectMaterialFile,
    TemplateCatalog,
)
from ...infrastructure.document_parser import DocumentParser
from ...infrastructure.integrations import ChromaAdapter, LLMAdapter, MinioAdapter
from ...infrastructure.multi_recall_engine import MultiRecallEngine
from ..quality_assurance import (
    build_requirement_traceability_matrix,
    bind_requirements_to_chapters,
    inject_constraints_into_prompt,
    post_generation_verify,
    build_coverage_report,
)
from ...infrastructure.task_queue import TaskQueueManager
from ...core.response import page_success
from ..common import (
    CHAPTER_FIELD_UNSET,
    TENDER_ALLOWED_EXTENSIONS,
    TaskExecutionCancelledError,
    dump_json,
    get_subject_material_completeness,
    normalize_knowledge_base_ids,
    validate_subject_knowledge_bases,
)
from ..storage import StorageService

logger = logging.getLogger(__name__)
_FIELD_UNSET = object()
_EMPTY_PAGE_MARKER = "[[EMPTY_PAGE]]"


def _normalize_catalog_generation_level(level):
    """规范化目录颗粒度配置。"""
    normalized = str(level or "MEDIUM").strip().upper()
    if normalized not in {"LOW", "MEDIUM", "HIGH"}:
        return "MEDIUM"
    return normalized


def _get_catalog_generation_profile(level):
    """返回目录颗粒度对应的描述长度和写作要求。"""
    normalized = _normalize_catalog_generation_level(level)
    profile_map = {
        "LOW": {
            "description_max_length": 70,
            "directive": "目录颗粒度：LOW，章节内容保持简洁直达，突出核心响应点即可。",
        },
        "MEDIUM": {
            "description_max_length": 120,
            "directive": "目录颗粒度：MEDIUM，章节内容需要兼顾概述、关键响应点与必要说明。",
        },
        "HIGH": {
            "description_max_length": 180,
            "directive": "目录颗粒度：HIGH，章节内容需要展开到实施细节、评分响应、风险控制和支撑材料说明。",
        },
    }
    return {"level": normalized, **profile_map[normalized]}


def _normalize_word_count_level(level):
    """规范化字数等级配置。"""
    normalized = str(level or "MEDIUM").strip().upper()
    if normalized not in {"SHORT", "MEDIUM", "LONG"}:
        return "MEDIUM"
    return normalized


def _get_word_count_profile(level):
    """返回字数等级对应的篇幅控制建议。"""
    normalized = _normalize_word_count_level(level)
    profile_map = {
        "SHORT": {"label": "SHORT", "instruction": "建议篇幅：约800-1200字，内容精准直达核心响应点。", "max_tokens": 2000},
        "MEDIUM": {"label": "MEDIUM", "instruction": "建议篇幅：约1200-2000字，内容完整并覆盖主要响应点，适当展开说明。", "max_tokens": 3000},
        "LONG": {"label": "LONG", "instruction": "建议篇幅：约2000-3000字，内容充分展开并包含详细支撑说明。", "max_tokens": 4000},
    }
    return profile_map[normalized]


def _get_task_chapters(task_id):
    """读取任务的全部章节记录并按章节号排序。"""
    chapters = (
        BiddingTaskChapter.query.filter_by(task_id=task_id)
        .order_by(BiddingTaskChapter.chapter_no.asc(), BiddingTaskChapter.id.asc())
        .all()
    )
    return [item.to_dict() for item in chapters]


def _build_shared_resource_analysis_text(shared_resource_id, package_no=None):
    """汇总共享资源下的主招标文件与附件文本，作为统一分析输入。"""
    shared_resource = db.session.get(BiddingSharedResource, shared_resource_id)
    if not shared_resource:
        return {"raw_text": "", "effective_text": "", "source_files": []}

    source_files = []
    file_records = []

    tender_file = db.session.get(FileStorage, shared_resource.tender_file_id) if shared_resource.tender_file_id else None
    if tender_file and not tender_file.deleted_flag:
        file_records.append(("TENDER", tender_file))

    attachments = (
        BiddingTenderAttachment.query.filter_by(shared_resource_id=shared_resource_id)
        .order_by(BiddingTenderAttachment.uploaded_at.asc(), BiddingTenderAttachment.id.asc())
        .all()
    )
    for attachment in attachments:
        file_record = db.session.get(FileStorage, attachment.file_id) if attachment.file_id else None
        if file_record and not file_record.deleted_flag:
            file_records.append(("ATTACHMENT", file_record))

    raw_parts = [None] * len(file_records)
    effective_parts = [None] * len(file_records)
    source_files = [None] * len(file_records)

    # 捕获 Flask 应用上下文，供并行线程使用
    _app = current_app._get_current_object()

    def _read_single_file(idx, file_role, file_record):
        # 每个并行线程需要自己的 Flask 应用上下文
        with _app.app_context():
            file_text = (_read_file_text(file_record) or "").strip()
        sf = {
            "file_id": file_record.id,
            "file_name": file_record.file_name,
            "file_role": file_role,
        }
        if not file_text:
            return (idx, sf, "", "")

        labeled_text = f"[{file_role}] {file_record.file_name}\n{file_text}"

        if package_no:
            filtered_text = (_extract_effective_text(file_text, package_no) or "").strip()
            eff = f"[{file_role}] {file_record.file_name}\n{filtered_text}" if filtered_text else ""
        else:
            eff = labeled_text

        return (idx, sf, labeled_text, eff)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = []
        for idx, (file_role, file_record) in enumerate(file_records):
            futures.append(executor.submit(_read_single_file, idx, file_role, file_record))
        for future in as_completed(futures):
            idx, sf, raw_text, eff_text = future.result()
            source_files[idx] = sf
            raw_parts[idx] = raw_text
            effective_parts[idx] = eff_text

    raw_parts = [p for p in raw_parts if p]
    effective_parts = [p for p in effective_parts if p]
    source_files = [s for s in source_files if s]

    return {
        "raw_text": "\n\n".join(raw_parts).strip(),
        "effective_text": "\n\n".join(effective_parts).strip(),
        "source_files": source_files,
    }


def _extract_analysis_context(analysis_result):
    """统一从 analysis_data/兼容字段中提取目录与正文生成需要的分析上下文。"""
    context = {
        "bidder_notice": {},
        "qualification_review": {},
        "source_files": [],
        "overview": "",
        "requirements": "",
        "business_requirements": "",
        "qualification_requirements": "",
        "technical_requirements": "",
        "scoring_items": "",
        "disqualification_items": "",
    }
    if not analysis_result:
        return context

    payload = {}
    if getattr(analysis_result, "analysis_data", None):
        try:
            payload = json.loads(analysis_result.analysis_data)
        except (TypeError, json.JSONDecodeError):
            payload = {}

    if isinstance(payload, dict) and payload.get("version") == "v2":
        bidder_notice = payload.get("bidder_notice", {}) or {}
        qualification_review = payload.get("qualification_review", {}) or {}
        context["source_files"] = payload.get("source_files", []) or []
        context["bidder_notice"] = bidder_notice
        context["qualification_review"] = qualification_review
        context["overview"] = bidder_notice.get("overview", "") or getattr(analysis_result, "overview", "") or ""
        context["requirements"] = payload.get("requirements", "") or getattr(analysis_result, "requirements", "") or ""
        context["business_requirements"] = (
            payload.get("business_requirements", "") or getattr(analysis_result, "business_requirements", "") or ""
        )
        context["qualification_requirements"] = (
            qualification_review.get("qualification_check", "")
            or getattr(analysis_result, "qualification_requirements", "")
            or ""
        )
        context["technical_requirements"] = (
            payload.get("technical_requirements", "") or getattr(analysis_result, "technical_requirements", "") or ""
        )
        context["scoring_items"] = payload.get("scoring_items", "") or getattr(analysis_result, "scoring_items", "") or ""
        context["disqualification_items"] = (
            qualification_review.get("disqualification_items", "")
            or getattr(analysis_result, "disqualification_items", "")
            or ""
        )
    else:
        context["source_files"] = payload.get("source_files", []) if isinstance(payload, dict) else []
        context["overview"] = getattr(analysis_result, "overview", "") or ""
        context["requirements"] = getattr(analysis_result, "requirements", "") or ""
        context["business_requirements"] = getattr(analysis_result, "business_requirements", "") or ""
        context["qualification_requirements"] = getattr(analysis_result, "qualification_requirements", "") or ""
        context["technical_requirements"] = getattr(analysis_result, "technical_requirements", "") or ""
        context["scoring_items"] = getattr(analysis_result, "scoring_items", "") or ""
        context["disqualification_items"] = getattr(analysis_result, "disqualification_items", "") or ""
    return context


def _get_catalog_outline(catalog_record):
    """将目录记录解析为章节大纲列表。"""
    payload = json.loads(catalog_record.catalog_content) if isinstance(catalog_record.catalog_content, str) else catalog_record.catalog_content
    outline = payload.get("outline") if isinstance(payload, dict) else None
    return outline if isinstance(outline, list) else []


def _prepare_task_chapters(task, catalog_record):
    """根据确认目录为任务初始化章节记录。"""
    BiddingTaskChapter.query.filter_by(task_id=task.id).delete()
    outline = _get_catalog_outline(catalog_record)
    if not outline:
        outline = [{"title": "综合响应", "description": ""}]
    for idx, item in enumerate(outline, start=1):
        record = BiddingTaskChapter(
            task_id=task.id,
            chapter_no=idx,
            chapter_title=(item.get("title") or f"章节{idx + 1}").strip(),

            status="PENDING",
            stage_code="QUEUED",
            stage_message="等待生成",
            progress=0,
        )
        db.session.add(record)
    db.session.flush()


def _update_task_generate_stage(task, stage_code=None, stage_message=None, error_message=None):
    """更新任务级生成阶段编码、提示语和错误信息。"""
    # CHAPTER_FIELD_UNSET 已在文件顶部导入
    if stage_code is not None:
        task.generate_stage_code = stage_code
    if stage_message is not None:
        task.generate_stage_message = stage_message
    if error_message is not None:
        task.error_message = error_message
    # 如果未传值则从 task 现有字段读取
    if stage_code is None:
        task.generate_stage_code = getattr(task, "generate_stage_code", CHAPTER_FIELD_UNSET)
    if stage_message is None:
        task.generate_stage_message = getattr(task, "generate_stage_message", CHAPTER_FIELD_UNSET)
    if error_message is None:
        task.error_message = getattr(task, "error_message", "")

def _maybe_fail_generate_stage_for_testing(stage_code):
    """在测试模式下按配置模拟指定生成阶段失败。"""
    force_fail_codes = current_app.config.get("GENERATE_FORCE_FAIL_STAGE_CODES")
    if isinstance(force_fail_codes, str):
        force_fail_codes = {force_fail_codes}
    if force_fail_codes and stage_code in (force_fail_codes or set()):
        raise RuntimeError(f"模拟生成阶段失败: {stage_code}")


def _get_failed_generate_stage(stage_code):
    """根据阶段编码映射生成失败后的任务阶段。"""
    mapping = {
        "CHAPTER_GENERATING": ("CHAPTER_GENERATION_FAILED", "章节正文生成失败"),
        "ASSEMBLING_CONTENT": ("CONTENT_ASSEMBLY_FAILED", "章节内容组装失败"),
        "BUILDING_DOCX": ("DOCX_BUILD_FAILED", "结果文档构建失败"),
        "SAVING_RESULT": ("RESULT_SAVE_FAILED", "结果文件保存失败"),
    }
    return mapping.get(stage_code, ("GENERATE_FAILED", "标书生成失败"))


def _update_chapter_runtime_state(
    chapter_record,
    status=_FIELD_UNSET,
    progress=_FIELD_UNSET,
    stage_code=_FIELD_UNSET,
    stage_message=_FIELD_UNSET,
    error_message=_FIELD_UNSET,
    started_at=_FIELD_UNSET,
    finished_at=_FIELD_UNSET,
    content_snapshot=_FIELD_UNSET,
):
    """更新单个章节的运行状态、进度和错误信息。"""
    if status is not _FIELD_UNSET:
        chapter_record.status = status
    if progress is not _FIELD_UNSET:
        chapter_record.progress = progress
    if stage_code is not _FIELD_UNSET:
        chapter_record.stage_code = stage_code
    if stage_message is not _FIELD_UNSET:
        chapter_record.stage_message = stage_message
    if error_message is not _FIELD_UNSET:
        chapter_record.error_message = error_message
    if started_at is not _FIELD_UNSET:
        chapter_record.started_at = started_at
    if finished_at is not _FIELD_UNSET:
        chapter_record.finished_at = finished_at
    if content_snapshot is not _FIELD_UNSET:
        chapter_record.content_snapshot = content_snapshot


def _ensure_task_chapters(task, catalog_record):
    """确保任务已存在与目录一致的章节记录。"""
    existing = (
        BiddingTaskChapter.query.filter_by(task_id=task.id)
        .order_by(BiddingTaskChapter.chapter_no.asc(), BiddingTaskChapter.id.asc())
        .all()
    )
    if not existing:
        _prepare_task_chapters(task, catalog_record)
        db.session.commit()
        existing = (
            BiddingTaskChapter.query.filter_by(task_id=task.id)
            .order_by(BiddingTaskChapter.chapter_no.asc(), BiddingTaskChapter.id.asc())
            .all()
        )
    return existing


def _calculate_generate_task_progress(chapter_records):
    """根据章节进度估算任务级百分比进度。"""
    if not chapter_records:
        return 0
    total = sum(item.progress for item in chapter_records)
    return min(round(total / len(chapter_records)), 99)


def _refresh_generate_task_progress(task):
    """重新计算并写回任务级生成进度。"""
    chapters = (
        BiddingTaskChapter.query.filter_by(task_id=task.id)
        .order_by(BiddingTaskChapter.chapter_no.asc())
        .all()
    )
    raw = _calculate_generate_task_progress(chapters)
    # 生成中的进度映射到 40~100 范围
    if task.status == "GENERATING":
        task.progress = 40 + int(raw * 60 / 100)
    else:
        task.progress = raw
    db.session.commit()


def _get_chapter_progress_floor(task):
    """根据任务状态计算进度下限（与需求文档状态机映射同步）。"""
    floor_map = {
        "INIT": 0,
        "UPLOADED": 10,
        "ANALYZING": 10,
        "PACKAGE_PENDING": 15,
        "ANALYZED": 20,
        "CHECKED": 30,
        "CATALOG_CONFIRMED": 40,
        "GENERATING": 41,
        "GENERATED": 100,
        "CANCELLED": 0,
        "FAILED": 0,
    }
    return floor_map.get(task.status, 0)


def _get_task_progress_value(task):
    """返回适合前端展示的任务进度值（与需求文档状态机映射同步）。"""
    if task.status == "GENERATING":
        chapters = (
            BiddingTaskChapter.query.filter_by(task_id=task.id)
            .order_by(BiddingTaskChapter.chapter_no.asc())
            .all()
        )
        if chapters:
            raw = _calculate_generate_task_progress(chapters)
            # 将章节进度 0~100 映射到 40~100 范围（GENERATING 起始进度为40）
            return 40 + int(raw * 60 / 100)
        return 41
    if task.status == "GENERATED":
        return 100
    return _get_chapter_progress_floor(task)
def _extract_failed_chapter_nos(chapter_records):
    """提取当前失败章节编号列表。"""
    return [item.chapter_no for item in chapter_records if item.status == "FAILED"]


def _extract_retry_chapter_nos(chapter_records):
    """提取适合重试的章节编号列表。"""
    return _extract_failed_chapter_nos(chapter_records)


def _build_generate_retry_hint(task, chapter_records):
    """构建前端可直接使用的生成重试提示信息。"""
    failed = _extract_failed_chapter_nos(chapter_records)
    if task.status in ("GENERATING", "GENERATED") and not failed:
        return {}
    all_generated = all(item.status == "GENERATED" for item in chapter_records)
    if all_generated:
        return {"retry_type": "TASK_OR_CHAPTERS", "failed_chapters": failed}
    return {"retry_type": "CHAPTERS", "failed_chapters": failed}


def _normalize_retry_chapter_nos(chapter_nos):
    """规范化重试请求中的章节编号参数。"""
    if not chapter_nos:
        return []
    if isinstance(chapter_nos, str):
        parts = chapter_nos.split(",")
        result = []
        for part in parts:
            part = part.strip()
            try:
                result.append(int(part))
            except (ValueError, TypeError):
                pass
        return result
    if isinstance(chapter_nos, (list, tuple)):
        return [int(x) for x in chapter_nos if x is not None]
    raise ValueError("chapter_nos 格式不正确")


def _resolve_retry_chapter_nos(chapter_nos, chapter_records, outline, retry_all=False):
    """结合章节现状和重试策略确定最终重试范围。"""
    if retry_all:
        return [item.chapter_no for item in chapter_records]
    normalized = _normalize_retry_chapter_nos(chapter_nos) if chapter_nos else _extract_retry_chapter_nos(chapter_records)
    valid_nos = {item.chapter_no for item in chapter_records}
    for no in normalized:
        if no not in valid_nos:
            raise ValueError(f"章节编号不存在: {no}")
    return sorted(set(normalized))


def _validate_generate_prerequisites(task):
    """校验任务是否满足生成标书的全部前置条件。"""
    if not task.subject_id:
        raise ValueError("请先选择主体公司")
    subject = db.session.get(SubjectCompany, task.subject_id)
    if not subject or subject.status is False:
        raise ValueError("所选主体公司不可用")
    if not task.model_type:
        raise ValueError("请先选择模型")
    material_status = get_subject_material_completeness(task.subject_id)
    if not material_status.get("is_complete"):
        missing = "、".join(material_status.get("missing_material_types", []))
        raise ValueError(f"主体资料未上传齐全，缺少: {missing}")
    kb_ids = normalize_knowledge_base_ids(task.knowledge_base_ids)
    if task.use_knowledge_base and not kb_ids:
        raise ValueError("启用知识库时必须选择 knowledge_base_ids")
    if task.use_knowledge_base:
        validate_subject_knowledge_bases(task.subject_id, kb_ids)


def _get_confirmed_catalog_record(task):
    """读取任务已确认的最终目录记录。"""
    catalog = BiddingCatalog.query.filter_by(shared_resource_id=task.shared_resource_id).first()
    if not catalog:
        raise LookupError("请先确认目录")
    return catalog


def _build_chapter_contents_from_records(chapter_records):
    """从章节记录中提取已生成的内容快照。"""
    chapter_contents = []
    for item in chapter_records:
        if not item.content_snapshot:
            raise RuntimeError(f"章节{item.chapter_no}尚未生成完成，无法组装结果文件")
        chapter_contents.append({"title": item.chapter_title, "content": item.content_snapshot})
    return chapter_contents


def _maybe_fail_chapter_for_testing(chapter_no):
    """在测试模式下按配置模拟章节生成失败。"""
    force_fail = current_app.config.get("GENERATE_FORCE_FAIL_CHAPTERS")
    if isinstance(force_fail, str):
        force_fail = {int(x.strip()) for x in force_fail.split(",") if x.strip().isdigit()}
    if force_fail and chapter_no in force_fail:
        raise RuntimeError(f"模拟章节失败: {chapter_no}")


def _validate_tender_file(file_storage):
    """校验招标文件是否存在且扩展名受支持。保留原始文件名（含中文）。"""
    original_filename = file_storage.filename or "uploaded_file"
    extension = Path(original_filename).suffix.lower().lstrip(".")
    if extension not in TENDER_ALLOWED_EXTENSIONS:
        raise ValueError("招标文件仅支持 doc、docx、pdf 格式")
    # 返回原始文件名，让调用方决定何时使用 secure_filename
    return original_filename, extension


def _build_knowledge_base_context(task, query_text=None):
    """构建本次生成需要拼接的知识库上下文。
    
    Args:
        task: BiddingTask 对象
        query_text: 可选的搜索文本，不传则使用 effective_text[:200]
    """
    kb_ids = normalize_knowledge_base_ids(task.knowledge_base_ids)
    if not task.use_knowledge_base or not kb_ids:
        return {}
    knowledge_bases = KnowledgeBase.query.filter(KnowledgeBase.id.in_(kb_ids)).all()
    if not knowledge_bases:
        return {}
    context = {"knowledge_list": []}
    
    if not query_text:
        analysis_result = BiddingAnalysisResult.query.filter_by(shared_resource_id=task.shared_resource_id).first()
        query_text = analysis_result.effective_text if analysis_result else (analysis_result.raw_text if analysis_result else "")
    
    for kb in knowledge_bases:
        files = KnowledgeBaseFile.query.filter_by(knowledge_base_id=kb.id).order_by(KnowledgeBaseFile.id.asc()).all()
        if not files:
            continue
        enabled_files = [item for item in files if item.reference_enabled]
        if not enabled_files:
            continue
        enabled_file_ids = {item.file_id for item in enabled_files}
        enabled_file_names = {item.file_name for item in enabled_files if item.file_name}
        chroma_tenant = kb.chroma_tenant or current_app.config.get("CHROMA_TENANT")
        chroma_database = kb.chroma_database or current_app.config.get("CHROMA_DATABASE")
        chroma_collection = kb.chroma_collection or f"kb_{kb.id}"
        
        snippets = []
        try:
            adapter = ChromaAdapter(
                host=current_app.config.get("CHROMA_HOST"),
                port=current_app.config.get("CHROMA_PORT"),
                tenant=chroma_tenant,
                database=chroma_database,
            )
            # 使用传入的 query_text 搜索，最多取1000字符用于查询
            search_text = (query_text or "")[:5000]
            if not search_text.strip():
                continue
            engine = MultiRecallEngine()
            recall_results = engine.recall(
                query=search_text[:2000],
                collection=chroma_collection,
                top_k=15,
                tenant=chroma_tenant,
                database=chroma_database,
                file_id=None,
            )
            for rr in recall_results:
                if rr.get("text") and len(rr["text"].strip()) > 20:
                    # 过滤：只保留已启用的文件
                    src = rr.get("source", {}) or {}
                    fid = src.get("file_id")
                    fname = src.get("file_name", "")
                    if fid is not None:
                        try:
                            if int(fid) not in enabled_file_ids:
                                continue
                        except (TypeError, ValueError):
                            pass
                    if fname and fname not in enabled_file_names:
                        continue
                    snippets.append(rr["text"].strip())
        except Exception as exc:
            logger.warning("[kb] 知识库查询异常: %s", exc)
        
        if snippets:
            context["knowledge_list"].append({
                "knowledge_base_name": kb.name,
                "tenant": chroma_tenant,
                "database": chroma_database,
                "collection": chroma_collection,
                "snippets": snippets,
            })
    return context


def _knowledge_base_snippet_allowed(metadata, enabled_file_ids, enabled_file_names):
    if not metadata:
        return False
    file_id = metadata.get("file_id")
    if file_id is not None:
        try:
            return int(file_id) in enabled_file_ids
        except (TypeError, ValueError):
            pass
    file_name = str(metadata.get("file_name") or "").strip()
    if file_name:
        return file_name in enabled_file_names
    return False


def _build_product_context(task):
    """构建产品库检索得到的产品上下文。"""
    if not task.use_product_library:
        return {}
    analysis_result = BiddingAnalysisResult.query.filter_by(shared_resource_id=task.shared_resource_id).first()
    requirements = analysis_result.technical_requirements if analysis_result else ""
    effective = analysis_result.effective_text if analysis_result and analysis_result.effective_text else (analysis_result.raw_text if analysis_result else "")
    
    terms = _extract_product_terms(effective + "\n" + (requirements or ""))
    if not terms:
        return {"snippets": [], "matched_products": []}
    
    try:
        chroma_tenant = current_app.config.get("CHROMA_TENANT")
        chroma_database = current_app.config.get("CHROMA_DATABASE")
        adapter = ChromaAdapter(
            host=current_app.config.get("CHROMA_HOST"),
            port=current_app.config.get("CHROMA_PORT"),
            tenant=chroma_tenant,
            database=chroma_database,
        )
        engine = MultiRecallEngine()
        recall_results = engine.recall(
            query=" ".join(terms),
            collection="product_library",
            top_k=10,
            tenant=chroma_tenant,
            database=chroma_database,
        )
        matched = []
        for rr in recall_results:
            if rr.get("text") and len(rr["text"].strip()) > 10:
                matched.append({"query_term": terms[0] if terms else "", "matched_text": rr["text"].strip()})
        return {"matched_products": matched, "product_terms": terms, "snippets": [m["matched_text"] for m in matched]}
    except Exception:
        return {"snippets": [], "matched_products": [], "product_terms": terms}


def _extract_product_terms(text):
    """从当前有效分析文本中抽取产品项关键词。"""
    if not text:
        return []
    terms = []
    for match in re.finditer(r"(?:采购|供货|产品|设备|系统|服务)[：:：\s]*([^\s，。,\.]{2,30})", text):
        term = match.group(1).strip()
        if term and len(term) >= 2:
            terms.append(term)
    return list(dict.fromkeys(terms))[:10]


def _get_bid_type_prompt_profile(bid_type, title):
    """根据标书类型生成对应的提示词模板配置。"""
    profiles = {
        "GOODS": {
            "fallback_focus": "重点响应产品规格参数、技术指标、供货范围、质量标准、验收方法、包装运输、售后服务等货物采购核心要素。",
        },
        "SERVICE": {
            "fallback_focus": "重点响应服务范围、服务方案、实施计划、团队配置、服务承诺、SLA保障、质量控制、沟通机制等服务采购核心要素。",
        },
        "ENGINEERING": {
            "fallback_focus": "重点响应施工组织设计、技术方案、资源配置、工期计划、质量安全保证措施、项目管理人员配置等工程采购核心要素。",
        },
    }
    return profiles.get(bid_type, {"fallback_focus": "综合响应招标文件各项要求。"})


def _build_subject_material_context(subject_id):
    """汇总主体资料文本，生成主体相关上下文。"""
    if not subject_id:
        return {}
    subject = db.session.get(SubjectCompany, subject_id)
    if not subject:
        return {}
    materials = SubjectMaterialFile.query.filter_by(subject_id=subject_id).order_by(SubjectMaterialFile.uploaded_at.asc()).all()
    material_labels = {
        "BUSINESS_LICENSE": "营业执照",
        "QUALIFICATION_FILE": "资质文件",
        "LEGAL_PERSON_ID_CARD": "法人身份证",
        "AUTHORIZATION_LETTER": "授权委托书",
        "AUTHORIZED_PERSON_ID_CARD": "被授权人身份证",
        "QUALIFICATION_DECLARATION": "资质声明函",
        "LEGAL_PERSON_STATEMENT": "法定代表人身份证明",
        "FINANCIAL_STATEMENT": "财务报表",
        "INTEGRITY_COMMITMENT": "廉洁承诺书",
    }
    items = []
    for m in materials:
        label = material_labels.get(m.material_type, m.material_type or "其他资料")
        file_record = db.session.get(FileStorage, m.file_id) if m.file_id else None
        text_excerpt = ""
        if file_record:
            try:
                text_excerpt = (_read_file_text(file_record) or "")[:800]
            except Exception as exc:
                logger.warning("[subject] 读取主体资料文本失败 material=%s file=%s: %s", m.id, m.file_id, exc)
        items.append(
            {
                "id": m.id,
                "file_id": m.file_id,
                "material_type": m.material_type,
                "material_label": label,
                "file_name": m.file_name or "",
                "file_ext": (file_record.file_ext or "") if file_record else "",
                "storage_provider": (file_record.storage_provider or "") if file_record else "",
                "text_excerpt": text_excerpt,
            }
        )
    return {
        "company_name": subject.company_name or "",
        "credit_code": subject.credit_code or "",
        "materials": items,
    }


def _extract_outline_leaf_titles(children, prefix_titles=None):
    leaf_titles = []
    for child in children or []:
        title = (child.get("title") or "").strip()
        if not title:
            continue
        current_path = [*(prefix_titles or []), title]
        nested_children = child.get("children", []) or []
        if nested_children:
            leaf_titles.extend(_extract_outline_leaf_titles(nested_children, current_path))
        else:
            leaf_titles.append(" / ".join(current_path))
    return leaf_titles


def _chapter_requires_evidence_placeholder(chapter):
    text = f"{chapter.get('title', '')} {chapter.get('description', '')}".strip()
    strict_phrases = (
        "资格审查资料",
        "资格证明",
        "资质证明",
        "授权文件",
        "授权委托",
        "营业执照",
        "身份证明",
    )
    return any(phrase in text for phrase in strict_phrases)


def _chapter_has_supporting_material(subject_context, knowledge_contexts, product_context):
    if subject_context and any((item.get("text_excerpt") or "").strip() for item in subject_context.get("materials", [])):
        return True
    if knowledge_contexts and knowledge_contexts.get("knowledge_list"):
        for kb in knowledge_contexts.get("knowledge_list", []):
            if any((snippet or "").strip() for snippet in kb.get("snippets", [])):
                return True
    if product_context and any((item.get("matched_text") or "").strip() for item in product_context.get("matched_products", [])):
        return True
    return False


def _split_generated_sections_by_titles(content_text, titles):
    if not content_text or not titles:
        return {}
    lines = [line.strip() for line in str(content_text or "").splitlines()]
    positions = []
    normalized_titles = {re.sub(r"\s+", "", title): title for title in titles if title}
    for index, line in enumerate(lines):
        normalized_line = re.sub(r"\s+", "", line)
        if normalized_line in normalized_titles:
            positions.append((index, normalized_titles[normalized_line]))
    if not positions:
        return {}
    sections = {}
    for position, (start_index, title) in enumerate(positions):
        end_index = positions[position + 1][0] if position + 1 < len(positions) else len(lines)
        body_lines = [line for line in lines[start_index + 1 : end_index] if line]
        if body_lines:
            sections[title] = "\n".join(body_lines).strip()
    return sections


def _truncate_binding_text(text, max_length=180):
    normalized = " ".join(str(text or "").replace("\r", "\n").split())
    return normalized[:max_length].strip()


def _build_leaf_response_bindings(chapter, analysis_context, subject_context, knowledge_contexts, product_context):
    children = chapter.get("children", []) or []
    if not children:
        return []

    bidder_notice = analysis_context.get("bidder_notice", {}) or {}
    qualification_review = analysis_context.get("qualification_review", {}) or {}
    project_summary = "；".join(
        item
        for item in [
            f"标的名称：{bidder_notice.get('project_name', '').strip()}" if bidder_notice.get("project_name") else "",
            f"项目编号：{bidder_notice.get('project_no', '').strip()}" if bidder_notice.get("project_no") else "",
            f"项目概况：{bidder_notice.get('overview', '').strip()}" if bidder_notice.get("overview") else "",
        ]
        if item
    )
    subject_snippets = [
        f"{item.get('material_label', '')}：{_truncate_binding_text(item.get('text_excerpt', ''), 120)}"
        for item in (subject_context or {}).get("materials", [])
        if (item.get("text_excerpt") or "").strip()
    ]
    kb_snippets = []
    for kb in (knowledge_contexts or {}).get("knowledge_list", []):
        for snippet in kb.get("snippets", [])[:10]:
            if (snippet or "").strip():
                kb_snippets.append(_truncate_binding_text(snippet, 120))
    product_snippets = [
        _truncate_binding_text(item.get("matched_text", ""), 120)
        for item in (product_context or {}).get("matched_products", [])[:3]
        if (item.get("matched_text") or "").strip()
    ]

    bindings = []
    for child in children:
        child_title = (child.get("title") or "").strip()
        child_desc = (child.get("description") or "").strip()
        if not child_title:
            continue

        evidence = []
        combined_text = f"{child_title} {child_desc}"
        if any(keyword in combined_text for keyword in ("项目", "标的", "采购范围", "概述")) and project_summary:
            evidence.append(project_summary)
        if any(keyword in combined_text for keyword in ("技术", "参数", "实施", "交付", "部署")):
            for text in [analysis_context.get("technical_requirements", ""), analysis_context.get("requirements", "")]:
                if text:
                    evidence.append(_truncate_binding_text(text))
        if any(keyword in combined_text for keyword in ("商务", "履约", "交货", "售后")):
            if analysis_context.get("business_requirements"):
                evidence.append(_truncate_binding_text(analysis_context.get("business_requirements", "")))
        if any(keyword in combined_text for keyword in ("资格", "资质", "授权", "证明", "审查")):
            for text in [
                analysis_context.get("qualification_requirements", ""),
                qualification_review.get("qualification_check", ""),
                qualification_review.get("conformity_check", ""),
            ]:
                if text:
                    evidence.append(_truncate_binding_text(text))
            evidence.extend(subject_snippets[:3])
        if "评分" in combined_text:
            if analysis_context.get("scoring_items"):
                evidence.append(_truncate_binding_text(analysis_context.get("scoring_items", "")))
        if "废标" in combined_text:
            for text in [analysis_context.get("disqualification_items", ""), qualification_review.get("disqualification_items", "")]:
                if text:
                    evidence.append(_truncate_binding_text(text))
        if not evidence:
            evidence.extend(kb_snippets[:2])
        if not evidence:
            evidence.extend(product_snippets[:2])

        unique_evidence = []
        for item in evidence:
            normalized = (item or "").strip()
            if normalized and normalized not in unique_evidence:
                unique_evidence.append(normalized)
        bindings.append(
            {
                "title": child_title,
                "requirement": child_desc,
                "evidence": unique_evidence[:3],
                "status": "COVERED" if unique_evidence else "PENDING",
                "require_blank": _chapter_requires_evidence_placeholder(child) and not unique_evidence,
            }
        )
    return bindings


def _compose_leaf_binding_body(binding):
    if binding.get("require_blank"):
        return _EMPTY_PAGE_MARKER

    lines = []
    if binding.get("requirement"):
        lines.append(f"招标要求：{binding['requirement']}")
    evidence = binding.get("evidence", [])
    if evidence:
        lines.append("现有依据如下：")
        for item in evidence:
            lines.append(item)
    elif binding.get("status") == "PENDING":
        lines.append("本节按招标文件要求预留位置，当前未检索到可直接填充的支撑资料。")
    return "\n".join(lines).strip()


def _normalize_chapter_content_by_bindings(content_text, bindings):
    if not bindings:
        return (content_text or "").strip()

    titles = [item["title"] for item in bindings]
    existing_sections = _split_generated_sections_by_titles(content_text, titles)
    sections = []
    for binding in bindings:
        body = (existing_sections.get(binding["title"]) or "").strip()
        if not body:
            body = _compose_leaf_binding_body(binding)
        sections.append(binding["title"])
        if body:
            sections.append(body)
    return "\n".join(sections).strip()


def _extract_binding_body_from_content(content_text, title):
    sections = _split_generated_sections_by_titles(content_text, [title])
    return (sections.get(title) or "").strip()


def _build_generation_coverage_snapshot(
    outline,
    chapter_contents,
    analysis_context,
    subject_context,
    knowledge_contexts,
    product_context,
    generation_plan=None,
):
    source_files = analysis_context.get("source_files", []) if isinstance(analysis_context, dict) else []
    tender_files = [item.get("file_name") for item in source_files if item.get("file_role") == "TENDER" and item.get("file_name")]
    attachment_files = [item.get("file_name") for item in source_files if item.get("file_role") == "ATTACHMENT" and item.get("file_name")]
    source_reference_parts = []
    if tender_files:
        source_reference_parts.append(f"主招标文件：{'、'.join(dict.fromkeys(tender_files))}")
    if attachment_files:
        source_reference_parts.append(f"招标附件：{'、'.join(dict.fromkeys(attachment_files))}")
    source_reference = "；".join(source_reference_parts)

    chapter_map = {}
    for chapter in chapter_contents or []:
        chapter_title = (chapter.get("title") or "").strip()
        if chapter_title:
            chapter_map[chapter_title] = (chapter.get("content") or "").strip()

    plan_lookup = {}
    for item in (generation_plan or {}).get("plan_items", []) or []:
        key = ((item.get("chapter_title") or "").strip(), (item.get("target_title") or "").strip())
        if key[0] and key[1]:
            plan_lookup[key] = item

    requirement_items = []
    for chapter in outline or []:
        chapter_title = (chapter.get("title") or "").strip()
        chapter_content = chapter_map.get(chapter_title, "")
        bindings = _build_leaf_response_bindings(
            chapter,
            analysis_context,
            subject_context,
            knowledge_contexts,
            product_context,
        )
        if bindings:
            for binding in bindings:
                body = _extract_binding_body_from_content(chapter_content, binding["title"])
                covered = bool(body and body != _EMPTY_PAGE_MARKER)
                requirement_items.append(
                    {
                        "chapter_title": chapter_title,
                        "target_title": binding["title"],
                        "requirement": binding.get("requirement", ""),
                        "status": "MISSING" if binding.get("require_blank") else ("COVERED" if covered else "PENDING"),
                        "has_evidence": bool(binding.get("evidence")),
                        "source_reference": source_reference,
                        "requirement_level": plan_lookup.get((chapter_title, binding["title"]), {}).get("requirement_level", "NORMAL"),
                        "original_requirement_excerpt": plan_lookup.get((chapter_title, binding["title"]), {}).get(
                            "original_requirement_excerpt", ""
                        ),
                    }
                )
            continue

        chapter_body = (chapter_content or "").strip()
        if chapter_body:
            chapter_body = re.sub(rf"^{re.escape(chapter_title)}\s*", "", chapter_body).strip()
        requirement_items.append(
            {
                "chapter_title": chapter_title,
                "target_title": chapter_title,
                "requirement": chapter.get("description", "") or "",
                "status": "COVERED" if chapter_body and chapter_body != _EMPTY_PAGE_MARKER else "PENDING",
                "has_evidence": bool(chapter_body and chapter_body != _EMPTY_PAGE_MARKER),
                "source_reference": source_reference,
                "requirement_level": plan_lookup.get((chapter_title, chapter_title), {}).get("requirement_level", "NORMAL"),
                "original_requirement_excerpt": plan_lookup.get((chapter_title, chapter_title), {}).get(
                    "original_requirement_excerpt", ""
                ),
            }
        )

    total = len(requirement_items)
    covered_count = sum(1 for item in requirement_items if item["status"] == "COVERED")
    missing_items = [item for item in requirement_items if item["status"] != "COVERED"]
    # 从 generation_plan 补充原子要求覆盖率
    atomic_total = 0
    atomic_covered = 0
    if generation_plan and isinstance(generation_plan, dict):
        plan_items = generation_plan.get("plan_items", []) or []
        atomic_total = generation_plan.get("total_atomic_requirements", len(plan_items))
        # 检查每个 plan_item 在 requirement_items 中的覆盖情况
        for plan_item in plan_items:
            pt = plan_item.get("target_title", "")
            pc = plan_item.get("chapter_title", "")
            for req_item in requirement_items:
                if req_item.get("target_title") == pt and req_item.get("chapter_title") == pc:
                    if req_item.get("status") == "COVERED":
                        atomic_covered += 1
                    break

    return {
        "generated_at": utc_now().isoformat(),
        "total_requirements": total,
        "covered_requirements": covered_count,
        "missing_requirements": len(missing_items),
        "coverage_ratio": round((covered_count / total), 4) if total else 1.0,
        "missing_items": missing_items,
        "requirement_items": requirement_items,
        "atomic_requirements": {
            "total": atomic_total,
            "covered": atomic_covered,
            "missing": atomic_total - atomic_covered,
        },
    }


def _build_analysis_source_reference(analysis_context):
    source_files = analysis_context.get("source_files", []) if isinstance(analysis_context, dict) else []
    tender_files = [item.get("file_name") for item in source_files if item.get("file_role") == "TENDER" and item.get("file_name")]
    attachment_files = [item.get("file_name") for item in source_files if item.get("file_role") == "ATTACHMENT" and item.get("file_name")]
    source_reference_parts = []
    if tender_files:
        source_reference_parts.append(f"主招标文件：{'、'.join(dict.fromkeys(tender_files))}")
    if attachment_files:
        source_reference_parts.append(f"招标附件：{'、'.join(dict.fromkeys(attachment_files))}")
    return "；".join(source_reference_parts)


def _split_requirement_units(text, max_items=8):
    normalized = str(text or "").replace("\r", "\n")
    parts = re.split(r"[\n；;。]", normalized)
    units = []
    for part in parts:
        item = " ".join(part.split()).strip(" ；;。")
        if len(item) < 4:
            continue
        if item not in units:
            units.append(item)
        if len(units) >= max_items:
            break
    return units


def _build_atomic_requirement_items(analysis_context):
    bidder_notice = analysis_context.get("bidder_notice", {}) or {}
    qualification_review = analysis_context.get("qualification_review", {}) or {}
    source_reference = _build_analysis_source_reference(analysis_context)

    field_specs = [
        ("general", "NORMAL", analysis_context.get("requirements", ""), "招标要求"),
        ("business", "NORMAL", analysis_context.get("business_requirements", ""), "商务要求"),
        ("technical", "NORMAL", analysis_context.get("technical_requirements", ""), "技术要求"),
        ("qualification", "REQUIRED", analysis_context.get("qualification_requirements", ""), "资格性审查"),
        ("conformity", "REQUIRED", qualification_review.get("conformity_check", ""), "符合性审查"),
        ("scoring", "IMPORTANT", analysis_context.get("scoring_items", ""), "评分项"),
        ("disqualification", "REQUIRED", analysis_context.get("disqualification_items", ""), "废标项"),
    ]
    items = []
    item_index = 1

    bidder_notice_specs = [
        ("project_name", "标的名称"),
        ("project_no", "项目编号"),
        ("package_no", "包号"),
        ("budget", "预算金额"),
        ("tenderee", "招标人"),
        ("agent", "代理机构"),
    ]
    for key, label in bidder_notice_specs:
        value = str(bidder_notice.get(key, "") or "").strip()
        if not value:
            continue
        items.append(
            {
                "item_id": f"REQ-{item_index:03d}",
                "requirement_type": "basic_info",
                "requirement_level": "REQUIRED",
                "requirement_title": label,
                "requirement_text": f"{label}：{value}",
                "source_reference": source_reference,
            }
        )
        item_index += 1

    for requirement_type, level, text, title in field_specs:
        for unit in _split_requirement_units(text):
            items.append(
                {
                    "item_id": f"REQ-{item_index:03d}",
                    "requirement_type": requirement_type,
                    "requirement_level": level,
                    "requirement_title": title,
                    "requirement_text": unit,
                    "source_reference": source_reference,
                }
            )
            item_index += 1

    return items


def _select_requirement_items_for_target(target_title, target_desc, atomic_requirement_items):
    target_text = f"{target_title} {target_desc}".strip()
    if not target_text:
        return []

    keyword_groups = [
        ("technical", ("技术", "参数", "实施", "交付", "部署", "性能", "规格")),
        ("business", ("商务", "履约", "交货", "售后", "付款", "验收", "质保")),
        ("qualification", ("资格", "资质", "授权", "证明", "审查", "营业执照", "财务", "社保")),
        ("conformity", ("符合性", "格式", "签字", "盖章", "有效期")),
        ("scoring", ("评分", "打分", "评审")),
        ("disqualification", ("废标", "否决", "无效投标")),
        ("basic_info", ("项目", "标的", "编号", "包号", "概况", "采购范围")),
    ]
    selected_types = []
    for requirement_type, keywords in keyword_groups:
        if any(keyword in target_text for keyword in keywords):
            selected_types.append(requirement_type)
    if not selected_types:
        selected_types = ["general"]

    matched = [item for item in atomic_requirement_items if item.get("requirement_type") in selected_types]
    if not matched and "general" not in selected_types:
        matched = [item for item in atomic_requirement_items if item.get("requirement_type") == "general"]
    if not matched:
        matched = atomic_requirement_items[:2]
    return matched[:4]


def _build_generation_plan_snapshot(outline, analysis_context, subject_context, product_context):
    atomic_requirement_items = _build_atomic_requirement_items(analysis_context)
    plan_items = []

    for chapter in outline or []:
        chapter_title = (chapter.get("title") or "").strip()
        bindings = _build_leaf_response_bindings(
            chapter,
            analysis_context,
            subject_context,
            knowledge_contexts={},
            product_context=product_context,
        )
        if bindings:
            for binding in bindings:
                matched_items = _select_requirement_items_for_target(
                    binding.get("title", ""),
                    binding.get("requirement", ""),
                    atomic_requirement_items,
                )
                plan_items.append(
                    {
                        "chapter_title": chapter_title,
                        "target_title": binding.get("title", ""),
                        "target_requirement": binding.get("requirement", ""),
                        "binding_status": binding.get("status", "PENDING"),
                        "plan_action": "LEAVE_BLANK"
                        if binding.get("require_blank")
                        else ("FILL" if binding.get("evidence") else "REVIEW"),
                        "requirement_level": "REQUIRED"
                        if any(item.get("requirement_level") == "REQUIRED" for item in matched_items)
                        else "NORMAL",
                        "matched_requirement_items": matched_items,
                        "evidence_preview": binding.get("evidence", [])[:3],
                    }
                )
            continue

        matched_items = _select_requirement_items_for_target(
            chapter_title,
            chapter.get("description", ""),
            atomic_requirement_items,
        )
        require_blank = _chapter_requires_evidence_placeholder(chapter) and not _chapter_has_supporting_material(
            subject_context,
            {},
            product_context,
        )
        plan_items.append(
            {
                "chapter_title": chapter_title,
                "target_title": chapter_title,
                "target_requirement": chapter.get("description", ""),
                "binding_status": "PENDING" if require_blank else ("COVERED" if matched_items else "PENDING"),
                "plan_action": "LEAVE_BLANK" if require_blank else ("FILL" if matched_items else "REVIEW"),
                "requirement_level": "REQUIRED"
                if require_blank or any(item.get("requirement_level") == "REQUIRED" for item in matched_items)
                else "NORMAL",
                "matched_requirement_items": matched_items,
                "evidence_preview": [],
            }
        )

    pending_count = sum(1 for item in plan_items if item.get("plan_action") != "FILL")
    return {
        "generated_at": utc_now().isoformat(),
        "total_atomic_requirements": len(atomic_requirement_items),
        "total_targets": len(plan_items),
        "pending_targets": pending_count,
        "atomic_requirement_items": atomic_requirement_items,
        "plan_items": plan_items,
    }


def _enrich_generation_plan_with_original_excerpts(generation_plan, analysis_result):
    if not isinstance(generation_plan, dict):
        return generation_plan
    enriched_items = []
    for item in generation_plan.get("plan_items", []) or []:
        item_copy = dict(item)
        item_copy["matched_requirement_items"] = [dict(req) for req in item.get("matched_requirement_items", []) or []]
        enriched_items.append(_maybe_attach_original_excerpt(item_copy, analysis_result))
    generation_plan["plan_items"] = enriched_items
    return generation_plan


def _persist_generation_plan_snapshot(analysis_result, generation_plan):
    if not analysis_result:
        return
    payload = {}
    if getattr(analysis_result, "analysis_data", None):
        try:
            payload = json.loads(analysis_result.analysis_data)
        except (TypeError, json.JSONDecodeError):
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if not payload.get("version"):
        payload["version"] = "v2"
    payload["generation_plan"] = generation_plan
    analysis_result.analysis_data = json.dumps(payload, ensure_ascii=False)


def _get_generation_plan_snapshot(analysis_result):
    if not analysis_result or not getattr(analysis_result, "analysis_data", None):
        return {}
    try:
        payload = json.loads(analysis_result.analysis_data)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    generation_plan = payload.get("generation_plan", {})
    return generation_plan if isinstance(generation_plan, dict) else {}


def _extract_original_requirement_excerpt(analysis_result, requirement_texts):
    candidates = []
    for text in requirement_texts or []:
        normalized = " ".join(str(text or "").split()).strip("；;。")
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    if not candidates:
        return ""

    analysis_text = ""
    if analysis_result:
        analysis_text = str(
            getattr(analysis_result, "effective_text", "") or getattr(analysis_result, "raw_text", "") or ""
        )
    if not analysis_text.strip():
        return candidates[0][:180]

    units = [item.strip() for item in re.split(r"[\n。；;]", analysis_text) if item.strip()]
    if not units:
        return candidates[0][:180]

    for candidate in candidates:
        if candidate in analysis_text:
            for unit in units:
                if candidate in unit:
                    return unit[:180]

    keywords = []
    for candidate in candidates:
        keywords.extend([part for part in re.split(r"[\s,，、/]+", candidate) if len(part) >= 4])
    for keyword in keywords:
        for unit in units:
            if keyword in unit:
                return unit[:180]
    return candidates[0][:180]


def _maybe_attach_original_excerpt(plan_item, analysis_result):
    if not isinstance(plan_item, dict):
        return plan_item
    if plan_item.get("plan_action") != "LEAVE_BLANK" or plan_item.get("requirement_level") != "REQUIRED":
        return plan_item

    requirement_texts = []
    if plan_item.get("target_requirement"):
        requirement_texts.append(plan_item.get("target_requirement"))
    for item in plan_item.get("matched_requirement_items", []) or []:
        if item.get("requirement_text"):
            requirement_texts.append(item.get("requirement_text"))
    excerpt = _extract_original_requirement_excerpt(analysis_result, requirement_texts)
    if excerpt:
        plan_item["original_requirement_excerpt"] = excerpt
    return plan_item


def _persist_generation_coverage_snapshot(analysis_result, coverage_snapshot):
    if not analysis_result:
        return
    payload = {}
    if getattr(analysis_result, "analysis_data", None):
        try:
            payload = json.loads(analysis_result.analysis_data)
        except (TypeError, json.JSONDecodeError):
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if not payload.get("version"):
        payload["version"] = "v2"
    payload["generation_coverage"] = coverage_snapshot
    analysis_result.analysis_data = json.dumps(payload, ensure_ascii=False)


def _get_generation_coverage_snapshot(analysis_result):
    if not analysis_result or not getattr(analysis_result, "analysis_data", None):
        return {}
    try:
        payload = json.loads(analysis_result.analysis_data)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    coverage_snapshot = payload.get("generation_coverage", {})
    return coverage_snapshot if isinstance(coverage_snapshot, dict) else {}




def _verify_kb_citations(generated_content, knowledge_contexts):
    """验证生成内容中对知识库的引用是否真实存在。
    
    对生成内容中疑似引用知识库的段落，用 Chroma 反向检索验证。
    
    Args:
        generated_content: 生成的章节正文文本
        knowledge_contexts: 知识库上下文（含 snippets）
    
    Returns:
        dict: {verified: [引用文本列表], unverified: [未通过验证的引用文本列表]}
    """
    if not generated_content or not knowledge_contexts:
        return {"verified": [], "unverified": []}
    
    verified = []
    unverified = []
    
    # 收集知识库中的所有可用片段文本
    kb_snippets = []
    for kb in knowledge_contexts.get("knowledge_list", []):
        for snippet in kb.get("snippets", []):
            if snippet and len(snippet.strip()) > 20:
                kb_snippets.append(snippet.strip())
    
    if not kb_snippets:
        return {"verified": [], "unverified": []}
    
    # 在生成内容中查找疑似引用知识库的段落
    # 匹配模式：包含知识库文件名、"知识库"关键词、或与知识库片段相似的文本
    lines = generated_content.split("\n")
    for line in lines:
        stripped = line.strip()
        if not stripped or len(stripped) < 30:
            continue
        
        # 检查是否与知识库内容高度相似（说明是引用）
        for snippet in kb_snippets:
            # 计算简单重叠：如果行中的长句子出现在 snippet 中
            words = set(stripped.split())
            snippet_words = set(snippet.split())
            if len(words) > 5 and len(snippet_words) > 5:
                overlap = len(words & snippet_words)
                overlap_ratio = overlap / min(len(words), len(snippet_words))
                if overlap_ratio > 0.4:
                    verified.append(stripped[:200])
                    break
    
    return {"verified": verified, "unverified": unverified}


def _build_tender_chroma_context(task, chapter_title, chapter_desc=None):
    """从招标文件 Chroma 集合中检索与当前章节相关的原文片段（多路召回）。
    
    Args:
        task: BiddingTask 对象
        chapter_title: 当前章节标题
        chapter_desc: 当前章节描述
    
    Returns:
        list: 检索到的原文片段列表（每条带出处）
    """
    query_text = chapter_title
    if chapter_desc:
        query_text = f"{chapter_title} {chapter_desc}"
    
    collection = current_app.config.get("CHROMA_COLLECTION", "tender")
    tenant = current_app.config.get("CHROMA_TENANT", "erp")
    database = current_app.config.get("CHROMA_DATABASE", "bidding")
    
    try:
        engine = MultiRecallEngine()
        results = engine.recall(
            query=query_text[:2000],
            collection=collection,
            top_k=8,
            tenant=tenant,
            database=database,
        )
        snippets = [r["text"] for r in results if r.get("text") and len(r["text"].strip()) > 20]
        logger.info("[tender_chroma] 多路召回完成: title=%s, snippets=%s", chapter_title, len(snippets))
        return snippets
    except Exception as exc:
        logger.warning("[tender_chroma] 招标文件检索异常: %s", exc)
        return []


def _generate_chapter_content(task, chapter, analysis_result, subject_context, knowledge_contexts, product_context):
    """调用模型生成单个章节的详细正文内容。"""
    bid_type_label_map = {"GOODS": "\u8d27\u7269\u7c7b", "SERVICE": "\u670d\u52a1\u7c7b", "ENGINEERING": "\u5de5\u7a0b\u7c7b"}
    bid_type_label = bid_type_label_map.get(task.bid_type, "\u8d27\u7269\u7c7b")
    chapter_title = chapter.get("title", "").strip()
    chapter_desc = chapter.get("description", "") or ""

    effective_text = analysis_result.effective_text if analysis_result and analysis_result.effective_text else (analysis_result.raw_text if analysis_result else "\u6682\u65e0\u62db\u6807\u4f9d\u636e\u6587\u672c\u3002")
    analysis_context = _extract_analysis_context(analysis_result)

    catalog_profile = _get_catalog_generation_profile(task.catalog_generation_level)
    word_profile = _get_word_count_profile(task.word_count_level)

    selected_package_no = getattr(task, "selected_package_no", None) or ""

    children = chapter.get("children", [])
    leaf_bindings = _build_leaf_response_bindings(
        chapter,
        analysis_context,
        subject_context,
        knowledge_contexts,
        product_context,
    )

    if _chapter_requires_evidence_placeholder(chapter) and not _chapter_has_supporting_material(
        subject_context,
        knowledge_contexts,
        product_context,
    ):
        return _EMPTY_PAGE_MARKER

    system_prompt = (
        "\u4f60\u662f\u4e00\u540d\u6295\u6807\u6587\u4ef6\u5185\u5bb9\u7f16\u6392\u52a9\u624b\uff0c\u4e0d\u662f\u81ea\u7531\u521b\u4f5c\u52a9\u624b\u3002" + "\n\n"
        "\u8bf7\u57fa\u4e8e\u7ed9\u5b9a\u7684\u76ee\u5f55\u7ae0\u8282\u8bf4\u660e\u3001\u62db\u6807\u9700\u6c42\u4f9d\u636e\u3001\u6295\u6807\u4e3b\u4f53\u8d44\u6599\uff0c" + "\n"
        "\u53ea\u5bf9\u5df2\u7ecf\u63d0\u4f9b\u7684\u5185\u5bb9\u505a\u7ed3\u6784\u5316\u6574\u7406\u4e0e\u54cd\u5e94\uff0c\u4e0d\u5f97\u7f16\u9020\u3001\u4e0d\u5f97\u8865\u5199\u672a\u63d0\u4f9b\u7684\u627f\u8bfa\u6216\u80fd\u529b\u3002" + "\n\n"
        "\u4ee5\u4e0b\u8981\u6c42\u8bf7\u4e25\u683c\u9075\u5b88\uff1a" + "\n"
        "1. \u6b63\u6587\u5185\u5bb9\u5fc5\u987b\u7d27\u7d27\u56f4\u7ed5\u7ae0\u8282\u6807\u9898\u548c\u8bf4\u660e\u5c55\u5f00\uff0c\u4e0d\u53ef\u504f\u79bb\u4e3b\u9898\u3002" + "\n"
        "2. \u4f18\u5148\u5c55\u793a\u62db\u6807\u6587\u4ef6\u660e\u786e\u8981\u6c42\u7684\u5185\u5bb9\u548c\u5df2\u786e\u8ba4\u8d44\u6599\uff0c\u5b81\u7f3a\u6bef\u6ee5\u3002" + "\n"
        "3. \u5982\u6750\u6599\u4e0d\u8db3\u4ee5\u652f\u6491\u5b9e\u8d28\u6027\u627f\u8bfa\uff0c\u8bf7\u4ec5\u6574\u7406\u5df2\u63d0\u4f9b\u7684\u8981\u6c42\u6216\u4e8b\u5b9e\uff0c\u4e0d\u5f97\u81ea\u884c\u6269\u5c55\u3002" + "\n"
        "4. \u4e0d\u8981\u4f7f\u7528 Markdown \u8bed\u6cd5\u6807\u8bb0\uff08\u5982 #\u3001##\u3001**\u3001*\u3001-\u5217\u8868\u3001```\u3001| \u8868\u683c\u7ebf\u7b49\uff09\u3002" + "\n"
        "5. \u53ea\u8f93\u51fa\u7eaf\u4e2d\u6587\u6b63\u6587\u5185\u5bb9\uff0c\u4e0d\u8981\u91cd\u590d\u8f93\u51fa\u9876\u7ea7\u7ae0\u8282\u6807\u9898\uff0c\u4e0d\u8981\u8f93\u51fa\u89e3\u91ca\u6027\u6587\u5b57\u3002" + "\n"
        "6. \u6b63\u6587\u4f7f\u7528\u89c4\u8303\u7684\u4e66\u9762\u8bed\uff0c\u6bb5\u843d\u4e4b\u95f4\u7528\u7a7a\u884c\u5206\u9694\u3002" + "\n"
        "7. \u6b63\u6587\u4e2d\u5f15\u7528\u7684\u6295\u6807\u4e3b\u4f53\u540d\u79f0\u4f7f\u7528\u516c\u53f8\u5168\u79f0\u3002"
    )

    user_parts = []
    user_parts.append(f"\u7ae0\u8282\u6807\u9898\uff1a{chapter_title}")
    user_parts.append(f"\u7ae0\u8282\u8bf4\u660e\uff1a{chapter_desc or chapter_title}")
    user_parts.append(f"\u6807\u4e66\u7c7b\u578b\uff1a{bid_type_label}")

    if selected_package_no:
        user_parts.append(
            f"\u5206\u5305\u4fe1\u606f\uff1a\u672c\u9879\u76ee\u6709\u5206\u5305\uff0c\u5f53\u524d\u5305\u53f7\u4e3a {selected_package_no}\u3002"
            f"\u5185\u5bb9\u53ea\u80fd\u56f4\u7ed5\u5f53\u524d\u5305\u53f7\u7684\u9700\u6c42\u7f16\u5199\uff0c"
            f"\u4e0d\u5f97\u63d0\u53ca\u5176\u4ed6\u5305\u53f7\u7684\u5185\u5bb9\u3002"
        )

        # \u622a\u53d6\u6709\u6548\u6587\u672c\u65f6\u53ea\u4fdd\u7559\u5f53\u524d\u5305\u7684\u5185\u5bb9
        filtered_text = _extract_effective_text(effective_text, selected_package_no)
        if filtered_text:
            effective_text = filtered_text

    user_parts.append(f"\u5199\u4f5c\u6307\u5bfc\uff1a{catalog_profile['directive']}")
    user_parts.append(f"\u7bc7\u5e45\u63a7\u5236\uff1a{word_profile['instruction']}")
    user_parts.append("\u7f16\u5199\u539f\u5219\uff1a\u4e25\u683c\u4f9d\u636e\u5df2\u63d0\u4f9b\u6750\u6599\u7ec4\u7ec7\u5185\u5bb9\uff0c\u4e0d\u5f97\u7f16\u9020\u3002\u5982\u67d0\u9879\u65e0\u652f\u6491\u8d44\u6599\uff0c\u5b81\u53ef\u4fdd\u6301\u7b80\u7565\uff0c\u4e5f\u4e0d\u8981\u8865\u5199\u627f\u8bfa\u3002")
    bidder_notice = analysis_context.get("bidder_notice", {}) or {}
    if bidder_notice:
        info_lines = []
        if bidder_notice.get("project_name"):
            info_lines.append(f"项目名称：{bidder_notice['project_name']}")
        if bidder_notice.get("project_no"):
            info_lines.append(f"项目编号：{bidder_notice['project_no']}")
        if bidder_notice.get("budget"):
            info_lines.append(f"预算：{bidder_notice['budget']}")
        if bidder_notice.get("tenderee"):
            info_lines.append(f"招标人：{bidder_notice['tenderee']}")
        if bidder_notice.get("agent"):
            info_lines.append(f"代理机构：{bidder_notice['agent']}")
        if bidder_notice.get("overview"):
            info_lines.append(f"项目概况：{bidder_notice['overview']}")
        if info_lines:
            user_parts.append("\n结构化项目信息：")
            user_parts.extend(info_lines)
    if analysis_context.get("business_requirements"):
        user_parts.append(f"\n结构化商务要求：\n{analysis_context['business_requirements'][:1200]}")
    if analysis_context.get("technical_requirements"):
        user_parts.append(f"\n结构化技术要求：\n{analysis_context['technical_requirements'][:1200]}")
    if analysis_context.get("qualification_requirements"):
        user_parts.append(f"\n结构化资格性审查：\n{analysis_context['qualification_requirements'][:1200]}")
    qualification_review = analysis_context.get("qualification_review", {}) or {}
    if qualification_review.get("conformity_check"):
        user_parts.append(f"\n结构化符合性审查：\n{qualification_review['conformity_check'][:1200]}")
    if analysis_context.get("disqualification_items"):
        user_parts.append(f"\n结构化废标项：\n{analysis_context['disqualification_items'][:1200]}")
    if analysis_context.get("scoring_items"):
        user_parts.append(f"\n结构化评分标准：\n{analysis_context['scoring_items'][:1200]}")
    # 追加子项信息到提示词
    if children:
        leaf_titles = _extract_outline_leaf_titles(children)
        user_parts.append("\n该章节应包含以下具体子项及响应要求：")
        for child in children:
            child_title = child.get("title", "").strip()
            child_desc = child.get("description", "").strip()
            if child_title:
                if child_desc:
                    user_parts.append(f"  - {child_title}：{child_desc[:200]}")
                else:
                    user_parts.append(f"  - {child_title}")
        user_parts.append("\n以上子项的具体内容需在正文中逐一覆盖，按顺序展开说明，不可遗漏。")
        if leaf_titles:
            user_parts.append("请按以下子项标题分别成段输出正文，小标题必须与目录子项标题保持一致：")
            for leaf_title in leaf_titles:
                user_parts.append(f"- {leaf_title}")
        if leaf_bindings:
            user_parts.append("\n以下是系统整理出的子项绑定清单，请严格按子项逐一响应：")
            for binding in leaf_bindings:
                user_parts.append(
                    f"- {binding['title']} | 状态：{binding['status']} | 要求：{binding.get('requirement', '') or '未提取到明确要求'}"
                )
                if binding.get("evidence"):
                    for item in binding["evidence"]:
                        user_parts.append(f"  依据：{item}")
                elif binding.get("require_blank"):
                    user_parts.append("  依据：未检索到可用资料，需保留空白。")
                else:
                    user_parts.append("  依据：未检索到直接证据，仅保留招标要求本身。")

    # 改用招标文件 Chroma 语义检索替代原文截断（多路召回）
    tender_snippets = _build_tender_chroma_context(task, chapter_title, chapter_desc)
    
    # 注入质量保证约束（需求追踪矩阵）
    try:
        from ..quality_assurance import inject_constraints_into_prompt
        analysis_result = BiddingAnalysisResult.query.filter_by(shared_resource_id=task.shared_resource_id).first()
        if analysis_result and analysis_result.analysis_data:
            ad = json.loads(analysis_result.analysis_data) if isinstance(analysis_result.analysis_data, str) else analysis_result.analysis_data
            matrix = ad.get("requirement_traceability_matrix", {}) if isinstance(ad, dict) else {}
            if matrix and matrix.get("requirements"):
                constraints = inject_constraints_into_prompt(chapter_title, chapter_desc or "", matrix, {"bindings": []})
                if constraints.get("tier1_items") or constraints.get("hard_constraints"):
                    user_parts.append("\n=== 质量保证约束 ===")
                    if constraints["tier1_items"]:
                        user_parts.append("\n[第一层] 主体已有材料（必须引用）：")
                        for item in constraints["tier1_items"]:
                            src = item.get("evidence_source", {}) or {}
                            user_parts.append(f"  ✅ {item['requirement_text'][:80]} → 材料: {src.get('file_name', '')}")
                    if constraints["hard_constraints"]:
                        user_parts.append("\n[硬约束] 废标项（全程不可违反）：")
                        for item in constraints["hard_constraints"]:
                            user_parts.append(f"  🔴 {item['requirement_text'][:80]}")
    except Exception as exc:
        logger.warning("[qa] 约束注入失败: %s", exc)
    
    if tender_snippets:
        user_parts.append("\n招标需求依据（招标文件原文检索）：")
        for idx, snippet in enumerate(tender_snippets[:8], start=1):
            user_parts.append(f"[原文片段 {idx}] {snippet[:800]}")
    else:
        # 降级：使用 effective_text 前 800 字符
        user_parts.append(f"\n招标需求依据（有效文本）：\n{effective_text[:800]}")

    if subject_context:
        company = subject_context.get("company_name", "")
        user_parts.append(f"\n\u6295\u6807\u4e3b\u4f53\uff1a{company}")
        for mat in subject_context.get("materials", []):
            user_parts.append(f"- [{mat['material_label']}] {mat['file_name']}")
            if mat.get("text_excerpt"):
                user_parts.append(f"  资料摘录：{mat['text_excerpt'][:200]}")

    if knowledge_contexts and knowledge_contexts.get("knowledge_list"):
        for kb in knowledge_contexts["knowledge_list"]:
            user_parts.append(f"\n\u77e5\u8bc6\u5e93\u53c2\u8003 [{kb.get('knowledge_base_name', '')}]:")
            for snip in kb.get("snippets", [])[:5]:
                user_parts.append(f"  - {snip[:300]}")

    if product_context:
        terms = product_context.get("product_terms", [])
        if terms:
            user_parts.append(f"\n产品项抽取：" + "、".join(terms[:8]) + "")
        for mp in product_context.get("matched_products", [])[:3]:
            user_parts.append(f"  {mp.get('query_term', '')} -> {mp.get('matched_text', '')[:200]}")

    user_parts.append("\n\u8bf7\u76f4\u63a5\u8f93\u51fa\u7ae0\u8282\u6b63\u6587\u5185\u5bb9\uff0c\u4e0d\u8981\u8f93\u51fa\u89e3\u91ca\u548c\u5176\u4ed6\u6807\u9898\u3002")
    user_prompt = "\n".join(user_parts)

    if current_app.config.get("FLASK_ENV") == "TESTING":
        _maybe_fail_chapter_for_testing(int(chapter.get("chapter_no", 0)))
        return f"\u3010\u6d4b\u8bd5\u5185\u5bb9\u3011{chapter_title} \u7684\u6a21\u62df\u6b63\u6587\u3002"

    adapter = LLMAdapter(
        api_key=current_app.config.get("OPENAI_API_KEY"),
        base_url=current_app.config.get("OPENAI_BASE_URL"),
        default_model=current_app.config.get("OPENAI_MODEL_NAME"),
    )
    if not adapter.is_available():
        raise RuntimeError("LLM \u670d\u52a1\u4e0d\u53ef\u7528")

    temperature = current_app.config.get("LLM_TEMPERATURE", 0.4)
    # \u786e\u4fdd\u6bcf\u7ae0\u81f3\u5c11\u67092000 tokens
    max_tokens = max(int(word_profile.get("max_tokens", 1500)), 2000)

    raw = adapter.generate_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=float(temperature),
        max_tokens=int(max_tokens),
    )
    return _normalize_chapter_content_by_bindings(raw, leaf_bindings)


def _read_file_text(file_record):
    """读取文件记录内容并解析为文本。
    
    根据存储方式选择读取路径：
    - doc_parse_cache（优先，上传阶段已同步写入）
    - CHROMA → 从 ChromaDB 按 chunk_id 读取并拼接
    - MINIO  → 从 MinIO 下载后解析
    - LOCAL  → 从本地文件读取后解析
    """
    if not file_record:
        return ""

    # 0. 优先从doc_parse_cache读取（上传阶段已同步写入）
    cached_text = StorageService.read_parsed_text(file_record.id)
    if cached_text:
        return cached_text

    # 1. CHROMA 存储：直接按 chroma_doc_id 从向量库读取
    if file_record.storage_provider in ("CHROMA", "CHROMA_MANAGED"):
        return _read_text_from_chroma(file_record)

    # 2. MINIO / LOCAL 存储：先下载文件再解析
    try:
        payload = StorageService.read_bytes(file_record)
        if payload:
            parser = DocumentParser()
            text = parser.parse_bytes(file_record.file_name or "未知文件", payload)
            if text:
                return text
    except Exception as exc:
        logger.warning("[helpers] MinIO/本地文件读取失败，尝试 ChromaDB 降级: %s", exc)

    # 3. 降级：尝试从 ChromaDB 读取
    return _read_text_from_chroma(file_record)


def _read_text_from_chroma(file_record):
    """从 ChromaDB 按 document_id 读取文件的所有 chunks 并拼接。
    支持 chroma_doc_id 格式: "document_id" (同步上传) 或 "document_id||task_id" (异步上传)。
    """
    chroma_doc_id = getattr(file_record, "chroma_doc_id", None)
    if not chroma_doc_id:
        return ""
    chroma_collection = file_record.chroma_collection or current_app.config.get("CHROMA_COLLECTION", "tender")
    raw = str(chroma_doc_id).strip()
    if not raw:
        return ""
    # 异步上传时 chroma_doc_id = document_id||task_id，提取 document_id
    document_id = raw.split("||")[0] if "||" in raw else raw
    try:
        adapter = ChromaAdapter(
            host=current_app.config.get("CHROMA_HOST"),
            port=current_app.config.get("CHROMA_PORT"),
            tenant=current_app.config.get("CHROMA_TENANT"),
            database=current_app.config.get("CHROMA_DATABASE"),
        )
        result = adapter.get_file_documents(chroma_collection, document_id)
        if result and result.get("documents"):
            docs = result["documents"]
            if isinstance(docs, list) and docs:
                text_parts = []
                for doc in docs:
                    if isinstance(doc, str) and doc.strip():
                        text_parts.append(doc.strip())
                    elif hasattr(doc, "page_content"):
                        text_parts.append(doc.page_content.strip())
                    elif doc is not None:
                        text_parts.append(str(doc).strip())
                if text_parts:
                    return "\n".join(text_parts)
    except Exception as exc:
        logger.warning("[helpers] ChromaDB 读取失败: %s", exc)
    return ""

def _detect_package_info(text):
    """判断招标文本中是否存在分包信息。"""
    packages = _extract_package_numbers(text)
    if packages:
        return True, packages[0]["package_no"]
    return False, None


def _extract_package_numbers(text):
    """从招标文本中提取分包列表（仅正则，无LLM）。
    
    返回 [{"package_no": "...", "package_name": "..."}]
    """
    if not text:
        return []
    return _extract_packages_fallback(text)

def _extract_packages_fallback(text):
    """正则方式回退提取包号。"""
    results = []
    seen = set()

    # 模式1: "第X包" + 名称
    for match in re.finditer(
        r"第\s*([A-Za-z0-9一二三四五六七八九十百零]+)\s*包\s*[：:、\s]*([^\n。，,；;]{0,50})",
        text,
    ):
        package_no = match.group(1).strip()
        package_name = (match.group(2) or "").strip()
        if package_no and package_no not in seen:
            seen.add(package_no)
            results.append({"package_no": package_no, "package_name": package_name})

    # 模式2: "包号：1" 此类格式
    if not results:
        for match in re.finditer(r"包号\s*[:：]\s*([A-Za-z0-9一二三四五六七八九十百零]+)", text):
            package_no = match.group(1).strip()
            if package_no and package_no not in seen:
                seen.add(package_no)
                results.append({"package_no": package_no, "package_name": ""})

    # 模式3: "采购包1"、"标包01" 等
    if not results:
        for match in re.finditer(r"(?:采购|标|招投标?)[包匹]\s*([A-Za-z0-9一二三四五六七八九十百零]+)", text):
            package_no = match.group(1).strip()
            if package_no and package_no not in seen:
                seen.add(package_no)
                results.append({"package_no": package_no, "package_name": ""})

    return results


def _extract_effective_text(raw_text, package_no):
    """根据包号选择结果裁剪后续流程使用的有效文本。"""
    if not raw_text:
        return ""
    if not package_no:
        return raw_text
    package_no = str(package_no).strip()
    pattern = r"(第\s*([A-Za-z0-9一二三四五六七八九十]+)\s*包|包号\s*[:：]?\s*([A-Za-z0-9一二三四五六七八九十]+))"
    matches = list(re.finditer(pattern, raw_text))
    if not matches:
        return raw_text
    for index, match in enumerate(matches):
        current_package_no = str(match.group(2) or match.group(3) or "").strip()
        if current_package_no == package_no:
            start = match.start()
            if index + 1 < len(matches):
                end = matches[index + 1].start()
            else:
                end = len(raw_text)
            return raw_text[start:end].strip()
    return raw_text


def _build_check_items(shared_resource_id, overview, requirements):
    """基于分析结果生成待人工确认的核对项。"""
    BiddingCheckItem.query.filter_by(shared_resource_id=shared_resource_id).delete()
    items = [
        ("overview", "项目概述", overview, 1),
        ("requirements", "招标要求", requirements, 2),
    ]
    for check_key, check_label, check_value, sort_no in items:
        db.session.add(
            BiddingCheckItem(
                shared_resource_id=shared_resource_id,
                check_key=check_key,
                check_label=check_label,
                check_value=check_value,
                confirmed_flag=False,
                sort_no=sort_no,
            )
        )


def _build_docx_bytes(task, catalog_record, analysis_result, knowledge_contexts, product_context, subject_context, chapter_contents):
    """将章节内容组装为最终 docx 二进制文件（带专业格式、目录层级、无调试信息）。"""
    from docx.shared import Pt, Cm, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    catalog_payload = json.loads(catalog_record.catalog_content) if isinstance(catalog_record.catalog_content, str) else (catalog_record.catalog_content or {})
    outline = catalog_payload.get("outline", []) if isinstance(catalog_payload, dict) else []

    company_name = subject_context.get("company_name", "") if subject_context else ""
    analysis_context = _extract_analysis_context(analysis_result) if analysis_result else {}
    coverage_snapshot = _get_generation_coverage_snapshot(analysis_result)
    generation_plan = _get_generation_plan_snapshot(analysis_result)
    bidder_notice = analysis_context.get("bidder_notice", {}) or {}
    cover_item_name = (bidder_notice.get("project_name") or "").strip()
    cover_project_no = (bidder_notice.get("project_no") or "").strip()
    cover_package_no = (getattr(task, "selected_package_no", "") or bidder_notice.get("package_no") or "").strip()
    cover_bid_time = utc_now().strftime("%Y年%m月%d日")

    document = Document()

    # ========== 插入免责声明页（第一页） ==========
    def _add_disclaimer_page(doc):
        from docx.shared import Cm, Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        for _ in range(4):
            doc.add_paragraph("")
        title_p = doc.add_paragraph()
        title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = title_p.add_run("\u514d\u8d23\u58f0\u660e")
        run.font.name = "\u9ed1\u4f53"
        run.font.size = Pt(22)
        run.bold = True
        run.element.rPr.rFonts.set(qn("w:eastAsia"), "\u9ed1\u4f53")
        doc.add_paragraph("")

        disclaimer_lines = [
            ("\u4e00\u3001\u670d\u52a1\u6027\u8d28", "\u672c\u670d\u52a1\u4e3a AI \u8f85\u52a9\u5de5\u5177\uff0c\u7528\u4e8e\u751f\u6210\u6807\u4e66\u53c2\u8003\u521d\u7a3f\u3002\u60a8\u5fc5\u987b\u5bf9\u6700\u7ec8\u63d0\u4ea4\u7684\u6807\u4e66\u6587\u4ef6\u8d1f\u5168\u90e8\u8d23\u4efb\uff0c\u5305\u62ec\u5ba1\u67e5\u3001\u4fee\u6539\u5185\u5bb9\u4ee5\u786e\u4fdd\u5176\u7b26\u5408\u6240\u6709\u6cd5\u89c4\u4e0e\u9879\u76ee\u8981\u6c42\u3002"),
            ("\u4e8c\u3001\u4e0d\u62c5\u4fdd\u51c6\u786e\u6027", "\u672c\u516c\u53f8\u4e0d\u4fdd\u8bc1 AI \u751f\u6210\u5185\u5bb9\u7684\u7edd\u5bf9\u51c6\u786e\u6027\u4e0e\u5b8c\u6574\u6027\u3002\u60a8\u5fc5\u987b\u81ea\u884c\u6838\u5b9e\u6240\u6709\u5173\u952e\u4fe1\u606f\uff0c\u5e76\u627f\u62c5\u56e0\u4f7f\u7528\u672c\u670d\u52a1\u800c\u4ea7\u751f\u7684\u4efb\u4f55\u540e\u679c\u3002"),
            ("\u4e09\u3001\u77e5\u8bc6\u4ea7\u6743\u627f\u8bfa\u4e0e\u98ce\u9669", "\u60a8\u987b\u786e\u4fdd\u4e0a\u4f20\u7684\u6240\u6709\u8d44\u6599\u4e0d\u4fb5\u72af\u4efb\u4f55\u7b2c\u4e09\u65b9\u6743\u5229\u3002\u7531\u6b64\u5f15\u53d1\u7684\u4efb\u4f55\u6cd5\u5f8b\u8d23\u4efb\u53ca\u8d54\u507f\uff0c\u5747\u7531\u60a8\u81ea\u884c\u627f\u62c5\u3002\u672c\u516c\u53f8\u5bf9\u7528\u6237\u4e0a\u4f20\u5185\u5bb9\u4e0d\u4eab\u6709\u6743\u5229\uff0c\u4e5f\u4e0d\u627f\u62c5\u5ba1\u67e5\u4e49\u52a1\u3002"),
            ("\u56db\u3001\u56fe\u7247\u7d20\u6750\u98ce\u9669\u63d0\u793a", "\u670d\u52a1\u63d0\u4f9b\u7684\u56fe\u7247\u7d20\u6750\u4ec5\u4f9b\u53c2\u8003\u3002\u60a8\u82e5\u4f7f\u7528\uff08\u5305\u62ec\u5f15\u7528\u3001\u4fee\u6539\u6216\u4e8c\u6b21\u521b\u4f5c\uff09\uff0c\u5fc5\u987b\u81ea\u884c\u627f\u62c5\u5176\u5bfc\u81f4\u7684\u4fb5\u6743\u7b49\u5168\u90e8\u98ce\u9669\u4e0e\u8d23\u4efb\uff0c\u672c\u516c\u53f8\u6982\u4e0d\u8d1f\u8d23\u3002"),
            ("\u4e94\u3001\u8d23\u4efb\u9650\u5236", "\u5728\u4efb\u4f55\u60c5\u51b5\u4e0b\uff0c\u672c\u516c\u53f8\u5747\u4e0d\u5bf9\u56e0\u4f7f\u7528\u672c\u670d\u52a1\u9020\u6210\u7684\u4efb\u4f55\u76f4\u63a5\u3001\u95f4\u63a5\u6216\u540e\u679c\u6027\u635f\u5931\uff08\u5982\u5229\u6da6\u635f\u5931\u3001\u4e1a\u52a1\u4e2d\u65ad\u3001\u6570\u636e\u4e22\u5931\uff09\u627f\u62c5\u8d23\u4efb\u3002"),
            ("\u516d\u3001\u5176\u4ed6", "\u672c\u516c\u53f8\u4fdd\u7559\u968f\u65f6\u4fee\u6539\u6216\u7ec8\u6b62\u670d\u52a1\u7684\u6743\u5229\u3002\u672c\u987b\u77e5\u7684\u89e3\u91ca\u4e0e\u4e89\u8bae\u89e3\u51b3\u5747\u9002\u7528\u4e2d\u534e\u4eba\u6c11\u5171\u548c\u56fd\u6cd5\u5f8b\u3002"),
        ]

        for clause_title, clause_body in disclaimer_lines:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            p.paragraph_format.first_line_indent = Cm(0.74)
            p.paragraph_format.space_after = Pt(10)
            p.paragraph_format.line_spacing = 1.5
            run_title = p.add_run(f"{clause_title}\uff1a")
            run_title.font.name = "\u9ed1\u4f53"
            run_title.font.size = Pt(12)
            run_title.bold = True
            run_title.element.rPr.rFonts.set(qn("w:eastAsia"), "\u9ed1\u4f53")
            run_body = p.add_run(clause_body)
            run_body.font.name = "\u5b8b\u4f53"
            run_body.font.size = Pt(12)
            run_body.element.rPr.rFonts.set(qn("w:eastAsia"), "\u5b8b\u4f53")

        doc.add_paragraph("")
        note_p = doc.add_paragraph()
        note_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run = note_p.add_run("\uff08\u4f7f\u7528\u672c\u670d\u52a1\u5373\u89c6\u4e3a\u5df2\u9605\u8bfb\u5e76\u540c\u610f\u4ee5\u4e0a\u6761\u6b3e\uff09")
        run.font.name = "\u5b8b\u4f53"
        run.font.size = Pt(11)
        run.italic = True
        run.element.rPr.rFonts.set(qn("w:eastAsia"), "\u5b8b\u4f53")

    _add_disclaimer_page(document)
    document.add_page_break()

    # ========== \u9875\u9762\u8bbe\u7f6e ==========
    section = document.sections[0]
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(3.17)
    section.right_margin = Cm(3.17)

    # ========== \u9ed8\u8ba4\u5b57\u4f53 ==========
    style = document.styles["Normal"]
    font = style.font
    font.name = "\u5b8b\u4f53"
    font.size = Pt(12)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "\u5b8b\u4f53")
    pf = style.paragraph_format
    pf.line_spacing = 1.5
    pf.space_after = Pt(6)

    # ========== \u5b9a\u4e49\u6807\u9898\u6837\u5f0f ==========
    def _set_heading_style(heading_level, font_name, font_size, bold=True, space_before=12, space_after=6):
        hs = document.styles[f"Heading {heading_level}"]
        hs.font.name = font_name
        hs.font.size = Pt(font_size)
        hs.font.bold = bold
        hs.font.color.rgb = RGBColor(0, 0, 0)
        hs.element.rPr.rFonts.set(qn("w:eastAsia"), font_name)
        hpf = hs.paragraph_format
        hpf.space_before = Pt(space_before)
        hpf.space_after = Pt(space_after)
        hpf.line_spacing = 1.5

    _set_heading_style(1, "\u9ed1\u4f53", 18, True, 24, 12)
    _set_heading_style(2, "\u9ed1\u4f53", 15, True, 18, 8)
    _set_heading_style(3, "\u5b8b\u4f53", 13, True, 12, 6)
    _set_heading_style(4, "\u5b8b\u4f53", 12, True, 6, 6)

    # ========== \u8f85\u52a9\u51fd\u6570 ==========
    def _clean_markdown(text):
        cleaned = text
        cleaned = re.sub(r'```[\w]*\n?', '', cleaned)
        cleaned = re.sub(r'`([^`]+)`', r'\1', cleaned)
        cleaned = re.sub(r'\*\*(.+?)\*\*', r'\1', cleaned)
        cleaned = re.sub(r'\*(.+?)\*', r'\1', cleaned)
        cleaned = re.sub(r'^#{1,6}\s+', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', cleaned)
        cleaned = re.sub(r'!\[([^\]]*)\]\([^)]+\)', '', cleaned)
        cleaned = re.sub(r'^[-*_]{3,}\s*$', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^[\s]*[-*+]\s+', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^\s*\d+[.\)]\s+', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'----media/[\w./-]+----', '', cleaned)
        cleaned = re.sub(r'media/image\d+\.\w+', '', cleaned)
        return cleaned.strip()

    def _write_formatted_content(doc, text):
        if not text or not text.strip():
            return
        cleaned_text = _clean_markdown(text)
        if not cleaned_text.strip():
            return
        lines = cleaned_text.split("\n")
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if re.match(r'^[\s]*----media/', stripped) or 'media/image' in stripped:
                continue
            p = doc.add_paragraph(stripped)
            p.style = document.styles["Normal"]
            pf = p.paragraph_format
            pf.first_line_indent = Pt(24)
            pf.line_spacing = 1.5

    def _write_table_from_lines(doc, lines):
        table_rows = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            cells_data = [c.strip() for c in re.split(r'\t+|\s{3,}|\|', stripped) if c.strip()]
            if len(cells_data) >= 2:
                table_rows.append(cells_data)
        if not table_rows:
            return
        max_cols = max(len(row) for row in table_rows)
        table = doc.add_table(rows=len(table_rows), cols=max_cols)
        table.style = "Table Grid"
        table.alignment = 1
        for row_idx, row_data in enumerate(table_rows):
            for col_idx, cell_text in enumerate(row_data):
                if col_idx < len(table.rows[row_idx].cells):
                    cell = table.rows[row_idx].cells[col_idx]
                    cell.text = cell_text
                    if row_idx == 0:
                        for paragraph in cell.paragraphs:
                            for run in paragraph.runs:
                                run.bold = True
                                run.font.size = Pt(11)
                                run.font.name = "\u5b8b\u4f53"
                                run.element.rPr.rFonts.set(qn("w:eastAsia"), "\u5b8b\u4f53")
        doc.add_paragraph("")

    image_extensions = {"png", "jpg", "jpeg", "bmp", "gif", "webp"}
    inserted_material_ids = set()

    def _build_subject_declaration_text():
        materials = subject_context.get("materials", []) if subject_context else []
        if not company_name or not materials:
            return ""
        labels = [item.get("material_label", "").strip() for item in materials if item.get("material_label")]
        joined_labels = "、".join(dict.fromkeys(labels))
        if not joined_labels:
            return ""
        return (
            f"{company_name}郑重声明：本单位已按本项目要求提供主体资质、身份证明及授权相关材料，"
            f"包括{joined_labels}。凡在本标书中引用到前述主体资料的章节，均同步插入对应原始文件、扫描页或图片内容；"
            "未在正文中单独展开的资料，统一附于本文件后续附件章节备查。"
        )

    def _get_material_identity(material):
        return material.get("id") or material.get("file_id") or material.get("file_name")

    def _get_material_file_record(material):
        file_id = material.get("file_id")
        if not file_id:
            return None
        try:
            return db.session.get(FileStorage, int(file_id))
        except Exception:
            return None

    def _extract_docx_media_payloads(payload, max_images=6):
        from zipfile import ZipFile

        results = []
        try:
            with ZipFile(BytesIO(payload)) as archive:
                media_names = [name for name in archive.namelist() if name.startswith("word/media/")]
                for media_name in media_names[:max_images]:
                    media_payload = archive.read(media_name)
                    if media_payload:
                        results.append(media_payload)
        except Exception as exc:
            logger.warning("[docx] 提取 docx 图片失败: %s", exc)
        return results

    def _render_pdf_pages_as_png(payload, max_pages=4):
        import fitz

        images = []
        try:
            pdf = fitz.open(stream=payload, filetype="pdf")
            try:
                for page_index in range(min(len(pdf), max_pages)):
                    page = pdf.load_page(page_index)
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False)
                    images.append(pixmap.tobytes("png"))
            finally:
                pdf.close()
        except Exception as exc:
            logger.warning("[docx] 渲染 PDF 图片失败: %s", exc)
        return images

    def _load_material_visual_payloads(material, max_assets=6):
        file_record = _get_material_file_record(material)
        if not file_record:
            return []
        payload = StorageService.read_bytes(file_record)
        if not payload:
            return []
        extension = (file_record.file_ext or material.get("file_ext") or Path(file_record.file_name or "").suffix.lstrip(".")).lower()
        if extension in image_extensions:
            return [payload]
        if extension == "pdf":
            return _render_pdf_pages_as_png(payload, max_pages=max_assets)
        if extension == "docx":
            return _extract_docx_media_payloads(payload, max_images=max_assets)
        return []

    def _add_picture_payload(payload, width_cm=15.5):
        try:
            pic_paragraph = document.add_paragraph()
            pic_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            pic_paragraph.add_run().add_picture(BytesIO(payload), width=Cm(width_cm))
            return True
        except Exception as exc:
            logger.warning("[docx] 插入图片失败: %s", exc)
            return False

    def _write_material_block(material):
        material_title = material.get("material_label") or "主体资料"
        file_name = material.get("file_name") or ""
        material_id = _get_material_identity(material)

        document.add_heading(material_title, level=3)
        if file_name:
            file_para = document.add_paragraph(f"文件名称：{file_name}")
            file_para.style = document.styles["Normal"]

        text_excerpt = (material.get("text_excerpt") or "").strip()
        if text_excerpt:
            _write_formatted_content(document, text_excerpt)

        inserted_visual = False
        for image_payload in _load_material_visual_payloads(material):
            if _add_picture_payload(image_payload):
                inserted_visual = True

        if not inserted_visual and not text_excerpt:
            fallback = document.add_paragraph(
                "当前资料记录仅保留文本索引，未保留可回填的原始文件或图片流，无法按原样插入扫描页。"
                "如需恢复该类图片展示，请重新上传对应主体资料后重新生成标书。"
            )
            fallback.style = document.styles["Normal"]

        inserted_material_ids.add(material_id)

    def _material_matches_outline_item(material, title, desc=""):
        material_type = str(material.get("material_type") or "").strip().upper()
        outline_text = f"{title} {desc}".strip()
        auth_keywords = ("授权", "委托", "身份证明", "法定代表人", "被授权人")
        qualification_keywords = ("资质", "资格", "营业执照", "证明材料", "审查", "声明函", "响应文件格式")

        if any(keyword in outline_text for keyword in auth_keywords):
            return material_type in {
                "AUTHORIZATION_LETTER",
                "AUTHORIZED_PERSON_ID_CARD",
                "LEGAL_PERSON_ID_CARD",
                "LEGAL_PERSON_STATEMENT",
            }
        if any(keyword in outline_text for keyword in qualification_keywords):
            return material_type in {
                "BUSINESS_LICENSE",
                "QUALIFICATION_FILE",
                "QUALIFICATION_DECLARATION",
                "LEGAL_PERSON_ID_CARD",
            }
        return False

    def _write_subject_materials_for_outline_item(title, desc=""):
        materials = subject_context.get("materials", []) if subject_context else []
        matched = []
        for material in materials:
            material_id = _get_material_identity(material)
            if material_id in inserted_material_ids:
                continue
            if _material_matches_outline_item(material, title, desc):
                matched.append(material)
        if not matched:
            return 0

        intro = document.add_paragraph("以下插入与本节内容直接对应的主体资质/授权原始资料：")
        intro.style = document.styles["Normal"]
        for material in matched:
            _write_material_block(material)
        return len(matched)

    def _write_remaining_subject_materials():
        materials = subject_context.get("materials", []) if subject_context else []
        remaining = [item for item in materials if _get_material_identity(item) not in inserted_material_ids]
        if not remaining:
            return

        document.add_page_break()
        document.add_heading("主体资料附件", level=1)
        declaration_text = _build_subject_declaration_text()
        if declaration_text:
            _write_formatted_content(document, declaration_text)
        for material in remaining:
            _write_material_block(material)

    def _write_missing_requirements_page():
        missing_items = coverage_snapshot.get("missing_items", []) if coverage_snapshot else []
        if not missing_items:
            return
        document.add_page_break()
        document.add_heading("待人工补齐清单", level=1)
        intro = document.add_paragraph(
            "以下目录项在当前生成过程中未形成有效正文，需根据招标文件要求和实际资料进行人工补充。"
        )
        intro.style = document.styles["Normal"]
        for item in missing_items:
            target_title = (item.get("target_title") or item.get("chapter_title") or "未命名条目").strip()
            requirement = (item.get("requirement") or "").strip()
            status = item.get("status") or "PENDING"
            source_reference = (item.get("source_reference") or "").strip()
            p = document.add_paragraph()
            p.style = document.styles["Normal"]
            p.paragraph_format.first_line_indent = Pt(0)
            head = p.add_run(f"{target_title} [{status}]")
            head.bold = True
            head.font.name = "宋体"
            head.font.size = Pt(12)
            head.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
            if requirement:
                detail = p.add_run(f"：{requirement}")
            else:
                detail = p.add_run("：当前未提取到更明确的要求描述，请回查招标原文。")
            detail.font.name = "宋体"
            detail.font.size = Pt(12)
            detail.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
            if source_reference:
                source_para = document.add_paragraph(f"来源文件：{source_reference}")
                source_para.style = document.styles["Normal"]
                source_para.paragraph_format.first_line_indent = Pt(24)
            original_excerpt = (item.get("original_requirement_excerpt") or "").strip()
            if item.get("requirement_level") == "REQUIRED" and original_excerpt:
                excerpt_para = document.add_paragraph(f"招标文件原文提示：{original_excerpt}")
                excerpt_para.style = document.styles["Normal"]
                excerpt_para.paragraph_format.first_line_indent = Pt(24)

    def _normalize_outline_title_for_match(title):
        return re.sub(r"\s+", "", str(title or "").strip())

    def _find_plan_item(chapter_title, target_title):
        for item in (generation_plan.get("plan_items", []) if generation_plan else []):
            item_chapter_title = (item.get("chapter_title") or "").strip()
            item_target_title = (item.get("target_title") or "").strip()
            if item_chapter_title == (chapter_title or "").strip() and item_target_title == (target_title or "").strip():
                return item
        return {}

    def _extract_child_content_sections(content_text, children):
        if not content_text or not children:
            return {}
        lines = [line.strip() for line in str(content_text or "").splitlines()]
        child_titles = [(child.get("title") or "").strip() for child in children if (child.get("title") or "").strip()]
        if not child_titles:
            return {}

        title_positions = []
        for index, line in enumerate(lines):
            normalized_line = _normalize_outline_title_for_match(line)
            if not normalized_line:
                continue
            for child_title in child_titles:
                if normalized_line == _normalize_outline_title_for_match(child_title):
                    title_positions.append((index, child_title))
                    break
        if not title_positions:
            return {}

        sections = {}
        for position, (start_index, child_title) in enumerate(title_positions):
            end_index = title_positions[position + 1][0] if position + 1 < len(title_positions) else len(lines)
            body_lines = [line for line in lines[start_index + 1 : end_index] if line]
            if body_lines:
                sections[child_title] = "\n".join(body_lines).strip()
        return sections

    # ========== 第二页固定封面 ==========
    for _ in range(5):
        document.add_paragraph("")
    title_para = document.add_paragraph()
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title_para.add_run("\u6295\u6807\u6587\u4ef6")
    run.font.name = "\u9ed1\u4f53"
    run.font.size = Pt(26)
    run.bold = True
    run.element.rPr.rFonts.set(qn("w:eastAsia"), "\u9ed1\u4f53")

    document.add_paragraph("")
    cover_fields = [
        ("\u6807\u7684\u540d\u79f0", cover_item_name),
        ("\u9879\u76ee\u7f16\u53f7", cover_project_no),
        ("\u6295\u6807\u4eba\u540d\u79f0", company_name),
        ("\u6295\u6807\u65f6\u95f4", cover_bid_time),
    ]
    if cover_package_no:
        cover_fields.append(("\u5305\u53f7", cover_package_no))

    for label, value in cover_fields:
        field_para = document.add_paragraph()
        field_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = field_para.add_run(f"{label}\uff1a{value}")
        run.font.name = "\u5b8b\u4f53"
        run.font.size = Pt(16)
        run.element.rPr.rFonts.set(qn("w:eastAsia"), "\u5b8b\u4f53")

    document.add_page_break()

    # ========== \u76ee\u5f55\u9875\uff08\u5360\u4f4d\uff09 ==========
    for _ in range(4):
        document.add_paragraph("")
    toc_title = document.add_paragraph()
    toc_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = toc_title.add_run("\u76ee  \u5f55")
    run.font.name = "\u9ed1\u4f53"
    run.font.size = Pt(22)
    run.bold = True
    run.element.rPr.rFonts.set(qn("w:eastAsia"), "\u9ed1\u4f53")

    document.add_paragraph("")
    # \u8f93\u51fa\u76ee\u5f55\u7ed3\u6784
    def _write_toc_items(items, indent=0):
        for item in items:
            title = item.get("title", "").strip()
            if not title:
                continue
            indent_str = "    " * indent
            p = document.add_paragraph(f"{indent_str}{title}")
            p.style = document.styles["Normal"]
            pf = p.paragraph_format
            pf.line_spacing = 1.8
            for run in p.runs:
                run.font.name = "\u5b8b\u4f53"
                run.font.size = Pt(12)
                run.element.rPr.rFonts.set(qn("w:eastAsia"), "\u5b8b\u4f53")
            children = item.get("children", [])
            if children:
                _write_toc_items(children, indent + 1)

    _write_toc_items(outline)

    document.add_paragraph("")
    p = document.add_paragraph("\uff08\u4ee5\u4e0a\u76ee\u5f55\u7531 AI \u8f85\u52a9\u751f\u6210\uff0c\u5efa\u8bae\u5728 Word \u4e2d\u4f7f\u7528\u201c\u63d2\u5165 \u2192 \u76ee\u5f55\u201d\u529f\u80fd\u751f\u6210\u89c4\u8303\u76ee\u5f55\uff09")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in p.runs:
        run.font.name = "\u5b8b\u4f53"
        run.font.size = Pt(10)
        run.italic = True
        run.element.rPr.rFonts.set(qn("w:eastAsia"), "\u5b8b\u4f53")

    document.add_page_break()

    # ========== \u9012\u5f52\u5199\u5165 outline \u8282\u70b9 ==========
    def _write_outline_item(outline_item, level=1, inherited_child_sections=None, parent_title=None):
        title = outline_item.get("title", "").strip()
        desc = outline_item.get("description", "").strip()
        if not title:
            return
        h = document.add_heading(title, level=min(level, 4))
        chapter_title_for_plan = parent_title or title

        matched_content = None
        # \u9876\u7ea7\u8282\u70b9\uff1a\u4ece chapter_contents \u5339\u914d LLM \u751f\u6210\u7684\u5185\u5bb9
        if level == 1:
            chapter_idx = outline_item.get("_chapter_idx")
            if chapter_idx is not None and chapter_idx < len(chapter_contents):
                matched_content = chapter_contents[chapter_idx].get("content", "")

            if not matched_content:
                for cc in chapter_contents:
                    if title in cc.get("title", "") or cc.get("title", "") in title:
                        matched_content = cc.get("content", "")
                        break

        if not matched_content and inherited_child_sections:
            matched_content = inherited_child_sections.get(title)
        if not matched_content and desc and not _chapter_requires_evidence_placeholder(outline_item):
            matched_content = desc

        children = outline_item.get("children", [])
        child_sections = _extract_child_content_sections(matched_content, children) if matched_content and matched_content != _EMPTY_PAGE_MARKER and children else {}

        if matched_content == _EMPTY_PAGE_MARKER:
            plan_item = _find_plan_item(chapter_title_for_plan, title)
            original_excerpt = (plan_item.get("original_requirement_excerpt") or "").strip()
            if plan_item.get("plan_action") == "LEAVE_BLANK" and plan_item.get("requirement_level") == "REQUIRED":
                note = document.add_paragraph("本节属于强要求内容，当前未检索到可直接填充的有效资料，已按要求留白，请人工补充。")
                note.style = document.styles["Normal"]
                if original_excerpt:
                    excerpt_para = document.add_paragraph(f"招标文件原文提示：{original_excerpt}")
                    excerpt_para.style = document.styles["Normal"]
            document.add_paragraph("")
            document.add_page_break()
            return

        if matched_content:
            # \u5982\u679c\u5185\u5bb9\u4ee5\u6807\u9898\u5f00\u5934\uff0c\u53bb\u6389\u91cd\u590d\u7684\u6807\u9898\u6587\u5b57
            content_text = matched_content
            first_line = content_text.split("\n")[0].strip()
            if title in first_line or first_line in title:
                content_text = "\n".join(content_text.split("\n")[1:]).strip()

            table_lines = []
            normal_lines = []
            in_table_block = False
            for line in content_text.split("\n"):
                stripped = line.strip()
                if not stripped:
                    if in_table_block:
                        _write_table_from_lines(document, table_lines)
                        table_lines = []
                        in_table_block = False
                    continue
                if "\t" in stripped or re.search(r"\|", stripped):
                    in_table_block = True
                    table_lines.append(stripped)
                else:
                    if in_table_block:
                        _write_table_from_lines(document, table_lines)
                        table_lines = []
                        in_table_block = False
                    normal_lines.append(stripped)
            if in_table_block and table_lines:
                _write_table_from_lines(document, table_lines)
            if normal_lines:
                _write_formatted_content(document, "\n".join(normal_lines))

        _write_subject_materials_for_outline_item(title, desc)

        for child in children:
            _write_outline_item(child, level=level + 1, inherited_child_sections=child_sections, parent_title=chapter_title_for_plan)

    # ========== \u7ed9 outline \u9876\u7ea7\u6bcf\u9879\u6ce8\u5165 chapter_idx ==========
    for idx, item in enumerate(outline):
        item["_chapter_idx"] = idx

    # ========== \u6309\u76ee\u5f55\u7ed3\u6784\u751f\u6210\u6b63\u6587 ==========
    for item in outline:
        _write_outline_item(item, level=1)

    _write_missing_requirements_page()
    _write_remaining_subject_materials()

    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()
