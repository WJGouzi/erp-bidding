"""LLM 提取模块 — 对规则无法处理的部分用大模型补位。

每个提取函数：
1. 输入最小化（只传必要片段）
2. 输出结构化 JSON
3. 内置错误处理和兜底
"""

import json
import logging
import re

from flask import current_app

from ....infrastructure.integrations import LLMAdapter
from .llm_prompts import (
    PROMPT_METADATA,
    PROMPT_BUDGET,
    PROMPT_SCORING,
    PROMPT_BUSINESS,
    PROMPT_TECHNICAL,
)

logger = logging.getLogger(__name__)


def _get_llm():
    """获取 LLMAdapter 实例。优先用 QWEN 配置（通义千问），降级到 OPENAI 配置。"""
    api_key = current_app.config.get("QWEN_API_KEY") or current_app.config.get("OPENAI_API_KEY")
    base_url = current_app.config.get("QWEN_BASE_URL") or current_app.config.get("OPENAI_BASE_URL")
    model = current_app.config.get("QWEN_MODEL_NAME") or current_app.config.get("OPENAI_MODEL_NAME")
    adapter = LLMAdapter(
        api_key=api_key,
        base_url=base_url,
        default_model=model or "qwen-plus",
    )
    return adapter


def call_llm_json(prompt, max_tokens=500, temperature=0.1):
    """调用大模型返回结构化 JSON。

    Args:
        prompt: 完整的 prompt（含 system + user 指令）
        max_tokens: 最大输出 token
        temperature: 温度（越低越确定）

    Returns:
        dict | list | None: 解析后的 JSON 对象，失败返回 None
    """
    adapter = _get_llm()
    if not adapter.is_available():
        logger.warning("[llm_extractor] LLM 服务不可用")
        return None

    try:
        raw = adapter.generate_text(
            system_prompt="你是一个招标文件解析专家。只输出 JSON，不要包含其他内容。",
            user_prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("[llm_extractor] LLM 调用失败: %s", exc)
        return None

    if not raw:
        return None

    # 尝试提取 JSON（可能被 markdown 包裹）
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
    if json_match:
        raw = json_match.group(1).strip()

    try:
        result = json.loads(raw)
        return result
    except json.JSONDecodeError:
        logger.warning("[llm_extractor] LLM 返回非 JSON: %s...", raw[:200])
        return None


# ========== Phase 1: 元数据提取 ==========

def extract_metadata(doc_text, table_kv=None):
    """用 LLM 提取购买人/代理名称。

    Args:
        doc_text: 文档前3000字符（含封面页）
        table_kv: 前附表 KV 对（dict），可选

    Returns:
        dict: {"project_name": ..., "project_code": ...,
               "purchaser_name": ..., "agent_name": ..., 
               "purchaser_contact": ..., "agent_contact": ...}
    """
    excerpt = doc_text[:3000] if doc_text else ""
    if not excerpt:
        return {}

    prompt = PROMPT_METADATA.format(document_excerpt=excerpt)
    result = call_llm_json(prompt, max_tokens=400)

    if not isinstance(result, dict):
        return {}

    # 清理结果
    cleaned = {}
    for key in ["project_name", "project_code", "purchaser_name", "purchaser_contact", "agent_name", "agent_contact"]:
        val = result.get(key)
        if val and str(val).strip().lower() not in ("null", "none", ""):
            cleaned[key] = str(val).strip()
    return cleaned


def extract_budget(table_kv_text):
    """用 LLM 提取预算金额并按分包拆分。

    Args:
        table_kv_text: 前附表 KV 对的文本表示

    Returns:
        dict: {"budget_total": 0, "budget_note": "", "packages": []}
    """
    if not table_kv_text:
        return {"budget_total": 0, "budget_note": "", "packages": []}

    prompt = PROMPT_BUDGET.format(table_kv=table_kv_text[:2000])
    result = call_llm_json(prompt, max_tokens=500)

    if not isinstance(result, dict):
        return {"budget_total": 0, "budget_note": "", "packages": []}

    # 确保 packages 是列表
    packages = result.get("packages", [])
    if not isinstance(packages, list):
        packages = []

    # 清理并规范化
    cleaned_packages = []
    for pkg in packages:
        if isinstance(pkg, dict):
            cleaned_packages.append({
                "package_no": int(pkg.get("package_no", 0)),
                "amount": float(pkg.get("amount", 0)),
                "note": str(pkg.get("note", "")),
            })

    return {
        "budget_total": float(result.get("budget_total", 0)),
        "budget_note": str(result.get("budget_note", "")),
        "packages": cleaned_packages,
    }


# ========== Phase 3: 评分表结构化 ==========

def extract_scoring(scoring_text):
    """用 LLM 结构化评分表。

    Args:
        scoring_text: 评分表的原始文本

    Returns:
        dict: {"method": "", "total_score": 0, "dimensions": []}
    """
    if not scoring_text or len(scoring_text.strip()) < 20:
        return {"method": "", "total_score": 0, "dimensions": []}

    prompt = PROMPT_SCORING.format(scoring_text=scoring_text[:3000])
    result = call_llm_json(prompt, max_tokens=800)

    if not isinstance(result, dict):
        return {"method": "", "total_score": 0, "dimensions": []}

    dimensions = result.get("dimensions", [])
    if not isinstance(dimensions, list):
        dimensions = []

    return {
        "method": str(result.get("method", "")),
        "total_score": int(result.get("total_score", 0)),
        "dimensions": dimensions,
    }


# ========== Phase 3: 商务要求提取 ==========

def extract_business(section_text):
    """用 LLM 提取商务要求。

    Args:
        section_text: 商务要求/商务条款章节文本

    Returns:
        list[dict]: 商务要求列表
    """
    if not section_text or len(section_text.strip()) < 30:
        return []

    prompt = PROMPT_BUSINESS.format(section_text=section_text[:4000])
    result = call_llm_json(prompt, max_tokens=800)

    if not isinstance(result, dict):
        return []

    reqs = result.get("business_requirements", [])
    return reqs if isinstance(reqs, list) else []


# ========== Phase 3: 技术要求提取 ==========

def extract_technical(section_text):
    """用 LLM 提取技术要求。

    Args:
        section_text: 技术要求/技术参数章节文本

    Returns:
        list[dict]: 技术要求列表
    """
    if not section_text or len(section_text.strip()) < 30:
        return []

    prompt = PROMPT_TECHNICAL.format(section_text=section_text[:4000])
    result = call_llm_json(prompt, max_tokens=800)

    if not isinstance(result, dict):
        return []

    reqs = result.get("technical_requirements", [])
    return reqs if isinstance(reqs, list) else []
