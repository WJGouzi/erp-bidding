"""Phase 2 (v2): 动态章节提取 + 法规固定清单 — 零 bid_type/doc_type 依赖。

核心变更：
  1. 移除 ELIGIBILITY_TEMPLATES 预设矩阵
  2. 法规固定检查项从 YAML 配置文件加载（config/presets/statutory_checklist.yaml）
  3. 资格章节定位改用评分机制替代关键词列表
  4. 内容归类用信号词配置（config/presets/signal_words.yaml）
  5. 不接收 bid_type 和 doc_type 参数

流程：
  1. 加载法规固定清单 → 在文档中验证每项是否提及
  2. 评分机制定位资格章节
  3. 从定位章节中动态提取资格要求
  4. 按信号词归类
  5. 合并 statutory + dynamic，去重输出
"""

import logging
import os
import yaml
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 缓存 ──
_statutory_cache = None
_signal_words_cache = None

# ── 配置路径 ──
_CONFIG_DIR = Path(__file__).parent.parent.parent.parent.parent / "config" / "presets"


# ════════════════════════════════════════════
#  配置加载
# ════════════════════════════════════════════

def _load_statutory_checklist():
    """加载法规固定检查清单。

    Returns:
        list[dict]: 每个 dict 包含 id/category/requirement/law_ref/severity
    """
    global _statutory_cache
    if _statutory_cache is not None:
        return _statutory_cache
    path = _CONFIG_DIR / "statutory_checklist.yaml"
    if not path.exists():
        logger.warning("[phase2_extractor] 法规清单文件不存在: %s", path)
        _statutory_cache = []
        return _statutory_cache
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _statutory_cache = data.get("statutory_items", [])
    logger.info("[phase2_extractor] 已加载 %d 项法规固定检查项", len(_statutory_cache))
    return _statutory_cache


def _load_signal_words():
    """加载内容归类信号词。

    Returns:
        dict: {category: [keyword, ...]}
    """
    global _signal_words_cache
    if _signal_words_cache is not None:
        return _signal_words_cache
    path = _CONFIG_DIR / "signal_words.yaml"
    if not path.exists():
        logger.warning("[phase2_extractor] 信号词配置不存在: %s", path)
        _signal_words_cache = {}
        return _signal_words_cache
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _signal_words_cache = data.get("classification_signals", {})
    logger.info("[phase2_extractor] 已加载 %d 类信号词", len(_signal_words_cache))
    return _signal_words_cache


# ════════════════════════════════════════════
#  章节文本工具
# ════════════════════════════════════════════

def _section_to_text(section):
    """递归 section 为纯文本。"""
    texts = []
    title = getattr(section, "title", "") or ""
    if title:
        texts.append(title)
    for block in getattr(section, "content", []):
        if getattr(block, "text", ""):
            texts.append(block.text)
        elif block.type == "table":
            parts = []
            if block.headers:
                parts.append(" | ".join(block.headers))
            for row in block.rows:
                parts.append(" | ".join(row))
            if parts:
                texts.append("\n".join(parts))
    for child in getattr(section, "children", []):
        child_text = _section_to_text(child)
        if child_text:
            texts.append(child_text)
    return "\n".join(texts)


def _get_full_text(sections):
    """将所有 sections 合并为全文。"""
    return "\n".join(_section_to_text(s) for s in sections)


# ════════════════════════════════════════════
#  第1步：法规固定项验证
# ════════════════════════════════════════════

def _extract_keyword_hints(requirement_text):
    """从法规要求文本中提取搜索关键词。"""
    # 按常见分隔符切分
    keywords = []
    # 提取括号中的内容作为备选
    import re
    # "营业执照/法人证书/执业许可证" → 分别检出
    for segment in re.split(r"[（(（]", requirement_text):
        for sub in re.split(r"[）)/、,，]", segment):
            sub = sub.strip()
            if len(sub) >= 2:
                keywords.append(sub)
    return keywords


def _verify_statutory_items(sections, statutory_items):
    """验证法规固定项在文档中是否有对应条款。

    策略：对每项 statutory_item，提取关键词在文档中搜索。
    只要有一个关键词命中，就认为该条款已被文档提及。
    命中时摘录原文上下文作为 material 字段。
    """
    full_text = _get_full_text(sections)

    results = []
    for item in statutory_items:
        # 提取关键词
        keywords = _extract_keyword_hints(item["requirement"])
        # 再加上 requirement 自身作为兜底
        keywords.insert(0, item["requirement"][:20])

        # 找出匹配的关键词和位置
        found = False
        material = ""
        matched_pos = -1
        for kw in keywords:
            if not kw:
                continue
            pos = full_text.find(kw)
            if pos >= 0:
                found = True
                matched_pos = pos
                break

        if found and matched_pos >= 0:
            # 从匹配位置向前找到行首
            start = matched_pos
            while start > 0 and full_text[start - 1] != '\n':
                start -= 1

            # 判断当前行是否是独立编号项（如"1." "（1）"等）
            current_line = full_text[start:matched_pos].strip()
            current_is_numbered = False
            if current_line:
                if current_line[0] in "（((":
                    current_is_numbered = True
                elif len(current_line) > 1 and current_line[0].isdigit() and current_line[1] in ".．、)）":
                    current_is_numbered = True

            # 仅当前行不是独立编号项时，才包含上一行（处理续行场景）
            if not current_is_numbered and start > 1:
                prev_line_start = full_text.rfind('\n', 0, start - 1) + 1
                prev_line = full_text[prev_line_start:start - 1].strip()
                if prev_line:
                    fc = prev_line[0]
                    is_numbered = len(prev_line) > 1 and prev_line[0].isdigit() and prev_line[1] in ".．、)）"
                    if fc in "（((" or is_numbered:
                        start = prev_line_start

            # 从匹配位置向后找到句尾或下一个编号项
            end = matched_pos + len(kw)
            max_end = min(end + 500, len(full_text))
            while end < max_end:
                if end + 1 < len(full_text) and full_text[end] == '\n':
                    # 检查下一个非空行是否是编号项
                    next_line_start = end + 1
                    while next_line_start < len(full_text) and full_text[next_line_start] in ('\n', '\r'):
                        next_line_start += 1
                    if next_line_start < len(full_text):
                        next_line = full_text[next_line_start:next_line_start + 20]
                        fc2 = next_line[0]
                        is_numbered2 = len(next_line) > 1 and next_line[0].isdigit() and next_line[1] in ".．、)）"
                        if fc2 in "（((" or is_numbered2 or next_line.startswith('★') or next_line.startswith('注'):
                            break
                    # 连续两个换行 → 段落结束
                    if end + 2 <= len(full_text) and full_text[end:end+2] == '\n\n':
                        break
                end += 1
            material = full_text[start:end].strip()

        results.append({
            "id": item["id"],
            "category": item["category"],
            "requirement": item["requirement"],
            "law_ref": item.get("law_ref", ""),
            "found": found,
            "material": material,
            "status": "passed" if found else "attention",
            "severity": item.get("severity", "normal"),
        })

    return results


# ════════════════════════════════════════════
#  第2步：章节定位 v2（评分机制）
# ════════════════════════════════════════════

def _score_section(node):
    """计算一个章节的"资格相关性得分"。

    得分 = 标题信号加分 + 内容密度加分 - 干扰扣分
    """
    score = 0
    title = getattr(node, "title", "") or ""

    # ── 标题信号加分（高/中/低三级） ──
    title_signals = [
        # 高置信度（≥8分）
        ("资格要求", 10), ("供应商资格", 10), ("投标人资格", 10),
        ("资格审查", 10), ("资格性审查", 10), ("申请人资格", 10),
        # 中置信度（5-7分）
        ("投标人须知", 7), ("供应商须知", 7), ("比选须知", 7),
        ("磋商须知", 7), ("谈判须知", 7),
        ("资质要求", 6), ("供应商条件", 6),
        # 章节号信号
        ("第四章", 5), ("第五章", 5),
        # 低置信度（2-3分）
        ("资格证明", 3), ("资质", 2), ("资格", 2),
    ]
    for signal, points in title_signals:
        if signal in title:
            score += points

    # ── 标题扣分（干扰章节） ──
    noise_signals = [
        "目录", "TOC", "前附表", "合同模板", "合同草案",
        "响应文件格式", "评标办法", "评分", "评分标准",
        "磋商程序", "谈判程序", "评审程序",
    ]
    for ns in noise_signals:
        if ns in title:
            score -= 5

    # ── 内容密度加分 ──
    content_text = _section_to_text(node)
    density_signals = [
        "营业执照", "资格", "资质", "许可证", "注册证",
        "财务报告", "纳税", "社保", "信用中国",
        "供应商", "投标人", "申请人",
    ]
    density_hits = sum(1 for s in density_signals if s in content_text)
    score += min(density_hits, 10)  # 最多加10分

    # 内容长度加分（有实际内容）
    if len(content_text) > 100:
        score += 2
    if len(content_text) > 500:
        score += 3

    return score


def _find_qualification_sections_v2(sections):
    """评分机制定位资格章节（替代旧版关键词列表）。

    对每个章节计算"资格相关性得分"，
    取最高分章节的 80% 阈值作为资格章节集。

    Args:
        sections: list[Section]

    Returns:
        list[Section]: 资格相关章节列表
    """
    # 计算所有章节得分
    scored = []
    for section in sections:
        s = _score_section(section)
        if s > 0:
            scored.append((s, section))

    # 对子章节也评分
    for section in sections:
        for child in getattr(section, "children", []):
            s = _score_section(child)
            if s > 0:
                scored.append((s, child))

    if not scored:
        logger.info("[phase2_extractor] 未找到资格相关章节（所有章节得分均为0）")
        return []

    # 按得分排序
    scored.sort(key=lambda x: -x[0])
    top_score = scored[0][0]

    # 取最高分 80% 阈值
    threshold = max(top_score * 0.7, 5)  # 最低 5 分（70%阈值确保不遗漏相关章节）
    result = [s for s_score, s in scored if s_score >= threshold]

    logger.info(
        "[phase2_extractor] 章节定位完成: top_score=%d, threshold=%.1f, found=%d",
        top_score, threshold, len(result),
    )

    return result


# ════════════════════════════════════════════
#  第3步：信号词归类
# ════════════════════════════════════════════

def _classify_by_signal(text, signals):
    """根据信号词分类文本属于哪个类别。

    优先级（高到低）：
      1. 实质性条款（★符号优先检测）
      2. 废标信号（影响最严重）
      3. 其他信号词

    Args:
        text: 文本行
        signals: {category: [keyword, ...]}

    Returns:
        str or None: 匹配的类别名
    """
    # 高优先级：★
    if "★" in text and signals.get("实质性条款"):
        return "实质性条款"
    
    # 废标信号
    if signals.get("废标信号"):
        disq_kw = signals["废标信号"]
        if any(kw in text for kw in disq_kw):
            return "废标信号"

    # 普通优先级
    priority_order = ["保证金", "联合体", "特定资格", "业绩要求"]
    for cat in priority_order:
        if cat in signals:
            if any(kw in text for kw in signals[cat]):
                return cat

    # 其他信号
    for category, keywords in signals.items():
        if category in ("实质性条款", "废标信号"):
            continue  # 已检测过
        if any(kw in text for kw in keywords):
            return category

    return None


def _detect_severity(text, category):
    """检测严重级别。"""
    if category == "废标信号":
        return "fatal"
    if category == "实质性条款":
        return "critical"
    fatal_kw = ["废标", "无效投标", "拒绝", "取消资格", "不予受理"]
    if any(kw in text for kw in fatal_kw):
        return "fatal"
    return "normal"


def _is_disqualification(text):
    """判断是否是废标条件（使用信号词配置，与 _classify_by_signal 保持一致）。"""
    signals = _load_signal_words()
    disq_words = signals.get("废标信号", [])
    # 也保留硬编码常见词作为兜底
    hardcoded = ["废标", "无效投标", "拒收", "不予受理", "否决", "作废", "投标无效"]
    all_keywords = list(set(disq_words + hardcoded))
    return any(kw in text for kw in all_keywords)


def _is_starred(text):
    """判断是否是 ★ 实质性条款。"""
    # 硬编码的高频模式
    if "★" in text or "（实质性要求）" in text or "(实质性要求)" in text:
        return True
    # 从信号词配置补充检测
    signals = _load_signal_words()
    star_words = signals.get("实质性条款", [])
    if any(kw in text for kw in star_words if kw not in ("★",)):
        return True
    return False


# ════════════════════════════════════════════
#  第4步：从定位章节中提取要求
# ════════════════════════════════════════════

def _extract_requirements_from_sections(qual_sections, signals):
    """从资格章节中提取具体的资格要求。

    不使用预设模板，直接从文本中检测。
    检测到的内容按信号词自动归类。

    Args:
        qual_sections: list[Section]（资格相关章节）
        signals: 信号词配置

    Returns:
        dict: {"qualifications": [...], "disqualifications": [...], "starred": [...]}
    """
    all_text = "\n".join(_section_to_text(s) for s in qual_sections)

    results = {"qualifications": [], "disqualifications": [], "starred": []}

    seen_texts = set()

    for line in all_text.split("\n"):
        line = line.strip()
        if not line or len(line) < 5:
            continue
        if line in seen_texts:
            continue
        seen_texts.add(line)

        # 检测分类
        category = _classify_by_signal(line, signals)
        if not category:
            continue

        entry = {
            "requirement": line[:300],
            "material": line,
            "category": category,
            "severity": _detect_severity(line, category),
        }

        # 路由：先按信号词分类（高优先级），再按关键词检测（兜底）
        if category in ("废标信号",) or _is_disqualification(line):
            entry["status"] = "attention"
            results["disqualifications"].append(entry)
        elif category in ("实质性条款",) or _is_starred(line):
            entry["status"] = "attention"
            results["starred"].append(entry)
        else:
            entry["status"] = "passed"
            results["qualifications"].append(entry)

    return results


# ════════════════════════════════════════════
#  第5步：全文 fallback 扫描
# ════════════════════════════════════════════

def _fallback_scan(sections, signals):
    """无资格章节时的 fallback：在全文中搜索废标和★条款。"""
    full_text = _get_full_text(sections)
    results = []

    for line in full_text.split("\n"):
        line = line.strip()
        if not line or len(line) < 5:
            continue
        if _is_disqualification(line) or _is_starred(line):
            category = _classify_by_signal(line, signals) or "其他"
            results.append({
                "requirement": line[:300],
                "category": category,
                "severity": _detect_severity(line, category),
                "status": "attention",
            })

    return results


# ════════════════════════════════════════════
#  第6步：去重
# ════════════════════════════════════════════

def _deduplicate_by_text(items):
    """按 requirement 文本去重。"""
    seen = set()
    result = []
    for item in items:
        key = item.get("requirement", "")[:100]
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


# ════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════

def scan_eligibility_v2(sections):
    """执行专家级资格检查 — 零 bid_type/doc_type 依赖。

    Args:
        sections: StructuredDocument.sections

    Returns:
        dict: 与旧版 scan_eligibility 输出格式兼容
    """
    # 1. 加载配置
    statutory_items = _load_statutory_checklist()
    signals = _load_signal_words()

    # 2. 验证法规固定项
    logger.info("[phase2_extractor] 第1步: 验证 %d 项法规固定要求", len(statutory_items))
    statutory_results = _verify_statutory_items(sections, statutory_items)

    # 3. 章节定位
    logger.info("[phase2_extractor] 第2步: 评分机制定位资格章节")
    qual_sections = _find_qualification_sections_v2(sections)

    if qual_sections:
        # 4. 从章节动态提取
        logger.info(
            "[phase2_extractor] 第3步: 从 %d 个资格章节提取要求",
            len(qual_sections),
        )
        dynamic = _extract_requirements_from_sections(qual_sections, signals)
    else:
        # 5. fallback
        logger.info("[phase2_extractor] 第3步: 无资格章节，使用全文 fallback")
        # fallback 扫描：分离废标和★条款
        fallback_items = _fallback_scan(sections, signals)
        fallback_disq = []
        fallback_starred = []
        for item in fallback_items:
            if _is_disqualification(item.get("requirement", "")):
                fallback_disq.append(item)
            elif _is_starred(item.get("requirement", "")):
                fallback_starred.append(item)
            else:
                fallback_disq.append(item)  # 无法判断的归入废标（保守）
        dynamic = {
            "qualifications": [],
            "disqualifications": fallback_disq,
            "starred": fallback_starred,
        }

    # 6. 合并 statutory + dynamic
    logger.info("[phase2_extractor] 第4步: 合并去重")
    all_quals = statutory_results + dynamic["qualifications"]
    all_quals = _deduplicate_by_text(all_quals)

    # 统计
    passed = len([q for q in all_quals if q["status"] == "passed"])
    attention = len([q for q in all_quals if q["status"] == "attention"])

    return {
        "summary": {
            "total_items": len(all_quals)
                + len(dynamic["disqualifications"])
                + len(dynamic["starred"]),
            "passed": passed,
            "attention_required": attention
                + len(dynamic["disqualifications"])
                + len(dynamic["starred"]),
            "failed": 0,
            "statutory_items": len(statutory_results),
            "dynamic_items": len(dynamic["qualifications"]),
        },
        "qualifications": all_quals,
        "disqualifications": dynamic["disqualifications"],
        "starred_requirements": dynamic["starred"],
    }
