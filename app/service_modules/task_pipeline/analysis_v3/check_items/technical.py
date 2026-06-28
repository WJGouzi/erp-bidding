"""技术要求模块：从 technical_requirements 字段组装。"""
import logging

logger = logging.getLogger(__name__)


def _parse_requirements_list(text: str) -> list:
    """与 business 模块相同的解析逻辑。"""
    if not text or not text.strip():
        return []

    import re
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        sentences = [s.strip() + "。" for s in re.split(r"[。；\n]", text) if s.strip()]
        if len(sentences) >= 2:
            return [{"content": s, "source_section": ""} for s in sentences]
        return [{"content": text.strip(), "source_section": ""}]

    items = []
    for line in lines:
        clean = re.sub(r"^[\d①②③④⑤⑥⑦⑧⑨⑩]+[.、．\s)]*", "", line).strip()
        if clean:
            items.append({"content": clean, "source_section": ""})

    return items if items else [{"content": text.strip(), "source_section": ""}]


# 已知占位文本列表（分析管线未提取到技术要求时写入的默认值）
_PLACEHOLDER_PATTERNS = [
    "暂未提取到技术要求",
    "暂未提取到",
    "未提取到技术要求",
    "暂无技术要求",
    "无技术要求",
]


def _is_placeholder(text: str) -> bool:
    """判断是否为占位文本。"""
    for pattern in _PLACEHOLDER_PATTERNS:
        if pattern in text:
            return True
    return False


def assemble_technical(result, analysis: dict) -> dict:
    """组装技术要求。"""
    tech_text = result.technical_requirements
    if not tech_text or not tech_text.strip():
        return {"items": [], "raw": ""}

    # 过滤占位文本
    if _is_placeholder(tech_text):
        return {"items": [], "raw": ""}

    items = _parse_requirements_list(tech_text)
    return {
        "items": items,
        "raw": tech_text if not items else "",
    }
