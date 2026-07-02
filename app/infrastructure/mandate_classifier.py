#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
强制条款识别器 — 规则优先，LLM兜底。

判定逻辑（按优先级）：
  1. 精确标题匹配    → HARD
  2. 标题模式匹配    → HARD
  3. 内容特征匹配    → HARD
  4. 位置特征匹配    → HARD (位于"投标文件组成"章节内)
  5. 表格类型匹配    → HARD (资格检查表/响应表)
  6. 规则未命中      → LLM 兜底
  7. LLM 异常       → 默认 FREE（宁松勿严）

用法:
    from app.infrastructure.mandate_classifier import classify_mandate
    result = classify_mandate(section_title, section_text, parent_titles, table_types)
    # {"level": "HARD"|"SOFT"|"FREE", "reason": "...", "source": "rule:exact_title"}
"""

import logging
import re

logger = logging.getLogger(__name__)

# ── 常量定义 ──
MANDATE_HARD = "HARD"
MANDATE_SOFT = "SOFT"
MANDATE_FREE = "FREE"


# ═══════════════════════════════════════════════════════════════
# 规则层
# ═══════════════════════════════════════════════════════════════

# 第一档：精确标题匹配（标题完全一致）
HARD_EXACT_TITLES = frozenset({
    # 投标函类
    "投标函", "投标函及报价承诺",
    # 身份证明类
    "法定代表人身份证明", "法定代表人身份证明书",
    "法定代表人授权书", "授权委托书", "法定代表人授权委托书",
    # 声明承诺类
    "廉洁承诺书", "诚信承诺书", "投标承诺函", "投标承诺书",
    "中小企业声明函", "中小企业声明函（货物）", "中小企业声明函（服务）",
    "残疾人福利性单位声明函", "监狱企业声明函",
    "无重大违法记录声明函", "无重大违法记录书面声明函",
    "资格声明函", "投标人资格声明函", "书面声明函", "承诺函",
    "不参与围标串标承诺书", "保密承诺函",
    # 报价类
    "开标一览表", "报价一览表", "分项报价表", "分项报价明细表",
    "分项报价明细表（货物类）", "分项报价明细表（服务类）",
    "报价明细表", "初次报价表", "最终报价表",
    # 偏离表类
    "技术规格偏离表", "商务条款偏离表",
    "技术参数偏离表", "商务条款响应/偏离表",
    "技术要求响应/偏离表", "商务要求响应/偏离表",
    # 协议类
    "联合体协议书", "分包意向协议", "分包意向协议（格式）",
    # 保证金类
    "投标保证金缴纳凭证", "投标保函", "投标保证金退还申请书",
    # 其他固定格式
    "投标文件封面", "投标文件密封袋封面",
    "投标文件递交登记表", "投标人基本情况表",
    "业绩一览表", "类似项目业绩一览表",
    "拟派项目负责人简历表", "拟投入本项目人员表",
    "主要设备一览表", "财务状况表",
    "售后服务承诺函", "质量保证承诺函",
})

# 第二档：标题模式匹配（正则）
HARD_TITLE_PATTERNS = [
    (re.compile(r'声明.{0,6}书?$'), "标题以声明结尾"),
    (re.compile(r'承诺.{0,6}书?$'), "标题以承诺结尾"),
    (re.compile(r'保证.{0,6}书?$'), "标题以保证结尾"),
    (re.compile(r'函$'), "标题以函结尾"),
    (re.compile(r'[格的]式(表)?$'), "标题以格式/格/式结尾"),
    (re.compile(r'^.{0,4}承诺'), "标题以承诺开头"),
    (re.compile(r'^.{0,4}声明'), "标题以声明开头"),
]

# 第三档：内容特征匹配（匹配正文前 500 字符）
HARD_CONTENT_PATTERNS = [
    (re.compile(r'致[：:]\s*.{0,30}(招标人|采购人|代理机构|采购代理)'), "函件开头:致招标人"),
    (re.compile(r'本(单位|公司|企业).{0,20}(郑重|特此|自愿|声明|承诺)'), "郑重声明句式"),
    (re.compile(r'[（(]盖章[）)]|[（(]签字[）)]|[（(]公章[）)]|[（(]签名[）)]|[（(]签章[）)]'), "含盖章/签字占位"),
    (re.compile(r'特此.{0,10}(声明|承诺|函告|函复|证明)'), "特此句式"),
    (re.compile(r'有效期.{0,10}(天|日|月|年)'), "有效期声明"),
    (re.compile(r'供应商[（(]公章[）)]'), "供应商公章占位"),
    (re.compile(r'法定代表人[（(]签字[）)]'), "法定代表人签字占位"),
    (re.compile(r'被授权人[（(]签字[）)]'), "被授权人签字占位"),
]

# 第四档：位置特征 — 父级标题包含以下关键词时，子节点视为 HARD
HARD_PARENT_KEYWORDS = frozenset({
    "投标文件组成", "投标文件的编制", "应提交的文件",
    "投标文件的格式", "投标文件格式", "投标文件编写",
    "投标文件的组成", "响应文件的组成",
})

# 第五档：表格类型匹配（复用 table_classifier 的类型定义）
HARD_TABLE_TYPES = frozenset({"QUALIFICATION_CHECK", "RESPONSE_FORM"})


def classify_mandate(
    title: str,
    text: str = "",
    parent_title_chain: list = None,
    table_types: list = None,
) -> dict:
    """对单个章节执行强制条款识别。

    Args:
        title: 章节标题
        text: 章节正文内容（用于内容特征匹配）
        parent_title_chain: 父级标题链（从根到父），用于位置判定
        table_types: 本章节包含的表格类型列表

    Returns:
        {"level": "HARD"|"SOFT"|"FREE",
         "reason": "判定理由",
         "source": "rule:exact_title|rule:title_pattern|rule:content|..."}
    """
    title = (title or "").strip()
    text = (text or "").strip()
    parent_title_chain = parent_title_chain or []
    table_types = table_types or []

    # ── 1. 精确标题匹配 ──
    if title in HARD_EXACT_TITLES:
        return {
            "level": MANDATE_HARD,
            "reason": f"精确标题匹配: {title}",
            "source": "rule:exact_title",
        }

    # ── 2. 标题模式匹配 ──
    for pattern, desc in HARD_TITLE_PATTERNS:
        if pattern.search(title):
            return {
                "level": MANDATE_HARD,
                "reason": f"标题模式: {desc}",
                "source": "rule:title_pattern",
            }

    # ── 3. 内容特征匹配（只检查前 500 字符） ──
    text_excerpt = text[:500]
    for pattern, desc in HARD_CONTENT_PATTERNS:
        if pattern.search(text_excerpt):
            return {
                "level": MANDATE_HARD,
                "reason": f"内容特征: {desc}",
                "source": "rule:content_pattern",
            }

    # ── 4. 位置特征匹配 ──
    for parent_title in parent_title_chain:
        if parent_title in HARD_PARENT_KEYWORDS:
            return {
                "level": MANDATE_HARD,
                "reason": f"位于「{parent_title}」章节内",
                "source": "rule:position",
            }

    # ── 5. 表格类型匹配 ──
    for tt in table_types:
        if tt in HARD_TABLE_TYPES:
            return {
                "level": MANDATE_HARD,
                "reason": f"表格类型: {tt}",
                "source": "rule:table_type",
            }

    # ── 6. LLM 兜底 ──
    return _llm_fallback(title, text_excerpt)


def _llm_fallback(title: str, text_excerpt: str) -> dict:
    """规则未命中时，LLM 兜底判定。"""
    try:
        from flask import current_app
        from app.infrastructure.integrations import LLMAdapter

        adapter = LLMAdapter(
            api_key=current_app.config.get("OPENAI_API_KEY"),
            base_url=current_app.config.get("OPENAI_BASE_URL"),
            default_model=current_app.config.get("OPENAI_MODEL_NAME"),
        )
        if not adapter.is_available():
            return {
                "level": MANDATE_FREE,
                "reason": "LLM 不可用，默认 FREE",
                "source": "default:llm_unavailable",
            }

        prompt = f"""判断以下招标文件章节是否属于「强制格式内容」。

定义：
- HARD：必须严格原文照抄，不可改写或重组措辞。通常是声明函、承诺书、固定格式表。
- SOFT：有固定结构但内容可灵活调整。通常是技术参数响应表、商务条款响应表。
- FREE：自由撰写章节，按招标要求自行组织内容。通常是技术方案、服务方案、实施计划。

仅输出 JSON，不要多余内容：
{{"mandate": "HARD/SOFT/FREE", "reason": "不超过20字的理由"}}

章节标题：{title}
章节内容前300字：{text_excerpt[:300]}
"""
        raw = adapter.generate_text(
            system_prompt="你是一个招标文件格式分类专家。",
            user_prompt=prompt,
            temperature=0.0,
            max_tokens=100,
        )
        if not raw:
            return {
                "level": MANDATE_FREE,
                "reason": "LLM 返回为空，默认 FREE",
                "source": "default:llm_empty",
            }

        import json
        cleaned = raw.strip()
        if "```" in cleaned:
            parts = cleaned.split("```")
            if len(parts) >= 2:
                cleaned = parts[1]
        brace_start = cleaned.find("{")
        brace_end = cleaned.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            cleaned = cleaned[brace_start : brace_end + 1]
        result = json.loads(cleaned)
        level = result.get("mandate", MANDATE_FREE)
        if level not in (MANDATE_HARD, MANDATE_SOFT, MANDATE_FREE):
            level = MANDATE_FREE
        return {
            "level": level,
            "reason": result.get("reason", "LLM 判定"),
            "source": "llm",
        }

    except Exception as exc:
        logger.warning("[mandate] LLM 兜底异常: %s", exc)
        return {
            "level": MANDATE_FREE,
            "reason": f"LLM 异常，默认 FREE",
            "source": "default:llm_error",
        }


def batch_classify(sections: list) -> list:
    """批量识别多个章节的强制级别。

    Args:
        sections: 章节列表，每个元素需包含：
            - title: str
            - text: str (可选)
            - parent_title_chain: list[str] (可选)
            - table_types: list[str] (可选)

    Returns:
        每个元素新增 mandate 字段
    """
    results = []
    for sec in sections:
        result = classify_mandate(
            title=sec.get("title", ""),
            text=sec.get("text", ""),
            parent_title_chain=sec.get("parent_title_chain"),
            table_types=sec.get("table_types"),
        )
        sec["mandate"] = result
        results.append(sec)
    return results
