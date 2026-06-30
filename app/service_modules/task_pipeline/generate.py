"""标书任务生成阶段相关流程，包括生成配置、执行、重试、进度与下载。"""

import logging; logger = logging.getLogger(__name__)
from io import BytesIO
from pathlib import Path
import time

from flask import current_app, send_file
from werkzeug.utils import secure_filename

from ...core.extensions import db
from ...core.time_utils import utc_now
from ...domain import BiddingAnalysisResult, BiddingTask, BiddingTaskChapter, FileStorage
from ...infrastructure.integrations import MinioAdapter
from ..common import log_operation, normalize_knowledge_base_ids, validate_subject_knowledge_bases
from ..storage import StorageService
from .catalog import refresh_auto_catalog_content
from .execution import _assert_execution_active, _get_latest_task_execution, _set_execution_progress, _submit_background_execution
from .helpers import (
    _build_chapter_contents_from_records,
    _build_docx_bytes,
    _build_generate_retry_hint,
    _build_generation_coverage_snapshot,
    _get_generation_coverage_snapshot,
    _build_generation_plan_snapshot,
    _enrich_generation_plan_with_original_excerpts,
    _extract_analysis_context,
    _build_knowledge_base_context,
    _build_product_context,
    _build_subject_material_context,
    _ensure_task_chapters,
    _get_catalog_outline,
    _get_confirmed_catalog_record,
    _get_task_chapters,
    _get_task_progress_value,
    _maybe_fail_chapter_for_testing,
    _maybe_fail_generate_stage_for_testing,
    _prepare_task_chapters,
    _persist_generation_plan_snapshot,
    _persist_generation_coverage_snapshot,
    _verify_kb_citations,
    _refresh_generate_task_progress,
    _resolve_retry_chapter_nos,
    _update_chapter_runtime_state,
    _update_task_generate_stage,
    _validate_generate_prerequisites,
    _generate_chapter_content,
)


def _sync_existing_chapter_titles(task, catalog_record):
    """当自动目录被刷新后，同步更新已有章节标题。"""

    if not task or not catalog_record:
        return
    outline = _get_catalog_outline(catalog_record)
    if not outline:
        return
    chapter_records = (
        BiddingTaskChapter.query.filter_by(task_id=task.id)
        .order_by(BiddingTaskChapter.chapter_no.asc(), BiddingTaskChapter.id.asc())
        .all()
    )
    for chapter_record in chapter_records:
        if chapter_record.chapter_no <= 0 or chapter_record.chapter_no > len(outline):
            continue
        chapter_title = outline[chapter_record.chapter_no - 1].get("title") or chapter_record.chapter_title
        chapter_record.chapter_title = chapter_title


def _complete_generate(task_id, chapter_nos=None, retry_all=False, execution_id=None):
    """执行整本或局部章节的生成流程并落库结果。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    if task.status != "GENERATING":
        raise ValueError("当前任务不在生成中")
    if execution_id:
        _assert_execution_active(execution_id)
    _update_task_generate_stage(task, stage_code="CHAPTER_GENERATING", stage_message="正在生成章节正文")
    db.session.commit()

    delay_seconds = current_app.config.get("GENERATE_SIMULATE_DELAY", 0)
    if delay_seconds:
        time.sleep(delay_seconds)
    if execution_id:
        _assert_execution_active(execution_id)
        _set_execution_progress(execution_id, 10)

    analysis_result = BiddingAnalysisResult.query.filter_by(shared_resource_id=task.shared_resource_id).first()
    catalog_record = _get_confirmed_catalog_record(task)
    product_context = _build_product_context(task)
    subject_context = _build_subject_material_context(task.subject_id)
    outline = _get_catalog_outline(catalog_record) or [{"title": "综合响应", "description": ""}]
    chapter_records = _ensure_task_chapters(task, catalog_record)
    chapter_record_map = {item.chapter_no: item for item in chapter_records}
    target_chapter_nos = _resolve_retry_chapter_nos(chapter_nos, chapter_records, outline, retry_all=retry_all)
    analysis_context = _extract_analysis_context(analysis_result) if analysis_result else {}
    if analysis_result:
        generation_plan = _build_generation_plan_snapshot(
            outline,
            analysis_context=analysis_context,
            subject_context=subject_context,
            product_context=product_context,
        )
        generation_plan = _enrich_generation_plan_with_original_excerpts(generation_plan, analysis_result)
        _persist_generation_plan_snapshot(analysis_result, generation_plan)
        db.session.commit()

    # 预留空知识库上下文供后续组装docx使用（该函数不需要知识库内容）
    knowledge_contexts = {}
    for index, chapter in enumerate(outline, start=1):
        chapter_record = chapter_record_map.get(index)
        if not chapter_record:
            raise RuntimeError(f"章节记录不存在: {index}")
        if index not in target_chapter_nos:
            continue
        if execution_id:
            _assert_execution_active(execution_id)

        _update_chapter_runtime_state(
            chapter_record,
            status="RUNNING",
            progress=5,
            stage_code="PREPARING_CONTEXT",
            stage_message="正在准备章节生成上下文",
            error_message=None,
            started_at=utc_now(),
            finished_at=None,
            content_snapshot=None,
        )
        _refresh_generate_task_progress(task)
        db.session.commit()

        _update_chapter_runtime_state(
            chapter_record,
            progress=30,
            stage_code="GENERATING_CONTENT",
            stage_message="正在生成章节正文",
        )
        _refresh_generate_task_progress(task)
        db.session.commit()
        if execution_id:
            _set_execution_progress(execution_id, min(80, max(10, task.progress)))

        _maybe_fail_chapter_for_testing(index)
        # 每个章节单独查询知识库，使用章节标题+描述作为搜索文本
        chapter_query = f"{chapter.get('title', '')} {chapter.get('description', '')}"
        chapter_kb_context = _build_knowledge_base_context(task, query_text=chapter_query) if chapter_query.strip() else {}
        generated_content = _generate_chapter_content(
            task,
            chapter,
            analysis_result,
            subject_context=subject_context,
            knowledge_contexts=chapter_kb_context,
            product_context=product_context,
        )
        _update_chapter_runtime_state(
            chapter_record,
            status="SUCCESS",
            progress=100,
            stage_code="COMPLETED",
            stage_message="章节生成完成",
            error_message=None,
            finished_at=utc_now(),
            content_snapshot=generated_content,
        )
        # 知识库引用验证
        if generated_content and chapter_kb_context:
            try:
                citation_result = _verify_kb_citations(generated_content, chapter_kb_context)
                if citation_result.get("unverified"):
                    logger.info("[generate] 章节 %s 有 %s 处未验证引用", index, len(citation_result["unverified"]))
            except Exception as cite_exc:
                logger.warning("[generate] 引用验证异常: %s", cite_exc)
        _refresh_generate_task_progress(task)
        db.session.commit()
        if execution_id:
            _set_execution_progress(execution_id, min(90, max(10, task.progress)))

    chapter_records = (
        BiddingTaskChapter.query.filter_by(task_id=task.id)
        .order_by(BiddingTaskChapter.chapter_no.asc(), BiddingTaskChapter.id.asc())
        .all()
    )
    _update_task_generate_stage(task, stage_code="ASSEMBLING_CONTENT", stage_message="正在组装章节内容")
    task.progress = 96
    db.session.commit()
    if execution_id:
        _assert_execution_active(execution_id)
        _set_execution_progress(execution_id, 92)
    _maybe_fail_generate_stage_for_testing("ASSEMBLING_CONTENT")
    chapter_contents = _build_chapter_contents_from_records(chapter_records)
    if analysis_result:
        coverage_snapshot = _build_generation_coverage_snapshot(
            outline,
            chapter_contents,
            analysis_context=analysis_context,
            subject_context=subject_context,
            knowledge_contexts=knowledge_contexts,
            product_context=product_context,
            generation_plan=generation_plan,
        )
        _persist_generation_coverage_snapshot(analysis_result, coverage_snapshot)
        
        # 对标验证：如果有遗漏要求，写入任务级告警
        if coverage_snapshot.get("missing_requirements", 0) > 0:
            missing = coverage_snapshot.get("missing_items", [])
            missing_count = len(missing)
            # 最多报告5条遗漏要求
            top_missing = []
            for item in missing[:5]:
                title = item.get("target_title", "") or item.get("chapter_title", "")
                req = item.get("requirement", "")
                if req:
                    top_missing.append(f"{title}: {req[:60]}")
                else:
                    top_missing.append(title)
            if top_missing:
                warning_msg = f"标书生成完成，但有{missing_count}项招标要求未覆盖到标书中。遗漏项示例：{'；'.join(top_missing)}"
                task.error_message = warning_msg
                logger.warning("[generate] %s", warning_msg)

    _update_task_generate_stage(task, stage_code="BUILDING_DOCX", stage_message="正在构建结果文档")
    task.progress = 97
    db.session.commit()
    if execution_id:
        _assert_execution_active(execution_id)
        _set_execution_progress(execution_id, 95)
    _maybe_fail_generate_stage_for_testing("BUILDING_DOCX")
    content = _build_docx_bytes(task, catalog_record, analysis_result, knowledge_contexts, product_context, subject_context, chapter_contents)
    filename = f"{secure_filename(task.task_name) or 'bidding_task'}_result.docx"
    _update_task_generate_stage(task, stage_code="SAVING_RESULT", stage_message="正在保存结果文件")
    task.progress = 98
    db.session.commit()
    if execution_id:
        _assert_execution_active(execution_id)
        _set_execution_progress(execution_id, 98)
    _maybe_fail_generate_stage_for_testing("SAVING_RESULT")
    result_record = StorageService.save_bytes(
        filename=filename,
        payload=content,
        biz_type="BIDDING_RESULT",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    task.progress = 99
    task.result_file_id = result_record.id
    task.status = "GENERATED"
    task.progress = 100
    task.current_step = "done"
    _update_task_generate_stage(task, stage_code="COMPLETED", stage_message="标书生成完成", error_message=None)
    db.session.commit()
    if execution_id:
        _set_execution_progress(execution_id, 100)
    return {
        "task_id": task.id,
        "status": task.status,
        "progress": task.progress,
        "current_step": task.current_step,
        "result_file_id": task.result_file_id,
    }


def get_generate_config(task_id):
    """读取当前任务的生成配置。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    return {
        "task_id": task.id,
        "subject_id": task.subject_id,
        "model_type": task.model_type,
        "use_knowledge_base": task.use_knowledge_base,
        "knowledge_base_ids": normalize_knowledge_base_ids(task.knowledge_base_ids),
        "use_product_library": task.use_product_library,
        "catalog_generation_level": task.catalog_generation_level,
        "word_count_level": task.word_count_level,
    }


def save_generate_config(
    task_id,
    subject_id=None,
    model_type=None,
    use_knowledge_base=False,
    knowledge_base_ids=None,
    use_product_library=False,
    catalog_generation_level=None,
    word_count_level=None,
):
    """保存主体、模型和知识库等生成配置。"""
    logger.info("[task] 保存生成配置 task=%s model=%s", task_id, model_type)
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    if task.status not in {"CATALOG_CONFIRMED", "GENERATING", "GENERATED"}:
        raise ValueError("当前任务状态不允许保存生成配置")

    normalized_kb_ids = normalize_knowledge_base_ids(knowledge_base_ids)
    if use_knowledge_base:
        normalized_kb_ids = validate_subject_knowledge_bases(subject_id, normalized_kb_ids)

    task.subject_id = subject_id
    task.model_type = model_type
    task.use_knowledge_base = bool(use_knowledge_base)
    task.knowledge_base_ids = ",".join(str(item) for item in normalized_kb_ids) if normalized_kb_ids else None
    task.use_product_library = bool(use_product_library)
    task.catalog_generation_level = catalog_generation_level
    task.word_count_level = word_count_level
    catalog_record = refresh_auto_catalog_content(task)
    _sync_existing_chapter_titles(task, catalog_record)
    log_operation(
        module="task",
        action="save_generate_config",
        target_type="BiddingTask",
        target_id=task.id,
        task_id=task.id,
        summary="保存生成配置",
        detail={"task_id": task.id, "subject_id": subject_id, "model_type": model_type},
    )
    db.session.commit()
    return get_generate_config(task.id)


def start_generate(task_id):
    """启动整本标书生成后台任务。"""
    logger.info("[task] 启动生成 task=%s", task_id)
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    if task.status not in {"CATALOG_CONFIRMED", "GENERATED", "FAILED", "CANCELLED"}:
        raise ValueError("当前任务状态不允许启动生成")
    if task.status != "CATALOG_CONFIRMED":
        logger.info("[task] 重新执行生成，重置任务状态: task=%s, previous_status=%s", task_id, task.status)
        task.result_file_id = None
        task.error_message = None
    _validate_generate_prerequisites(task)
    catalog_record = _get_confirmed_catalog_record(task)

    _prepare_task_chapters(task, catalog_record)
    task.status = "GENERATING"
    task.current_step = "generate"
    _update_task_generate_stage(task, stage_code="CHAPTER_GENERATING", stage_message="等待生成启动", error_message=None)
    _refresh_generate_task_progress(task)
    app = current_app._get_current_object()

    if current_app.config.get("GENERATE_ASYNC", True):
        execution = _submit_background_execution(
            app,
            task.id,
            "GENERATE",
            _complete_generate,
            runner_kwargs={"task_id": task.id, "chapter_nos": None, "retry_all": True},
            request_payload={"task_id": task.id, "retry_all": False},
        )
        db.session.refresh(task)
        return {
            "task_id": task.id,
            "status": task.status,
            "progress": task.progress,
            "current_step": task.current_step,
            "generate_stage_code": task.generate_stage_code,
            "generate_stage_message": task.generate_stage_message,
            "background": True,
            "execution": execution.to_dict(),
            "chapters": _get_task_chapters(task.id),
        }
    log_operation(
        module="task",
        action="start_generate",
        target_type="BiddingTask",
        target_id=task.id,
        task_id=task.id,
        summary=f"启动标书生成: {task.task_name}",
        detail={"task_id": task.id},
    )
    db.session.commit()
    return _complete_generate(task.id, retry_all=True)


def retry_generate(task_id, chapter_nos=None, retry_all=False):
    """按章节或整本范围重试标书生成。"""
    logger.info("[task] 重试生成 task=%s retry_all=%s chapters=%s", task_id, retry_all, chapter_nos)
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    if task.status not in {"FAILED", "GENERATED", "CANCELLED"}:
        raise ValueError("当前任务状态不允许重试生成")

    _validate_generate_prerequisites(task)
    catalog_record = _get_confirmed_catalog_record(task)
    chapter_records = _ensure_task_chapters(task, catalog_record)
    target_chapter_nos = _resolve_retry_chapter_nos(
        chapter_nos,
        chapter_records,
        _get_catalog_outline(catalog_record) or [{"title": "综合响应", "description": ""}],
        retry_all=bool(retry_all),
    )

    for chapter_record in chapter_records:
        if chapter_record.chapter_no not in target_chapter_nos:
            continue
        _update_chapter_runtime_state(
            chapter_record,
            status="PENDING",
            progress=0,
            stage_code="QUEUED",
            stage_message="等待生成",
            error_message=None,
            finished_at=None,
        )

    task.status = "GENERATING"
    task.current_step = "generate"
    task.result_file_id = None
    _update_task_generate_stage(task, stage_code="CHAPTER_GENERATING", stage_message="等待重新生成启动", error_message=None)
    _refresh_generate_task_progress(task)
    app = current_app._get_current_object()

    if current_app.config.get("GENERATE_ASYNC", True):
        execution = _submit_background_execution(
            app,
            task.id,
            "GENERATE",
            _complete_generate,
            runner_kwargs={"task_id": task.id, "chapter_nos": target_chapter_nos, "retry_all": bool(retry_all)},
            request_payload={"task_id": task.id, "chapter_nos": target_chapter_nos, "retry_all": bool(retry_all)},
        )
        db.session.refresh(task)
        return {
            "task_id": task.id,
            "status": task.status,
            "progress": task.progress,
            "current_step": task.current_step,
            "generate_stage_code": task.generate_stage_code,
            "generate_stage_message": task.generate_stage_message,
            "background": True,
            "execution": execution.to_dict(),
            "retry_all": bool(retry_all),
            "retry_chapter_nos": target_chapter_nos,
            "chapters": _get_task_chapters(task.id),
        }
    db.session.commit()
    return _complete_generate(task.id, chapter_nos=target_chapter_nos, retry_all=bool(retry_all))


def get_generate_progress(task_id):
    """获取生成阶段的整体进度、阶段和重试提示。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    progress = _get_task_progress_value(task)
    current_execution = _get_latest_task_execution(task.id)
    chapter_records = (
        BiddingTaskChapter.query.filter_by(task_id=task.id)
        .order_by(BiddingTaskChapter.chapter_no.asc(), BiddingTaskChapter.id.asc())
        .all()
    )
    result_file = None
    if task.result_file_id:
        file_record = FileStorage.query.filter_by(id=task.result_file_id, deleted_flag=False).first()
        if file_record:
            result_file = file_record.to_dict()
    # 尝试读取对标报告
    coverage_report = None
    if task.shared_resource_id:
        try:
            from ...domain import BiddingAnalysisResult as _BAR
            _ar = _BAR.query.filter_by(shared_resource_id=task.shared_resource_id).first()
            if _ar:
                coverage_report = _get_generation_coverage_snapshot(_ar)
        except Exception:
            pass

    return {
        "task_id": task.id,
        "status": task.status,
        "progress": progress,
        "current_step": task.current_step,
        "generate_stage_code": task.generate_stage_code,
        "generate_stage_message": task.generate_stage_message,
        "result_file": result_file,
        "error_message": task.error_message,
        "chapters": [item.to_dict() for item in chapter_records],
        "current_execution": current_execution.to_dict() if current_execution else None,
        "coverage_report": coverage_report,
        **_build_generate_retry_hint(task, chapter_records),
    }


def get_generate_chapters(task_id):
    """获取章节级生成状态列表。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    return {"task_id": task.id, "chapters": _get_task_chapters(task.id)}


def download_result_file(task_id):
    """下载生成完成后的标书文件。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    if task.status != "GENERATED" or not task.result_file_id:
        raise ValueError("当前任务尚未生成完成，无法下载")
    file_record = FileStorage.query.filter_by(id=task.result_file_id, deleted_flag=False).first()
    if not file_record:
        raise LookupError("结果文件不存在")

    if file_record.storage_provider == "MINIO":
        endpoint = current_app.config.get("MINIO_ENDPOINT")
        access_key = current_app.config.get("MINIO_ACCESS_KEY")
        secret_key = current_app.config.get("MINIO_SECRET_KEY")
        bucket_name = current_app.config.get("MINIO_BUCKET_NAME")
        secure = current_app.config.get("MINIO_SECURE")
        adapter = MinioAdapter(endpoint, access_key, secret_key, bucket_name, secure)
        payload = adapter.download_bytes(file_record.minio_object_name)
    elif file_record.local_path and Path(file_record.local_path).exists():
        payload = Path(file_record.local_path).read_bytes()
    else:
        raise LookupError("结果文件物理内容不存在")

    return send_file(
        BytesIO(payload),
        as_attachment=True,
        download_name=file_record.file_name,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
