import logging; logger = logging.getLogger(__name__)
import json

from ..domain import KnowledgeBase, OperationLog, SubjectCompany, SubjectMaterialFile

CHAPTER_FIELD_UNSET = object()
TENDER_ALLOWED_EXTENSIONS = {"doc", "docx", "pdf"}
REQUIRED_SUBJECT_MATERIAL_TYPES = {
    "BUSINESS_LICENSE",
    "QUALIFICATION_FILE",
    "LEGAL_PERSON_ID_CARD",
}


class TaskExecutionCancelledError(RuntimeError):
    """表示后台执行已被取消或不再允许继续执行。"""

    pass


def get_subject_material_completeness(subject_id):
    """检查主体资料是否满足最小完整度要求。"""

    subject = SubjectCompany.query.filter_by(id=subject_id).first()
    if not subject:
        raise LookupError("主体公司不存在")
    materials = SubjectMaterialFile.query.filter_by(subject_id=subject_id).all()
    uploaded_types = {item.material_type for item in materials}
    missing_types = sorted(REQUIRED_SUBJECT_MATERIAL_TYPES - uploaded_types)
    return {
        "subject_id": subject_id,
        "is_complete": len(missing_types) == 0,
        "missing_material_types": missing_types,
    }


def normalize_knowledge_base_ids(knowledge_base_ids):
    """将知识库 ID 参数统一规范化为整数列表。"""

    if knowledge_base_ids is None:
        return []
    if isinstance(knowledge_base_ids, str):
        return [int(item) for item in knowledge_base_ids.split(",") if item.strip()]
    if isinstance(knowledge_base_ids, list):
        normalized = []
        for item in knowledge_base_ids:
            if item in (None, ""):
                continue
            normalized.append(int(item))
        return normalized
    raise ValueError("knowledge_base_ids 格式不正确")


def validate_subject_knowledge_bases(subject_id, knowledge_base_ids):
    """校验所选知识库是否存在且属于当前主体。"""

    kb_ids = normalize_knowledge_base_ids(knowledge_base_ids)
    if not kb_ids:
        return []
    if not subject_id:
        raise ValueError("使用知识库前必须先选择主体公司")

    knowledge_bases = KnowledgeBase.query.filter(KnowledgeBase.id.in_(kb_ids)).all()
    existing_map = {item.id: item for item in knowledge_bases}
    missing_ids = [item for item in kb_ids if item not in existing_map]
    if missing_ids:
        raise ValueError("知识库不存在: " + ",".join(str(item) for item in missing_ids))

    invalid_ids = [
        str(item.id)
        for item in knowledge_bases
        if item.subject_id is not None and int(item.subject_id) != int(subject_id)
    ]
    if invalid_ids:
        raise ValueError("知识库不属于当前所选主体: " + ",".join(invalid_ids))
    return kb_ids




def log_operation(module, action, target_type=None, target_id=None, task_id=None, summary=None, detail=None, result="SUCCESS"):
    """记录一条业务操作日志。

    Args:
        module: 操作所属模块名，如 subject, knowledge_base, template, task, chroma。
        action: 操作动作，如 create, update, delete, upload, analyze 等。
        target_type: 操作目标模型名，如 SubjectCompany, BiddingTask 等。
        target_id: 操作目标记录 ID。
        task_id: 关联的任务 ID（仅任务相关操作时填写）。
        summary: 操作的简要中文描述。
        detail: 操作详情字典，将被序列化为 JSON 字符串。
        result: 操作结果，SUCCESS 或 FAILED。
    """

    try:
        detail_json = json.dumps(detail, ensure_ascii=False) if detail else None
        record = OperationLog(
            module=module,
            action=action,
            target_type=target_type,
            target_id=target_id,
            task_id=task_id,
            summary=summary,
            detail=detail_json,
            result=result,
        )
        from ..core.extensions import db
        db.session.add(record)
    except Exception:
        pass



def dump_json(value):
    """将字典或列表序列化为 JSON 字符串，其他值原样返回。"""

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value
