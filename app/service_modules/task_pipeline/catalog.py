"""标书任务目录阶段相关流程，包括目录候选生成与最终确认。"""

import logging; logger = logging.getLogger(__name__)
import json
import re
from flask import current_app

from ...core.extensions import db
from ...domain import BiddingAnalysisResult, BiddingCatalog, BiddingCheckItem, BiddingSharedResource, BiddingTask, TemplateCatalog
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




# ── 新增：包过滤、确认项分类、动态目录结构推断 ──

def _get_filtered_analysis_data(analysis_result, selected_package_no):
    """按 selected_package_no 过滤 analysis_data，只保留当前包的数据。"""
    if not analysis_result:
        return {}
    analysis_data = analysis_result.safe_analysis_data()
    if not analysis_data:
        return {}
    # 单包场景或未选择包号：不过滤
    if not selected_package_no or not bool(analysis_data.get("has_package")):
        return analysis_data
    # 多包场景：只保留当前包
    packages = analysis_data.get("packages", [])
    if not isinstance(packages, list):
        return analysis_data
    filtered = [
        p for p in packages
        if isinstance(p, dict) and str(p.get("package_no")) == str(selected_package_no)
    ]
    analysis_data["packages"] = filtered
    analysis_data["package_count"] = len(filtered)
    return analysis_data


def _classify_check_items(check_items):
    """将 check_items 按前缀分类为 qualification / compliance / disqualification / scoring。"""
    classified = {"qualification": [], "compliance": [], "disqualification": [], "scoring": []}
    for item in (check_items or []):
        key = item.check_key or ""
        if key.startswith("qual_"):
            classified["qualification"].append(item)
        elif key.startswith("star_"):
            classified["compliance"].append(item)
        elif key.startswith("disq_"):
            classified["disqualification"].append(item)
        elif key.startswith("score_dim_"):
            classified["scoring"].append(item)
    return classified


# ═══════════════════════════════════════════════════════════════════
# 目录合并引擎（替代旧的 _build_package_aware_outline）
# ═══════════════════════════════════════════════════════════════════

def _parse_format_tree(required_sections):
    """阶段1：解析 format_requirements.required_sections 为目录树。
    
    检测规则：
    - 标题以 一、二、三... 开头 → 父级节点
    - 其他标题 → 归属于最近父级的子项
    """
    if not required_sections:
        return []
    cn_pat = re.compile(r'^[一二三四五六七八九十]+、')
    parent_indices = [i for i, s in enumerate(required_sections) if cn_pat.match(s.get("title", ""))]
    if not parent_indices:
        return []
    tree = []
    for idx, p_idx in enumerate(parent_indices):
        parent = required_sections[p_idx]
        next_p = parent_indices[idx + 1] if idx + 1 < len(parent_indices) else len(required_sections)
        children = required_sections[p_idx + 1:next_p]
        tree.append({
            "source": "format_requirements",
            "title": parent.get("title", ""),
            "has_template": parent.get("has_template", False),
            "template_tables": parent.get("template_tables", []),
            "children": [
                {"source": "format_requirements", "title": c.get("title", "")}
                for c in children
            ],
            "description": "",
        })
    return tree


def _infer_skeleton_fallback(analysis_data):
    """降级路径：无 format_requirements 时，从文档章节推断骨架。"""
    chapters = analysis_data.get("document_chapters", [])
    if not chapters:
        return []
    chapter_section_map = [
        ("报价|报价格", "报价函"),
        ("资格|资质", "资格证明文件"),
        ("技术|参数|采购需求", "技术响应"),
        ("商务|合同", "商务响应"),
        ("评分|评选|评审", "评分响应"),
    ]
    seen = set()
    skeleton = []
    for ch in chapters:
        for pattern, section_name in chapter_section_map:
            if re.search(pattern, ch) and section_name not in seen:
                skeleton.append({
                    "source": "inferred",
                    "title": section_name,
                    "description": "",
                    "children": [],
                })
                seen.add(section_name)
    return skeleton


def build_base_skeleton(analysis_data):
    """阶段1主入口：构建基础骨架。"""
    fmt = analysis_data.get("format_requirements", {})
    if fmt and fmt.get("required_sections"):
        tree = _parse_format_tree(fmt["required_sections"])
        if tree:
            return tree
    return _infer_skeleton_fallback(analysis_data)


def _get_dimensions_compat(scoring):
    """兼容 analyze 格式（dimensions）和 check-items 格式（business/technical）。"""
    dims = scoring.get("dimensions", [])
    if dims:
        return dims
    dims = []
    for group in ("business", "technical"):
        for item in scoring.get(group, []):
            dims.append({
                "name": item.get("name", ""),
                "score": item.get("score", 0),
                "type": item.get("type", "objective"),
            })
    return [d for d in dims if "合计" not in d.get("name", "") and "总计" not in d.get("name", "")]


def _is_covered(skeleton, dim_name):
    """判断评分维度是否已被骨架章节覆盖。"""
    explicit_map = {
        "报价": ["报价一览表", "报价表", "报价部分"],
        "供应商业绩": ["类似项目业绩", "业绩一览表", "业绩"],
        "业绩": ["类似项目业绩", "业绩一览表"],
    }
    expected_sections = explicit_map.get(dim_name, [dim_name])
    for node in skeleton:
        node_title = node.get("title", "")
        for expected in expected_sections:
            if expected in node_title:
                return True, node
        if dim_name in node_title:
            return True, node
    return False, None


def _find_insert_position(skeleton, dim_name):
    """确定新增评分驱动章节的插入位置。"""
    keywords = [dim_name[:2], dim_name[:3]]
    candidates = []
    for i, node in enumerate(skeleton):
        node_title = node.get("title", "")
        for kw in keywords:
            if kw and kw in node_title:
                candidates.append(i + 1)
    if candidates:
        return min(candidates)
    for i, node in enumerate(skeleton):
        if "其他" in node.get("title", ""):
            return i
    return len(skeleton)


def merge_scoring_sections(skeleton, scoring):
    """阶段2：将评分维度合并到骨架中。
    
    - objective + 已覆盖 → 无操作
    - subjective + 未覆盖 → 新增章节
    - 合计/总计行 → 跳过
    """
    dims = _get_dimensions_compat(scoring)
    if not dims:
        return skeleton
    
    new_sections = []
    for dim in dims:
        name = dim.get("name", "")
        score = dim.get("score", 0)
        dim_type = dim.get("type", "")
        if "合计" in name or "总计" in name:
            continue
        covered, _ = _is_covered(skeleton, name)
        if covered:
            continue
        if dim_type == "subjective":
            section = {
                "source": "scoring",
                "title": name,
                "description": f"根据本项目采购需求，编制{name}",
                "children": [],
                "score": score,
            }
            pos = _find_insert_position(skeleton, name)
            new_sections.append((pos, section))
        else:
            logger.info("[catalog] 客观评分项 '%s'(%s分) 未覆盖", name, score)
    
    # 从后往前插入，避免位置偏移
    new_sections.sort(key=lambda x: -x[0])
    for pos, section in new_sections:
        skeleton.insert(pos, section)
    return skeleton


def _fill_business_children(skeleton, business_items):
    """从 business.items 动态生成商务偏离表子项（已去重）。"""
    if not business_items:
        return
    keyword_section_map = [
        # 更具体的模式排在前面，避免"售后"误匹配"报价方式"中的"售后"
        ("报价方式", "报价方式说明"),
        ("付款", "付款方式响应"),
        ("交付地点", "交货地点"),
        ("交付要求|交货时间", "交货时间"),
        ("验收", "验收方案"),
        ("售后服务", "售后服务承诺"),
        ("质保", "质保期承诺"),
    ]
    seen_titles = set()
    children = []
    for item in business_items:
        content = item.get("content", "")
        for pattern, title in keyword_section_map:
            if re.search(pattern, content) and title not in seen_titles:
                seen_titles.add(title)
                children.append({
                    "source": "business_items",
                    "title": title,
                    "description": content[:80],
                })
                break
    for node in skeleton:
        if "商务" in node.get("title", "") and "偏离" in node.get("title", ""):
            node["children"] = children
            break


def _fill_tech_description(skeleton, technical_items, packages):
    """统计产品数量，填充技术偏离表描述。"""
    product_count = 0
    if packages:
        for pkg in packages:
            if not isinstance(pkg, dict):
                continue
            table_items = (pkg.get("parameters") or {}).get("table_items", [])
            for item in table_items:
                if item.get("采购产品名称", ""):
                    product_count += 1
    if product_count == 0 and technical_items:
        product_count = len(technical_items)
    for node in skeleton:
        if "技术" in node.get("title", "") and "偏离" in node.get("title", ""):
            if product_count > 0:
                node["description"] = f"共{product_count}种产品，逐项响应技术参数要求"
            break


def _fill_qualification(skeleton, classified_items):
    """资格项去重后填充到资格证明文件章节。"""
    qual_items = classified_items.get("qualification", [])
    if not qual_items:
        return
    
    def _get_val(item, key):
        """兼容 ORM 对象和 dict。

        ORM 对象 (BiddingCheckItem) 属性: check_key, check_label, check_value
        dict 对象字段: requirement, material, check_label, check_value
        """
        if isinstance(item, dict):
            return item.get(key, "") or ""
        _m = {"requirement": "check_label", "material": "check_value",
              "check_label": "check_label", "check_value": "check_value"}
        return getattr(item, _m.get(key, key), "") or ""
    seen = set()
    deduped = []
    for item in qual_items:
        req = _get_val(item, "requirement") or _get_val(item, "check_label") or ""
        key = req[:20]
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    
    qual_node = None
    for node in skeleton:
        if "资格" in node.get("title", ""):
            qual_node = node
            break
    if qual_node:
        qual_node["children"] = [
            {
                "source": "qualification",
                "title": _get_val(item, "requirement") or _get_val(item, "check_label"),
                "description": (_get_val(item, "material") or "")[:100],
            }
            for item in deduped
        ]


def _fill_compliance(skeleton, classified_items):
    """实质性/符合性要求填充。"""
    comp_items = classified_items.get("compliance", [])
    if not comp_items:
        return
    
    def _get_val(item, key):
        if isinstance(item, dict):
            return item.get(key, "") or ""
        _m = {"requirement": "check_label", "material": "check_value",
              "check_label": "check_label", "check_value": "check_value"}
        return getattr(item, _m.get(key, key), "") or ""
    
    comp_node = None
    for node in skeleton:
        if "实质性" in node.get("title", ""):
            comp_node = node
            break
    if comp_node:
        comp_node["children"] = [
            {
                "source": "compliance",
                "title": _get_val(item, "check_label") or _get_val(item, "requirement"),
                "description": (_get_val(item, "check_value") or "")[:80],
            }
            for item in comp_items
        ]


def enrich_section_details(skeleton, analysis_data, classified_items):
    """阶段3：用各数据源填充章节详情。"""
    # 3.1 商务偏离表子项
    business_items = analysis_data.get("business", {}).get("items", []) if isinstance(analysis_data.get("business"), dict) else []
    if business_items:
        _fill_business_children(skeleton, business_items)
    
    # 3.2 技术偏离表描述
    technical_items = analysis_data.get("technical", {}).get("items", []) if isinstance(analysis_data.get("technical"), dict) else []
    packages = analysis_data.get("packages", [])
    if technical_items or packages:
        _fill_tech_description(skeleton, technical_items, packages)
    
    # 3.3 资格项填充：如果骨架无资格节点但文档有资格章节+资格项，新增
    _fill_qualification(skeleton, classified_items)
    qual_items = classified_items.get("qualification", [])
    chapters = analysis_data.get("document_chapters", [])
    has_qual_chapter = any("资格" in ch for ch in chapters)
    has_qual_node = any("资格" in n.get("title", "") for n in skeleton)
    if qual_items and has_qual_chapter and not has_qual_node:
        # 插入资格节点（比选函之后，即 index 1）
        def _gv(item, key):
            if isinstance(item, dict):
                return item.get(key, "") or ""
            _m = {"requirement": "check_label", "material": "check_value",
                  "check_label": "check_label", "check_value": "check_value"}
            return getattr(item, _m.get(key, key), "") or ""
        # 去重后再插入
        _seen_titles = set()
        _deduped_qual = []
        for item in qual_items:
            _t = _gv(item, "requirement") or _gv(item, "check_label")
            if _t[:20] not in _seen_titles:
                _seen_titles.add(_t[:20])
                _deduped_qual.append(item)
        skeleton.insert(1, {
            "source": "qualification",
            "title": "资格证明文件",
            "description": "根据招标文件要求提供以下资格证明材料",
            "children": [
                {
                    "source": "qualification",
                    "title": (_gv(item, "requirement") or _gv(item, "check_label"))[:60],
                    "description": (_gv(item, "material") or "")[:100],
                }
                for item in _deduped_qual
            ],
        })
    
    # 3.4 实质性要求填充
    _fill_compliance(skeleton, classified_items)


def validate_completeness(outline, document_chapters):
    """阶段4：验证目录是否覆盖源文档所有章节。"""
    if not document_chapters:
        return []
    chapter_section_map = [
        ("比选邀请", ["比选函"]),
        ("须知", ["比选函"]),
        ("申请文件格式", []),
        ("资格证明", ["资格证明"]),
        ("比选项目及要求", ["报价一览表", "商务", "技术", "偏离表"]),
        ("评选办法", ["服务方案", "售后保障", "评分"]),
        ("合同", []),
    ]
    warnings = []
    for ch in document_chapters:
        ch_stripped = ch.strip()
        if ch_stripped in ("目录", "比选编号"):
            continue
        matched = False
        for keyword, expected_sections in chapter_section_map:
            if keyword in ch_stripped:
                if not expected_sections:
                    matched = True
                    break
                for node in outline:
                    node_title = node.get("title", "")
                    for expected in expected_sections:
                        if expected in node_title:
                            matched = True
                            break
                    if matched:
                        break
                break
        if not matched:
            warnings.append(f"章节 '{ch_stripped}' 在目录中无明确对应")
    return warnings


def _assign_numbers(skeleton):
    """给骨架节点分配统一编号（一、二、三...）。"""
    # 先清除所有已有的中文编号前缀
    cn_prefix = re.compile(r'^[一二三四五六七八九十]+、')
    for node in skeleton:
        title = node.get("title", "")
        node["title"] = cn_prefix.sub("", title).strip()
    
    chinese_nums = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八"]
    for idx, node in enumerate(skeleton):
        num = chinese_nums[idx] if idx < len(chinese_nums) else str(idx + 1)
        node["title"] = f"{num}、{node['title']}"
    
    last_num = chinese_nums[len(skeleton)] if len(skeleton) < len(chinese_nums) else str(len(skeleton) + 1)
    skeleton.append({
        "source": "catch_all",
        "title": f"{last_num}、其他材料",
        "description": "供应商认为需要提交的其他材料",
        "children": [],
    })
    return skeleton


def build_catalog(analysis_data, classified_items):
    """目录合并引擎主入口。"""
    # 阶段1：基础骨架
    skeleton = build_base_skeleton(analysis_data)
    if not skeleton:
        logger.warning("[catalog] 骨架为空，返回空目录")
        return []
    
    # 阶段2：合并评分维度
    scoring = analysis_data.get("scoring", {})
    if isinstance(scoring, dict):
        skeleton = merge_scoring_sections(skeleton, scoring)
    
    # 阶段3：填充详情
    enrich_section_details(skeleton, analysis_data, classified_items)
    
    # 阶段4：编号
    outline = _assign_numbers(skeleton)
    
    # 验证
    chapters = analysis_data.get("document_chapters", [])
    warnings = validate_completeness(outline, chapters)
    if warnings:
        logger.info("[catalog] 覆盖验证警告: %s", warnings)
    
    return outline


def _build_bid_letter_section(analysis_context):
    """构建投标函章节。"""
    return {
        "title": "投标函",
        "description": _build_catalog_description(
            analysis_context.get("overview", ""),
            "投标函及报价承诺",
            max_length=80,
        ),
        "children": [],
    }


def _build_price_section(analysis_context, analysis_data):
    """构建报价部分章节。"""
    pkg_items = []
    packages = analysis_data.get("packages", [])
    for pkg in packages:
        if not isinstance(pkg, dict):
            continue
        params = pkg.get("parameters") or {}
        if not isinstance(params, dict):
            continue
        core_products = params.get("core_products", [])
        if core_products:
            pkg_items.extend(core_products)
    has_items = len(pkg_items) > 0
    children = [
        {"title": "（一）报价一览表", "description": "项目总报价"},
    ]
    if has_items:
        children.append({
            "title": "（二）分项报价明细表",
            "description": f"含{len(pkg_items)}项产品分项报价",
        })
    return {
        "title": "报价部分",
        "description": "报价一览表及分项报价明细",
        "children": children,
    }


def _build_authorization_section():
    """构建法定代表人授权书章节。"""
    return {
        "title": "法定代表人授权书",
        "description": "法定代表人身份证明及授权委托书",
        "children": [],
    }


def _build_qualification_section(classified_items, analysis_context, filtered_analysis_data=None):
    """从确认的资格项构建资格证明文件章节。
    
    当 BiddingCheckItem.check_label 为空时，从 analysis_data.eligibility.qualifications
    中按 check_key 匹配获取完整的要求文本。
    """
    quals = classified_items.get("qualification", [])
    
    # 从 analysis_data 构建资格要求查找表: stat_01 → "具有独立承担民事责任的能力..."
    qual_lookup = {}
    if filtered_analysis_data:
        elig = filtered_analysis_data.get("eligibility", {})
        if isinstance(elig, dict):
            for q in elig.get("qualifications", []):
                qid = q.get("id", "")
                if qid:
                    qual_lookup[qid] = q.get("requirement", "")
    
    children = []
    sub_idx = 1
    for item in quals:
        key = item.check_key or ""
        label = item.check_label or ""
        value = item.check_value or ""
        
        # 如果 check_label 为空或是默认占位符，从 analysis_data 中按 check_key 匹配
        if not label or label == "核对项":
            for prefix in ("qual_", "star_", "disq_"):
                if key.startswith(prefix):
                    lookup_key = key[len(prefix):]
                    if lookup_key in qual_lookup:
                        label = qual_lookup[lookup_key]
                    break
        # 再次兜底：若 label 仍为短占位符，尝试用 value/check_key 中的关键词匹配 qual_lookup
        if not label or len(label) < 4:
            for q_req in qual_lookup.values():
                # 尝试用 check_value 的前20个字匹配
                if value and len(value) >= 4 and value[:20] in q_req:
                    label = q_req
                    break
            # 再尝试用 check_key 中的英文词匹配
            if (not label or len(label) < 4) and qual_lookup:
                label = list(qual_lookup.values())[0]
        
        # 如果 value 为空，用 label 代替
        if not value:
            value = label
        
        desc = (value[:60] if value else label[:60]) if (value or label) else "资格证明材料"
        marker = "（待准备）" if not item.confirmed_flag else ""
        sub_prefix = ["（一）", "（二）", "（三）", "（四）", "（五）", "（六）", "（七）", "（八）", "（九）", "（十）"]
        prefix = sub_prefix[sub_idx - 1] if sub_idx <= len(sub_prefix) else f"（{sub_idx}）"
        children.append({
            "title": f"{prefix}{label}{marker}",
            "description": desc[:100],
        })
        sub_idx += 1

    return {
        "title": "资格证明文件",
        "description": "根据招标文件资格要求提供以下证明材料",
        "children": children,
    }


def _build_compliance_section(classified_items):
    """从确认的实质性要求项构建实质性要求响应章节。"""
    items = classified_items.get("compliance", [])
    children = []
    for i, item in enumerate(items):
        sub_prefix = ["（一）", "（二）", "（三）", "（四）", "（五）", "（六）", "（七）", "（八）"]
        prefix = sub_prefix[i] if i < len(sub_prefix) else f"（{i + 1}）"
        children.append({
            "title": f"{prefix}{item.check_label}（★实质性要求）",
            "description": (item.check_value or "")[:100],
        })
    return {
        "title": "实质性要求响应",
        "description": "以下为招标文件标注★的实质性要求，须完全响应",
        "children": children,
    }


def _count_package_items(analysis_data):
    """统计当前包内的产品/物料条目数。"""
    packages = analysis_data.get("packages", [])
    total = 0
    for pkg in packages:
        if not isinstance(pkg, dict):
            continue
        params = pkg.get("parameters") or {}
        if not isinstance(params, dict):
            continue
        total += params.get("starred_count", 0) + params.get("important_count", 0) + params.get("general_count", 0)
        core_products = params.get("core_products", [])
        if core_products and total == 0:
            total = len(core_products)
    return total


def _build_tech_section(analysis_context, analysis_data):
    """构建技术参数响应章节，根据产品数量决定颗粒度。"""
    item_count = _count_package_items(analysis_data)
    children = []
    if item_count > 5:
        children = [
            {"title": "（一）技术参数总偏离表", "description": "全部产品技术参数响应总表"},
            {"title": "（二）产品详细技术响应", "description": f"逐项响应{_build_catalog_description(analysis_context.get('technical_requirements', ''), '', max_length=60)}"},
            {"title": "（三）质量保证措施", "description": "产品质量控制及保障方案"},
        ]
    elif item_count > 0:
        children = [
            {"title": "（一）技术参数偏离表", "description": "技术参数响应及偏离说明"},
        ]
    else:
        children = [
            {"title": "（一）技术方案", "description": "技术路线及实施方案"},
        ]
    return {
        "title": "技术参数响应",
        "description": _build_catalog_description(
            analysis_context.get("technical_requirements", ""),
            "技术参数响应及偏离说明",
            max_length=100,
        ),
        "children": children,
    }


def _build_business_section(analysis_context):
    """构建商务要求响应章节。"""
    return {
        "title": "商务要求响应",
        "description": _build_catalog_description(
            analysis_context.get("business_requirements", ""),
            "商务条款响应",
            max_length=100,
        ),
        "children": [
            {"title": "（一）商务条款偏离表", "description": "商务要求响应及偏离说明"},
            {"title": "（二）交货及验收方案", "description": "交货时间、地点及验收方案"},
            {"title": "（三）付款方式响应", "description": "付款条件及方式响应"},
        ],
    }


def _build_scoring_section(analysis_data):
    """从评分维度构建评分标准响应章节。"""
    scoring = analysis_data.get("scoring", {})
    dims = scoring.get("dimensions", []) if isinstance(scoring, dict) else []
    children = []
    for i, dim in enumerate(dims):
        if not isinstance(dim, dict):
            dim_name = str(dim)
            dim_score = 0
            dim_criteria = ""
        else:
            dim_name = dim.get("name", "") or ""
            # 跳过"合计"类汇总维度
            if "合计" in dim_name or "总计" in dim_name:
                continue
            dim_score = dim.get("score", 0)
            dim_criteria = dim.get("criteria", "") or ""
            # criteria 可能是 JSON 字符串，提取可读内容
            if dim_criteria.startswith("["):
                try:
                    parsed = json.loads(dim_criteria)
                    if isinstance(parsed, list):
                        items = []
                        for item in parsed[:3]:
                            if isinstance(item, dict):
                                items.append(f"{item.get('name','')}({item.get('score',0)}分)")
                        if items:
                            dim_criteria = "，".join(items)
                except (json.JSONDecodeError, TypeError):
                    dim_criteria = dim_criteria[:80]
        sub_prefix = ["（一）", "（二）", "（三）", "（四）", "（五）", "（六）"]
        prefix = sub_prefix[i] if i < len(sub_prefix) else f"（{i + 1}）"
        desc = f"{dim_score}分" if dim_score else ""
        if dim_criteria:
            desc = desc + f" - {dim_criteria[:60]}" if desc else dim_criteria[:60]
        children.append({
            "title": f"{prefix}{dim_name}",
            "description": desc,
        })
    return {
        "title": "评分标准响应",
        "description": "逐项响应评分标准各评审维度",
        "children": children,
    }


def _build_service_section():
    """构建售后服务/培训方案章节。"""
    return {
        "title": "售后服务及培训方案",
        "description": "售后服务体系、技术培训及应急响应",
        "children": [
            {"title": "（一）售后服务体系", "description": "售后服务承诺及体系说明"},
            {"title": "（二）技术培训方案", "description": "产品使用培训计划"},
            {"title": "（三）应急响应及退换货承诺", "description": "应急响应机制、退换货及质保承诺"},
        ],
    }


def _build_performance_section():
    """构建类似项目业绩章节。"""
    return {
        "title": "类似项目业绩",
        "description": "近三年类似项目业绩及证明材料",
        "children": [],
    }


def _build_other_section():
    """构建其他材料章节。"""
    return {
        "title": "其他材料",
        "description": "供应商认为需要提交的其他材料",
        "children": [],
    }

def _has_chapter_keyword(chapter_titles, keywords):
    """检查文档章节标题中是否包含目标关键词。"""
    if not chapter_titles:
        return None  # 未知，不做判断
    for title in chapter_titles:
        title_lower = title.lower()
        for kw in keywords:
            if kw in title_lower or kw in title:
                return True
    return False


def _get_format_requirement_titles(analysis_data):
    """从 analysis_data 中提取格式要求的章节标题列表。"""
    fmt = analysis_data.get("format_requirements")
    if not fmt or not isinstance(fmt, dict):
        return []
    sections = fmt.get("required_sections", [])
    return [s.get("title", "") for s in sections if s.get("title")]



def _build_package_aware_outline(task, analysis_result, filtered_analysis_data, classified_items, generation_level=None):
    """替换为新的合并引擎。"""
    return build_catalog(filtered_analysis_data, classified_items)



def _should_fallback_to_legacy(task, analysis_result, selected_package_no, check_items):
    """判断是否需要回退到旧的 3 章硬编码结构。"""
    if not analysis_result:
        return True
    analysis_data = analysis_result.safe_analysis_data()
    if not analysis_data:
        return True
    # 多包项目（>1包）但未选择包号时回退
    package_count = analysis_data.get("package_count", 0) or len(analysis_data.get("packages", []) or [])
    if bool(analysis_data.get("has_package")) and package_count > 1 and not selected_package_no:
        return True
    # check_items 为空且无 analysis_data 关键字段
    if not check_items:
        pass  # 仍然可以生成基础章节，不回退
    return False


def _build_constrained_requirement_outline(
    task, analysis_result, generation_level=None,
    selected_package_no=None, check_items=None,
):
    """为 tab1 生成受招标文件约束的目录结构。
    
    新增参数:
        selected_package_no: 用户选择的包号，用于过滤多包数据
        check_items: BiddingCheckItem 查询结果列表，用于展开确认项为章节
    
    当参数不足时自动回退到旧的 3 章硬编码结构。
    """
    # 判断是否需要回退
    if _should_fallback_to_legacy(task, analysis_result, selected_package_no, check_items):
        logger.info("[catalog] 回退到旧 3 章目录结构")
        return _build_dynamic_outline(task, analysis_result, variant="requirement", generation_level=generation_level)

    # 1. 按包过滤 analysis_data
    filtered_analysis_data = _get_filtered_analysis_data(analysis_result, selected_package_no)
    if not filtered_analysis_data:
        logger.warning("[catalog] 过滤后 analysis_data 为空，回退到旧结构")
        return _build_dynamic_outline(task, analysis_result, variant="requirement", generation_level=generation_level)

    # 2. 解析确认项分类
    classified_items = _classify_check_items(check_items)

    # 3. 动态构建目录
    outline = _build_package_aware_outline(
        task=task,
        analysis_result=analysis_result,
        filtered_analysis_data=filtered_analysis_data,
        classified_items=classified_items,
        generation_level=generation_level,
    )
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
    
    # 从 analysis_data (v3/v2) 中提取结构化字段
    analysis_data = None
    if hasattr(analysis_result, "analysis_data") and analysis_result.analysis_data:
        try:
            analysis_data = json.loads(analysis_result.analysis_data)
        except (json.JSONDecodeError, TypeError):
            pass
    
    meta = None
    if analysis_data:
        if analysis_data.get("version") in ("v2", "v3"):
            meta = analysis_data.get("metadata") or analysis_data.get("bidder_notice", {})
        else:
            meta = analysis_data.get("metadata") or analysis_data.get("bidder_notice", {})
    if meta:
        context_parts.append("=== 项目信息 ===")
        if meta.get("project_name", {}).get("value"): context_parts.append(f"项目名称：{meta['project_name']['value']}")
        if meta.get("project_code", {}).get("value"): context_parts.append(f"项目编号：{meta['project_code']['value']}")
        if meta.get("budget"): context_parts.append(f"预算：{meta.get('budget', {}).get('total', 0)}")
        if meta.get("overview"): context_parts.append(f"项目概况：{meta['overview']}")
        # 注入选定的包号信息
        selected_pkg_no = getattr(task, "selected_package_no", None)
        if selected_pkg_no:
            context_parts.append(f"当前包号：第{selected_pkg_no}包")
            selected_pkg_name = getattr(task, "selected_package_name", None) or ""
            if selected_pkg_name:
                context_parts.append(f"当前包名称：{selected_pkg_name}")
        
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
        return _build_constrained_requirement_outline(
            task, analysis_result, generation_level=generation_level,
            selected_package_no=getattr(task, "selected_package_no", None),
            check_items=None,
        )
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
    
    # ── 新增：读取包号和确认项，供目录生成使用 ──
    selected_package_no = getattr(task, "selected_package_no", None)
    check_items = BiddingCheckItem.query.filter_by(
        shared_resource_id=task.shared_resource_id
    ).order_by(BiddingCheckItem.sort_no.asc(), BiddingCheckItem.id.asc()).all()
    
    logger.info(
        "[catalog] get_catalog_options: task=%s selected_package_no=%s check_items_count=%s",
        task_id, selected_package_no, len(check_items),
    )
    
    # Tab1: 按标书评分点生成 — 尝试从数据库读取缓存，没有再调 LLM
    existing = BiddingCatalog.query.filter_by(
        shared_resource_id=task.shared_resource_id,
        catalog_source_type="FROM_TENDER_REQUIREMENT",
    ).first()
    
    if existing:
        try:
            cached_content = json.loads(existing.catalog_content)
            outline = cached_content.get("outline", [])
            # 新动态目录至少应有 6 个顶级章节（旧 3 章缓存视为过期）
            if len(outline) < 6:
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
            selected_package_no=selected_package_no,
            check_items=check_items,
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
