import logging; logger = logging.getLogger(__name__)
import json

from ..core.extensions import db
from ..domain import TemplateCatalog
from ..core.response import page_success
from .common import log_operation


def list_template_catalogs(page_no=1, page_size=10, bid_type=None, keyword=None):
    """分页查询模板目录列表。"""

    query = TemplateCatalog.query
    if bid_type:
        query = query.filter_by(bid_type=bid_type)
    if keyword:
        query = query.filter(TemplateCatalog.template_name.like(f"%{keyword}%"))
    pagination = query.order_by(TemplateCatalog.id.desc()).paginate(page=page_no, per_page=page_size, error_out=False)
    return page_success([item.to_dict() for item in pagination.items], pagination.total, page_no, page_size)


def get_template_catalog_detail(template_id):
    """获取单个模板目录详情，并反序列化目录内容。"""

    template = TemplateCatalog.query.filter_by(id=template_id).first()
    if not template:
        raise LookupError("模板目录不存在")
    data = template.to_dict()
    try:
        data["catalog_content"] = json.loads(template.catalog_content)
    except json.JSONDecodeError:
        pass
    return data


def save_template_catalog(template_id=None, **payload):
    """新增或更新模板目录配置。"""
    logger.info("[template] %s模板: %s", "更新" if template_id else "新增", payload.get("template_name", ""))

    if template_id:
        template = TemplateCatalog.query.filter_by(id=template_id).first()
        if not template:
            raise LookupError("模板目录不存在")
    else:
        template = TemplateCatalog()
        db.session.add(template)
    if not payload.get("template_name"):
        raise ValueError("模板名称不能为空")
    if payload.get("bid_type") not in {"GOODS", "SERVICE", "ENGINEERING"}:
        raise ValueError("模板适用标书类型不正确")
    catalog_content = payload.get("catalog_content")
    if not catalog_content:
        raise ValueError("模板目录内容不能为空")
    template.template_name = payload.get("template_name")
    template.bid_type = payload.get("bid_type")
    template.template_desc = payload.get("template_desc")
    template.catalog_content = json.dumps(catalog_content, ensure_ascii=False)
    action = "create_template" if not template_id else "update_template"
    log_operation(
        module="template",
        action=action,
        target_type="TemplateCatalog",
        target_id=template.id,
        summary=f'{"创建" if not template_id else "更新"}模板目录: {template.template_name}',
        detail={"template_name": template.template_name, "bid_type": template.bid_type},
    )
    db.session.commit()
    return get_template_catalog_detail(template.id)


def delete_template_catalog(template_id):
    """删除指定模板目录。"""
    logger.info("[template] 删除模板 id=%s", template_id)

    template = TemplateCatalog.query.filter_by(id=template_id).first()
    if not template:
        raise LookupError("模板目录不存在")
    log_operation(
        module="template",
        action="delete_template",
        target_type="TemplateCatalog",
        target_id=template_id,
        summary=f'删除模板目录: {template.template_name}',
        detail={"template_name": template.template_name, "bid_type": template.bid_type},
    )
    db.session.delete(template)
    db.session.commit()
    return {"template_id": template_id}
