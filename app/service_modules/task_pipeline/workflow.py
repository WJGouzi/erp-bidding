"""标书任务流程总入口，保留任务级接口并兼容导出分析、目录、生成子流程。"""

import logging; logger = logging.getLogger(__name__)
from datetime import datetime
from flask import current_app
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.utils import secure_filename

SORT_FIELDS = {"created_at", "updated_at", "task_name", "status", "bid_type"}

from ...core.extensions import db
from ...core.enums import TASK_STATUSES
from ...core.response import page_success
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
)
from ..chroma_files import delete_file_chroma_documents, ingest_file_to_chroma
from ..storage import StorageService
from ..common import log_operation
from .analysis import (
    _complete_analysis,
    save_review,
    get_analysis_result,
    get_check_items,
    get_packages,
    select_package,
    start_analyze,
)
from .catalog import confirm_catalog, extract_catalog_from_file, get_catalog_options, get_subject_templates
from .execution import _get_latest_task_execution
from .generate import (
    _complete_generate,
    download_result_file,
    get_generate_chapters,
    get_generate_config,
    get_generate_progress,
    retry_generate,
    save_generate_config,
    start_generate,
)
from .helpers import (
    _build_generate_retry_hint,
    _get_task_progress_value,
    _prepare_task_chapters,
    _validate_tender_file,
)


def _async_ingest_to_chroma(app, tender_record_id, payload, filename, bid_type, chroma_tenant, chroma_database, chroma_collection):
    """后台异步执行 ChromaDB 文件入库。"""
    with app.app_context():
        try:
            from ...domain import FileStorage
            tender_record = db.session.get(FileStorage, tender_record_id)
            if not tender_record:
                logger.error("[task] 文件记录不存在: id=%s", tender_record_id)
                return
            ingest_file_to_chroma(
                tender_record,
                filename=filename,
                payload=payload,
                chunk_id_prefix="tenderfile",
                chroma_tenant=chroma_tenant,
                chroma_database=chroma_database,
                chroma_collection=chroma_collection,
                metadata_builder=lambda index, chunk: {
                    "biz_type": "BIDDING_TENDER",
                    "file_id": tender_record_id,
                    "file_name": filename,
                    "bid_type": bid_type,
                    "chunk_index": index,
                },
            )
            db.session.commit()
            logger.info("[task] 后台 ChromaDB 入库完成: file=%s", filename)
        except Exception as exc:
            logger.error("[task] 后台 ChromaDB 入库失败: file=%s, %s", filename, exc)


def create_original_task(file_storage, bid_type, task_name=None, chroma_tenant=None, chroma_database=None, chroma_collection=None):
    """上传招标文件后创建原始标书任务（ChromaDB 入库后台异步执行）。"""
    import threading

    logger.info("[task] 创建原始任务 name=%s bid_type=%s", task_name, bid_type)
    if bid_type not in {"GOODS", "SERVICE", "ENGINEERING"}:
        raise ValueError("不支持的标书类型")
    if not file_storage:
        raise ValueError("招标文件不能为空")
    filename, _ = _validate_tender_file(file_storage)

    try:
        payload = file_storage.read()
        tender_record = StorageService.save_bytes(
            filename=filename,
            payload=payload,
            biz_type="BIDDING_TENDER",
            skip_file_storage=True,
            chroma_tenant=chroma_tenant or current_app.config.get("CHROMA_TENANT"),
            chroma_database=chroma_database or current_app.config.get("CHROMA_DATABASE"),
            chroma_collection=chroma_collection or current_app.config.get("CHROMA_COLLECTION"),
            content_type=file_storage.mimetype or "application/octet-stream",
        )
        shared_resource = BiddingSharedResource(
            bid_type=bid_type,
            tender_file_id=tender_record.id,
            reference_count=1,
        )
        db.session.add(shared_resource)
        db.session.flush()

        task = BiddingTask(
            task_name=task_name or tender_record.file_name,
            task_origin="ORIGINAL",
            shared_resource_id=shared_resource.id,
            tender_file_name=tender_record.file_name,
            bid_type=bid_type,
            status="UPLOADED",
            progress=10,
            current_step="analyze",
        )
        db.session.add(task)
        db.session.flush()
        shared_resource.root_task_id = task.id
        log_operation(
            module="task",
            action="create_original_task",
            target_type="BiddingTask",
            target_id=task.id,
            task_id=task.id,
            summary=f'创建原始标书任务: {task.task_name} (标书类型: {bid_type})',
            detail={"bid_type": bid_type, "tender_file_name": task.tender_file_name},
        )
        db.session.commit()
        # 后台异步执行 ChromaDB 入库，不阻塞响应
        _app = current_app._get_current_object()
        t = threading.Thread(
            target=_async_ingest_to_chroma,
            args=(
                _app,
                tender_record.id,
                payload,
                tender_record.file_name,
                bid_type,
                chroma_tenant or current_app.config.get("CHROMA_TENANT"),
                chroma_database or current_app.config.get("CHROMA_DATABASE"),
                chroma_collection or current_app.config.get("CHROMA_COLLECTION"),
            ),
            daemon=True,
        )
        t.start()
        return {
            **task.to_dict(),
            "tender_file": tender_record.to_dict(),
            "shared_resource": shared_resource.to_dict(),
        }
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("[task] 创建原始任务失败: %s", exc)
        raise RuntimeError(f"创建标书任务失败: {exc}") from exc


def list_tasks(page_no=1, page_size=10, keyword=None, status=None, bid_type=None, subject_id=None, date_from=None, date_to=None, sort_by=None, sort_order=None):
    """分页查询标书任务列表。"""
    query = BiddingTask.query.filter_by(deleted_flag=False)
    if keyword:
        query = query.filter(BiddingTask.tender_file_name.like(f"%{keyword}%"))
    if status:
        query = query.filter_by(status=status)
    if bid_type:
        query = query.filter_by(bid_type=bid_type)
    if subject_id is not None:
        query = query.filter_by(subject_id=int(subject_id))
    if date_from:
        query = query.filter(BiddingTask.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        query = query.filter(BiddingTask.created_at <= datetime.fromisoformat(date_to))
    if sort_by in SORT_FIELDS:
        sort_column = getattr(BiddingTask, sort_by)
        sort_column = sort_column.asc() if sort_order == "asc" else sort_column.desc()
    else:
        sort_column = BiddingTask.created_at.desc()
    pagination = query.order_by(sort_column).paginate(page=page_no, per_page=page_size, error_out=False)
    return page_success([item.to_dict() for item in pagination.items], pagination.total, page_no, page_size)


def get_task_stats():
    """统计标书任务概览，返回各状态和标书类型的任务数量。"""
    from datetime import timezone
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    total = BiddingTask.query.filter_by(deleted_flag=False).count()
    by_status = {}
    for entry in TASK_STATUSES:
        value = entry["value"]
        count = BiddingTask.query.filter_by(deleted_flag=False, status=value).count()
        if count > 0:
            by_status[value] = count
    by_bid_type = {}
    for value in ("GOODS", "SERVICE", "ENGINEERING"):
        count = BiddingTask.query.filter_by(deleted_flag=False, bid_type=value).count()
        if count > 0:
            by_bid_type[value] = count
    today_created = BiddingTask.query.filter(
        BiddingTask.deleted_flag.is_(False),
        BiddingTask.created_at >= today_start,
    ).count()
    today_completed = BiddingTask.query.filter(
        BiddingTask.deleted_flag.is_(False),
        BiddingTask.status == "GENERATED",
        BiddingTask.updated_at >= today_start,
    ).count()
    return {
        "total": total,
        "by_status": by_status,
        "by_bid_type": by_bid_type,
        "today_created": today_created,
        "today_completed": today_completed,
    }


def get_task_detail(task_id):
    """获取单个标书任务的完整详情。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    shared_resource = BiddingSharedResource.query.filter_by(id=task.shared_resource_id).first()
    current_execution = _get_latest_task_execution(task.id)
    chapter_records = (
        BiddingTaskChapter.query.filter_by(task_id=task.id)
        .order_by(BiddingTaskChapter.chapter_no.asc(), BiddingTaskChapter.id.asc())
        .all()
    )
    return {
        **task.to_dict(),
        "progress": _get_task_progress_value(task),
        "shared_resource": shared_resource.to_dict() if shared_resource else None,
        "attachments": _list_tender_attachments_by_shared_resource(task.shared_resource_id),
        "chapters": [item.to_dict() for item in chapter_records],
        "current_execution": current_execution.to_dict() if current_execution else None,
        **_build_generate_retry_hint(task, chapter_records),
    }


def get_current_step(task_id):
    """获取任务当前所在步骤与状态信息。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    current_execution = _get_latest_task_execution(task.id)
    chapter_records = (
        BiddingTaskChapter.query.filter_by(task_id=task.id)
        .order_by(BiddingTaskChapter.chapter_no.asc(), BiddingTaskChapter.id.asc())
        .all()
    )
    return {
        "task_id": task.id,
        "status": task.status,
        "progress": _get_task_progress_value(task),
        "current_step": task.current_step,
        "generate_stage_code": task.generate_stage_code,
        "generate_stage_message": task.generate_stage_message,
        "current_execution": current_execution.to_dict() if current_execution else None,
        **_build_generate_retry_hint(task, chapter_records),
    }


def create_derived_task(
    source_task_id,
    subject_id=None,
    task_name=None,
    model_type=None,
    use_knowledge_base=False,
    use_product_library=False,
    catalog_generation_level=None,
    word_count_level=None,
):
    """基于已有任务复用上游成果创建派生任务。"""
    logger.info("[task] 创建派生任务 source=%s name=%s", source_task_id, task_name)
    source_task = BiddingTask.query.filter_by(id=source_task_id, deleted_flag=False).first()
    if not source_task:
        raise LookupError("来源标书任务不存在")
    if source_task.status not in {"CATALOG_CONFIRMED", "GENERATING", "GENERATED"}:
        raise ValueError("当前任务尚未完成目录确认，不能再次生成")
    shared_resource = BiddingSharedResource.query.filter_by(id=source_task.shared_resource_id).first()
    if not shared_resource:
        raise LookupError("共享资源不存在")

    try:
        shared_resource.reference_count += 1
        task = BiddingTask(
            task_name=task_name or f"{source_task.task_name}-再次生成",
            task_origin="DERIVED",
            parent_task_id=source_task.id,
            shared_resource_id=source_task.shared_resource_id,
            tender_file_name=source_task.tender_file_name,
            bid_type=source_task.bid_type,
            subject_id=subject_id,
            status="CATALOG_CONFIRMED",
            progress=40,
            current_step="generate_config",
            model_type=model_type,
            use_knowledge_base=bool(use_knowledge_base),
            use_product_library=bool(use_product_library),
            catalog_generation_level=catalog_generation_level,
            word_count_level=word_count_level,
        )
        db.session.add(task)
        log_operation(
            module="task",
            action="create_derived_task",
            target_type="BiddingTask",
            target_id=task.id,
            task_id=task.id,
            summary=f'创建派生标书任务: {task.task_name}',
            detail={"source_task_id": source_task_id, "subject_id": subject_id, "model_type": model_type},
        )
        db.session.commit()
        return {**task.to_dict(), "shared_resource": shared_resource.to_dict()}
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("[task] 创建派生任务失败: %s", exc)
        raise RuntimeError(f"创建再次生成任务失败: {exc}") from exc


def _list_tender_attachments_by_shared_resource(shared_resource_id):
    """按共享资源读取招标文件附件列表。"""
    attachments = (
        BiddingTenderAttachment.query.filter_by(shared_resource_id=shared_resource_id)
        .order_by(BiddingTenderAttachment.uploaded_at.desc(), BiddingTenderAttachment.id.desc())
        .all()
    )
    items = []
    for item in attachments:
        file_record = FileStorage.query.filter_by(id=item.file_id, deleted_flag=False).first()
        items.append(
            {
                **item.to_dict(),
                "file": file_record.to_dict() if file_record else None,
            }
        )
    return items


def list_tender_attachments(task_id):
    """获取任务对应共享招标源下的附件列表。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    return {
        "task_id": task.id,
        "shared_resource_id": task.shared_resource_id,
        "items": _list_tender_attachments_by_shared_resource(task.shared_resource_id),
    }


def upload_tender_attachment(task_id, file_storage):
    """向任务所属的共享招标源上传一份附件。"""
    logger.info("[task] 上传附件 task=%s", task_id)
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    if not file_storage:
        raise ValueError("附件文件不能为空")
    _validate_tender_file(file_storage)

    shared_resource = BiddingSharedResource.query.filter_by(id=task.shared_resource_id).first()
    if not shared_resource:
        raise LookupError("共享资源不存在")

    try:
        file_record = StorageService.save_upload(
            file_storage=file_storage,
            biz_type="BIDDING_TENDER_ATTACHMENT",
            skip_file_storage=True,
            chroma_tenant=current_app.config.get("CHROMA_TENANT"),
            chroma_database=current_app.config.get("CHROMA_DATABASE"),
            chroma_collection=current_app.config.get("CHROMA_COLLECTION"),
        )
        attachment = BiddingTenderAttachment(
            shared_resource_id=shared_resource.id,
            file_id=file_record.id,
            file_name=file_record.file_name,
        )
        db.session.add(attachment)
        log_operation(
            module="task",
            action="upload_attachment",
            target_type="BiddingTenderAttachment",
            target_id=attachment.id,
            task_id=task.id,
            summary=f'上传招标附件: {file_record.file_name}',
            detail={"task_id": task.id, "shared_resource_id": shared_resource.id, "file_name": file_record.file_name},
        )
        db.session.commit()
        return {
            "task_id": task.id,
            "shared_resource_id": shared_resource.id,
            "attachment": {
                **attachment.to_dict(),
                "file": file_record.to_dict(),
            },
        }
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("[task] 上传附件失败: %s", exc)
        raise RuntimeError(f"上传招标附件失败: {exc}") from exc


def delete_tender_attachment(task_id, attachment_id):
    """删除任务所属共享招标源下的一份附件。"""
    logger.info("[task] 删除附件 task=%s attachment=%s", task_id, attachment_id)
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    attachment = BiddingTenderAttachment.query.filter_by(
        id=attachment_id,
        shared_resource_id=task.shared_resource_id,
    ).first()
    if not attachment:
        raise LookupError("招标附件不存在")

    try:
        _delete_file_storage_record(attachment.file_id)
        db.session.delete(attachment)
        log_operation(
            module="task",
            action="delete_attachment",
            target_type="BiddingTenderAttachment",
            target_id=attachment_id,
            task_id=task.id,
            summary=f'删除招标附件: {attachment.file_name}',
            detail={"task_id": task.id, "attachment_id": attachment_id, "file_name": attachment.file_name},
        )
        db.session.commit()
        return {
            "task_id": task.id,
            "attachment_id": attachment_id,
            "shared_resource_id": task.shared_resource_id,
        }
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("[task] 删除附件失败: %s", exc)
        raise RuntimeError(f"删除招标附件失败: {exc}") from exc


def _delete_file_storage_record(file_id):
    """删除任务关联文件的物理内容、向量内容和数据库记录。"""
    if not file_id:
        return
    file_record = db.session.get(FileStorage, file_id)
    if not file_record:
        return

    if file_record.chroma_doc_id:
        try:
            delete_file_chroma_documents(
                file_record,
                chroma_tenant=file_record.chroma_tenant,
                chroma_database=file_record.chroma_database,
                chroma_collection=file_record.chroma_collection,
            )
        except Exception:
            pass

    StorageService.delete(file_record)
    db.session.delete(file_record)


def _delete_task_runtime_records(task_id):
    """删除任务自身的章节记录和后台执行记录。"""
    chapter_records = BiddingTaskChapter.query.filter_by(task_id=task_id).all()
    execution_records = BiddingTaskExecution.query.filter_by(task_id=task_id).all()
    for item in chapter_records:
        db.session.delete(item)
    for item in execution_records:
        db.session.delete(item)


def _cleanup_shared_resource_records(shared_resource):
    """当共享资源不再被任何任务引用时，清理其分析数据和招标文件。"""
    analysis_result = BiddingAnalysisResult.query.filter_by(shared_resource_id=shared_resource.id).first()
    if analysis_result:
        db.session.delete(analysis_result)

    catalog_records = BiddingCatalog.query.filter_by(shared_resource_id=shared_resource.id).all()
    check_items = BiddingCheckItem.query.filter_by(shared_resource_id=shared_resource.id).all()
    for item in catalog_records:
        db.session.delete(item)
    for item in check_items:
        db.session.delete(item)

    attachment_records = BiddingTenderAttachment.query.filter_by(shared_resource_id=shared_resource.id).all()
    for item in attachment_records:
        _delete_file_storage_record(item.file_id)
        db.session.delete(item)

    _delete_file_storage_record(shared_resource.tender_file_id)
    db.session.delete(shared_resource)


def _delete_task_records(tasks):
    """删除一组任务及其引用资源，并根据剩余引用决定是否清理共享资源。"""
    if not tasks:
        return {"deleted_task_ids": [], "deleted_count": 0}

    normalized_tasks = sorted(tasks, key=lambda item: item.id)
    delete_task_ids = [item.id for item in normalized_tasks]
    shared_resource_ids = sorted({item.shared_resource_id for item in normalized_tasks if item.shared_resource_id})

    for task in normalized_tasks:
        _delete_task_runtime_records(task.id)
        _delete_file_storage_record(task.result_file_id)
        db.session.delete(task)

    db.session.flush()

    cleaned_shared_resource_ids = []
    for shared_resource_id in shared_resource_ids:
        shared_resource = db.session.get(BiddingSharedResource, shared_resource_id)
        if not shared_resource:
            continue
        remaining_tasks = (
            BiddingTask.query.filter(
                BiddingTask.shared_resource_id == shared_resource_id,
                BiddingTask.deleted_flag.is_(False),
                BiddingTask.id.notin_(delete_task_ids),
            )
            .order_by(BiddingTask.id.asc())
            .all()
        )
        if remaining_tasks:
            shared_resource.reference_count = len(remaining_tasks)
            shared_resource.root_task_id = remaining_tasks[0].id
            continue
        _cleanup_shared_resource_records(shared_resource)
        cleaned_shared_resource_ids.append(shared_resource_id)

    return {
        "deleted_task_ids": delete_task_ids,
        "deleted_count": len(delete_task_ids),
        "cleaned_shared_resource_ids": cleaned_shared_resource_ids,
    }


def delete_task(task_id):
    """删除单个标书任务，并按引用关系清理其共享资源。"""
    logger.info("[task] 删除任务 task=%s", task_id)
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")

    try:
        result = _delete_task_records([task])
        db.session.commit()
        log_operation(
            module="task",
            action="delete_task",
            target_type="BiddingTask",
            target_id=task_id,
            task_id=task_id,
            summary=f'删除标书任务: {task.task_name}',
            detail={"task_name": task.task_name, "cleaned_shared_resource_ids": result["cleaned_shared_resource_ids"]},
        )
        return {
            "task_id": task_id,
            "deleted": True,
            "cleaned_shared_resource_ids": result["cleaned_shared_resource_ids"],
        }
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("[task] 删除任务失败: %s", exc)
        raise RuntimeError(f"删除标书任务失败: {exc}") from exc


def batch_delete_tasks(task_ids):
    """批量删除多个标书任务，并统一处理共享资源引用。"""
    logger.info("[task] 批量删除任务 ids=%s", task_ids)
    if not isinstance(task_ids, list) or not task_ids:
        raise ValueError("task_ids 不能为空")

    normalized_task_ids = []
    for item in task_ids:
        task_id = int(item)
        if task_id not in normalized_task_ids:
            normalized_task_ids.append(task_id)

    tasks = (
        BiddingTask.query.filter(
            BiddingTask.id.in_(normalized_task_ids),
            BiddingTask.deleted_flag.is_(False),
        )
        .order_by(BiddingTask.id.asc())
        .all()
    )
    found_ids = [item.id for item in tasks]
    missing_ids = [item for item in normalized_task_ids if item not in found_ids]
    if missing_ids:
        raise LookupError("以下标书任务不存在: " + ",".join(str(item) for item in missing_ids))

    try:
        result = _delete_task_records(tasks)
        db.session.commit()
        log_operation(
            module="task",
            action="batch_delete_tasks",
            target_type="BiddingTask",
            summary=f'批量删除标书任务: {len(tasks)}个',
            detail={"task_ids": normalized_task_ids, "deleted_count": len(tasks), "cleaned_shared_resource_ids": result["cleaned_shared_resource_ids"]},
        )
        return result
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("[task] 批量删除任务失败: %s", exc)
        raise RuntimeError(f"批量删除标书任务失败: {exc}") from exc


__all__ = [
    "_complete_analysis",
    "_complete_generate",
    "_delete_task_records",
    "_prepare_task_chapters",
    "batch_delete_tasks",
    "get_task_stats",
    "delete_task",
    "delete_tender_attachment",
    "confirm_catalog",
    "save_review",
    "create_derived_task",
    "create_original_task",
    "download_result_file",
    "get_analysis_result",
    "get_catalog_options",
    "get_subject_templates",
    "extract_catalog_from_file",
    "get_check_items",
    "get_current_step",
    "get_generate_chapters",
    "get_generate_config",
    "get_generate_progress",
    "get_packages",
    "get_task_detail",
    "list_tender_attachments",
    "list_tasks",
    "retry_generate",
    "save_generate_config",
    "select_package",
    "start_analyze",
    "start_generate",
    "upload_tender_attachment",
]
