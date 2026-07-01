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

def _strip_xml_control_chars(text: str) -> str:
    """移除 XML 不兼容的控制字符，防止 _build_docx_bytes 序列化时崩溃。

    python-docx 底层使用 lxml 生成 XML，控制字符（NULL 字节、起止控制符等）
    在 XML 1.0 中不合法，必须移除。
    """
    if not text:
        return text
    # 保留 \t(09) \n(0a) \r(0d) 等 XML 允许的空白字符
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)



# ========== 全局产品列名映射表 ==========
# 统一所有表格解析系统使用的字段映射，确保 _extract_table_data_from_analysis()
# table_parser.py, table_classifier.py 共用同一套映射规则
PRODUCT_COLUMN_MAP = {
    "name": ["品名", "名称", "产品名称", "试剂名称", "货物名称",
             "商品名", "采购产品名称", "标的名称", "产品名",
             "采购产品名称", "产品（设备）名称"],
    "spec": ["规格", "规格型号", "型号", "技术规格", "参数",
             "★规格参数", "技术参数与性能指标", "规格参数",
             "技术规格参数"],
    "brand": ["品牌", "生产厂家", "厂家", "制造商"],
    "qty": ["数量", "需求量", "预估数量", "采购量", "★数量"],
    "unit": ["单位", "计量单位", "★计量单位"],
    "unit_price": ["单价", "预算单价", "最高限价", "★单价最高限价",
                   "单价最高限价"],
    "total_price": ["总价", "金额", "合计"],
    "产地": ["产地", "来源"],
    "备注": ["备注", "说明"],
}


# 产品库 API 字段名 → 中标文件表格列名映射
# 用于将 _fetch_product_data() 返回的结构化字段填充到表格空格中
PRODUCT_FIELD_TO_COLUMN = {
    "brand": ["品牌", "生产厂家", "厂家", "制造商", "★品牌"],
    "specAndModel": ["规格", "规格型号", "型号", "技术规格", "规格参数",
                     "★规格参数", "技术参数与性能指标"],
    "manufacturer": ["生产厂家", "厂家", "制造商"],
    "unit": ["单位", "计量单位", "★计量单位"],
    "articleNo": ["货号", "商品编号", "产品编号"],
    "serialNo": ["序列号", "批号"],
    "descOfFunc": ["功能描述", "产品描述", "描述", "主要功能"],
    "detectionOfSpec": ["检测标准", "检测规范"],
    "storageCondition": ["储存条件", "存储条件", "存放条件", "保存条件"],
    "concentration": ["浓度"],
    "registrationCertificateNo": ["注册证号", "注册号", "医疗器械注册证", "注册证"],
    "qualityPeriod": ["保质期", "有效期", "质量保证期"],
}


def _map_product_headers_unified(headers):
    """统一的表头→标准字段映射，供所有表格解析模块共用。
    
    Returns:
        dict: {standard_field: col_index}
    """
    mapping = {}
    for i, h in enumerate(headers):
        h_clean = h.strip()
        for std_field, candidates in PRODUCT_COLUMN_MAP.items():
            if any(c in h_clean for c in candidates):
                if std_field not in mapping:
                    mapping[std_field] = i
                break
    return mapping


_FIELD_UNSET = object()
_EMPTY_PAGE_MARKER = "[[EMPTY_PAGE]]"
_TABLE_MARKER_PREFIX = "[[TABLE:"
_QUALIFICATION_MARKER = "[[QUALIFICATION_DOCS]]"


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

    if isinstance(payload, dict):
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
        # 确保 bidder_notice 有 project_name/project_no（可能顶层也有）
        if not bidder_notice.get("project_name"):
            for field in ("project_name", "项目名称", "标的名称"):
                val = payload.get(field) or getattr(analysis_result, field, None) or ""
                if val:
                    bidder_notice["project_name"] = val
                    break
        if not bidder_notice.get("project_no"):
            for field in ("project_no", "项目编号", "比选编号"):
                val = payload.get(field) or getattr(analysis_result, field, None) or ""
                if val:
                    bidder_notice["project_no"] = val
                    break        # 提取 product_lists 供表格填充引擎使用
        product_lists = []
        # 同时保留原始表格结构（headers + rows）用于原样复制
        raw_tables = []
        tc = payload.get("table_classification", {}) if isinstance(payload, dict) else {}
        if tc:
            for pl in tc.get("product_lists", []):
                for item in pl.get("items", []):
                    product_lists.append(item)
                # 保留原始表格结构用于原样复制
                if pl.get("headers") and pl.get("rows"):
                    raw_tables.append({
                        "headers": pl["headers"],
                        "rows": pl["rows"][:100],
                    })
        context["_raw_product_lists"] = product_lists
        context["_raw_product_tables"] = raw_tables
        context["_eligibility"] = payload.get("eligibility", {}) if isinstance(payload, dict) else {}
        context["_table_classification"] = payload.get("table_classification", {}) if isinstance(payload, dict) else {}
        context["_format_requirements"] = payload.get("format_requirements", {}) if isinstance(payload, dict) else {}
        context["_scoring"] = payload.get("scoring", {}) if isinstance(payload, dict) else {}
        context["_packages"] = payload.get("packages", []) if isinstance(payload, dict) else []

    
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
            # 置信度门控：召回相关性最低阈值
            MIN_RECALL_SCORE = current_app.config.get("MIN_RECALL_CONFIDENCE", 0.3)
            for rr in recall_results:
                if rr.get("text") and len(rr["text"].strip()) > 20:
                    # 相关性门控：score 低于阈值的片段丢弃
                    rr_score = rr.get("score", 0) or 0
                    if rr_score < MIN_RECALL_SCORE:
                        logger.debug("[confidence] 召回片段相关性偏低 score=%.4f, 已过滤", rr_score)
                        continue
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
    
    # 从 analysis_data.table_classification.product_lists 提取结构化产品名称
    product_names_from_tables = []
    if analysis_result and analysis_result.analysis_data:
        try:
            ad = json.loads(analysis_result.analysis_data) if isinstance(analysis_result.analysis_data, str) else analysis_result.analysis_data
            tc = ad.get("table_classification", {}) if isinstance(ad, dict) else {}
            for pl in tc.get("product_lists", []):
                for item in pl.get("items", []):
                    name = item.get("\u91c7\u8d2d\u4ea7\u54c1\u540d\u79f0", "") or item.get("\u4ea7\u54c1\u540d\u79f0", "") or item.get("\u6807\u7684\u540d\u79f0", "")
                    if name and len(name) >= 2:
                        product_names_from_tables.append(name)
        except Exception:
            pass
    
    terms = _extract_product_terms(effective + "\n" + (requirements or ""))
    # 合并结构化表格中的产品名
    for pname in product_names_from_tables:
        if pname not in terms:
            terms.append(pname)
    
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


def _match_products_from_library(product_items, top_k=3):
    """从产品库检索匹配产品信息，填充到产品列表的空白字段。

    对每个产品名称做 LLM embedding 检索，从 Chroma product_library 中
    找到最相似的产品，提取其规格参数、品牌、单价等信息。

    Args:
        product_items: list[dict] - 产品列表，每个 item 至少包含 name
        top_k: 每个产品返回的最多匹配数

    Returns:
        dict: {product_name: {matched_text: "...", spec: "...", score: float}}
    """
    if not product_items:
        return {}

    try:
        chroma_tenant = current_app.config.get("CHROMA_TENANT")
        chroma_database = current_app.config.get("CHROMA_DATABASE")
        engine = MultiRecallEngine()
        results = {}

        for item in product_items:
            name = item.get("name", "") or item.get("采购产品名称", "") or ""
            if not name or len(name) < 2:
                continue

            recall_results = engine.recall(
                query=name,
                collection="product_library",
                top_k=top_k,
                tenant=chroma_tenant,
                database=chroma_database,
            )

            best_match = None
            best_score = 0.0
            for rr in recall_results:
                score = rr.get("score", 0) or 0
                if score > best_score and rr.get("text") and len(rr["text"].strip()) > 10:
                    best_score = score
                    best_match = rr["text"].strip()

            if best_match:
                results[name] = {
                    "matched_text": best_match,
                    "score": best_score,
                }

        return results
    except Exception as exc:
        logger.warning("[product] 产品库匹配异常: %s", exc)
        return {}


def _fetch_product_data(product_names, adapter=None):
    """从产品库批量查询产品信息，返回结构化数据。

    使用 ChromaAdapter（业务服务端口 28712）的 /objects/query 接口，
    直接返回 object_json 中的结构化字段，无需 LLM 解析。

    Args:
        product_names: list[str] - 产品名称列表
        adapter: ChromaAdapter 实例（可选，自动创建）

    Returns:
        dict: {product_name: {brand, specAndModel, manufacturer, unit, ...}}
    """
    if not product_names:
        return {}

    if adapter is None:
        adapter = ChromaAdapter(
            host=current_app.config.get("CHROMA_HOST"),
            tenant=current_app.config.get("PRODUCT_CHROMA_TENANT", "erp"),
            database=current_app.config.get("PRODUCT_CHROMA_DATABASE", "erp"),
        )

    collection = current_app.config.get("PRODUCT_CHROMA_COLLECTION", "product")
    result = {}

    for name in product_names:
        pname = (name or "").strip()
        if not pname or len(pname) < 2:
            continue
        try:
            data = adapter.query_objects(collection, query_text=pname, top_k=1)
            matches = (data or {}).get("matches", []) or []
            if matches:
                best = matches[0]
                obj_str = best.get("object_json", "") or ""
                if obj_str:
                    obj = json.loads(obj_str)
                    info = {}
                    for field in ["productName", "brand", "specAndModel", "manufacturer",
                                   "unit", "articleNo", "serialNo", "descOfFunc",
                                   "detectionOfSpec", "storageCondition", "concentration",
                                   "qualityPeriod", "qualityPeriodUnit", "registrationCertificateNo"]:
                        val = obj.get(field)
                        if val is not None and str(val).strip() not in ("", "-"):
                            info[field] = str(val)
                    if info:
                        result[pname] = info
        except Exception as exc:
            logger.warning("[product] 产品库查询失败 name=%s: %s", pname, exc)

    return result

def _fill_table_from_original(original_headers, original_rows):
    """从产品库填充原始表格的空白单元格。

    策略：
    1. 保留原始表格的完整框架（表头+行）
    2. 用 PRODUCT_COLUMN_MAP 定位产品名列
    3. 收集全部产品名，批量查询产品库
    4. 用 PRODUCT_FIELD_TO_COLUMN 匹配哪些列可填充
    5. 只填充空单元格，已有内容的单元格不动

    Args:
        original_headers: list[str] - 原始表头
        original_rows: list[list[str]] - 原始数据行

    Returns:
        list[list[str]]: 填充后的数据行
    """
    if not original_headers or not original_rows:
        return original_rows or []

    # 1. 定位产品名列索引
    name_col_idx = -1
    for i, h in enumerate(original_headers):
        for candidate in PRODUCT_COLUMN_MAP.get("name", []):
            if candidate in h:
                name_col_idx = i
                break
        if name_col_idx >= 0:
            break

    if name_col_idx < 0:
        return original_rows

    # 2. 收集产品名
    product_names = []
    for row in original_rows:
        name = (row[name_col_idx] or "").strip() if name_col_idx < len(row) else ""
        if name and len(name) >= 2:
            product_names.append(name)

    if not product_names:
        return original_rows

    # 3. 批量查询产品库
    product_data = _fetch_product_data(product_names)

    if not product_data:
        return original_rows

    # 4. 建立 表头→产品字段 的映射
    header_to_product_field = {}
    for col_idx, h in enumerate(original_headers):
        h_clean = h.strip()
        for product_field, col_candidates in PRODUCT_FIELD_TO_COLUMN.items():
            if any(c in h_clean for c in col_candidates):
                header_to_product_field[col_idx] = product_field
                break

    # 5. 填充每行的空白单元格
    filled_rows = []
    for row in original_rows:
        new_row = list(row)
        while len(new_row) < len(original_headers):
            new_row.append("")

        product_name = (new_row[name_col_idx] or "").strip() if name_col_idx < len(new_row) else ""
        product_info = product_data.get(product_name, {})

        if product_info:
            for col_idx in range(len(original_headers)):
                cell_val = (new_row[col_idx] or "").strip()
                if not cell_val:
                    product_field = header_to_product_field.get(col_idx)
                    if product_field and product_field in product_info:
                        fill_val = product_info[product_field]
                        new_row[col_idx] = fill_val[:100]

        new_row = [str(c)[:100] if c else "" for c in new_row]
        filled_rows.append(new_row)

    return filled_rows


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
        # 注意：主体资料是用户上传的正式文件，不做置信度过滤
        # 置信度标记由下游 _filter_low_confidence_subject_materials() 处理
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
    # 字段格式校验
    company_name = subject.company_name or ""
    credit_code = subject.credit_code or ""
    # 主体数据由用户管理，不做格式校验

    return {
        "company_name": company_name,
        "credit_code": credit_code,
        "address": subject.address or "",
        "contact_person": subject.contact_person or "",
        "contact_phone": subject.contact_phone or "",
        "legal_person": "",  # SubjectCompany 表无法人字段，需从材料 OCR 提取
        "_validations": {},
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
                # 对资格证明类型特殊处理：检验父章节标题，而非子项标题
                # 资格证明文件章节的各子项不需要在正文中展开写
                # 只要 chapter_content 包含 _QUALIFICATION_MARKER，说明已由
                # _generate_qualification_content() 通过 chapter.children 处理
                # 所有子项都应视为已覆盖，不依赖正文文本匹配
                is_qual_chapter = any(kw in chapter_title for kw in ["资格证明", "资格审查", "资质证明", "资格性"])
                if is_qual_chapter:
                    # 如果章节内容包含 QUALIFICATION_MARKER → 所有子项已处理
                    if _QUALIFICATION_MARKER in chapter_content:
                        covered = True
                    else:
                        # 兜底：检查是否有 evidence（来自主体材料或分析数据）
                        covered = bool(binding.get("evidence")) or covered
                requirement_items.append(
                    {
                        "chapter_title": chapter_title,
                        "target_title": binding["title"],
                        "requirement": binding.get("requirement", ""),
                        "status": "COVERED" if covered else ("MISSING" if (binding.get("require_blank") and not binding.get("evidence")) else "PENDING"),
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

# ============================================================================
# 承诺函/声明函 填空引擎（v2）
# ============================================================================

# 章节类型常量
CHAPTER_TYPE_TEXT_TEMPLATE = "TEMPLATE_TEXT"      # 文本填空（承诺函/声明函等）
CHAPTER_TYPE_TABLE_TEMPLATE = "TEMPLATE_TABLE"    # 表格填充（报价表/应答表等）
CHAPTER_TYPE_QUALIFICATION = "QUALIFICATION"      # 资格证明文件
CHAPTER_TYPE_FREE_WRITE = "FREE_WRITE"            # LLM 自由写作


def _classify_chapter_type(chapter_title, chapter_desc, tender_text=None):
    """分类章节类型，决定走哪条处理路径。

    返回:
        str: CHAPTER_TYPE_* 常量之一
    """
    if not chapter_title and not chapter_desc:
        return CHAPTER_TYPE_FREE_WRITE

    combined = f"{chapter_title} {chapter_desc}".strip()

    # 1. 文本模板检测（承诺函、声明函、授权书等固定格式文本）
    text_keywords = (
        "承诺函", "声明函", "响应函", "授权委托书", "授权书",
        "廉洁承诺书", "法定代表人身份证明", "法定代表人授权",
        "资质声明函", "身份证明",
        "无行贿犯罪记录", "无重大违法记录",
    )
    if any(kw in combined for kw in text_keywords):
        return CHAPTER_TYPE_TEXT_TEMPLATE

    # 2. 表格模板检测（报价表、偏离表、应答表、业绩表、人员情况表等）
    table_keywords = (
        "报价一览表", "报价表", "报价", "偏离表",
        "应答表", "业绩一览表", "人员情况表", "基本情况表",
        "商务要求偏离", "技术要求偏离", "商务应答", "技术应答",
    )
    if any(kw in combined for kw in table_keywords):
        return CHAPTER_TYPE_TABLE_TEMPLATE

    # 3. 资格证明检测
    qual_keywords = (
        "资格证明", "资格审查", "资质证明",
        "资格性审查", "符合性审查",
    )
    if any(kw in combined for kw in qual_keywords):
        return CHAPTER_TYPE_QUALIFICATION

    # 4. 其他 → LLM 写作
    return CHAPTER_TYPE_FREE_WRITE


# ========== 路径 A：文本填空引擎 ==========


# ============================================================================
# 路径 D：置信度门控系统
# ============================================================================
# 用于在数据进入生成流程前过滤脏数据。提供：
# 1. 字段格式校验（信用代码、电话等）
# 2. OCR 文本置信度评估
# 3. 知识库召回相关性门控

# 格式校验规则集：字段名 → (regex_pattern, description)
_FORMAT_VALIDATORS = {
    "credit_code": (r'^[0-9A-HJ-NPQRTUWXY]{18}$', "统一社会信用代码：18位字母数字（不含I/O/S/V/Z）"),
    "credit_code_loose": (r'^[0-9A-Za-z]{15,18}$', "统一社会信用代码（宽松）：15-18位字母数字"),
    "phone_mobile": (r'^1[3-9]\d{9}$', "手机号：11位，1开头"),
    "phone_landline": (r'^0\d{2,3}-\d{7,8}$', "固话：带区号"),
    "email": (r'^[\w.+-]+@[\w-]+(\.[\w-]+)+$', "邮箱地址"),
    "company_name": (r'.{2,100}', "公司名称：2-100字符"),
    "project_no": (r'^[A-Za-z0-9\-]+$', "项目编号：字母数字+连字符"),
    "amount": (r'^\d+(\.\d{1,2})?$', "金额：数字，最多2位小数"),
}


def _validate_field_format(field_name: str, value: str) -> tuple[bool, str]:
    """校验字段格式是否合规。

    Args:
        field_name: 字段名（如 "credit_code", "phone"）
        value: 待校验的值

    Returns:
        (is_valid: bool, message: str)
    """
    if not value or not value.strip():
        return False, "空值"

    value = value.strip()

    # 按优先级尝试匹配
    if field_name in ("credit_code", "统一社会信用代码"):
        patterns = ["credit_code", "credit_code_loose"]
    elif field_name in ("phone", "联系电话", "contact_phone"):
        patterns = ["phone_mobile", "phone_landline"]
    elif field_name in ("email",):
        patterns = ["email"]
    elif field_name in ("company_name", "公司名称"):
        patterns = ["company_name"]
    elif field_name in ("project_no", "项目编号"):
        patterns = ["project_no"]
    elif field_name in ("amount", "budget", "预算"):
        patterns = ["amount"]
    else:
        # 未知字段，只检查非空
        return bool(value.strip()), "未注册字段，仅非空校验"

    for pname in patterns:
        if pname not in _FORMAT_VALIDATORS:
            continue
        pattern, desc = _FORMAT_VALIDATORS[pname]
        if re.match(pattern, value):
            return True, desc

    # 都不匹配
    primary_pattern = _FORMAT_VALIDATORS.get(patterns[0], (None, "校验失败"))[0] if patterns else None
    return False, f"格式不符（期望：{_FORMAT_VALIDATORS.get(patterns[0], ('', '无'))[1] if patterns else '未知'}）"


def _compute_text_confidence(text: str, source: str = "ocr") -> float:
    """评估文本质量置信度（0.0 ~ 1.0）。

    适用于无法从源头获得置信度时的启发式评估。

    Args:
        text: 待评估文本
        source: 数据来源（"ocr", "kb_recall", "llm"）

    Returns:
        0.0 ~ 1.0 的置信度分数
    """
    if not text or not text.strip():
        return 0.0

    text = text.strip()
    length = len(text)

    if length < 5:
        return 0.2

    # 基础分
    score = 0.7

    # 中文字符占比（OCR 文本应该以中文为主）
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf')
    chinese_ratio = chinese_chars / length if length > 0 else 0

    if chinese_ratio > 0.5:
        score += 0.2
    elif chinese_ratio > 0.2:
        score += 0.1
    else:
        score -= 0.2  # 非中文为主的文本，大概率是乱码

    # 控制字符比例（已经在入口清洗，但评估原始质量）
    control_chars = sum(1 for c in text if ord(c) < 32 and c not in ('\n', '\r', '\t'))
    if control_chars > 0:
        score -= 0.2 * min(1.0, control_chars / max(length, 1))

    # 异常字符比例（非中文、非英文、非数字、非标点的字符）
    abnormal = sum(1 for c in text if ord(c) > 127 and not ('\u4e00' <= c <= '\u9fff')
                   and not ('\u3400' <= c <= '\u4dbf') and c not in ('\u3000', '\u3001', '\u3002',
                    '\uff0c', '\uff1b', '\uff1a', '\uff08', '\uff09', '\u2014', '\u2018',
                    '\u2019', '\u201c', '\u201d', '\u00b7'))
    if abnormal / max(length, 1) > 0.1:
        score -= 0.2

    # 来源特定调整
    if source == "kb_recall":
        # 知识库文本，稍微降低信任
        score -= 0.1

    return max(0.0, min(1.0, score))


def _filter_low_confidence_kb_snippets(knowledge_contexts: dict, min_score: float = 0.3) -> dict:
    """过滤低置信度的知识库片段。

    从 knowledge_contexts 中移除置信度低于阈值的片段。

    Args:
        knowledge_contexts: _build_knowledge_base_context 的返回值
        min_score: 最低保留分数（默认 0.3）

    Returns:
        过滤后的 knowledge_contexts
    """
    if not knowledge_contexts:
        return knowledge_contexts

    filtered = dict(knowledge_contexts)
    kb_list = filtered.get("knowledge_list", [])
    for kb in kb_list:
        snippets = kb.get("snippets", [])
        filtered_snippets = []
        for snip in snippets:
            score = _compute_text_confidence(snip, source="kb_recall")
            if score >= min_score:
                filtered_snippets.append(snip)
        kb["snippets"] = filtered_snippets
        kb["_filtered_count"] = len(snippets) - len(filtered_snippets)

    return filtered


def _filter_low_confidence_subject_materials(subject_context: dict, min_score: float = 0.5) -> dict:
    """为主体资料打置信度标签，供下游 LLM 路径使用。

    不阻断数据，不清除文本。低置信度内容由 LLM 路径自行决定是否使用。

    Args:
        subject_context: _build_subject_material_context 的返回值
        min_score: 置信度阈值（低于此值标记为 low_confidence）

    Returns:
        打上置信度标签的 subject_context
    """
    if not subject_context:
        return subject_context

    filtered = dict(subject_context)
    materials = list(filtered.get("materials", []))
    for mat in materials:
        excerpt = mat.get("text_excerpt", "")
        if excerpt:
            score = _compute_text_confidence(excerpt, source="ocr")
            rounded = round(score, 2)
            mat["_confidence"] = rounded
            mat["_low_confidence"] = rounded < min_score
            # 不清除文本，保留原文供用户参考

    return filtered


# 字段映射注册表：LLM hint 关键词 → 数据源 → 取值方法
_TEMPLATE_FIELD_MAP = [
    # 主体公司字段
    (("公司名称", "申请人名称", "单位名称", "供应商名称", "投标人名称", "比选申请人名称"),
     "subject", lambda ctx: ctx["subject"].get("company_name", "")),
    (("统一社会信用代码", "信用代码"),
     "subject", lambda ctx: ctx["subject"].get("credit_code", "")),
    (("联系电话", "电话", "手机"),
     "subject", lambda ctx: ctx["subject"].get("contact_phone", "")),
    (("联系地址", "地址", "通讯地址"),
     "subject", lambda ctx: ctx["subject"].get("address", "")),
    (("联系人",),
     "subject", lambda ctx: ctx["subject"].get("contact_person", "")),
    # 项目字段
    (("项目名称", "采购项目名称", "比选项目名称"),
     "analysis", lambda ctx: ctx["analysis"].get("project_name", "")),
    (("项目编号", "招标编号", "比选编号", "采购编号"),
     "analysis", lambda ctx: ctx["analysis"].get("project_no", "")),
    (("包号", "分包号"),
     "analysis", lambda ctx: ctx["analysis"].get("package_no", "")),
    (("采购人", "招标人", "业主"),
     "analysis", lambda ctx: ctx["analysis"].get("bidder_name", "")),
    (("代理机构", "采购代理机构", "招标代理"),
     "analysis", lambda ctx: ctx["analysis"].get("agent_name", "")),
    (("预算金额", "采购预算", "预算"),
     "analysis", lambda ctx: ctx["analysis"].get("budget_amount", "")),
    # 计算字段
    (("日期", "申请日期", "报价日期", "响应日期"),
     "calc", lambda ctx: utc_now().strftime("%Y年%m月%d日")),
    (("年",),
     "calc", lambda ctx: utc_now().strftime("%Y")),
    (("月",),
     "calc", lambda ctx: utc_now().strftime("%m")),
    (("日",),
     "calc", lambda ctx: utc_now().strftime("%d")),
]


def _build_template_field_map(subject_context, analysis_context):
    """构建模板填充用的字段值映射表。

    hint 关键词 → 实际值 的映射，支持同义词匹配。
    """
    context = {
        "subject": subject_context or {},
        "analysis": analysis_context.get("bidder_notice", {}) if analysis_context else {},
    }

    field_map = {}
    for keywords, source, getter in _TEMPLATE_FIELD_MAP:
        value = getter(context)
        if value:
            for kw in keywords:
                field_map[kw] = value

    return field_map


def _resolve_field_by_hint(hint, field_map):
    """根据 LLM 给出的字段 hint，在 field_map 中找最佳匹配值。

    策略：
    1. 精确匹配
    2. 包含匹配（hint in key 或 key in hint），取最长匹配
    """
    if not hint:
        return ""

    hint = hint.strip()

    # 精确匹配
    if hint in field_map:
        return field_map[hint]

    # 包含匹配
    best_key = ""
    best_value = ""
    for key, value in field_map.items():
        if key in hint or hint in key:
            if len(key) > len(best_key):
                best_key = key
                best_value = value

    return best_value


# 正则兜底模式集合（当 LLM 不可用时使用）
_FALLBACK_PLACEHOLDER_PATTERNS = [
    (r'(XXX|____|__________)[（(]([^）)]+)[）)]', 'bracket'),
    (r'(单位名称|法定代表人|授权代表|被授权人|联系电话|联系地址|项目名称|项目编号)[：:：]\s*(\_+|XXX)', 'field_value'),
    (r'(?<!（)XXX(?!（)', 'xxx_standalone'),
    (r'(?<![一-龥])\_{4,}(?![一-龥])', 'underline_standalone'),
]


def _fallback_extract_placeholders(text):
    """正则兜底：当 LLM 不可用时，用规则提取占位符。

    返回:
        list[dict]: [{"raw": "...", "start": N, "end": N, "hint": "..."}]
    """
    if not text:
        return []
    placeholders = []
    for pattern, ptype in _FALLBACK_PLACEHOLDER_PATTERNS:
        for match in re.finditer(pattern, text):
            if ptype == 'bracket' and len(match.groups()) > 1:
                hint = match.group(2).strip()
            elif ptype == 'field_value' and match.groups():
                hint = match.group(1).strip()
            else:
                hint = ""
            placeholders.append({
                "raw": match.group(0),
                "start": match.start(),
                "end": match.end(),
                "hint": hint,
            })
    placeholders.sort(key=lambda x: x["start"])
    return placeholders


def _identify_placeholders_via_llm(template_text, adapter=None):
    """调用 LLM 识别模板文本中的占位符。

    LLM 只做识别不填充，返回结构化占位符信息。
    如果 LLM 不可用，降级到正则兜底。

    返回:
        list[dict]: [{"raw": "...", "start": N, "end": N, "hint": "..."}]
    """
    if not template_text or not template_text.strip():
        return []

    # 尝试 LLM 识别
    llm_placeholders = []
    if adapter and adapter.is_available():
        try:
            prompt = (
                "你是一个占位符识别助手。找出下面文本中所有需要填写的空白位置。\n"
                "规则：\n"
                "1. 只识别，不填充，不改写原文\n"
                "2. 返回 JSON 数组格式，不要包含任何解释或其他文字\n"
                "3. 每个元素包含：raw(占位符原文), start(起始字符位置), end(结束位置), hint(推测字段含义)\n\n"
                "识别所有格式的占位符：\n"
                "- XXX（字段名）格式：XXX（比选申请人名称）\n"
                "- 下划线格式：______\n"
                "- 字段名+下划线格式：法定代表人：__________\n"
                "- 隐式空白：比选日期：  年   月   日\n"
                "- 方括号格式：【待填写】\n"
                "- 任何看起来需要填写的空白位置\n\n"
                "示例：\n"
                "本单位XXX（比选申请人名称）参加XXX（项目名称）的比选活动\n"
                '输出：[{"raw": "XXX（比选申请人名称）", "start": 3, "end": 16, "hint": "公司名称"},\n'
                ' {"raw": "XXX（项目名称）", "start": 19, "end": 28, "hint": "项目名称"}]\n\n'
                "文本：\n"
                f"{template_text[:2000]}\n\n"
                "如果文本中没有占位符，返回空数组 []。"
                "只返回 JSON 数组："
            )
            raw = adapter.generate_text(
                system_prompt="你是一个占位符识别助手，只输出 JSON 数组。",
                user_prompt=prompt,
                temperature=0.1,
                max_tokens=2000,
            )
            # 解析 JSON 结果
            import json as _json
            # 从返回中提取 JSON 数组
            json_match = re.search(r'\[.*?\]', raw.strip(), re.DOTALL)
            if json_match:
                llm_placeholders = _json.loads(json_match.group(0))
                if not isinstance(llm_placeholders, list):
                    llm_placeholders = []
                else:
                    logger.info("[template] LLM 识别占位符 %s 个", len(llm_placeholders))
        except Exception as exc:
            logger.warning("[template] LLM 占位符识别失败: %s", exc)

    # 正则仅做位置修正（不做兜底提取）
    regex_placeholders = _fallback_extract_placeholders(template_text)
    
    # 纯 LLM 模式：LLM 识别不到就返回空，不降级到正则
    if not llm_placeholders:
        logger.info("[template] LLM 未识别到占位符，保留原文")
        return []
    
    # 仅用正则修正 LLM 返回的位置偏移量
    seen_starts = {ph.get("start", -1) for ph in llm_placeholders}
    # 只在 LLM 已有结果时，用正则重算位置
    if regex_placeholders:
        # 为每个 LLM 识别的占位符找到原文中的准确位置
        corrected = []
        for ph in llm_placeholders:
            raw = ph.get("raw", "")
            hint = ph.get("hint", "")
            # 在原文中查找 raw 的实际位置
            start = template_text.find(raw)
            if start >= 0:
                corrected.append({
                    "raw": raw,
                    "start": start,
                    "end": start + len(raw),
                    "hint": hint,
                })
            else:
                # 如果 LLM 返回的 raw 在原文中找不到，尝试用 LLM 的 start 位置
                corrected.append(ph)
        return corrected
    
    logger.info("[template] LLM 识别占位符 %s 个", len(llm_placeholders))
    return llm_placeholders


def _fill_template(template_text, placeholders, field_map):
    """执行模板填充。

    参数：
        template_text: 模板原文
        placeholders: [{"raw": "...", "start": N, "end": N, "hint": "..."}]
        field_map: {hint关键词: 实际值}

    返回:
        (filled_text: str, unfilled: list[dict])
    """
    if not template_text:
        return "", []

    if not placeholders:
        return template_text, []

    # 从右向左替换，避免位置偏移
    unfilled = []
    result = list(template_text)

    for ph in reversed(sorted(placeholders, key=lambda x: x.get("start", 0))):
        raw = ph.get("raw", "")
        start = ph.get("start", 0)
        end = ph.get("end", 0)
        hint = ph.get("hint", "")

        if end > len(result) or start >= end:
            continue

        value = _resolve_field_by_hint(hint, field_map)
        if value:
            result[start:end] = value
        else:
            # 无对应值 → 留空白占位
            result[start:end] = "______"
            unfilled.append({"raw": raw, "hint": hint, "start": start})

    filled = "".join(result)
    
    # 替换后验证：检查是否还有未替换的占位符
    remaining_xxx = re.findall(r'XXX[（(]?', filled)
    remaining_ul = re.findall(r'_{4,}', filled)
    if remaining_xxx or remaining_ul:
        remaining_count = len(remaining_xxx) + len(remaining_ul)
        # 记录但不断流
        logger.info("[template] 填充后仍有 %s 个占位符未替换（XXX=%s, 下划线=%s）", 
                    remaining_count, len(remaining_xxx), len(remaining_ul))
    
    return filled, unfilled


def _verify_template_diff(original, filled):
    """校验填充结果：确保只变了占位符位置，没改其他原文。

    返回:
        (is_safe: bool, modified_positions: list[(start, end, before, after)])
    """
    if not original or not filled:
        return True, []

    # 逐字符比较
    modified = []
    o_idx, f_idx = 0, 0

    while o_idx < len(original) and f_idx < len(filled):
        if original[o_idx] == filled[f_idx]:
            o_idx += 1
            f_idx += 1
        else:
            # 记录差异
            o_start = o_idx
            f_start = f_idx
            # 找到差异结束位置
            while o_idx < len(original) and f_idx < len(filled) and original[o_idx] != filled[f_idx]:
                o_idx += 1
                f_idx += 1
            modified.append((f_start, f_idx,
                            original[o_start:o_idx],
                            filled[f_start:f_idx]))

    is_safe = not bool(modified) or all(
        len(after) <= len(before) + 20  # 允许小幅度长度变化（占位符→值）
        for _, _, before, after in modified
    )

    return is_safe, modified


def _template_has_meaningful_content(template_text):
    """判断模板文本是否包含有效内容（不只是占位符框架）。"""
    if not template_text or not template_text.strip():
        return False
    cleaned = re.sub(r'XXX|______|【[^】]+】|（[^）]*）', '', template_text).strip()
    return len(cleaned) > 30


def _extract_template_from_tender(chapter_info, tender_text):
    """从招标文件原文中提取与当前章节匹配的模板文本。

    策略：
    1. 用章节标题中的关键词在原文中定位
    2. 提取从标题行开始到下一个章节标题或文件末尾的内容
    """
    if not tender_text:
        return ""

    lines = tender_text.split("\n")

    # 从章节信息中提取搜索关键词
    search_raw = re.sub(r'^[\d一二三四五六七八九十]+[\s、.．,，、]\s*', '', chapter_info).strip()
    search_key = search_raw[:6] if len(search_raw) > 6 else search_raw
    if not search_key or len(search_key) < 2:
        return ""

    # 查找匹配行
    match_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        line_clean = re.sub(r'^[\d一二三四五六七八九十]+[\s、.．,，、]\s*', '', stripped).strip()
        if search_key in stripped or search_key in line_clean:
            match_idx = i
            break

    if match_idx < 0:
        return ""

    # 从匹配行开始，提取到下一个一级/二级标题或文件末尾
    extracted_lines = []
    next_section_pattern = re.compile(r'^[\d一二三四五六七八九十]+[\s、.．]')
    for line in lines[match_idx:]:
        stripped = line.strip()
        if extracted_lines and next_section_pattern.match(stripped):
            break
        extracted_lines.append(line)

    result = "\n".join(extracted_lines).strip()
    return result


def _detect_template_type(chapter_title, chapter_desc, tender_text):
    """检测当前章节是否为固定格式模板（承诺函/声明函等）。

    返回:
        (is_template: bool, template_text: str)
    """
    if not chapter_title and not chapter_desc:
        return False, ""

    combined = f"{chapter_title} {chapter_desc}".strip()

    matched = any(kw in combined for kw in (
        "承诺函", "声明函", "响应函", "授权委托书", "授权书",
        "法定代表人身份证明", "法定代表人授权", "廉洁承诺书",
        "资质声明函", "无行贿", "无重大违法",
    ))
    if not matched:
        return False, ""

    # 用章节标题（去掉编号）去原文中搜索，不要混入 desc
    search_title = re.sub(r'^[\d一二三四五六七八九十]+[\s、.．,，、]\s*', '', chapter_title).strip()
    template_text = _extract_template_from_tender(search_title or chapter_title, tender_text)
    return True, template_text




# ============================================================================
# 路径 B：表格填充引擎
# ============================================================================
# 常见表格模板的列结构定义
_TABLE_COLUMNS = {
    "报价一览表": ["序号", "标的名称", "规格型号", "数量", "单价（元）", "总价（元）", "备注"],
    "报价表": ["序号", "标的名称", "规格型号", "数量", "单价（元）", "总价（元）", "备注"],
    "商务要求偏离表": ["序号", "商务条款", "比选文件要求", "响应情况", "偏离说明", "备注"],
    "技术要求偏离表": ["序号", "技术条款", "比选文件要求", "响应情况", "偏离说明", "备注"],
    "商务应答表": ["序号", "商务条款", "比选文件要求", "响应情况", "偏离说明"],
    "技术应答表": ["序号", "技术条款", "比选文件要求", "响应情况", "偏离说明"],
    "业绩一览表": ["序号", "项目名称", "采购人名称", "合同金额", "签订时间", "证明材料"],
    "类似项目业绩一览表": ["序号", "项目名称", "采购人名称", "合同金额", "签订时间", "证明材料"],
    "基本情况表": ["单位名称", "注册地址", "统一社会信用代码", "法定代表人", "成立时间", "注册资本", "联系人", "联系电话"],
    "人员情况表": ["序号", "姓名", "职务", "职称", "学历", "专业", "相关经验", "拟任岗位"],
}


def _detect_table_columns(chapter_title, chapter_desc):
    """检测表格模板的列结构。

    返回:
        (columns: list[str], is_found: bool)
    """
    combined = f"{chapter_title} {chapter_desc}"
    for keyword, columns in _TABLE_COLUMNS.items():
        if keyword in combined:
            return columns, True
    # 默认列结构
    return ["序号", "内容", "要求", "响应", "备注"], False


def _extract_table_data_from_analysis(table_type, analysis_context, subject_context):
    """从分析结果中提取可用于表格填充的数据。

    返回:
        list[list[str]]: 数据行（不包含表头）
    """
    rows = []
    requirements_text = analysis_context.get("technical_requirements", "") or ""
    business_text = analysis_context.get("business_requirements", "") or ""
    bidder_notice = analysis_context.get("bidder_notice", {}) or {}

    if "报价" in table_type or "报价一览表" in table_type:
        # 优先使用原始表格结构（招标文件的原表复制）
        raw_tables = analysis_context.get("_raw_product_tables", [])
        if raw_tables:
            for rt in raw_tables:
                original_rows = rt.get("rows", [])
                original_headers = rt.get("headers", [])
                if not original_rows:
                    continue
                # 建立列名→标准字段映射，用于后续产品库填充
                header_to_std = {}
                for i, h in enumerate(original_headers):
                    for std_field, candidates in PRODUCT_COLUMN_MAP.items():
                        if any(c in h for c in candidates):
                            header_to_std[i] = std_field
                            break
                # 直接使用原始行数据（保留全部原始内容）
                for row_idx, row in enumerate(original_rows):
                    # 序号从 1 开始
                    new_row = list(row)
                    # 确保有足够的列
                    while len(new_row) < len(original_headers):
                        new_row.append("")
                    # 限制每列长度
                    new_row = [str(c)[:40] if c else "" for c in new_row]
                    rows.append(new_row)
            if rows:
                return rows
        
        # 降级：从 _raw_product_lists（结构化提取数据）获取产品数据
        product_data = analysis_context.get("_raw_product_lists", [])
        if product_data and isinstance(product_data[0], dict):
            for item in product_data:
                mapped = {}
                for std_field in PRODUCT_COLUMN_MAP:
                    candidates = PRODUCT_COLUMN_MAP[std_field]
                    matched_val = ""
                    for candidate in candidates:
                        val = item.get(candidate, "")
                        if val:
                            matched_val = val
                            break
                    mapped[std_field] = matched_val
                name = mapped.get("name", "") or ""
                spec = mapped.get("spec", "")
                unit = mapped.get("unit", "")
                qty = mapped.get("qty", "") or ""
                unit_price = mapped.get("unit_price", "")
                total_price = mapped.get("total_price", "")
                rows.append([str(len(rows) + 1),
                             name[:40] if name else "",
                             spec[:30] if spec else "",
                             qty,
                             unit_price,
                             total_price,
                             unit])
        if not rows:
            # 兜底：从 technical_requirements 中提取
            lines = requirements_text.split("\n")
            for line in lines:
                stripped = line.strip()
                if re.match(r'^\d+[.、]', stripped) and len(stripped) > 5:
                    clean = re.sub(r'^\d+[.、]\s*', '', stripped)
                    rows.append([str(len(rows) + 1), clean[:40], "", "", "", "", ""])
        if not rows:
            rows.append(["1", "", "", "", "", "", ""])
        return rows

    if "偏离" in table_type or "应答" in table_type:
        # 从 technical_requirements / business_requirements 提取条款
        source_text = requirements_text if "技术" in table_type else business_text
        lines = source_text.split("\n")
        for line in lines:
            stripped = line.strip()
            if re.match(r'^\d+[.、]', stripped) and len(stripped) > 5:
                clean = re.sub(r'^\d+[.、]\s*', '', stripped)
                rows.append([str(len(rows) + 1), clean[:60], clean[:60], "完全响应", "无偏离"])
        if not rows:
            rows.append(["1", "全部条款", "全部条款", "完全响应", "无偏离"])
        return rows

    if "业绩" in table_type:
        # 业绩表通常由用户自行填写
        rows.append(["1", "", "", "", "", ""])
        rows.append(["2", "", "", "", "", ""])
        return rows

    if "基本情况" in table_type:
        if subject_context:
            return [[
                subject_context.get("company_name", ""),
                subject_context.get("address", ""),
                subject_context.get("credit_code", ""),
                "",
                "",
                "",
                subject_context.get("contact_person", ""),
                subject_context.get("contact_phone", ""),
            ]]
        return [["", "", "", "", "", "", "", ""]]

    return rows


def _generate_table_content(chapter_title, chapter_desc, analysis_context, subject_context):
    """生成表格模板的填充内容（tab 分隔的文本格式）。

    策略变更：对报价/产品类表格，优先使用原始表格框架（招标文件原表复制），
    只从产品库填充空白单元格。没有原始表时降级到硬编码逻辑。

    返回:
        str: 包含表格数据的文本（_table_marker 开头）
    """
    table_type = chapter_title

    # ========== 新路径：原始表复用+填空（报价/产品类） ==========
    if "报价" in table_type or "报价一览表" in table_type:
        raw_tables = analysis_context.get("_raw_product_tables", [])
        if raw_tables:
            rt = raw_tables[0]
            original_headers = rt.get("headers", [])
            original_rows = rt.get("rows", [])
            if original_headers and original_rows:
                # 用原始表头作为表头
                columns = list(original_headers)
                # 填充空白单元格
                filled_rows = _fill_table_from_original(original_headers, original_rows)
                lines = []
                lines.append("\t".join(columns))
                for row in filled_rows:
                    padded = row + [""] * (len(columns) - len(row))
                    lines.append("\t".join(padded[:len(columns)]))
                marker = f"{_TABLE_MARKER_PREFIX}{table_type}]]"
                return marker + "\n" + "\n".join(lines)

    # ========== 旧路径：硬编码表格（非产品类原有逻辑） ==========
    columns, found = _detect_table_columns(chapter_title, chapter_desc)
    data_rows = _extract_table_data_from_analysis(table_type, analysis_context, subject_context)

    lines = []
    lines.append("\t".join(columns))
    for row in data_rows:
        padded = row + [""] * (len(columns) - len(row))
        lines.append("\t".join(padded[:len(columns)]))

    marker = f"{_TABLE_MARKER_PREFIX}{table_type}]]"
    return marker + "\n" + "\n".join(lines)


# ============================================================================
# 路径 C：资格证明文件插入引擎
# ============================================================================

# 资格证明关键词 → material_type 映射
_QUALIFICATION_MATERIAL_MAP = [
    ("营业执照", "BUSINESS_LICENSE"),
    ("法人证书", "BUSINESS_LICENSE"),
    ("统一社会信用代码", "BUSINESS_LICENSE"),
    ("法定代表人身份证明", "LEGAL_PERSON_STATEMENT"),
    ("法定代表人身份证", "LEGAL_PERSON_ID_CARD"),
    ("法人身份证", "LEGAL_PERSON_ID_CARD"),
    ("授权委托书", "AUTHORIZATION_LETTER"),
    ("授权书", "AUTHORIZATION_LETTER"),
    ("被授权人身份证", "AUTHORIZED_PERSON_ID_CARD"),
    ("资质声明函", "QUALIFICATION_DECLARATION"),
    ("资格声明", "QUALIFICATION_DECLARATION"),
    ("财务报表", "FINANCIAL_STATEMENT"),
    ("纳税", "FINANCIAL_STATEMENT"),
    ("社保", "FINANCIAL_STATEMENT"),
    ("廉洁承诺书", "INTEGRITY_COMMITMENT"),
    ("资质文件", "QUALIFICATION_FILE"),
    ("资质证书", "QUALIFICATION_FILE"),
    ("许可", "QUALIFICATION_FILE"),
]


def _extract_qualification_requirements(analysis_context):
    """从分析上下文提取资格证明文件要求清单。

    返回:
        list[dict]: [{"requirement": "...", "keyword": "...", "material_type": "..."}]
    """
    requirements = []

    # 从 qualification_requirements 中提取
    qual_text = analysis_context.get("qualification_requirements", "") or ""

    # 从 qualification_review 中提取
    qual_review = analysis_context.get("qualification_review", {}) or {}
    qual_check = qual_review.get("qualification_check", "") or ""
    conformity_check = qual_review.get("conformity_check", "") or ""

    combined = f"{qual_text}\n{qual_check}\n{conformity_check}"

    # 按行切分，提取资格要求（包括提供类关键词和法定合规性要求）
    for line in combined.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        # 匹配两类要求：
        # 1. 提供/提交类（文档证明材料）
        # 2. 法定合规类（具有、无行贿、依法等声明性要求）
        is_action_req = any(kw in stripped for kw in ["提供", "提交", "证明", "出具", "递交"])
        is_compliance_req = any(kw in stripped for kw in ["具有", "无行贿", "无重大违法", "依法", "良好", "独立承担民事责任"])
        
        if is_action_req or is_compliance_req:
            for keyword, material_type in _QUALIFICATION_MATERIAL_MAP:
                if keyword in stripped:
                    requirements.append({
                        "requirement": stripped[:120],
                        "keyword": keyword,
                        "material_type": material_type,
                    })
                    break
            else:
                # 匹配到合规要求但未匹配到材料类型时，标记为通用资质文件
                if is_compliance_req:
                    requirements.append({
                        "requirement": stripped[:120],
                        "keyword": "",
                        "material_type": "QUALIFICATION_FILE",
                    })

    # 去重（按 material_type）
    seen_types = set()
    unique_requirements = []
    for req in requirements:
        if req["material_type"] not in seen_types:
            seen_types.add(req["material_type"])
            unique_requirements.append(req)

    return unique_requirements


def _check_qualification_material_status(requirements, subject_context, knowledge_contexts=None):
    """检查每项资格要求对应的主体资料是否已上传。

    三级递进查找：
    1. 主体材料中匹配 → UPLOADED
    2. 知识库中检索 → KB_FOUND
    3. 都没有 → MISSING

    Args:
        requirements: 资格要求清单
        subject_context: 主体资料上下文
        knowledge_contexts: 知识库上下文（可选）

    返回:
        list[dict]: [{"requirement": "...", "material_type": "...",
                       "status": "UPLOADED|KB_FOUND|MISSING",
                       "material": {...}, "kb_excerpt": "..."}]
    """
    if not requirements:
        return []

    materials = (subject_context or {}).get("materials", []) or []
    result = []

    for req in requirements:
        mt = req["material_type"]
        keyword = req.get("keyword", "")
        # 确保 requirement 文本不含 XML 控制字符
        if req.get("requirement"):
            req["requirement"] = _strip_xml_control_chars(req["requirement"])
        
        # Level 1: 主体材料匹配
        matched = [m for m in materials if m.get("material_type") == mt]
        if matched:
            result.append({
                "requirement": req["requirement"],
                "material_type": mt,
                "status": "UPLOADED",
                "material": matched[0],
                "kb_excerpt": "",
            })
            continue
        
        # Level 2: 知识库检索
        kb_found = False
        kb_excerpt = ""
        if knowledge_contexts:
            for kb in knowledge_contexts.get("knowledge_list", []):
                for snippet in kb.get("snippets", []):
                    if not snippet:
                        continue
                    # 匹配关键词
                    if keyword and keyword in snippet:
                        kb_found = True
                        kb_excerpt = snippet[:200]
                        break
                    # 匹配 material_type 中文名
                    mt_label = {
                        "BUSINESS_LICENSE": "营业执照",
                        "QUALIFICATION_FILE": "资质文件",
                        "LEGAL_PERSON_ID_CARD": "法人身份证",
                        "AUTHORIZATION_LETTER": "授权委托书",
                        "AUTHORIZED_PERSON_ID_CARD": "被授权人身份证",
                        "QUALIFICATION_DECLARATION": "资质声明函",
                        "LEGAL_PERSON_STATEMENT": "法定代表人身份证明",
                        "FINANCIAL_STATEMENT": "财务报表",
                        "INTEGRITY_COMMITMENT": "廉洁承诺书",
                    }.get(mt, "")
                    if mt_label and mt_label in snippet:
                        kb_found = True
                        kb_excerpt = _strip_xml_control_chars(snippet)[:200]
                        break
                if kb_found:
                    break
        
        if kb_found:
            safe_kb_excerpt = _strip_xml_control_chars(kb_excerpt) if kb_excerpt else ""
            result.append({
                "requirement": req["requirement"],
                "material_type": mt,
                "status": "KB_FOUND",
                "material": None,
                "kb_excerpt": safe_kb_excerpt,
            })
        else:
            # Level 3: 都没有 → MISSING
            result.append({
                "requirement": req["requirement"],
                "material_type": mt,
                "status": "MISSING",
                "material": None,
                "kb_excerpt": "",
            })

    return result


def _generate_qualification_content(analysis_context, subject_context, knowledge_contexts=None, chapter=None):
    """生成资格证明文件的插入指令。

    三级递进查找：
    1. 先在主体材料中匹配
    2. 主体没有 → 去知识库检索
    3. 都没有 → 标记为待人工补充

    Args:
        analysis_context: 分析上下文
        subject_context: 主体资料上下文
        knowledge_contexts: 知识库上下文（用于二级查找）
        chapter: 当前章节信息（含 children 列表，优先使用 children 作为资格要求）

    返回:
        str: 含 _QUALIFICATION_MARKER 的内容，供 _build_docx_bytes 识别处理
    """
    # 优先使用目录 children 作为资格要求（来自 check_items 的结构化数据）
    children = (chapter or {}).get("children", []) or []
    if children:
        # 从 children 构建 requirements
        requirements = []
        for child in children:
            title = (child.get("title") or "").strip()
            desc = (child.get("description") or "").strip()
            if not title:
                continue
            # 从 title 中匹配材料类型
            matched_type = "QUALIFICATION_FILE"
            for keyword, material_type in _QUALIFICATION_MATERIAL_MAP:
                if keyword in title:
                    matched_type = material_type
                    break
            requirements.append({
                "requirement": (title + " " + desc).strip()[:120],
                "keyword": "",
                "material_type": matched_type,
            })
    else:
        # 降级：从文本分析提取
        requirements = _extract_qualification_requirements(analysis_context)
    
    status_list = _check_qualification_material_status(requirements, subject_context, knowledge_contexts)

    import json as _json
    data = {
        "items": status_list,
        "uploaded_count": sum(1 for s in status_list if s["status"] == "UPLOADED"),
        "kb_found_count": sum(1 for s in status_list if s["status"] == "KB_FOUND"),
        "missing_count": sum(1 for s in status_list if s["status"] == "MISSING"),
    }

    return _QUALIFICATION_MARKER + _json.dumps(data, ensure_ascii=False)


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

    # ========== 填空引擎（v2）：LLM 识别占位符 + 确定性替换 ==========
    chapter_type = _classify_chapter_type(chapter_title, chapter_desc)
    if chapter_type == CHAPTER_TYPE_TEXT_TEMPLATE:
        _, template_text = _detect_template_type(chapter_title, chapter_desc, effective_text)
        if template_text:
            field_map = _build_template_field_map(subject_context, analysis_context)
            # 优先用 LLM 识别占位符，降级到正则
            placeholders = _identify_placeholders_via_llm(template_text)
            filled, unfilled = _fill_template(template_text, placeholders, field_map)
            if _template_has_meaningful_content(filled):
                # 原文锁定校验
                is_safe, diffs = _verify_template_diff(template_text, filled)
                if not is_safe:
                    # 不降级到 LLM 生成（防止改写固定格式导致废标）
                    # 能填的填，填不了的原样保留
                    logger.warning("[template] 章节「%s」填充后原文锁定校验失败，保留填充后原文，%s个占位符未替换",
                                   chapter_title, len(unfilled))
                logger.info("[template] 章节「%s」填空完成，占位符%s个，未填充%s个",
                            chapter_title, len(placeholders), len(unfilled))
                return filled

    # ========== 表格填充引擎 ==========
    if chapter_type == CHAPTER_TYPE_TABLE_TEMPLATE:
        logger.info("[table] 章节「%s」使用表格引擎", chapter_title)
        return _generate_table_content(chapter_title, chapter_desc, analysis_context, subject_context)

    # ========== 资格证明文件填充引擎（三级递进：主体→知识库→留白） ==========
    if chapter_type == CHAPTER_TYPE_QUALIFICATION:
        logger.info("[qualification] 章节「%s」使用资格证明插入引擎（三级递进）", chapter_title)
        return _generate_qualification_content(analysis_context, subject_context, knowledge_contexts, chapter)

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
    # ========== 结构化分析数据（保留原始结构，非文本块） ==========
        _elig = analysis_context.get("_eligibility", {}) or {}
        if _elig and isinstance(_elig, dict):
            quals = _elig.get("qualifications", []) or []
            if quals:
                user_parts.append("\n[结构化] 资格要求清单（逐项）：")
                for idx, q in enumerate(quals, 1):
                    if isinstance(q, dict):
                        req = (q.get("requirement") or "").strip()
                        mat = (q.get("material") or q.get("required_material") or "").strip()
                        if req:
                            line_text = f"  {idx}. {req}"
                            if mat:
                                line_text += f" → 需提供材料：{mat}"
                            user_parts.append(line_text)
            starred = _elig.get("starred_requirements", []) or []
            if starred:
                user_parts.append("\n[结构化] ★ 实质性要求（必须完全响应）：")
                for idx, s in enumerate(starred, 1):
                    if isinstance(s, dict):
                        req = (s.get("requirement") or "").strip()
                        if req:
                            user_parts.append(f"  ★{idx}. {req}")
            disqs = _elig.get("disqualifications", []) or []
            if disqs:
                user_parts.append("\n[结构化] 废标条件（不可违反）：")
                for idx, d in enumerate(disqs, 1):
                    if isinstance(d, dict):
                        req = (d.get("requirement") or "").strip()
                        if req:
                            user_parts.append(f"  ✘{idx}. {req}")
        
        # 产品清单表
        _tc = analysis_context.get("_table_classification", {}) or {}
        if _tc and isinstance(_tc, dict):
            pl = _tc.get("product_lists", []) or []
            if pl:
                user_parts.append("\n[结构化] 产品清单表：")
                for ti, pl_item in enumerate(pl, 1):
                    items = pl_item.get("items", []) or []
                    for item in items:
                        if isinstance(item, dict):
                            name = item.get("采购产品名称", "") or item.get("产品名称", "") or ""
                            spec = item.get("★规格参数", "") or item.get("技术参数与性能指标", "") or ""
                            qty = item.get("★数量", "") or item.get("数量", "") or ""
                            limit = item.get("★单价最高限价", "") or item.get("单价最高限价", "") or ""
                            if name:
                                line_text = f"  - {name}"
                                if spec: line_text += f" | 规格：{spec[:60]}"
                                if qty: line_text += f" | 数量：{qty}"
                                if limit: line_text += f" | 限价：{limit}"
                                user_parts.append(line_text)
        
            tech_reqs = _tc.get("tech_requirements", []) or []
            if tech_reqs:
                user_parts.append("\n[结构化] 技术参数要求（逐项）：")
                for tr in tech_reqs:
                    if isinstance(tr, dict):
                        for item in tr.get("items", []) or []:
                            if isinstance(item, dict):
                                name = item.get("技术要求名称", "") or ""
                                param = item.get("技术参数与性能指标", "") or item.get("技术参数", "") or ""
                                if name:
                                    line_text = f"  - {name}"
                                    if param: line_text += f"：{param[:120]}"
                                    user_parts.append(line_text)
        
            biz_reqs = _tc.get("business_requirements", []) or []
            if biz_reqs:
                user_parts.append("\n[结构化] 商务要求（逐项）：")
                for br in biz_reqs:
                    if isinstance(br, dict):
                        for item in br.get("items", []) or []:
                            if isinstance(item, dict):
                                name = item.get("商务要求名称", "") or ""
                                val = item.get("商务要求内容", "") or ""
                                if name:
                                    line_text = f"  - {name}"
                                    if val: line_text += f"：{val[:120]}"
                                    user_parts.append(line_text)
        
        # 评分标准
        _sc = analysis_context.get("_scoring", {}) or {}
        if _sc and isinstance(_sc, dict):
            dims = _sc.get("dimensions", []) or []
            if dims:
                user_parts.append("\n[结构化] 评分维度：")
                for idx, dim in enumerate(dims, 1):
                    if isinstance(dim, dict):
                        name = (dim.get("name") or "").strip()
                        score = (dim.get("score") or "")
                        criteria = (dim.get("criteria") or dim.get("standard") or "").strip()
                        if name:
                            line_text = f"  {idx}. {name}（{score}分）"
                            if criteria: line_text += f" - {criteria[:100]}"
                            user_parts.append(line_text)
        
        # 核心产品
        _pkgs = analysis_context.get("_packages", []) or []
        if _pkgs:
            for pkg in _pkgs:
                if isinstance(pkg, dict):
                    params = pkg.get("parameters", {}) or {}
                    if params:
                        core_products = params.get("core_products", []) or []
                        if core_products:
                            user_parts.append("\n[结构化] 核心产品列表：")
                            for cp in core_products[:10]:
                                user_parts.append(f"  - {cp}")
        
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
    
    注意：二进制文件（图片、压缩包等）不会被解析为文本，
    直接返回空字符串，避免乱码写入文档。
    """
    if not file_record:
        return ""

    # 检查文件类型：跳过二进制/图片文件
    file_name = file_record.file_name or ""
    ext = (Path(file_name).suffix or "").lower()
    BINARY_EXTENSIONS = {
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
        ".webp", ".ico", ".svg",
        ".zip", ".rar", ".7z", ".tar", ".gz",
        ".exe", ".dll", ".so", ".dylib",
        ".pdf",  # PDF will be handled separately by DocumentParser
    }
    # PDF 有专门的解析器，不在此过滤
    BINARY_EXTENSIONS_FOR_SKIP = BINARY_EXTENSIONS - {".pdf"}
    if ext in BINARY_EXTENSIONS_FOR_SKIP:
        logger.debug("[file_text] 跳过二进制文件: %s (ext=%s)", file_name, ext)
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
    # 兜底：从 analysis_result 顶层字段获取（兼容分析未写入 bidder_notice 的情况）
    if not cover_item_name and analysis_result:
        cover_item_name = (getattr(analysis_result, "project_name", None) or
                          bidder_notice.get("标的名称", "") or "").strip()
    if not cover_project_no and analysis_result:
        cover_project_no = (getattr(analysis_result, "project_no", None) or "").strip()
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
    font.name = "\u4eff\u5b8b"
    font.size = Pt(12)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "\u4eff\u5b8b")
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

    _set_heading_style(1, "\u5b8b\u4f53", 22, True, 24, 12)
    _set_heading_style(2, "\u5b8b\u4f53", 14, True, 18, 8)
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
        cleaned = _strip_xml_control_chars(cleaned)
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
            # 安全网：确保每行文本不含 XML 控制字符
            safe_text = _strip_xml_control_chars(stripped)
            if not safe_text:
                continue
            p = doc.add_paragraph(safe_text)
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
                    # 表格内容也可能含有控制字符
                    safe_cell_text = _strip_xml_control_chars(cell_text)
                    cell.text = safe_cell_text
                    if row_idx == 0:
                        for paragraph in cell.paragraphs:
                            for run in paragraph.runs:
                                run.bold = True
                                run.font.size = Pt(12)
                                run.font.name = "\u4eff\u5b8b"
                                run.element.rPr.rFonts.set(qn("w:eastAsia"), "\u4eff\u5b8b")
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
        # 只显示有意义的文件名（排除系统生成的纯数字/哈希文件名）
        if file_name and not re.match(r'^[\d_]+(\.\w+)$', file_name):
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
            requirement = _strip_xml_control_chars(requirement)
            status = item.get("status") or "PENDING"
            source_reference = (item.get("source_reference") or "").strip()
            source_reference = _strip_xml_control_chars(source_reference)
            p = document.add_paragraph()
            p.style = document.styles["Normal"]
            p.paragraph_format.first_line_indent = Pt(0)
            head = p.add_run(f"{_strip_xml_control_chars(target_title)} [{status}]")
            head.bold = True
            head.font.name = "宋体"
            head.font.size = Pt(12)
            head.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
            if requirement:
                safe_req = _strip_xml_control_chars(requirement)
                detail = p.add_run(f"：{safe_req}")
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
            original_excerpt = _strip_xml_control_chars(original_excerpt)
            if item.get("requirement_level") == "REQUIRED" and original_excerpt:
                excerpt_para = document.add_paragraph(f"招标文件原文提示：{_strip_xml_control_chars(original_excerpt)}")
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
    run.font.name = "\u5b8b\u4f53"
    run.font.size = Pt(22)
    run.bold = True
    run.element.rPr.rFonts.set(qn("w:eastAsia"), "\u5b8b\u4f53")

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
        safe_value = _strip_xml_control_chars(str(value or ""))
        run = field_para.add_run(f"{label}\uff1a{safe_value}")
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
            p = document.add_paragraph(f"{indent_str}{_strip_xml_control_chars(title)}")
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
            # 留白一页：分页符 + 空白说明 + 分页符
            # 标题已在 _write_outline_item 开头通过 add_heading 写入
            # 分页符确保下个章节从下一页开始
            document.add_page_break()
            # 空白说明
            p = document.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run("（本节无内容）")
            run.font.size = Pt(14)
            run.font.color.rgb = RGBColor(0xAA, 0xAA, 0xAA)
            run.font.name = "仿宋"
            run.element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋")
            # 添加招标原文提示（如果有）
            plan_item = _find_plan_item(chapter_title_for_plan, title)
            original_excerpt = (plan_item.get("original_requirement_excerpt") or "").strip()
            original_excerpt = _strip_xml_control_chars(original_excerpt)
            if original_excerpt:
                excerpt_p = document.add_paragraph(f"招标文件原文要求：{original_excerpt}")
                excerpt_p.style = document.styles["Normal"]
                excerpt_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in excerpt_p.runs:
                    run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
                    run.font.size = Pt(10)
            # 分页符 → 下一个章节从下一页开始
            document.add_page_break()
            return

        if matched_content:
            # ========== 表格标记处理 ==========
            if isinstance(matched_content, str) and matched_content.startswith(_TABLE_MARKER_PREFIX):
                # 提取表格类型和 tab 分隔数据
                end_marker = matched_content.find("]]\n")
                if end_marker > 0:
                    table_data = matched_content[end_marker + 3:].strip()
                    if table_data:
                        _write_table_from_lines(document, table_data.split("\n"))
                else:
                    # 如果只有标记没有数据，写入说明
                    document.add_paragraph("（此处为表格模板，请根据实际情况填写）")
                _write_subject_materials_for_outline_item(title, desc)
                # 继续处理子项但不处理当前文本
                children = outline_item.get("children", [])
                for child in children:
                    _write_outline_item(child, level=level + 1, inherited_child_sections=child_sections, parent_title=chapter_title_for_plan)
                return

            # ========== 资格证明文件标记处理（三级递进：主体→知识库→留白） ==========
            if isinstance(matched_content, str) and matched_content.startswith(_QUALIFICATION_MARKER):
                import json as _json
                qual_data_str = matched_content[len(_QUALIFICATION_MARKER):]
                try:
                    qual_data = _json.loads(qual_data_str)
                    items = qual_data.get("items", [])
                    
                    # 统计状态
                    uploaded = qual_data.get("uploaded_count", 0)
                    kb_found = qual_data.get("kb_found_count", 0)
                    missing = qual_data.get("missing_count", 0)
                    total = uploaded + kb_found + missing
                    
                    document.add_paragraph("以下为本次需提交的资格证明材料清单及状态：")
                    for item in items:
                        req = item.get("requirement", "")
                        safe_req = _strip_xml_control_chars(req)
                        status = item.get("status", "MISSING")
                        
                        if status == "UPLOADED":
                            p = document.add_paragraph(f"✅ {safe_req}（主体已上传）")
                            p.style = document.styles["Normal"]
                        elif status == "KB_FOUND":
                            kb_excerpt = item.get("kb_excerpt", "")
                            if kb_excerpt:
                                p = document.add_paragraph(f"📄 {safe_req}（知识库检索到）")
                            else:
                                p = document.add_paragraph(f"📄 {safe_req}（知识库检索到）")
                            p.style = document.styles["Normal"]
                        else:
                            p = document.add_paragraph(f"⬜ {safe_req}（待人工补充）")
                            p.style = document.styles["Normal"]
                            if p.runs:
                                p.runs[0].font.color.rgb = RGBColor(0xCC, 0x00, 0x00)
                    
                    if missing > 0:
                        note = document.add_paragraph(
                            f"共需{total}项，"
                            f"主体已上传{uploaded}项，"
                            f"知识库检索到{kb_found}项，"
                            f"缺失{missing}项。缺失项请在[待人工补齐清单]中补充。"
                        )
                        note.style = document.styles["Normal"]
                        note.alignment = WD_ALIGN_PARAGRAPH.LEFT
                except Exception as exc:
                    logger.warning("[qualification] 资格证明数据解析失败: %s", exc)
                    document.add_paragraph("（资格证明文件处理异常，请在[待人工补齐清单]中查看）")
                _write_subject_materials_for_outline_item(title, desc)
                children_for_qual = outline_item.get("children", [])
                for child in children_for_qual:
                    _write_outline_item(child, level=level + 1, inherited_child_sections=child_sections, parent_title=chapter_title_for_plan)
                return

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
