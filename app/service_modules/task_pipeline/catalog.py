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
    """将 check_items 按前缀分类为 qualification / compliance / disqualification。"""
    classified = {"qualification": [], "compliance": [], "disqualification": []}
    for item in (check_items or []):
        key = item.check_key or ""
        if key.startswith("qual_"):
            classified["qualification"].append(item)
        elif key.startswith("star_"):
            classified["compliance"].append(item)
        elif key.startswith("disq_"):
            classified["disqualification"].append(item)
    return classified


# ═══════════════════════════════════════════════════════════════════
# 目录合并引擎（替代旧的 _build_package_aware_outline）
# ═══════════════════════════════════════════════════════════════════

def _parse_format_tree(required_sections):
    """解析 format_requirements.required_sections 为目录树。

    检测规则：
    - 标题以 一、二、三... 开头 → 父级节点
    - 标题以 digit. 或 digit.digit 开头 → 父级节点（如"2.综合评分"）
    - 其他标题 → 作为扁平节点保留在原始顺序中
    - 封面/封皮标题 → 标记 is_cover=True
    """
    if not required_sections:
        return []
    cn_pat = re.compile(r'^[一二三四五六七八九十]+、')
    digit_pat = re.compile(r'^\d+(\.\d+)*[、．.]?')
    _score_keywords = re.compile(r'(?:综合)?评分[法]?|评审|评选|明细表')

    parent_indices = []
    for i, s in enumerate(required_sections):
        title = s.get("title", "")
        if cn_pat.match(title):
            parent_indices.append(i)
        elif digit_pat.match(title) and len(title) > 3:
            if not _score_keywords.search(title):
                parent_indices.append(i)

    if not parent_indices:
        return []

    def _build_node(item, children=None):
        title = item.get("title", "")
        p_texts = item.get("template_texts", [])
        is_cover = item.get("is_cover", False) or "封面" in title or "封皮" in title
        return {
            "source": "format_requirements",
            "title": title,
            "has_template": item.get("has_template", False),
            "children": children if children is not None else [],
            "description": p_texts[0] if p_texts else "",
            "is_cover": is_cover,
            "template_content": item.get("template_content", []),
        }

    tree = []
    processed_up_to = 0

    for idx, p_idx in enumerate(parent_indices):
        # 父级之前的非编号项 → 扁平节点
        while processed_up_to < p_idx:
            item = required_sections[processed_up_to]
            if item.get("title", ""):
                node = _build_node(item)
                # 紧跟在封面之后的非编号项 → 合并到封面（封面内页）
                if tree and tree[-1].get("is_cover") and not node.get("is_cover"):
                    tree[-1].setdefault("template_texts", []).extend(node.get("template_texts", []))
                    # 携带 template_content
                    _item_tc = node.get("template_content", [])
                    if _item_tc:
                        tree[-1].setdefault("template_content", []).extend(_item_tc)
                    # 把内页标题也记入封面模板文本
                    cover_title = tree[-1].get("title", "")
                    inner_title = node.get("title", "")
                    if inner_title and inner_title not in cover_title:
                        tree[-1].setdefault("template_texts", []).insert(0, inner_title)
                else:
                    tree.append(node)
            processed_up_to += 1

        # 父级节点（带子级）
        parent = required_sections[p_idx]
        next_p = parent_indices[idx + 1] if idx + 1 < len(parent_indices) else len(required_sections)
        children = [c for c in required_sections[p_idx + 1:next_p]
                    if not _score_keywords.search(c.get("title", ""))]
        tree.append(_build_node(parent, [
            {"source": "format_requirements", "title": c.get("title", ""),
             "template_texts": c.get("template_texts", []),
             "template_content": c.get("template_content", [])}
            for c in children
        ]))
        processed_up_to = p_idx + 1

    # 最后一个父级之后的剩余项 → 扁平节点
    while processed_up_to < len(required_sections):
        item = required_sections[processed_up_to]
        if item.get("title", ""):
            node = _build_node(item)
            # 紧跟在封面之后的非编号项 → 合并到封面
            if tree and tree[-1].get("is_cover") and not node.get("is_cover"):
                tree[-1].setdefault("template_texts", []).extend(node.get("template_texts", []))
                # 携带 template_content
                _item_tc = node.get("template_content", [])
                if _item_tc:
                    tree[-1].setdefault("template_content", []).extend(_item_tc)
                inner_title = node.get("title", "")
                if inner_title and inner_title not in tree[-1].get("title", ""):
                    tree[-1].setdefault("template_texts", []).insert(0, inner_title)
            else:
                tree.append(node)
        processed_up_to += 1

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
        ("商务", "商务响应"),
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
    if not isinstance(analysis_data, dict):
        analysis_data = {}
    fmt = analysis_data.get("format_requirements", {})
    if fmt and isinstance(fmt, dict) and fmt.get("required_sections"):
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




def _build_fallback_skeleton(analysis_data, classified_items):
    """第四级兜底：从 check_items 和业务/技术数据推断骨架，不依赖评分维度。"""
    if not isinstance(analysis_data, dict):
        analysis_data = {}
    if not isinstance(classified_items, dict):
        classified_items = {}
    skeleton = []
    
    # 1. 资格证明文件（来自 qualification check_items）
    qual_items = classified_items.get("qualification", [])
    if qual_items:
        import re as _re1
        
        def _gv(item, key):
            if isinstance(item, dict):
                return item.get(key, "") or ""
            return getattr(item, key, "") or ""
        
        def _strip_num_prefix(text):
            """去除编号前缀，用于去重。
            如 "6、报价" → "报价", "7.1报价" → "报价", "（一）报价" → "报价"
            """
            t = text.strip()
            t = _re1.sub(r'^[\d一二三四五六七八九十]+[、\.．）\)\s]*', '', t)
            t = _re1.sub(r'^[（(][\d一二三四五六七八九十]+[）)]\s*', '', t)
            return t[:60]
        
        def _qual_shorten_title(t, idx):
            """精简资格项标题并添加序号。"""
            if not t or not t.strip():
                return ""
            t = t.strip()
            t = re.sub(r'^[\d一二三四五六七八九十]+[、\.．\s]*\|?\s*', '', t)
            t = re.sub(r'^[（(][\d一二三四五六七八九十]+[）)]\s*', '', t)
            if len(t) > 40:
                t = t[:38] + "…"
            return f"{idx+1}. {t}"
        
        _seen = set()
        _deduped = []
        for item in qual_items:
            _t = _strip_num_prefix(_gv(item, "check_label") or _gv(item, "requirement") or "")
            if _t not in _seen:
                _seen.add(_t)
                _deduped.append(item)
        skeleton.append({
            "source": "qualification",
            "title": "资格证明文件",
            "description": "根据招标文件要求提供以下资格证明材料",
            "children": [
                {"title": _refine_qual_title(_gv(item, 'check_label') or _gv(item, 'requirement')),
                 "description": (_gv(item, "check_value") or _gv(item, "material") or "")[:100]}
                for item in _deduped
            ],
        })
    
    # 2. 报价表（目标：最低价法/综合评分法都需要报价）
    skeleton.append({
        "source": "inferred",
        "title": "报价一览表",
        "description": "项目报价及价格构成",
        "children": [],
    })
    
    # 3. 技术/服务响应（来自 technical items 或 packages）
    tech_items = []
    if isinstance(analysis_data.get("technical"), dict):
        tech_items = analysis_data["technical"].get("items", [])
    if not tech_items:
        packages = analysis_data.get("packages", [])
        if packages and isinstance(packages, list):
            for p in packages:
                params = p.get("parameters") or {}
                if not isinstance(params, dict):
                    params = {}
                has_tech = bool(params.get("core_products") or
                               params.get("starred_count") or
                               params.get("important_count") or
                               params.get("general_count") or
                               params.get("specifications"))
                if has_tech:
                    tech_items.append({"content": p.get("name", "技术参数")})
    if tech_items:
        skeleton.append({
            "source": "technical",
            "title": "技术参数响应",
            "description": "根据采购需求逐项响应技术参数要求",
            "children": [],
        })
    
    # 4. 商务响应（来自 business items 或 metadata.extra）
    biz_items = []
    if isinstance(analysis_data.get("business"), dict):
        biz_items = analysis_data["business"].get("items", [])
    # 兜底：从 metadata.extra 检测是否有商务条款
    if not biz_items:
        meta = analysis_data.get("metadata", {})
        if isinstance(meta, dict):
            extra = meta.get("extra", {})
            if isinstance(extra, dict):
                biz_indicators = ["payment_terms", "service_period", "delivery_location",
                                  "acceptance_standard", "warranty_period", "after_sale_service"]
                if any(extra.get(k) for k in biz_indicators):
                    biz_items = [{"content": "商务条款"}]
    if biz_items:
        skeleton.append({
            "source": "business",
            "title": "商务应答",
            "description": "对商务条款（付款、交付、验收等）的响应",
            "children": [],
        })
    
    # 5. 实质性条款/承诺函（来自 compliance check_items）
    comp_items = classified_items.get("compliance", [])
    if comp_items:
        skeleton.append({
            "source": "compliance",
            "title": "承诺函",
            "description": "按采购文件要求提供的各项承诺函",
            "children": [],
        })
    
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



def _refine_qual_title(text):
    """提炼资格项标题为极简短描述（≤20 字）。

    去掉编号前缀、法律套话、括号说明，提取核心资格要求。
    """
    if not text:
        return ""
    t = text.strip()
    # 1. 去掉各种前缀（含管道符分隔的复合编号如 "1 | 1、供应商..."）
    t = re.sub(r'^[\d一二三四五六七八九十]+(?:\.\d+)*\s*[、\.．\)）]\s*(?:\|?\s*[\d一二三四五六七八九十]*[、\.．]?\s*)*', '', t)
    t = re.sub(r'^\d+\.\d+\s+', '', t)
    t = re.sub(r'报价产品以及所有配置产品如是[^，，]+，需具有', '', t)
    t = re.sub(r'供应商为生产厂家应具有符合[^要]*要求的', '', t)
    t = re.sub(r'供应商为非生产厂家应具有符合[^要]*要求的', '', t)
    t = re.sub(r'符合《[^》]+》等政策法规要求[的，]?', '', t)
    t = re.sub(r'^[（(]?\s*实质性要求[）)]?\s*', '', t)
    t = re.sub(r'^[|｜]\s*[\d一二三四五六七八九十]*[、\.．]?\s*', '', t)
    # 2. 提取采购包并压缩标记
    pkg = ""
    m = re.search(r'[（(]采购包(\d+)[）)]', t)
    if m:
        pkg = f"（包{m.group(1)}）"
        t = t.replace(m.group(), "")
    # 3. 去掉运输资质冗余尾部
    t = re.sub(r'(和运输资质)或与具有运输资质公司签订的有效期内的(运输协议|危险化学品运输备案证明).*', r'\1', t)
    t = re.sub(r'或与具有运输资质公司签订的有效期内的(运输协议|危险化学品运输备案证明).*', '', t)
    t = re.sub(r'或与具有运输资质公司签订的有.*', '', t)
    t = re.sub(r'，?并提供相应凭证[^）)]*', '', t)
    # 4. 去掉经营许可证后的括号说明
    t = re.sub(r'(经营许可证)[（(][^）)]*[）)]', r'\1', t)
    t = re.sub(r'（如[^）]*）', '', t)
    # 5. 分号拆分 + 合并生产/经营
    if "；" in t:
        has_produce = '生产许可证' in t
        has_operate = '经营许可证' in t
        if has_produce and has_operate:
            t = '医疗器械生产/经营许可证'
        else:
            parts = [p.strip() for p in t.split("；") if p.strip()]
            t = parts[0] if parts else t
    # 6. 尾部清理
    t = re.sub(r'[；;。，,\s]+$', '', t)
    t = re.sub(r'^[|｜]?\s*', '', t)
    t = t.strip()
    # 7. 极短化处理：逐级降级
    # 7a. 括号精简：优先提取括号内的关键词
    if len(t) > 20:
        m = re.search(r'[（(]([^）)]{2,20})[）)]', t)
        if m and len(m.group(1)) < len(t) - 2:
            t = m.group(1)
    # 7b. 特定长句缩略
    if len(t) > 20:
        replacements = [
            (r'参加政府采购活动前三年内，在经营活动中没有重大违法记录', '无重大违法记录'),
            (r'参加本次采购活动前三年内，在经营活动中没有重大违法记录', '无重大违法记录'),
            (r"未被列入'中国政府采购网'政府采购严重违法失信行为记录名单", '无政府采购严重违法失信记录'),
            (r"未被列入'信用中国'网站失信被执行人.*", '无信用中国失信记录'),
            (r'具有履行合同所必需的设备和专业技术能力', '履行合同所必需的设备和专业技术能力'),
        ]
        for pattern, replacement in replacements:
            if re.search(pattern, t):
                t = replacement
                break
    # 7c. "无行贿"短匹配
    if len(t) > 20:
        m = re.search(r'(无行贿[^，。]{2,20})', t)
        if m:
            t = m.group(1)
    # 7d. "信用中国"短匹配
    if len(t) > 20:
        m = re.search(r"('?信用中国'?)[^，。]*", t)
        if m:
            t = m.group(1) + "信用记录"
    # 7e. 截断兜底
    if len(t) > 20:
        t = t[:18] + "…"
    # 8. 追加采购包
    if pkg:
        t = t + pkg if t else pkg
    return t
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
    import re as _re_np
    def _strip_num_prefix(text):
        t = text.strip()
        t = _re_np.sub(r'^[\d一二三四五六七八九十]+(?:\.\d+)?[、\.．）\)\s]*', '', t)
        t = _re_np.sub(r'^[（(][\d一二三四五六七八九十]+[）)]\s*', '', t)
        return t[:40]
    seen = set()
    deduped = []
    for item in qual_items:
        req = _get_val(item, "requirement") or _get_val(item, "check_label") or ""
        key = _strip_num_prefix(req)
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
                "title": _refine_qual_title(_get_val(item, 'requirement') or _get_val(item, 'check_label')),
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
    if not isinstance(analysis_data, dict):
        analysis_data = {}
    if not isinstance(classified_items, dict):
        classified_items = {}
    # 3.1 商务偏离表子项
    business_items = analysis_data.get("business", {}).get("items", []) if isinstance(analysis_data.get("business"), dict) else []
    if business_items:
        _fill_business_children(skeleton, business_items)
    
    # 3.2 技术偏离表描述
    technical_items = analysis_data.get("technical", {}).get("items", []) if isinstance(analysis_data.get("technical"), dict) else []
    packages = analysis_data.get("packages", [])
    if technical_items or packages:
        _fill_tech_description(skeleton, technical_items, packages)
    
    # 3.3 资格项填充
    _fill_qualification(skeleton, classified_items)
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



def _merge_document_chapters(skeleton, analysis_data):
    """合并原文特色章节到骨架中（格式骨架未覆盖的章节）。"""
    chapters = analysis_data.get("document_chapters", [])
    if not chapters:
        return
    # 需要特殊处理的章节映射（关键词 → 目录节点）
    chapter_node_map = {
        "须知": {"source": "inferred", "title": "供应商须知", "description": "响应供应商须知要求", "children": []},
        "变动": {"source": "inferred", "title": "谈判可变动内容", "description": "响应谈判过程中可实质性变动的内容", "children": []},
    }
    # 骨架已有的标题关键词（用于判断是否已覆盖）
    existing_keywords = set()
    for n in skeleton:
        t = n.get("title", "")
        existing_keywords.add(t)
    existing_titles = {n.get("title", "") for n in skeleton}
    for ch in chapters:
        ch_clean = ch.strip()
        if ch_clean in ("目录",) or not ch_clean:
            continue
        # 匹配特殊映射
        mapped = False
        for kw, node in chapter_node_map.items():
            if kw in ch_clean:
                nt = node["title"]
                if nt not in existing_titles:
                    skeleton.append(dict(node))
                    existing_titles.add(nt)
                    existing_keywords.add(nt)
                    logger.info("[catalog] 补充原文章节: %s → %s", ch_clean, nt)
                    mapped = True
                break
        if not mapped:
            logger.debug("[catalog] 未覆盖的原文章节: %s", ch_clean)

def _assign_numbers(skeleton):
    """给骨架节点分配统一编号（一、二、三...），子级用级联编号（1.1, 1.1.1）。

    多封面（分册）处理：
    - 封面节点转为分册标签（不编号）
    - 每个分册内的内容节点独立从 一、二、三... 编号
    - 单封面/无封面时保持原有逻辑
    """
    cn_prefix = re.compile(r'^[一二三四五六七八九十]+、')
    casc_prefix = re.compile(r'^\d+(\.\d+)*\s+')

    def _clean_node(node):
        title = node.get("title", "")
        title = cn_prefix.sub("", title).strip()
        title = casc_prefix.sub("", title).strip()
        node["title"] = title
        for child in node.get("children", []):
            _clean_node(child)

    for node in skeleton:
        _clean_node(node)

    chinese_nums = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八"]

    def _number_children(children, parent_prefix):
        for i, child in enumerate(children):
            child_prefix = f"{parent_prefix}.{i+1}"
            child["title"] = child.get("title", "")
            if child["title"]:
                child["title"] = f"{child_prefix} {child['title']}"
            _number_children(child.get("children", []), child_prefix)

    # 检测是否有封面（分册）
    covers = [n for n in skeleton if n.get("is_cover")]
    has_multi_volumes = len(covers) > 0

    if has_multi_volumes:
        # ── 分册方案 ──
        new_skeleton = []
        vol_content_count = 0  # 当前分册的内容节点数

        for node in skeleton:
            if node.get("is_cover"):
                # 封面 → 转为分册标签
                vol_name = _extract_volume_name(node.get("title", ""))
                new_skeleton.append({
                    "source": "format_requirements",
                    "title": vol_name,
                    "is_volume_label": True,
                    "is_cover": True,
                    "children": [],
                    "template_content": node.get("template_content", []),
                })
                vol_content_count = 0
            else:
                num = chinese_nums[vol_content_count] if vol_content_count < len(chinese_nums) else str(vol_content_count + 1)
                node["title"] = f"{num}、{node['title']}"
                _number_children(node.get("children", []), str(vol_content_count + 1))
                new_skeleton.append(node)
                vol_content_count += 1

        skeleton = new_skeleton
        # 兜底编号：最后一个分册的内容数
        total_content = vol_content_count
    else:
        # ── 单分册：原有逻辑 ──
        for idx, node in enumerate(skeleton):
            num = chinese_nums[idx] if idx < len(chinese_nums) else str(idx + 1)
            node["title"] = f"{num}、{node['title']}"
            _number_children(node.get("children", []), str(idx + 1))
        total_content = len(skeleton)

    # 追加 "其他材料" 兜底节点
    last_num = chinese_nums[total_content] if total_content < len(chinese_nums) else str(total_content + 1)
    skeleton.append({
        "source": "catch_all",
        "title": f"{last_num}、其他材料",
        "description": "供应商认为需要提交的其他材料",
        "children": [],
    })
    return skeleton


def _extract_volume_name(cover_title):
    """从封面标题提取分册名称。

    '（资格性响应文件封面、封皮）' → '资格性响应文件'
    '（其他响应文件封面、封皮）'  → '其他响应文件'
    '资格性响应文件封面'          → '资格性响应文件'
    """
    if not cover_title:
        return "封面"
    m = re.search(r'[（(]([^）)]+)[）)]', cover_title)
    name = m.group(1) if m else cover_title
    name = re.sub(r'封面|封皮', '', name).strip()
    name = name.rstrip('，,、')
    return name if name else "封面"


def build_catalog(analysis_data, classified_items, section_index=None):
    """目录合并引擎主入口。

    Args:
        analysis_data: 分析数据
        classified_items: 分类的核对项
        section_index: 可选的章节索引（用于从招标文件提取骨架）

    骨架生成策略：
        1. 从招标文件的格式要求章节提取（最贴合原文，唯一权威来源）
        2. 从招标文件"投标文件组成"章节提取（降级）
        3. 从分析数据推断（无显式格式时）
        4. 旧版硬编码骨架（兜底）
    """
    
    if not isinstance(analysis_data, dict):
        analysis_data = {}
    if not isinstance(classified_items, dict):
        classified_items = {}
    skeleton = None
    source_type = None  # track where skeleton came from

    # 第一级：从格式要求构建（唯一权威来源）
    skeleton = build_base_skeleton(analysis_data)
    if skeleton:
        source_type = "format_requirements"
        logger.info("[catalog] 使用格式要求骨架: %d 个节点", len(skeleton))

    # 第二级：从招标文件"投标文件组成"章节提取
    if not skeleton and section_index:
        try:
            from .catalog_skeleton_extractor import extract_enriched_skeleton_from_tender
            skeleton = extract_enriched_skeleton_from_tender(section_index)
            if skeleton:
                source_type = "tender_skeleton"
                logger.info("[catalog] 使用招标文件提取的骨架: %d 个节点", len(skeleton))
        except Exception as exc:
            logger.warning("[catalog] 招标文件骨架提取失败: %s", exc)

    # 第三级：从分析数据推断（无显式格式时）
    if not skeleton:
        try:
            from .catalog_inference import infer_skeleton_from_analysis
            if isinstance(analysis_data, dict):
                skeleton = infer_skeleton_from_analysis(analysis_data, section_index)
                if skeleton:
                    source_type = "analysis_inference"
                    logger.info("[catalog] 使用分析推断的骨架: %d 个节点", len(skeleton))
        except Exception as exc:
            logger.warning("[catalog] 分析推断骨架失败: %s", exc)

    # 第四级：旧版硬编码骨架（终极兜底）
    if not skeleton:
        skeleton = _infer_skeleton_fallback(analysis_data)
        if skeleton:
            source_type = "fallback_hardcoded"
            logger.info("[catalog] 使用旧版硬编码骨架: %d 个节点", len(skeleton))

    if not skeleton:
        logger.warning("[catalog] 骨架为空（全部失败），返回空目录")
        return []
    
    # 阶段3：填充详情（格式要求来源时，不额外补充章节）
    enrich_section_details(skeleton, analysis_data, classified_items)
    
    # 只有非格式要求来源时，才补充推测章节
    if source_type != "format_requirements":
        # 从 check_items 和业务/技术数据推断骨架
        fallback = _build_fallback_skeleton(analysis_data, classified_items)
        if fallback:
            import re as _re
            _cn_prefix = _re.compile(r'^[一二三四五六七八九十]+、')
            existing_titles = {_cn_prefix.sub("", n.get("title", "")).strip() for n in skeleton}
            merged = list(skeleton)
            for fb_node in fallback:
                fb_title = _cn_prefix.sub("", fb_node.get("title", "")).strip()
                if fb_title and fb_title not in existing_titles:
                    merged.append(fb_node)
                    existing_titles.add(fb_title)
                    logger.info("[catalog] 补充章节: %s", fb_title)
            if len(merged) > len(skeleton):
                skeleton = merged
                logger.info("[catalog] 合并后骨架: %d 个节点",
                            len(skeleton))
        
        # 补充原文特有章节
        _merge_document_chapters(skeleton, analysis_data)
    
    # 阶段4：编号
    outline = _assign_numbers(skeleton)
    
    # 验证
    chapters = analysis_data.get("document_chapters", [])
    warnings = validate_completeness(outline, chapters)
    if warnings:
        logger.info("[catalog] 覆盖验证警告: %s", warnings)
    
    return outline


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



def _build_package_aware_outline(task, analysis_result, filtered_analysis_data, classified_items, generation_level=None):
    """替换为新的合并引擎。"""
    # 尝试从 analysis_result 获取 section_index
    section_index = None
    if analysis_result and hasattr(analysis_result, 'analysis_data'):
        try:
            import json
            payload = json.loads(analysis_result.analysis_data) if isinstance(analysis_result.analysis_data, str) else analysis_result.analysis_data or {}
            section_index = payload.get('_section_index') if isinstance(payload, dict) else None
        except Exception:
            pass
    return build_catalog(filtered_analysis_data, classified_items, section_index=section_index)



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


def _build_fallback_outline(analysis_result, text):
    """LLM 不可用时的降级目录。"""
    return [
        {"title": "一、项目概述", "description": ""},
        {"title": "二、技术响应", "description": ""},
        {"title": "三、商务应答", "description": ""},
        {"title": "四、资格审查资料", "description": ""},
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
                "template": ["投标总体说明与实施承诺", "货物技术偏离、供货组织与实施方案", "商务条款、资格证明"],
            },
            "SERVICE": {
                "requirement": ["项目概述、服务范围与交付边界", "服务方案、技术路线与保障机制", "商务资质、评分点与风险控制应答"],
                "template": ["投标总体说明与服务承诺", "服务流程、技术路线与保障方案", "商务条款、资格证明"],
            },
            "ENGINEERING": {
                "requirement": ["项目概述、工程范围与实施边界", "施工组织、技术措施与资源配置", "商务资质、评分点与风险控制应答"],
                "template": ["投标总体说明与履约承诺", "施工组织设计、技术措施与资源方案", "商务条款、资格证明"],
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
            # 始终重新生成：目录格式随代码版本更新，旧缓存不再适用
            logger.info("[catalog] 清除旧缓存，重新生成: shared_resource=%s", task.shared_resource_id)
            db.session.delete(existing)
            db.session.commit()
            outline = None
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
        api_key=current_app.config.get("DEEPSEEK_API_KEY"),
        base_url=current_app.config.get("DEEPSEEK_BASE_URL"),
        default_model=current_app.config.get("DEEPSEEK_MODEL_NAME"),
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
