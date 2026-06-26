"""标书任务目录阶段相关流程，包括目录候选生成与最终确认。"""

import logging; logger = logging.getLogger(__name__)
import json
from flask import current_app

from ...core.extensions import db
from ...domain import BiddingAnalysisResult, BiddingCatalog, BiddingSharedResource, BiddingTask, TemplateCatalog
from ..common import log_operation
from .helpers import _extract_analysis_context, _get_catalog_generation_profile, _normalize_catalog_generation_level


AUTO_GENERATED_CATALOG_SOURCE_TYPES = {"FROM_TENDER_REQUIREMENT", "FROM_TENDER_TEMPLATE"}


def _resolve_template_catalog(template_id, bid_type):
    """校验并返回模板库目录。"""

    if not template_id:
        raise ValueError("模板库来源必须传入模板ID")
    template = TemplateCatalog.query.filter_by(id=template_id).first()
    if not template:
        raise LookupError("模板目录不存在")
    if template.bid_type != bid_type:
        raise ValueError("模板目录与当前标书类型不匹配")
    return template


def _build_catalog_description(text, fallback, max_length=120):
    """将结构化分析字段裁剪为适合目录说明的摘要。"""

    normalized = (text or "").strip()
    if not normalized:
        normalized = fallback
    normalized = normalized.replace("\r", "\n")
    normalized = " ".join(item.strip() for item in normalized.splitlines() if item.strip())
    if len(normalized) > max_length:
        return normalized[:max_length].rstrip()
    return normalized


def _build_numbered_children(items):
    labels = ["（一）", "（二）", "（三）", "（四）", "（五）", "（六）", "（七）", "（八）"]
    children = []
    for index, item in enumerate(items):
        title = (item.get("title") or "").strip()
        description = (item.get("description") or "").strip()
        if not title or not description:
            continue
        prefix = labels[index] if index < len(labels) else f"（{index + 1}）"
        children.append({"title": f"{prefix}{title}", "description": description})
    return children


def _build_constrained_requirement_outline(task, analysis_result, generation_level=None):
    """为 tab1 生成受招标文件约束的目录结构。"""
    base = _build_dynamic_outline(task, analysis_result, variant="requirement", generation_level=generation_level)
    outline = base.get("outline", [])
    if not outline:
        return base

    analysis_context = _extract_analysis_context(analysis_result)
    bidder_notice = analysis_context.get("bidder_notice", {}) or {}
    qualification_review = analysis_context.get("qualification_review", {}) or {}
    effective_text = getattr(analysis_result, "effective_text", "") or getattr(analysis_result, "raw_text", "") or ""

    overview_children = _build_numbered_children(
        [
            {
                "title": "项目概况",
                "description": _build_catalog_description(
                    bidder_notice.get("overview", ""),
                    effective_text,
                    max_length=80,
                ),
            },
            {
                "title": "项目基础信息",
                "description": _build_catalog_description(
                    "；".join(
                        item
                        for item in [
                            f"标的名称：{bidder_notice.get('project_name', '').strip()}" if bidder_notice.get("project_name") else "",
                            f"项目编号：{bidder_notice.get('project_no', '').strip()}" if bidder_notice.get("project_no") else "",
                            f"包号：{bidder_notice.get('package_no', '').strip()}" if bidder_notice.get("package_no") else "",
                        ]
                        if item
                    ),
                    "",
                    max_length=80,
                ),
            },
        ]
    )

    technical_children = _build_numbered_children(
        [
            {
                "title": "技术要求响应",
                "description": _build_catalog_description(
                    analysis_context.get("technical_requirements", ""),
                    "",
                    max_length=100,
                ),
            },
            {
                "title": "招标要求原文对应项",
                "description": _build_catalog_description(
                    analysis_context.get("requirements", ""),
                    analysis_context.get("technical_requirements", ""),
                    max_length=100,
                ),
            },
        ]
    )

    business_children = _build_numbered_children(
        [
            {
                "title": "商务要求响应",
                "description": _build_catalog_description(
                    analysis_context.get("business_requirements", ""),
                    "",
                    max_length=100,
                ),
            },
            {
                "title": "资格性审查响应",
                "description": _build_catalog_description(
                    analysis_context.get("qualification_requirements", ""),
                    qualification_review.get("qualification_check", ""),
                    max_length=100,
                ),
            },
            {
                "title": "评分标准响应",
                "description": _build_catalog_description(
                    analysis_context.get("scoring_items", ""),
                    "",
                    max_length=100,
                ),
            },
            {
                "title": "废标项核查",
                "description": _build_catalog_description(
                    analysis_context.get("disqualification_items", ""),
                    qualification_review.get("disqualification_items", ""),
                    max_length=100,
                ),
            },
        ]
    )

    children_groups = [overview_children, technical_children, business_children]
    for index, item in enumerate(outline):
        item["children"] = children_groups[index] if index < len(children_groups) else []
    return {"outline": outline}



def _build_dynamic_outline_with_llm(task, analysis_result, text):
    """使用 LLM 从分析结果中生成带连续序号的目录大纲。
    
    返回统一格式的 outline JSON：
    [{"title": "一、XXX", "description": "...", "children": [{"title": "（一）XXX", "description": "..."}]}]
    """
    from ...infrastructure.integrations import LLMAdapter
    import json

    if not text:
        return [{"title": "一、综合响应", "description": "暂无招标依据文本"}]

    adapter = LLMAdapter(
        api_key=current_app.config.get("OPENAI_API_KEY"),
        base_url=current_app.config.get("OPENAI_BASE_URL"),
        default_model=current_app.config.get("OPENAI_MODEL_NAME"),
    )
    if not adapter.is_available():
        logger.warning("[catalog] LLM 不可用，跳过目录生成")
        return _build_fallback_outline(analysis_result, text)

    # 构建提示词上下文
    context_parts = []
    
    # 从 analysis_data (v2) 中提取结构化字段
    analysis_data = None
    if hasattr(analysis_result, "analysis_data") and analysis_result.analysis_data:
        try:
            analysis_data = json.loads(analysis_result.analysis_data)
        except (json.JSONDecodeError, TypeError):
            pass
    
    if analysis_data and analysis_data.get("version") == "v2":
        bn = analysis_data.get("bidder_notice", {})
        context_parts.append("=== 项目信息 ===")
        if bn.get("project_name"): context_parts.append(f"项目名称：{bn['project_name']}")
        if bn.get("project_no"): context_parts.append(f"项目编号：{bn['project_no']}")
        if bn.get("budget"): context_parts.append(f"预算：{bn['budget']}")
        if bn.get("overview"): context_parts.append(f"项目概况：{bn['overview']}")
        
        br = analysis_data.get("business_requirements", "")
        if br: context_parts.append(f"\n=== 商务要求 ===\n{br}")
        
        tr = analysis_data.get("technical_requirements", "")
        if tr: context_parts.append(f"\n=== 技术要求 ===\n{tr}")
        
        qr = analysis_data.get("qualification_review", {})
        if qr.get("qualification_check"): context_parts.append(f"\n=== 资格性审查 ===\n{qr['qualification_check']}")
        if qr.get("conformity_check"): context_parts.append(f"\n=== 符合性审查 ===\n{qr['conformity_check']}")
        if qr.get("disqualification_items"): context_parts.append(f"\n=== 废标项 ===\n{qr['disqualification_items']}")
        
        si = analysis_data.get("scoring_items", "")
        if si: context_parts.append(f"\n=== 评分标准 ===\n{si}")
    
    # 补充有效文本
    context_parts.append(f"\n=== 招标依据文本（节选）===\n{text[:3000]}")
    
    context_str = "\n".join(context_parts)

    system_prompt = (
        "你是一个投标文件目录生成专家。根据招标分析结果，"
        "生成一份结构完整、序号连续的投标文件目录大纲。"
    )

    user_prompt = (
        "根据以下招标分析信息，生成一份投标文件的目录大纲。\n\n"
        "要求：\n"
        "1. 目录章节按 一、二、三、四、五、六、七、八、九... 连续编号，不能跳号，必须生成7-12个顶级章节\n"
        "2. 每个顶级章节至少包含2-5个子章节，子章节按（一）（二）（三）... 编号\n"
        "3. 结合评分标准和招标要求，全面覆盖项目概述、商务要求、技术要求、资格性审查、符合性审查、\n"
        "   评分标准、报价要求、售后服务、项目实施等所有关键响应点\n"
        "4. 每个节点包含 title 和 description，子节点通过 children 数组表示\n"
        "5. 只返回 JSON，不要 markdown\n\n"
        "JSON 格式：\n"
        '{"outline": [\n'
        '  {"title": "一、章节标题", "description": "章节说明/评分点",\n'
        '   "children": [\n'
        '     {"title": "（一）子标题", "description": "子项说明"}\n'
        "   ]}\n"
        "]}\n\n"
        f"招标分析信息：\n{context_str[:6000]}"
    )

    try:
        raw = adapter.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=3000,
        )
        if not raw:
            return _build_fallback_outline(analysis_result, text)
        
        out = raw.strip()
        if out.startswith("```"):
            idx2 = out.find("\n")
            if idx2 > 0: out = out[idx2+1:]
        if out.endswith("```"):
            out = out[:-3].strip()
        
        brace_start = out.find("{")
        brace_end = out.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            out = out[brace_start:brace_end+1]
        
        data = json.loads(out)
        outline = data.get("outline", [])
        if isinstance(outline, list) and len(outline) > 0:
            return outline
        return _build_fallback_outline(analysis_result, text)
    except Exception as exc:
        logger.warning("[catalog] LLM 目录生成异常: %s", exc)
        return _build_fallback_outline(analysis_result, text)


def _build_fallback_outline(analysis_result, text):
    """LLM 不可用时的降级目录。"""
    return [
        {"title": "一、项目概述", "description": ""},
        {"title": "二、技术响应", "description": ""},
        {"title": "三、商务应答", "description": ""},
        {"title": "四、资格审查资料", "description": ""},
        {"title": "五、评分响应", "description": ""},
    ]



def _build_dynamic_outline(task, analysis_result, variant="requirement", generation_level=None):
    """基于结构化分析结果构建目录候选。"""

    catalog_profile = _get_catalog_generation_profile(generation_level or getattr(task, "catalog_generation_level", None))
    description_max_length = catalog_profile["description_max_length"]
    analysis_context = _extract_analysis_context(analysis_result)
    overview_text = _build_catalog_description(
        analysis_context.get("overview", ""),
        getattr(analysis_result, "effective_text", "") or getattr(analysis_result, "raw_text", "") or "暂无项目概述",
        max_length=description_max_length,
    )
    technical_text = _build_catalog_description(
        analysis_context.get("technical_requirements", ""),
        analysis_context.get("requirements", "") or overview_text,
        max_length=description_max_length,
    )
    business_text = _build_catalog_description(
        analysis_context.get("business_requirements", ""),
        analysis_context.get("qualification_requirements", "") or analysis_context.get("requirements", "") or overview_text,
        max_length=description_max_length,
    )
    score_text = _build_catalog_description(
        analysis_context.get("scoring_items", ""),
        analysis_context.get("disqualification_items", "") or business_text,
        max_length=description_max_length,
    )

    level = catalog_profile["level"]
    title_profiles = {
        "LOW": {
            "GOODS": {
                "requirement": ["项目概述", "技术响应", "商务应答"],
                "template": ["投标说明", "技术方案", "商务响应"],
            },
            "SERVICE": {
                "requirement": ["项目概述", "服务响应", "商务应答"],
                "template": ["投标说明", "服务方案", "商务响应"],
            },
            "ENGINEERING": {
                "requirement": ["项目概述", "施工响应", "商务应答"],
                "template": ["投标说明", "施工方案", "商务响应"],
            },
        },
        "MEDIUM": {
            "GOODS": {
                "requirement": ["项目概述与采购范围", "技术参数响应", "商务资质与评分应答"],
                "template": ["投标总体说明", "货物技术偏离与供货方案", "商务条款与资格响应"],
            },
            "SERVICE": {
                "requirement": ["项目概述与服务范围", "服务方案与技术响应", "商务资质与评分应答"],
                "template": ["投标总体说明", "服务流程与保障方案", "商务条款与资格响应"],
            },
            "ENGINEERING": {
                "requirement": ["项目概述与工程范围", "施工组织与技术响应", "商务资质与评分应答"],
                "template": ["投标总体说明", "施工组织设计与技术措施", "商务条款与资格响应"],
            },
        },
        "HIGH": {
            "GOODS": {
                "requirement": ["项目概述、采购范围与实施边界", "技术参数、兼容性与实施响应", "商务资质、评分点与风险控制应答"],
                "template": ["投标总体说明与实施承诺", "货物技术偏离、供货组织与实施方案", "商务条款、资格证明与评分响应"],
            },
            "SERVICE": {
                "requirement": ["项目概述、服务范围与交付边界", "服务方案、技术路线与保障机制", "商务资质、评分点与风险控制应答"],
                "template": ["投标总体说明与服务承诺", "服务流程、技术路线与保障方案", "商务条款、资格证明与评分响应"],
            },
            "ENGINEERING": {
                "requirement": ["项目概述、工程范围与实施边界", "施工组织、技术措施与资源配置", "商务资质、评分点与风险控制应答"],
                "template": ["投标总体说明与履约承诺", "施工组织设计、技术措施与资源方案", "商务条款、资格证明与评分响应"],
            },
        },
    }
    bid_type_titles = title_profiles.get(level, {}).get(task.bid_type, {})
    titles = bid_type_titles.get(variant) or bid_type_titles.get("requirement") or ["项目概述", "需求响应", "商务应答"]
    if level == "LOW":
        descriptions = [overview_text, technical_text, business_text]
    elif level == "HIGH":
        descriptions = [
            f"{overview_text} {analysis_context.get('requirements', '')[:60]}".strip(),
            f"{technical_text} {analysis_context.get('technical_requirements', '')[:60]}".strip(),
            f"{business_text} {score_text} {analysis_context.get('disqualification_items', '')[:60]}".strip(),
        ]
    else:
        descriptions = [overview_text, technical_text, f"{business_text} {score_text}".strip()]
    return {
        "outline": [
            {"id": "1", "title": titles[0], "description": descriptions[0]},
            {"id": "2", "title": titles[1], "description": descriptions[1]},
            {"id": "3", "title": titles[2], "description": descriptions[2]},
        ]
    }


def _build_auto_catalog_content(task, analysis_result, catalog_source_type, generation_level=None):
    """按目录来源和颗粒度生成自动目录内容。"""

    source_type = catalog_source_type or "FROM_TENDER_REQUIREMENT"
    if source_type == "FROM_TENDER_REQUIREMENT":
        return _build_constrained_requirement_outline(task, analysis_result, generation_level=generation_level)
    variant = "template" if source_type == "FROM_TENDER_TEMPLATE" else "requirement"
    return _build_dynamic_outline(task, analysis_result, variant=variant, generation_level=generation_level)


def refresh_auto_catalog_content(task):
    """在生成配置保存后刷新自动生成目录的内容。"""

    if not task or not task.shared_resource_id:
        return None
    catalog_record = BiddingCatalog.query.filter_by(shared_resource_id=task.shared_resource_id).first()
    if not catalog_record or catalog_record.catalog_source_type not in AUTO_GENERATED_CATALOG_SOURCE_TYPES:
        return catalog_record
    if catalog_record.confirmed_flag:
        return catalog_record
    analysis_result = BiddingAnalysisResult.query.filter_by(shared_resource_id=task.shared_resource_id).first()
    if not analysis_result:
        return catalog_record
    catalog_record.catalog_content = json.dumps(
        _build_auto_catalog_content(task, analysis_result, catalog_record.catalog_source_type, task.catalog_generation_level),
        ensure_ascii=False,
    )
    return catalog_record


def get_catalog_options(task_id):
    """生成并返回可供选择的目录方案。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    if task.status != "CHECKED":
        raise ValueError("当前任务状态不允许生成目录")
    analysis_result = BiddingAnalysisResult.query.filter_by(shared_resource_id=task.shared_resource_id).first()
    if not analysis_result:
        raise LookupError("分析结果不存在")

    basis_text = analysis_result.effective_text or analysis_result.raw_text or ""
    preview = basis_text[:120]
    generation_level = _normalize_catalog_generation_level(task.catalog_generation_level)
    basis_text = analysis_result.effective_text or analysis_result.raw_text or ""
    
    # Tab1: 按标书评分点生成 — 尝试从数据库读取缓存，没有再调 LLM
    existing = BiddingCatalog.query.filter_by(
        shared_resource_id=task.shared_resource_id,
        catalog_source_type="FROM_TENDER_REQUIREMENT",
    ).first()
    
    if existing:
        try:
            cached_content = json.loads(existing.catalog_content)
            outline = cached_content.get("outline", [])
            # tab1 当前约束化目录至少应有 3 个顶级章节
            if len(outline) < 3:
                logger.info("[catalog] 缓存目录章节数过少(%s)，重新生成: shared_resource=%s", len(outline), task.shared_resource_id)
                outline = None
                existing.confirmed_flag = False
                db.session.commit()
            else:
                logger.info("[catalog] 命中数据库缓存: shared_resource=%s", task.shared_resource_id)
        except (json.JSONDecodeError, TypeError):
            outline = None
    else:
        outline = None
    
    if not outline:
        logger.info("[catalog] 未命中缓存，生成受招标文件约束的 tab1 目录: task=%s", task_id)
        outline = _build_constrained_requirement_outline(
            task,
            analysis_result,
            generation_level=generation_level,
        )["outline"]
        # 入库缓存
        catalog_record = BiddingCatalog(
            shared_resource_id=task.shared_resource_id,
            catalog_source_type="FROM_TENDER_REQUIREMENT",
            catalog_content=json.dumps({"outline": outline}, ensure_ascii=False),
            confirmed_flag=False,
        )
        db.session.add(catalog_record)
        db.session.commit()
        logger.info("[catalog] 目录缓存已入库: shared_resource=%s", task.shared_resource_id)
    
    options = [
        {
            "catalog_source_type": "FROM_TENDER_REQUIREMENT",
            "catalog_name": "按标书评分点生成",
            "catalog_content": {"outline": outline},
        },
    ]
    return {
        "task_id": task.id,
        "basis_text_preview": preview,
        "options": options,
    }


def confirm_catalog(task_id, catalog_content, template_id=None):
    """确认最终目录并初始化章节数据。"""
    logger.info("[task] 确认目录 task=%s template=%s", task_id, template_id)
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    if task.status != "CHECKED":
        raise ValueError("当前任务状态不允许确认目录")
    shared_resource = BiddingSharedResource.query.filter_by(id=task.shared_resource_id).first()
    if not shared_resource:
        raise LookupError("共享资源不存在")
    if not catalog_content:
        raise ValueError("目录内容不能为空")

    existing = BiddingCatalog.query.filter_by(shared_resource_id=task.shared_resource_id).first()
    serialized_content = json.dumps(catalog_content, ensure_ascii=False)
    previous_template_id = existing.template_id if existing else None
    if not existing:
        existing = BiddingCatalog(
            shared_resource_id=task.shared_resource_id,
            catalog_source_type="USER_CONFIRMED",
            template_id=template_id,
            catalog_content=serialized_content,
            confirmed_flag=True,
        )
        db.session.add(existing)
    else:
        existing.catalog_content = serialized_content
        existing.template_id = template_id
        existing.confirmed_flag = True

    # 模板库使用次数 +1
    if template_id and template_id != previous_template_id:
        tmpl = TemplateCatalog.query.filter_by(id=template_id).first()
        if tmpl:
            tmpl.use_count = (tmpl.use_count or 0) + 1

    shared_resource.catalog_status = True
    shared_resource.catalog_source_type = "USER_CONFIRMED"
    task.status = "CATALOG_CONFIRMED"
    task.progress = 40
    task.current_step = "generate_config"
    log_operation(
        module="task",
        action="confirm_catalog",
        target_type="BiddingTask",
        target_id=task_id,
        task_id=task_id,
        summary='确认目录',
        detail={"task_id": task_id, "template_id": template_id},
    )
    db.session.commit()
    return BiddingCatalog.query.filter_by(shared_resource_id=task.shared_resource_id).first().to_dict()
def extract_catalog_from_file(task_id, file_storage):
    """从上传的投标文件（docx/doc/pdf）中提取目录结构（Tab2：按参考格式生成）。"""
    from ...infrastructure.document_parser import DocumentParser
    from ...infrastructure.integrations import LLMAdapter
    from ..storage import StorageService
    import json

    if not file_storage:
        raise ValueError("请上传投标文件")
    
    # 读取文件内容
    payload = file_storage.read()
    parser = DocumentParser()
    text = parser.parse_bytes(file_storage.filename or "未知文件", payload)
    if not text or not text.strip():
        raise ValueError("无法解析文件内容")
    
    logger.info("[catalog] 上传文件目录提取: %s (%s 字符)", file_storage.filename, len(text))
    
    # 调用 LLM 提取目录
    adapter = LLMAdapter(
        api_key=current_app.config.get("OPENAI_API_KEY"),
        base_url=current_app.config.get("OPENAI_BASE_URL"),
        default_model=current_app.config.get("OPENAI_MODEL_NAME"),
    )
    if not adapter.is_available():
        raise RuntimeError("LLM 不可用，无法提取目录")
    
    system_prompt = "你是一个投标文件解析专家。从投标文件中提取目录结构，输出 JSON。"
    user_prompt = (
        "从以下投标文件中提取目录（目录/大纲）结构，按原文序号输出。\n\n"
        "要求：\n"
        "1. 找到文件中标记为「目录」或「大纲」的部分\n"
        "2. 提取所有章节标题和子标题，保持原文顺序\n"
        "3. 序号重新编排为连续序号（一、二、三... / （一）（二）（三）...）\n"
        "4. 每个节点包含 title 和 description\n"
        "5. 子节点通过 children 数组表示\n"
        "6. 只返回 JSON，不要 markdown\n\n"
        "JSON 格式：\n"
        '{"outline": [\n'
        '  {"title": "一、章节标题", "description": "",\n'
        '   "children": [{"title": "（一）子标题", "description": ""}]}\n'
        "]}\n\n"
        f"文件内容：\n{text[:8000]}"
    )
    
    try:
        raw = adapter.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=3000,
        )
        if not raw:
            raise RuntimeError("LLM 未返回结果")
        
        out = raw.strip()
        if out.startswith("```"):
            idx = out.find("\n")
            if idx > 0: out = out[idx+1:]
        if out.endswith("```"):
            out = out[:-3].strip()
        
        brace_start = out.find("{")
        brace_end = out.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            out = out[brace_start:brace_end+1]
        
        data = json.loads(out)
        outline = data.get("outline", [])
        if not isinstance(outline, list) or len(outline) == 0:
            raise RuntimeError("未提取到有效目录结构")
        
        # 从任务获取 shared_resource_id
        task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
        if task:
            catalog_record = BiddingCatalog(
                shared_resource_id=task.shared_resource_id,
                catalog_source_type="FROM_TENDER_TEMPLATE",
                catalog_content=json.dumps({"outline": outline}, ensure_ascii=False),
                confirmed_flag=False,
            )
            db.session.add(catalog_record)
            db.session.commit()
            logger.info("[catalog] 上传文件目录已入库: task=%s shared_resource=%s", task_id, task.shared_resource_id)
        
        return {"catalog_source_type": "FROM_TENDER_TEMPLATE", "catalog_content": {"outline": outline}}
    except json.JSONDecodeError:
        raise RuntimeError("LLM 返回的目录格式不正确")
    except Exception as exc:
        logger.warning("[catalog] 文件目录提取异常: %s", exc)
        raise


def get_subject_templates(task_id):
    """获取任务对应标书类型的模板列表（Tab3：按模板库生成）。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    
    templates = TemplateCatalog.query.filter_by(bid_type=task.bid_type).order_by(TemplateCatalog.use_count.desc(), TemplateCatalog.id.desc()).all()
    result = []
    for t in templates:
        template_dict = t.to_dict()
        # 解析 catalog_content 为 JSON
        try:
            template_dict["catalog_content"] = json.loads(t.catalog_content) if isinstance(t.catalog_content, str) else t.catalog_content
        except (json.JSONDecodeError, TypeError):
            template_dict["catalog_content"] = {"outline": []}
        result.append(template_dict)
    
    return {
        "task_id": task.id,
        "bid_type": task.bid_type,
        "templates": result,
    }
