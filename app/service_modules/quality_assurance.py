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


# ========== 任务 7.1: 需求追踪矩阵构建 ==========

def build_requirement_traceability_matrix(analysis_result: BiddingAnalysisResult,
                                           subject_id: Optional[int] = None) -> dict:
    """构建需求追踪矩阵。

    从 analysis_data 中提取 atomic_requirement_items，
    与主体已有材料交叉比对，标记 evidence_status。

    Args:
        analysis_result: 分析结果对象
        subject_id: 主体公司 ID（可选）

    Returns:
        dict: 需求追踪矩阵
          {
            "requirements": [...],
            "summary": {"total", "tier1", "tier2", "tier3", "no_evidence"}
          }
    """
    # 1. 提取 atomic_requirement_items
    if not analysis_result or not analysis_result.analysis_data:
        return {"requirements": [], "summary": {"total": 0, "tier1": 0, "tier2": 0, "tier3": 0, "no_evidence": 0}}

    try:
        analysis_data = json.loads(analysis_result.analysis_data) if isinstance(analysis_result.analysis_data, str) else analysis_result.analysis_data
    except (json.JSONDecodeError, TypeError):
        analysis_data = {}

    if not isinstance(analysis_data, dict):
        return {"requirements": [], "summary": {"total": 0, "tier1": 0, "tier2": 0, "tier3": 0, "no_evidence": 0}}

    # 从 analysis_data 提取结构化字段
    analysis_context = analysis_data.get("analysis_context", analysis_data)
    bidder_notice = analysis_context.get("bidder_notice", {}) or {}

    # 构建 atomic_requirement_items（简化版，复用既有逻辑的提取结果）
    requirements = []
    idx = 1

    # 基本信息
    for key, label in [("project_name", "项目名称"), ("project_no", "项目编号"),
                       ("package_no", "包号"), ("budget", "预算金额"),
                       ("tenderee", "招标人"), ("agent", "代理机构")]:
        value = (bidder_notice.get(key) or "").strip()
        if value:
            requirements.append({
                "item_id": f"REQ-{idx:03d}",
                "requirement_text": f"{label}：{value}",
                "requirement_type": "basic_info",
                "requirement_level": "REQUIRED",
                "evidence_status": "TIER3",  # 招标文件原文
                "evidence_source": None,
            })
            idx += 1

    # 结构化字段
    for field_key, req_type, level, label in [
        ("business_requirements", "business", "NORMAL", "商务要求"),
        ("technical_requirements", "technical", "NORMAL", "技术要求"),
        ("qualification_requirements", "qualification", "REQUIRED", "资格性审查"),
        ("scoring_items", "scoring", "IMPORTANT", "评分项"),
        ("disqualification_items", "disqualification", "REQUIRED", "废标项"),
    ]:
        text = (analysis_context.get(field_key) or "").strip()
        if text:
            units = _split_units(text)
            for unit in units:
                requirements.append({
                    "item_id": f"REQ-{idx:03d}",
                    "requirement_text": unit,
                    "requirement_type": req_type,
                    "requirement_level": level,
                    "evidence_status": "TIER3",  # 先标记为第三层
                    "evidence_source": None,
                })
                idx += 1

    # 2. 与主体已有材料交叉比对（第一层）
    if subject_id:
        materials = SubjectMaterialFile.query.filter_by(subject_id=subject_id).all()
        material_file_ids = [m.file_id for m in materials if m.file_id]

        # 读取材料文本摘要
        material_texts = {}
        for m in materials:
            if m.file_id:
                file_record = FileStorage.query.get(m.file_id)
                if file_record:
                    try:
                        from ..service_modules.task_pipeline.helpers import _read_file_text
                        text = _read_file_text(file_record)
                        if text:
                            material_texts[m.id] = {
                                "text": text[:2000],
                                "material_type": m.material_type,
                                "file_id": m.file_id,
                                "file_name": m.file_name or "",
                                "label": MATERIAL_LABELS.get(m.material_type, m.material_type or "其他"),
                            }
                    except Exception as exc:
                        logger.warning("[qa] 读取材料文本失败: %s", exc)

        # 逐条匹配
        for req in requirements:
            matched = _match_requirement_to_material(req, material_texts)
            if matched:
                req["evidence_status"] = "TIER1"
                req["evidence_source"] = {
                    "type": "subject_material",
                    "material_type": matched["material_type"],
                    "file_id": matched["file_id"],
                    "file_name": matched["file_name"],
                    "label": matched["label"],
                }

    # 3. 汇总
    tier1 = sum(1 for r in requirements if r["evidence_status"] == "TIER1")
    tier3 = sum(1 for r in requirements if r["evidence_status"] == "TIER3")
    no_evidence = sum(1 for r in requirements if r.get("evidence_status") in (None, ""))

    matrix = {
        "requirements": requirements,
        "summary": {
            "total": len(requirements),
            "tier1": tier1,
            "tier3": tier3,
            "no_evidence": no_evidence,
        },
    }

    # 持久化到 analysis_data
    if isinstance(analysis_data, dict):
        analysis_data["requirement_traceability_matrix"] = matrix
        analysis_result.analysis_data = json.dumps(analysis_data, ensure_ascii=False)
        db.session.flush()

    return matrix


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


# ========== 任务 7.2: 章节-需求绑定 ==========

def bind_requirements_to_chapters(requirements: list[dict], outline: list[dict]) -> dict:
    """使用 embedding 语义匹配将 requirements 绑定到章节。

    Args:
        requirements: 需求列表
        outline: 目录大纲 [{title, description, children}, ...]

    Returns:
        dict: 章节-需求映射
          {
            "bindings": [{"chapter_title": "...", "requirement_ids": [...]}, ...],
            "unbound_requirements": [...]
          }
    """
    embed_client = _get_embedding_client()
    bindings = []
    unbound = list(requirements)

    # 提取章节列表
    chapters = []
    _flatten_outline(outline, chapters)

    if embed_client.is_available() and chapters and requirements:
        try:
            # 对所有章节和需求做 embedding
            chapter_texts = [f"{c['title']} {c.get('description', '')}" for c in chapters]
            req_texts = [r["requirement_text"] for r in requirements]
            all_texts = chapter_texts + req_texts
            embeddings = embed_client.embed_texts(all_texts)

            chapter_embs = embeddings[:len(chapters)]
            req_embs = embeddings[len(chapters):]

            # 计算相似度矩阵
            import numpy as np
            chapter_arr = np.array(chapter_embs)
            req_arr = np.array(req_embs)
            similarity = np.dot(chapter_arr, req_arr.T)
            # 归一化
            chapter_norms = np.linalg.norm(chapter_arr, axis=1, keepdims=True)
            req_norms = np.linalg.norm(req_arr, axis=1, keepdims=True)
            similarity = similarity / (chapter_norms * req_norms.T + 1e-10)

            # 分配
            threshold = 0.6
            bound_ids = set()
            for ci, chapter in enumerate(chapters):
                chapter_req_ids = []
                for ri in range(len(requirements)):
                    if similarity[ci][ri] >= threshold and ri not in bound_ids:
                        chapter_req_ids.append(requirements[ri]["item_id"])
                        bound_ids.add(ri)
                bindings.append({
                    "chapter_title": chapter["title"],
                    "chapter_index": ci,
                    "requirement_ids": chapter_req_ids,
                })

            unbound = [r for ri, r in enumerate(requirements) if ri not in bound_ids]

        except Exception as exc:
            logger.warning("[qa] Embedding 匹配失败，降级为关键字匹配: %s", exc)
            # 降级：关键字匹配
            pass

    # 如果 embedding 不可用或失败，使用关键字降级
    if not bindings:
        for chapter in chapters:
            chapter_req_ids = []
            chapter_text = f"{chapter['title']} {chapter.get('description', '')}"
            for ri, req in enumerate(requirements):
                req_text = req["requirement_text"]
                if any(kw in chapter_text for kw in req_text[:4]):
                    chapter_req_ids.append(req["item_id"])
            bindings.append({
                "chapter_title": chapter["title"],
                "chapter_index": chapters.index(chapter),
                "requirement_ids": chapter_req_ids,
            })

    return {
        "bindings": bindings,
        "unbound_requirements": [r for r in unbound if r.get("requirement_level") != "NORMAL"],
    }


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


# ========== 任务 7.5: 覆盖率报告 ==========

def build_coverage_report(task, matrix: dict, verify_results: list[dict]) -> dict:
    """生成可读的覆盖率报告。

    Args:
        task: BiddingTask 对象
        matrix: 需求追踪矩阵
        verify_results: 各章节的校验结果列表

    Returns:
        dict: 覆盖率报告
    """
    summary = matrix.get("summary", {})
    total = summary.get("total", 0)
    tier1 = summary.get("tier1", 0)

    covered = 0
    hallucinated = 0
    violated = 0
    chapter_status = []

    for vr in verify_results:
        chapter_checks = vr.get("checks", [])
        chapter_covered = sum(1 for c in chapter_checks if c.get("covered"))
        chapter_total = len(chapter_checks)
        chapter_hallucinated = any(c.get("hallucinated") for c in chapter_checks)
        chapter_violated = any(c.get("violated") for c in chapter_checks)
        covered += chapter_covered
        if chapter_hallucinated:
            hallucinated += 1
        if chapter_violated:
            violated += 1

        if chapter_total > 0:
            status = "FAIL" if (chapter_hallucinated or chapter_violated) else ("WARN" if chapter_covered < chapter_total else "PASS")
        else:
            status = "PASS"

        chapter_status.append({
            "title": vr.get("chapter_title", ""),
            "status": status,
            "covered": chapter_covered,
            "total": chapter_total,
            "hallucinated": chapter_hallucinated,
            "violated": chapter_violated,
        })

    report = {
        "task_id": task.id,
        "task_name": task.task_name if hasattr(task, "task_name") else "",
        "summary": {
            "total_requirements": total,
            "tier1_direct_injection": tier1,
            "covered": covered,
            "uncovered": total - covered,
        },
        "hallucination_check": "PASS" if hallucinated == 0 else "FAIL",
        "violation_check": "PASS" if violated == 0 else "FAIL",
        "chapters": chapter_status,
        "suggestions": [],
    }

    # 生成建议
    if hallucinated > 0:
        report["suggestions"].append(f"检测到 {hallucinated} 章存在编造嫌疑，建议人工复核")
    if violated > 0:
        report["suggestions"].append(f"检测到 {violated} 章可能违反废标项，必须人工复核")
    if covered < total:
        uncovered_pct = (total - covered) / total * 100
        if uncovered_pct > 20:
            report["suggestions"].append(f"未覆盖要求比例较高 ({uncovered_pct:.0f}%)，建议补充材料后重新生成")

    return report
