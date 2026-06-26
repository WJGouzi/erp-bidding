import logging; logger = logging.getLogger(__name__)
from flask import current_app
from werkzeug.utils import secure_filename

from ..core.extensions import db
from ..core.response import page_success
from ..domain import FileStorage, SubjectCompany, SubjectMaterialFile
from .chroma_files import delete_file_chroma_documents, ingest_file_to_chroma
from .common import get_subject_material_completeness, log_operation
from .storage import StorageService


def list_subjects(page_no=1, page_size=10, keyword=None, status=None):
    """分页查询主体公司列表，并附带资料完整度。"""

    query = SubjectCompany.query
    if keyword:
        query = query.filter(SubjectCompany.company_name.like(f"%{keyword}%"))
    if status is not None:
        query = query.filter_by(status=bool(status))
    pagination = query.order_by(SubjectCompany.id.desc()).paginate(page=page_no, per_page=page_size, error_out=False)
    items = []
    for item in pagination.items:
        completeness = get_subject_material_completeness(item.id)
        items.append({**item.to_dict(), **completeness})
    return page_success(items, pagination.total, page_no, page_size)


def get_subject_detail(subject_id):
    """获取主体详情、资料列表和资料完整度。"""

    subject = SubjectCompany.query.filter_by(id=subject_id).first()
    if not subject:
        raise LookupError("主体公司不存在")
    materials = (
        SubjectMaterialFile.query.filter_by(subject_id=subject_id)
        .order_by(SubjectMaterialFile.uploaded_at.desc(), SubjectMaterialFile.id.desc())
        .all()
    )
    return {
        **subject.to_dict(),
        "materials": [item.to_dict() for item in materials],
        "material_completeness": get_subject_material_completeness(subject_id),
    }


def save_subject(subject_id=None, **payload):
    """新增或更新主体公司信息。"""
    logger.info("[subject] %s主体公司: %s", "更新" if subject_id else "新增", payload.get("company_name", ""))

    if subject_id:
        subject = SubjectCompany.query.filter_by(id=subject_id).first()
        if not subject:
            raise LookupError("主体公司不存在")
    else:
        subject = SubjectCompany()
        db.session.add(subject)

    company_name = payload.get("company_name")
    if not company_name:
        raise ValueError("主体公司名称不能为空")
    subject.company_name = company_name
    subject.chroma_tenant = payload.get("chroma_tenant")
    subject.chroma_database = payload.get("chroma_database")
    subject.chroma_collection = payload.get("chroma_collection")
    subject.credit_code = payload.get("credit_code")
    subject.contact_person = payload.get("contact_person")
    subject.contact_phone = payload.get("contact_phone")
    subject.address = payload.get("address")
    subject.remark = payload.get("remark")
    if "status" in payload and payload.get("status") is not None:
        subject.status = bool(payload.get("status"))
        db.session.commit()
    action = "create_subject" if not subject_id else "update_subject"
    log_operation(
        module="subject",
        action=action,
        target_type="SubjectCompany",
        target_id=subject.id,
        summary=f'{"创建" if not subject_id else "更新"}主体公司: {company_name}',
        detail={"company_name": company_name, "credit_code": payload.get("credit_code")},
    )
    db.session.commit()
    return subject.to_dict()


def delete_subject(subject_id):
    """停用主体公司。"""
    logger.info("[subject] 停用主体公司 ID=%s", subject_id)

    subject = SubjectCompany.query.filter_by(id=subject_id).first()
    if not subject:
        raise LookupError("主体公司不存在")
    subject.status = False
    log_operation(
        module="subject",
        action="delete_subject",
        target_type="SubjectCompany",
        target_id=subject_id,
        summary=f'停用主体公司: {subject.company_name}',
    )
    db.session.commit()
    return {"subject_id": subject_id, "status": subject.status}


def upload_subject_material(subject_id, material_type, file_storage):
    """向主体上传资料文件，并返回新的完整度状态。"""
    logger.info("[subject] 上传资料 subject=%s type=%s", subject_id, material_type)

    subject = SubjectCompany.query.filter_by(id=subject_id).first()
    if not subject:
        raise LookupError("主体公司不存在")
    if material_type not in {item["value"] for item in current_app.config.get("MATERIAL_TYPES_OVERRIDE", [])} and material_type not in {
        "BUSINESS_LICENSE",
        "QUALIFICATION_FILE",
        "LEGAL_PERSON_ID_CARD",
        "AUTHORIZATION_LETTER",
        "AUTHORIZED_PERSON_ID_CARD",
        "QUALIFICATION_DECLARATION",
        "LEGAL_PERSON_STATEMENT",
        "FINANCIAL_STATEMENT",
        "INTEGRITY_COMMITMENT",
    }:
        raise ValueError("资料类型不正确")
    if not file_storage:
        raise ValueError("资料文件不能为空")
    filename = secure_filename(file_storage.filename or "subject_material")
    payload = file_storage.read()
    file_record = StorageService.save_bytes(
        filename=filename,
        payload=payload,
        biz_type="SUBJECT_MATERIAL",
        chroma_tenant=subject.chroma_tenant or current_app.config.get("CHROMA_TENANT"),
        chroma_database=subject.chroma_database or current_app.config.get("CHROMA_DATABASE"),
        chroma_collection=subject.chroma_collection or current_app.config.get("CHROMA_COLLECTION"),
        content_type=file_storage.mimetype or "application/octet-stream",
    )
    ingest_file_to_chroma(
        file_record,
        filename=file_record.file_name,
        payload=payload,
        chunk_id_prefix="subjectmaterial",
        chroma_tenant=subject.chroma_tenant if subject else None,
        chroma_database=subject.chroma_database if subject else None,
        chroma_collection=subject.chroma_collection if subject else None,
        metadata_builder=lambda index, chunk: {
            "biz_type": "SUBJECT_MATERIAL",
            "subject_id": subject_id,
            "material_type": material_type,
            "file_id": file_record.id,
            "file_name": file_record.file_name,
            "chunk_index": index,
        },
    )
    material = SubjectMaterialFile(
        subject_id=subject_id,
        material_type=material_type,
        file_id=file_record.id,
        file_name=file_record.file_name,
    )
    db.session.add(material)
    log_operation(
        module="subject",
        action="upload_material",
        target_type="SubjectMaterialFile",
        target_id=material.id,
        summary=f'上传主体资料: {material.file_name} ({material_type})',
        detail={"subject_id": subject_id, "material_type": material_type, "file_name": material.file_name},
    )
    db.session.commit()
    return {
        "material": material.to_dict(),
        "material_completeness": get_subject_material_completeness(subject_id),
    }


def list_subject_materials(subject_id):
    """查询主体资料列表。"""

    subject = SubjectCompany.query.filter_by(id=subject_id).first()
    if not subject:
        raise LookupError("主体公司不存在")
    materials = (
        SubjectMaterialFile.query.filter_by(subject_id=subject_id)
        .order_by(SubjectMaterialFile.uploaded_at.desc(), SubjectMaterialFile.id.desc())
        .all()
    )
    return {
        "subject_id": subject_id,
        "items": [item.to_dict() for item in materials],
        "material_completeness": get_subject_material_completeness(subject_id),
    }


def delete_subject_material(subject_id, material_id):
    """删除主体资料及其关联文件。"""
    logger.info("[subject] 删除资料 subject=%s material=%s", subject_id, material_id)

    material = SubjectMaterialFile.query.filter_by(id=material_id, subject_id=subject_id).first()
    if not material:
        raise LookupError("主体资料不存在")
    file_record = FileStorage.query.filter_by(id=material.file_id, deleted_flag=False).first()
    if file_record:
        try:
            subject = db.session.get(SubjectCompany, subject_id)
            delete_file_chroma_documents(
                file_record,
                chroma_tenant=subject.chroma_tenant if subject else None,
                chroma_database=subject.chroma_database if subject else None,
            )
        except Exception as exc:
            logger.error("[subject] Chroma删除失败: %s", exc)
        StorageService.delete(file_record)
        db.session.delete(file_record)
    log_operation(
        module="subject",
        action="delete_material",
        target_type="SubjectMaterialFile",
        target_id=material_id,
        summary=f'删除主体资料: {material.file_name}',
        detail={"subject_id": subject_id, "material_type": material.material_type},
    )
    db.session.delete(material)
    db.session.commit()
    return {
        "subject_id": subject_id,
        "material_completeness": get_subject_material_completeness(subject_id),
    }
