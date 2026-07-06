"""生成质量保证模块 — 需求追踪矩阵 + 生成前后校验。

三层证据架构：
  第一层: 主体资料（确定性注入）→ 直接读 subject_material_file
  第二层: 知识库（语义检索）  → MultiRecallEngine
  第三层: 招标文件原文        → tender 集合检索
"""

import json
import logging
import re
from typing import Any, Optional

from flask import current_app

from ..core.extensions import db
from ..core.time_utils import utc_now
from ..domain import BiddingAnalysisResult, FileStorage, SubjectCompany, SubjectMaterialFile
from ..infrastructure.document_parser import DocumentParser
from ..infrastructure.embedding_client import EmbeddingClient
from ..infrastructure.multi_recall_engine import MultiRecallEngine
from .common import log_operation

logger = logging.getLogger(__name__)

# material_type 到 requirement_type 的映射表
MATERIAL_REQUIREMENT_MAP = {
    "BUSINESS_LICENSE": {"qualification", "qualification_review", "basic_info"},
    "QUALIFICATION_FILE": {"qualification", "qualification_review", "technical"},
    "LEGAL_PERSON_ID_CARD": {"legal", "qualification_review"},
    "LEGAL_PERSON_STATEMENT": {"legal", "qualification_review"},
    "AUTHORIZATION_LETTER": {"legal", "qualification_review"},
    "AUTHORIZED_PERSON_ID_CARD": {"legal"},
    "QUALIFICATION_DECLARATION": {"qualification", "qualification_review"},
    "FINANCIAL_STATEMENT": {"business", "qualification_review"},
    "INTEGRITY_COMMITMENT": {"business", "qualification"},
}

MATERIAL_LABELS = {
    "BUSINESS_LICENSE": "营业执照",
    "QUALIFICATION_FILE": "资质文件",
    "LEGAL_PERSON_ID_CARD": "法人身份证",
    "AUTHORIZATION_LETTER": "授权委托书",
    "AUTHORIZED_PERSON_ID_CARD": "被授权人身份证",
    "QUALIFICATION_DECLARATION": "资质声明函",
    "LEGAL_PERSON_STATEMENT": "法定代表人身份证明",
    "FINANCIAL_STATEMENT": "财务报表",
    "INTEGRITY_COMMITMENT": "廉洁承诺书",
}


def _get_embedding_client() -> EmbeddingClient:
    return EmbeddingClient(
        api_key=current_app.config.get("QWEN_API_KEY", ""),
        base_url=current_app.config.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        model=current_app.config.get("QWEN_EMBEDDING_MODEL", "text-embedding-v4"),
        max_batch_size=10,
    )


def _split_units(text: str, max_items=20) -> list[str]:
    """分割文本为语义单元。"""
    normalized = str(text or "").replace("\r", "\n")
    parts = re.split(r"[\n；;。]", normalized)
    units = []
    for part in parts:
        item = " ".join(part.split()).strip(" ；;。")
        if len(item) < 4:
            continue
        if item not in units:
            units.append(item)
        if len(units) >= max_items:
            break
    return units


def _match_requirement_to_material(req: dict, material_texts: dict) -> Optional[dict]:
    """将一条 requirement 与主体材料匹配。"""
    req_type = req.get("requirement_type", "")
    req_text = req.get("requirement_text", "")

    # 按 type 映射匹配
    for mat_id, mat_info in material_texts.items():
        mat_type = mat_info.get("material_type", "")
        matched_types = MATERIAL_REQUIREMENT_MAP.get(mat_type, set())
        if req_type in matched_types:
            # 进一步验证：requirement 文本和材料名称/内容是否有语义关联
            mat_label = mat_info.get("label", "")
            mat_file_name = mat_info.get("file_name", "")
            if any(kw in req_text for kw in [mat_label, mat_file_name[:6]]):
                return mat_info
            # 检查材料文本内容是否包含 requirement 关键词
            mat_text = mat_info.get("text", "")
            req_keywords = [w for w in req_text if len(w) > 1]
            if req_keywords and any(kw in mat_text for kw in req_keywords):
                return mat_info

    # 二次匹配：基于文件名和 requirement 文本的关键词
    for mat_id, mat_info in material_texts.items():
        mat_file_name = mat_info.get("file_name", "")
        mat_label = mat_info.get("label", "")
        if any(kw in mat_file_name or kw in mat_label for kw in [req_text[:6], req_text[:8]]):
            return mat_info

    return None


def _flatten_outline(outline: list[dict], result: list[dict], parent_title=""):
    """将嵌套的目录结构展开为扁平列表。"""
    for item in outline:
        title = item.get("title", "")
        full_title = f"{parent_title} > {title}" if parent_title else title
        result.append({
            "title": full_title,
            "description": item.get("description", ""),
            "original_title": title,
        })
        children = item.get("children", [])
        if children:
            _flatten_outline(children, result, full_title)


# ========== 任务 7.3: Prompt 约束注入 ==========

def inject_constraints_into_prompt(chapter_title: str, chapter_desc: str,
                                   matrix: dict, bindings: dict) -> dict:
    """构建章节的约束信息，供 Prompt 组装使用。

    Args:
        chapter_title: 章节标题
        chapter_desc: 章节描述
        matrix: 需求追踪矩阵（含 requirements）
        bindings: 章节-需求绑定

    Returns:
        dict: {
          "tier1_items": [...],  # 第一层：主体材料
          "tier2_items": [...],  # 第二层：知识库
          "tier3_items": [...],  # 第三层：招标要求
          "hard_constraints": [...]  # 废标项硬约束
        }
    """
    requirements = matrix.get("requirements", [])
    all_bindings = bindings.get("bindings", [])

    # 找到本章关联的 requirement IDs
    chapter_req_ids = set()
    for b in all_bindings:
        if b.get("chapter_title", "").endswith(chapter_title) or chapter_title in b.get("chapter_title", ""):
            chapter_req_ids.update(b.get("requirement_ids", []))

    # 如果没有精确匹配，尝试降级匹配
    if not chapter_req_ids:
        # 1) 按需求类型匹配：章节标题含"资质"→匹配 qualification 类
        chapter_lower = chapter_title.lower()
        type_keywords = {
            "资质": ("qualification", "disqualification"),
            "技术": ("technical",),
            "商务": ("business",),
            "评分": ("scoring",),
            "项目概况": ("basic_info",),
            "报价": ("business",),
            "售后": ("business",),
            "交货": ("business",),
        }
        matched_types = set()
        for kw, types in type_keywords.items():
            if kw in chapter_lower:
                matched_types.update(types)
        # 2) 按类型和关键词匹配
        for req in requirements:
            req_type = req.get("requirement_type", "")
            req_text = req.get("requirement_text", "")
            # 类型匹配
            if matched_types and req_type in matched_types:
                chapter_req_ids.add(req["item_id"])
            # 关键词匹配（章节标题中的词出现在需求文本中）
            elif any(kw in req_text for kw in chapter_title.replace(" ", "")):
                chapter_req_ids.add(req["item_id"])
            # 需求文本提及了章节标题
            elif any(kw in chapter_title for kw in req_text[:4]):
                chapter_req_ids.add(req["item_id"])

    tier1_items = []
    tier2_items = []
    tier3_items = []
    hard_constraints = []

    for req in requirements:
        if req["item_id"] not in chapter_req_ids:
            continue

        status = req.get("evidence_status", "")
        if req.get("requirement_type") == "disqualification":
            hard_constraints.append(req)
        elif status == "TIER1":
            tier1_items.append(req)
        elif status == "TIER3":
            tier3_items.append(req)
        else:
            tier2_items.append(req)

    return {
        "chapter_title": chapter_title,
        "chapter_desc": chapter_desc,
        "tier1_items": tier1_items,
        "tier2_items": tier2_items,
        "tier3_items": tier3_items,
        "hard_constraints": hard_constraints,
    }


# ========== 任务 7.4: 生成后校验 ==========

def post_generation_verify(chapter_title: str, generated_content: str,
                           constraints: dict) -> dict:
    """校验生成内容是否满足约束。

    Args:
        chapter_title: 章节标题
        generated_content: 生成的正文字
        constraints: inject_constraints_into_prompt 的返回

    Returns:
        dict: {
          "chapter_title": ...,
          "checks": [{"requirement_id": ..., "covered": bool, "hallucinated": bool}, ...],
          "overall": "PASS" | "WARN" | "FAIL"
        }
    """
    checks = []
    has_hallucination = False
    missing_coverage = False

    text_lower = generated_content.lower()

    # 检查第一层：主体材料必须引用
    for item in constraints.get("tier1_items", []):
        req_text = item.get("requirement_text", "")
        # 从需求文本中提取有意义的检索关键词
        # 策略: 1)按分隔符分割；2)提取英文/数字词；3)短文本整体作为关键词
        words = re.split(r'[\s,，。；;：:、（）()（）【】\[\]{}]', req_text)
        keywords = [w for w in words if len(w) > 1]
        # 补充: 提取英文+数字组合词
        eng_nums = re.findall(r'[A-Za-z0-9][A-Za-z0-9./-]+', req_text)
        keywords.extend([e for e in eng_nums if e not in keywords])
        # 如果整体文本较短(<20字符)且无分离结果，整体作为关键词
        if not keywords and len(req_text) <= 30:
            keywords = [req_text]
        elif len(keywords) == 1 and len(keywords[0]) == len(req_text):
            pass  # 已经是整体
        elif not keywords and len(req_text) > 30:
            # 长文本无分隔符: 取前20字符作为关键词
            keywords = [req_text[:20]]
        covered = any(kw.lower() in text_lower for kw in keywords)
        if not covered:
            missing_coverage = True
        checks.append({
            "requirement_id": item["item_id"],
            "evidence_tier": 1,
            "text": req_text[:80],
            "covered": covered,
            "hallucinated": False,
            "detail": "" if covered else "主体已有此材料但正文未引用",
        })

    # 检查第二层：知识库内容
    for item in constraints.get("tier2_items", []):
        req_text = item.get("requirement_text", "")
        # 从需求文本中提取有意义的检索关键词
        # 策略: 1)按分隔符分割；2)提取英文/数字词；3)短文本整体作为关键词
        words = re.split(r'[\s,，。；;：:、（）()（）【】\[\]{}]', req_text)
        keywords = [w for w in words if len(w) > 1]
        # 补充: 提取英文+数字组合词
        eng_nums = re.findall(r'[A-Za-z0-9][A-Za-z0-9./-]+', req_text)
        keywords.extend([e for e in eng_nums if e not in keywords])
        # 如果整体文本较短(<20字符)且无分离结果，整体作为关键词
        if not keywords and len(req_text) <= 30:
            keywords = [req_text]
        elif len(keywords) == 1 and len(keywords[0]) == len(req_text):
            pass  # 已经是整体
        elif not keywords and len(req_text) > 30:
            # 长文本无分隔符: 取前20字符作为关键词
            keywords = [req_text[:20]]
        covered = any(kw.lower() in text_lower for kw in keywords) if keywords else False
        if not covered:
            missing_coverage = True
        checks.append({
            "requirement_id": item["item_id"],
            "evidence_tier": 2,
            "text": req_text[:80],
            "covered": covered,
            "hallucinated": False,
        })

    # 检查第三层：招标要求
    for item in constraints.get("tier3_items", []):
        req_text = item.get("requirement_text", "")
        # 从需求文本中提取有意义的检索关键词
        # 策略: 1)按分隔符分割；2)提取英文/数字词；3)短文本整体作为关键词
        words = re.split(r'[\s,，。；;：:、（）()（）【】\[\]{}]', req_text)
        keywords = [w for w in words if len(w) > 1]
        # 补充: 提取英文+数字组合词
        eng_nums = re.findall(r'[A-Za-z0-9][A-Za-z0-9./-]+', req_text)
        keywords.extend([e for e in eng_nums if e not in keywords])
        # 如果整体文本较短(<20字符)且无分离结果，整体作为关键词
        if not keywords and len(req_text) <= 30:
            keywords = [req_text]
        elif len(keywords) == 1 and len(keywords[0]) == len(req_text):
            pass  # 已经是整体
        elif not keywords and len(req_text) > 30:
            # 长文本无分隔符: 取前20字符作为关键词
            keywords = [req_text[:20]]
        covered = any(kw.lower() in text_lower for kw in keywords) if keywords else False
        if not covered:
            missing_coverage = True
        checks.append({
            "requirement_id": item["item_id"],
            "evidence_tier": 3,
            "text": req_text[:80],
            "covered": covered,
            "hallucinated": False,
        })

    # 检查硬约束（废标项）
    for item in constraints.get("hard_constraints", []):
        req_text = item.get("requirement_text", "")
        violated_keywords = [w for w in req_text if len(w) > 1]
        violated = any(kw in text_lower for kw in violated_keywords) if violated_keywords else False
        checks.append({
            "requirement_id": item["item_id"],
            "evidence_tier": "hard_constraint",
            "text": req_text[:80],
            "violated": violated,
            "covered": False,
            "detail": "废标约束可能被违反" if violated else "",
        })
        if violated:
            has_hallucination = True

    # 汇总
    if has_hallucination:
        overall = "FAIL"
    elif missing_coverage:
        overall = "WARN"
    else:
        overall = "PASS"

    return {
        "chapter_title": chapter_title,
        "checks": checks,
        "overall": overall,
    }
