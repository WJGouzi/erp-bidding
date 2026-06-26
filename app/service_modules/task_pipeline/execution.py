"""标书任务后台执行相关逻辑，包括执行记录、取消、失败收敛与线程池提交。"""

import logging; logger = logging.getLogger(__name__)
import json
import re
import time
from io import BytesIO
from pathlib import Path

from docx import Document
from flask import current_app, send_file
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.utils import secure_filename

from ...core.extensions import db
from ...core.time_utils import utc_now
from ...domain import (
    BiddingAnalysisResult,
    BiddingCatalog,
    BiddingCheckItem,
    BiddingSharedResource,
    BiddingTask,
    BiddingTaskChapter,
    BiddingTaskExecution,
    FileStorage,
    KnowledgeBase,
    SubjectCompany,
    SubjectMaterialFile,
    TemplateCatalog,
)
from ...infrastructure.document_parser import DocumentParser
from ...infrastructure.integrations import ChromaAdapter, LLMAdapter, MinioAdapter
from ...infrastructure.task_queue import TaskQueueManager
from ...core.response import page_success
from ..common import (
    log_operation,
    CHAPTER_FIELD_UNSET,
    TENDER_ALLOWED_EXTENSIONS,
    TaskExecutionCancelledError,
    dump_json,
    get_subject_material_completeness,
    normalize_knowledge_base_ids,
    validate_subject_knowledge_bases,
)
from ..storage import StorageService


# 后台执行恢复与运行态查询。

from .helpers import _get_chapter_progress_floor, _get_failed_generate_stage, _update_chapter_runtime_state, _update_task_generate_stage


# 后台执行恢复与运行态查询。
def _load_execution_request_payload(execution):
    """解析后台执行记录中的请求参数。"""

    if not execution or not execution.request_payload:
        return {}
    try:
        payload = json.loads(execution.request_payload)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_recovery_runner(execution):
    """根据执行类型解析恢复所需的执行函数和参数。"""

    payload = _load_execution_request_payload(execution)
    if execution.execution_type == "ANALYZE":
        from .analysis import _complete_analysis

        return _complete_analysis, {"task_id": execution.task_id}, payload
    if execution.execution_type == "GENERATE":
        from .generate import _complete_generate

        return (
            _complete_generate,
            {
                "task_id": execution.task_id,
                "chapter_nos": payload.get("chapter_nos"),
                "retry_all": bool(payload.get("retry_all", False)),
            },
            payload,
        )
    raise ValueError(f"不支持恢复的后台执行类型: {execution.execution_type}")


def recover_background_tasks(app=None):
    """恢复服务重启前遗留的后台执行记录，并重新调度仍可恢复的任务。"""
    logger.info("[task] 开始恢复遗留后台任务")

    runtime_app = app or current_app._get_current_object()
    stale_executions = (
        BiddingTaskExecution.query.filter(BiddingTaskExecution.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]))
        .order_by(BiddingTaskExecution.id.asc())
        .all()
    )
    if not stale_executions:
        return {"recovered_execution_ids": [], "cancelled_execution_ids": [], "failed_execution_ids": []}

    recovered_execution_ids = []
    cancelled_execution_ids = []
    failed_execution_ids = []
    for execution in stale_executions:
        task = BiddingTask.query.filter_by(id=execution.task_id, deleted_flag=False).first()
        if execution.cancel_requested or execution.status == "CANCEL_REQUESTED":
            execution.status = "CANCELLED"
            execution.finished_at = utc_now()
            execution.error_message = execution.error_message or "服务重启前已收到取消请求，后台任务已取消"
            cancelled_execution_ids.append(execution.id)
            if task and task.status in {"ANALYZING", "GENERATING"}:
                task.status = "FAILED"
                task.error_message = execution.error_message
            continue
        if not task:
            execution.status = "INTERRUPTED"
            execution.finished_at = utc_now()
            execution.error_message = execution.error_message or "任务不存在，无法恢复后台执行"
            failed_execution_ids.append(execution.id)
            continue
        try:
            runner, runner_kwargs, request_payload = _resolve_recovery_runner(execution)
        except Exception as exc:
            execution.status = "INTERRUPTED"
            execution.finished_at = utc_now()
            execution.error_message = str(exc)
            failed_execution_ids.append(execution.id)
            if task.status in {"ANALYZING", "GENERATING"}:
                task.status = "FAILED"
                task.error_message = str(exc)
            continue
        execution.status = "QUEUED"
        execution.progress = max(0, min(99, int(execution.progress or 0)))
        execution.started_at = None
        execution.finished_at = None
        execution.error_message = None
        execution.cancel_requested = False
        recovered_execution_ids.append(execution.id)
        db.session.flush()
        _submit_background_execution(
            runtime_app,
            execution.task_id,
            execution.execution_type,
            runner,
            runner_kwargs=runner_kwargs,
            request_payload=request_payload,
            existing_execution_id=execution.id,
        )
    log_operation(
        module="task",
        action="recover_background_tasks",
        summary=f'恢复后台任务: 恢复{len(recovered_execution_ids)}个, 取消{len(cancelled_execution_ids)}个, 失败{len(failed_execution_ids)}个',
        detail={"recovered_count": len(recovered_execution_ids), "cancelled_count": len(cancelled_execution_ids), "failed_count": len(failed_execution_ids)},
    )
    db.session.commit()
    return {
        "recovered_execution_ids": recovered_execution_ids,
        "cancelled_execution_ids": cancelled_execution_ids,
        "failed_execution_ids": failed_execution_ids,
    }

def _get_task_queue_max_workers():
    """读取并规范化后台线程池并发配置。"""
    return max(10, int(current_app.config.get("TASK_EXECUTOR_MAX_WORKERS", 10)))

def get_task_runtime_snapshot():
    """汇总线程池与执行记录的运行时快照。"""
    runtime = TaskQueueManager.get_runtime_snapshot(_get_task_queue_max_workers())
    queued_count = BiddingTaskExecution.query.filter_by(status="QUEUED").count()
    running_count = BiddingTaskExecution.query.filter_by(status="RUNNING").count()
    cancel_requested_count = BiddingTaskExecution.query.filter_by(status="CANCEL_REQUESTED").count()
    return {
        **runtime,
        "queued_count": queued_count,
        "running_count": running_count,
        "cancel_requested_count": cancel_requested_count,
    }

def _create_task_execution(task_id, execution_type, request_payload=None):
    """创建一条新的后台执行记录。"""
    execution = BiddingTaskExecution(
        task_id=task_id,
        execution_type=execution_type,
        status="QUEUED",
        progress=0,
        request_payload=json.dumps(request_payload or {}, ensure_ascii=False),
    )
    db.session.add(execution)
    db.session.flush()
    return execution

def _update_execution_state(
    execution,
    *,
    status=None,
    progress=None,
    result_payload=CHAPTER_FIELD_UNSET,
    error_message=CHAPTER_FIELD_UNSET,
    started_at=CHAPTER_FIELD_UNSET,
    finished_at=CHAPTER_FIELD_UNSET,
    cancel_requested=CHAPTER_FIELD_UNSET,
):
    """更新后台执行记录的状态、进度和结果信息。"""
    if status is not None:
        execution.status = status
    if progress is not None:
        execution.progress = progress
    if result_payload is not CHAPTER_FIELD_UNSET:
        execution.result_payload = dump_json(result_payload)
    if error_message is not CHAPTER_FIELD_UNSET:
        execution.error_message = error_message
    if started_at is not CHAPTER_FIELD_UNSET:
        execution.started_at = started_at
    if finished_at is not CHAPTER_FIELD_UNSET:
        execution.finished_at = finished_at
    if cancel_requested is not CHAPTER_FIELD_UNSET:
        execution.cancel_requested = cancel_requested

def _set_execution_progress(execution_id, progress):
    """仅更新后台执行记录的进度百分比。"""
    execution = BiddingTaskExecution.query.filter_by(id=execution_id).first()
    if not execution or execution.status in {"SUCCESS", "FAILED", "CANCELLED", "INTERRUPTED"}:
        return
    execution.progress = max(0, min(100, int(progress)))
    db.session.commit()

def _assert_execution_active(execution_id):
    """校验后台执行是否仍处于可继续运行状态。"""
    execution = BiddingTaskExecution.query.filter_by(id=execution_id).first()
    if not execution:
        raise TaskExecutionCancelledError("后台任务已不存在")
    if execution.cancel_requested or execution.status == "CANCEL_REQUESTED":
        raise TaskExecutionCancelledError("后台任务已取消")
    timeout_seconds = float(current_app.config.get("TASK_EXECUTION_TIMEOUT_SECONDS", 600))
    if execution.started_at and timeout_seconds > 0:
        now = utc_now()
        started_at = execution.started_at
        if getattr(started_at, "tzinfo", None) is None:
            now = now.replace(tzinfo=None)
        elapsed_seconds = (now - started_at).total_seconds()
        if elapsed_seconds > timeout_seconds:
            raise RuntimeError(f"后台任务执行超时，超过 {int(timeout_seconds)} 秒")

def _get_latest_task_execution(task_id, execution_type=None):
    """获取任务最近的一条后台执行记录。"""
    query = BiddingTaskExecution.query.filter_by(task_id=task_id)
    if execution_type:
        query = query.filter_by(execution_type=execution_type)
    return query.order_by(BiddingTaskExecution.id.desc()).first()

def get_task_executions(task_id):
    """查询任务的后台执行历史列表。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    executions = (
        BiddingTaskExecution.query.filter_by(task_id=task_id)
        .order_by(BiddingTaskExecution.id.desc())
        .all()
    )
    return {
        "task_id": task_id,
        "executions": [item.to_dict() for item in executions],
    }

def get_current_task_execution(task_id):
    """获取任务当前或最近一次后台执行记录。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    execution = (
        BiddingTaskExecution.query.filter_by(task_id=task_id)
        .order_by(BiddingTaskExecution.id.desc())
        .first()
    )
    return {
        "task_id": task_id,
        "execution": execution.to_dict() if execution else None,
    }

def cancel_task_execution(task_id):
    """提交后台任务取消请求，并同步更新任务状态。"""
    logger.info("[task] 取消后台执行 task=%s", task_id)
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    execution = (
        BiddingTaskExecution.query.filter(
            BiddingTaskExecution.task_id == task_id,
            BiddingTaskExecution.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]),
        )
        .order_by(BiddingTaskExecution.id.desc())
        .first()
    )
    if not execution:
        raise ValueError("当前任务不存在可取消的后台执行")

    execution.cancel_requested = True
    if execution.status == "QUEUED":
        execution.status = "CANCELLED"
        execution.progress = 0
        execution.finished_at = utc_now()
    else:
        execution.status = "CANCEL_REQUESTED"
    task.status = "FAILED"
    task.error_message = "后台任务已取消"
    log_operation(
        module="task",
        action="cancel_execution",
        target_type="BiddingTask",
        target_id=task_id,
        task_id=task_id,
        summary=f'取消后台执行: {task.task_name} ({execution.execution_type})',
        detail={"task_id": task_id, "execution_type": execution.execution_type, "execution_status": execution.status},
    )
    db.session.commit()
    return {
        "task_id": task_id,
        "execution": execution.to_dict(),
    }



# 后台任务提交与失败收敛处理。
def _finalize_background_failure(task_id, execution_id, execution_type, exc):
    """统一处理后台执行失败后的任务与执行状态。"""
    logger.error("[task] 后台执行失败 type=%s task=%s exec=%s err=%s", execution_type, task_id, execution_id, exc)
    execution = BiddingTaskExecution.query.filter_by(id=execution_id).first()
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if isinstance(exc, TaskExecutionCancelledError):
        if execution:
            _update_execution_state(
                execution,
                status="CANCELLED",
                progress=execution.progress,
                error_message=str(exc),
                finished_at=utc_now(),
                cancel_requested=True,
            )
        if task:
            task.status = "FAILED"
            task.error_message = str(exc)
        db.session.commit()
        return

    if execution:
        _update_execution_state(
            execution,
            status="FAILED",
            progress=max(execution.progress or 0, 1),
            error_message=str(exc),
            finished_at=utc_now(),
        )
    if execution_type == "ANALYZE":
        if task:
            task.status = "FAILED"
            task.error_message = f"分析失败: {exc}"
    elif execution_type == "GENERATE":
        if task:
            failed_stage_code, failed_stage_message = _get_failed_generate_stage(task.generate_stage_code)
            task.status = "FAILED"
            _update_task_generate_stage(
                task,
                stage_code=failed_stage_code,
                stage_message=failed_stage_message,
                error_message=f"生成失败: {exc}",
            )
            running_chapter = (
                BiddingTaskChapter.query.filter_by(task_id=task.id, status="RUNNING")
                .order_by(BiddingTaskChapter.chapter_no.asc())
                .first()
            )
            if running_chapter:
                _update_chapter_runtime_state(
                    running_chapter,
                    status="FAILED",
                    progress=max(30, running_chapter.progress or 0),
                    stage_code="FAILED",
                    stage_message="章节生成失败",
                    error_message=str(exc),
                    finished_at=utc_now(),
                )
            task.progress = max(task.progress or 0, _get_chapter_progress_floor(task))
    db.session.commit()

def _submit_background_execution(
    app,
    task_id,
    execution_type,
    runner,
    runner_kwargs=None,
    request_payload=None,
    existing_execution_id=None,
):
    """向线程池提交后台任务；恢复场景会复用既有执行记录。"""

    execution = None
    if existing_execution_id:
        execution = BiddingTaskExecution.query.filter_by(id=existing_execution_id).first()
        if not execution:
            raise LookupError("待恢复的后台执行记录不存在")
        execution.execution_type = execution_type
        execution.request_payload = json.dumps(request_payload or {}, ensure_ascii=False)
        execution.result_payload = None
        execution.error_message = None
        execution.cancel_requested = False
    else:
        execution = _create_task_execution(task_id, execution_type, request_payload)
    execution_id = execution.id
    db.session.commit()

    def _worker():
        """在线程池工作线程中执行实际后台任务。"""
        with app.app_context():
            execution_record = BiddingTaskExecution.query.filter_by(id=execution_id).first()
            if not execution_record or execution_record.status == "CANCELLED":
                return
            _update_execution_state(
                execution_record,
                status="RUNNING",
                progress=1,
                started_at=utc_now(),
            )
            db.session.commit()
            try:
                result = runner(execution_id=execution_id, **(runner_kwargs or {}))
                execution_record = BiddingTaskExecution.query.filter_by(id=execution_id).first()
                if execution_record:
                    _update_execution_state(
                        execution_record,
                        status="SUCCESS",
                        progress=100,
                        result_payload=result,
                        error_message=None,
                        finished_at=utc_now(),
                    )
                    db.session.commit()
            except Exception as exc:  # pragma: no cover
                db.session.rollback()
                _finalize_background_failure(task_id, execution_id, execution_type, exc)
            finally:
                db.session.remove()

    TaskQueueManager.submit(
        _get_task_queue_max_workers(),
        f"{execution_type.lower()}-{task_id}-{execution_id}",
        _worker,
    )
    return execution
