import logging; logger = logging.getLogger(__name__)
import re

from ..core.extensions import db
from ..domain import FileStorage, KnowledgeBase, KnowledgeBaseFile, SubjectCompany
from ..core.response import page_success
from .chroma_files import delete_file_chroma_documents
from .common import log_operation
from .storage import StorageService


def list_knowledge_bases(page_no=1, page_size=10, subject_id=None, keyword=None):
    """分页查询知识库列表。"""

    query = KnowledgeBase.query
    if subject_id is not None:
        sid = int(subject_id)
        if sid == -1:
            query = query.filter(KnowledgeBase.subject_id.is_(None))
        else:
            query = query.filter_by(subject_id=sid)
    if keyword:
        query = query.filter(KnowledgeBase.name.like(f"%{keyword}%"))
    pagination = query.order_by(KnowledgeBase.id.desc()).paginate(page=page_no, per_page=page_size, error_out=False)
    return page_success([item.to_dict() for item in pagination.items], pagination.total, page_no, page_size)


def get_knowledge_base_detail(knowledge_base_id):
    """获取知识库详情和已上传文件。"""

    kb = KnowledgeBase.query.filter_by(id=knowledge_base_id).first()
    if not kb:
        raise LookupError("知识库不存在")
    files = (
        KnowledgeBaseFile.query.filter_by(knowledge_base_id=knowledge_base_id)
        .order_by(KnowledgeBaseFile.id.desc())
        .all()
    )
    items = []
    for item in files:
        d = item.to_dict()
        fs = FileStorage.query.filter_by(id=item.file_id).first()
        d["chroma_ingested"] = bool(fs and fs.chroma_doc_id)
        items.append(d)
    return {
        **kb.to_dict(),
        "files": items,
    }


def save_knowledge_base(knowledge_base_id=None, **payload):
    """新增或更新知识库配置。"""
    logger.info("[kb] %s知识库: %s", "更新" if knowledge_base_id else "新增", payload.get("name", ""))

    if knowledge_base_id:
        kb = KnowledgeBase.query.filter_by(id=knowledge_base_id).first()
        if not kb:
            raise LookupError("知识库不存在")
    else:
        kb = KnowledgeBase()
    if not payload.get("name"):
        raise ValueError("知识库名称不能为空")
    raw_sid = payload.get("subject_id")
    if raw_sid is not None and int(raw_sid) == -1:
        kb.subject_id = None
    else:
        kb.subject_id = raw_sid
    if raw_sid is not None and int(raw_sid) != -1:
        subject = SubjectCompany.query.filter_by(id=int(raw_sid), status=True).first()
        if not subject:
            raise LookupError("主体公司不存在")
        kb.subject_id = int(raw_sid)
    if not knowledge_base_id:
        db.session.add(kb)
    kb.name = payload.get("name")
    kb.description = payload.get("description")
    kb.chroma_tenant = payload.get("chroma_tenant")
    kb.chroma_database = payload.get("chroma_database")
    kb.chroma_collection = payload.get("chroma_collection")
    action = "create_knowledge_base" if not knowledge_base_id else "update_knowledge_base"
    log_operation(
        module="knowledge_base",
        action=action,
        target_type="KnowledgeBase",
        target_id=kb.id,
        summary=f'{"创建" if not knowledge_base_id else "更新"}知识库: {kb.name}',
        detail={"name": kb.name, "subject_id": kb.subject_id},
    )
    db.session.commit()
    return kb.to_dict()


def _get_knowledge_base(knowledge_base_id):
    """读取知识库记录，不存在时抛出异常。"""

    kb = KnowledgeBase.query.filter_by(id=knowledge_base_id).first()
    if not kb:
        raise LookupError("知识库不存在")
    return kb


def _async_kb_ingest(app, file_record_id, filename, payload, kb_id, chroma_tenant, chroma_database, chroma_collection):
    """后台异步执行知识库文件 ChromaDB 入库。"""
    with app.app_context():
        try:
            from ..domain import FileStorage
            from .chroma_files import ingest_file_to_chroma
            file_record = db.session.get(FileStorage, file_record_id)
            if not file_record:
                logger.error("[kb] 文件记录不存在: id=%s", file_record_id)
                return
            ingest_file_to_chroma(
                file_record,
                filename=filename,
                payload=payload,
                chunk_id_prefix="kbfile",
                chroma_tenant=chroma_tenant,
                chroma_database=chroma_database,
                chroma_collection=chroma_collection,
                metadata_builder=lambda index, chunk: {
                    "knowledge_base_id": kb_id,
                    "file_id": file_record_id,
                    "file_name": filename,
                    "chunk_index": index,
                },
            )
            db.session.commit()
            logger.info("[kb] chroma_doc_id 已提交: id=%s, doc_id=%s", file_record.id, file_record.chroma_doc_id)
            logger.info("[kb] 后台 ChromaDB 入库完成: file=%s", filename)
        except Exception as exc:
            logger.error("[kb] 后台 ChromaDB 入库失败: file=%s, %s", filename, exc)


def _save_knowledge_base_file(kb, file_storage):
    """保存单个知识库文件：仅记录元数据到 MySQL，ChromaDB 入库后台异步执行。"""
    import threading
    from flask import current_app

    if not file_storage:
        raise ValueError("知识库文件不能为空")
    payload = file_storage.read()
    filename = re.sub(r'[\\/:*?"<>|\x00-\x1f\s]', '_', file_storage.filename or "knowledge_file")

    # 既保留原始文件流，又写入 Chroma，后续生成阶段才能复用原始图片页。
    file_record = StorageService.save_bytes(
        filename=filename,
        payload=payload,
        biz_type="KNOWLEDGE_BASE_FILE",
        chroma_tenant=kb.chroma_tenant,
        chroma_database=kb.chroma_database,
        chroma_collection=kb.chroma_collection,
        content_type=file_storage.mimetype or "application/octet-stream",
    )
    db.session.flush()
    db.session.commit()

    kb_file = KnowledgeBaseFile(
        knowledge_base_id=kb.id,
        file_id=file_record.id,
        file_name=filename,
        file_size=len(payload),
        reference_enabled=True,
    )
    db.session.add(kb_file)
    db.session.flush()
    db.session.commit()

    # 后台异步执行 ChromaDB 入库
    _app = current_app._get_current_object()
    t = threading.Thread(
        target=_async_kb_ingest,
        args=(
            _app, file_record.id, filename, payload, kb.id,
            kb.chroma_tenant, kb.chroma_database, kb.chroma_collection,
        ),
        daemon=True,
    )
    t.start()

    kb.file_count = KnowledgeBaseFile.query.filter_by(knowledge_base_id=kb.id).count()
    kb.total_size = (kb.total_size or 0) + len(payload)
    return {
        "file": kb_file,
        "file_record": file_record,
        "parsed_text_preview": "后台解析中...",
        "chunk_count": 0,
    }


def upload_knowledge_base_file(knowledge_base_id, file_storage):
    """上传单个知识库文件、解析文本并同步写入 Chroma。"""
    logger.info("[kb] 上传文件 kb=%s file=%s", knowledge_base_id, file_storage.filename if file_storage else "None")

    kb = _get_knowledge_base(knowledge_base_id)
    saved_result = _save_knowledge_base_file(kb, file_storage)
    log_operation(
        module="knowledge_base",
        action="upload_file",
        target_type="KnowledgeBaseFile",
        target_id=saved_result["file"].id,
        summary=f'上传知识库文件: {saved_result["file"].file_name} (共{saved_result["chunk_count"]}个切片)',
        detail={"knowledge_base_id": knowledge_base_id, "file_name": saved_result["file"].file_name, "chunk_count": saved_result["chunk_count"]},
    )
    db.session.commit()
    return {
        "knowledge_base": kb.to_dict(),
        "file": saved_result["file"].to_dict(),
        "parsed_text_preview": saved_result["parsed_text_preview"],
        "chunk_count": saved_result["chunk_count"],
    }


def upload_knowledge_base_files(knowledge_base_id, file_storages):
    """批量上传多个知识库文件，并逐个写入 Chroma。"""
    logger.info("[kb] 批量上传文件 kb=%s count=%s", knowledge_base_id, len([i for i in (file_storages or []) if i]))

    kb = _get_knowledge_base(knowledge_base_id)
    normalized_files = [item for item in (file_storages or []) if item]
    if not normalized_files:
        raise ValueError("知识库文件不能为空")

    items = []
    for file_storage in normalized_files:
        saved_result = _save_knowledge_base_file(kb, file_storage)
        items.append(
            {
                "file": saved_result["file"].to_dict(),
                "parsed_text_preview": saved_result["parsed_text_preview"],
                "chunk_count": saved_result["chunk_count"],
            }
        )

    log_operation(
        module="knowledge_base",
        action="upload_files_batch",
        target_type="KnowledgeBase",
        target_id=kb.id,
        summary=f'批量上传知识库文件: {len(items)}个文件',
        detail={"knowledge_base_id": kb.id, "uploaded_count": len(items)},
    )
    db.session.commit()
    return {
        "knowledge_base": kb.to_dict(),
        "items": items,
        "uploaded_count": len(items),
    }


def list_knowledge_base_files(knowledge_base_id, keyword=None):
    """查询知识库下的文件列表，可按文件名关键字过滤。"""

    _get_knowledge_base(knowledge_base_id)
    query = KnowledgeBaseFile.query.filter_by(knowledge_base_id=knowledge_base_id)
    if keyword:
        query = query.filter(KnowledgeBaseFile.file_name.like(f"%{keyword}%"))
    files = query.order_by(KnowledgeBaseFile.id.desc()).all()
    items = []
    for item in files:
        d = item.to_dict()
        # 查询关联的 FileStorage，判断 ChromaDB 是否已入库
        fs = FileStorage.query.filter_by(id=item.file_id).first()
        d["chroma_ingested"] = bool(fs and fs.chroma_doc_id)
        items.append(d)
    return {
        "knowledge_base_id": knowledge_base_id,
        "items": items,
    }


def update_knowledge_base_file_reference_status(knowledge_base_id, knowledge_base_file_id, reference_enabled):
    """更新知识库文件是否允许在生成阶段被引用。"""

    _get_knowledge_base(knowledge_base_id)
    kb_file = KnowledgeBaseFile.query.filter_by(id=knowledge_base_file_id, knowledge_base_id=knowledge_base_id).first()
    if not kb_file:
        raise LookupError("知识库文件不存在")
    kb_file.reference_enabled = bool(reference_enabled)
    log_operation(
        module="knowledge_base",
        action="update_file_reference",
        target_type="KnowledgeBaseFile",
        target_id=knowledge_base_file_id,
        summary=f'{"启用" if bool(reference_enabled) else "禁用"}知识库文件引用: {kb_file.file_name}',
        detail={"knowledge_base_id": knowledge_base_id, "file_name": kb_file.file_name, "reference_enabled": bool(reference_enabled)},
    )
    db.session.commit()
    return {
        "knowledge_base_id": knowledge_base_id,
        "file": kb_file.to_dict(),
    }


def delete_knowledge_base_file(knowledge_base_id, knowledge_base_file_id, commit=True):
    """删除知识库文件，并清理对应存储和向量数据。"""
    logger.info("[kb] 删除文件 kb=%s file=%s", knowledge_base_id, knowledge_base_file_id)

    kb = _get_knowledge_base(knowledge_base_id)
    kb_file = KnowledgeBaseFile.query.filter_by(id=knowledge_base_file_id, knowledge_base_id=knowledge_base_id).first()
    if not kb_file:
        raise LookupError("知识库文件不存在")
    file_record = FileStorage.query.filter_by(id=kb_file.file_id, deleted_flag=False).first()
    if file_record:
        try:
            delete_file_chroma_documents(
                file_record,
                chroma_tenant=kb.chroma_tenant,
                chroma_database=kb.chroma_database,
                chroma_collection=kb.chroma_collection,
            )
        except Exception as exc:
            logger.error("[kb] Chroma删除失败: %s", exc)
        StorageService.delete(file_record)
        db.session.delete(file_record)
        kb.total_size = max(0, (kb.total_size or 0) - (file_record.file_size or 0))
    db.session.delete(kb_file)
    db.session.flush()
    db.session.commit()
    kb.file_count = KnowledgeBaseFile.query.filter_by(knowledge_base_id=knowledge_base_id).count()
    if commit:
        log_operation(
            module="knowledge_base",
            action="delete_file",
            target_type="KnowledgeBaseFile",
            target_id=knowledge_base_file_id,
            summary=f'删除知识库文件: {kb_file.file_name}',
            detail={"knowledge_base_id": knowledge_base_id, "file_name": kb_file.file_name},
        )
        db.session.commit()
    return {"knowledge_base_id": knowledge_base_id, "file_count": kb.file_count, "total_size": kb.total_size}


def delete_knowledge_base(knowledge_base_id):
    """删除整个知识库及其全部文件。"""
    logger.info("[kb] 删除知识库 id=%s", knowledge_base_id)

    kb = _get_knowledge_base(knowledge_base_id)
    files = KnowledgeBaseFile.query.filter_by(knowledge_base_id=knowledge_base_id).all()
    for item in files:
        delete_knowledge_base_file(knowledge_base_id, item.id, commit=False)
    log_operation(
        module="knowledge_base",
        action="delete_knowledge_base",
        target_type="KnowledgeBase",
        target_id=knowledge_base_id,
        summary=f'删除知识库: {kb.name} (共{len(files)}个文件)',
        detail={"name": kb.name, "file_count": len(files)},
    )
    db.session.delete(kb)
    db.session.commit()
    return {"knowledge_base_id": knowledge_base_id}
