"""商务要求模块：从 business_requirements 字段组装。"""
import logging

logger = logging.getLogger(__name__)


def _parse_requirements_list(text: str) -> list:
    """尝试解析需求文本为结构化列表。

    支持格式：
    - 带编号行："1. xxx" / "① xxx"
    - 带冒号/分号分隔的条目
    - 纯段落（降级为单条）
    """
    if not text or not text.strip():
        return []

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        # 用中文句号分句
        import re
        sentences = [s.strip() + "。" for s in re.split(r"[。；\n]", text) if s.strip()]
        if len(sentences) >= 2:
            return [{"content": s, "source_section": ""} for s in sentences]
        return [{"content": text.strip(), "source_section": ""}]

    items = []
    for line in lines:
        # 去掉编号前缀
        import re
        clean = re.sub(r"^[\d①②③④⑤⑥⑦⑧⑨⑩]+[.、．\s)]*", "", line).strip()
        if clean:
            items.append({"content": clean, "source_section": ""})

    return items if items else [{"content": text.strip(), "source_section": ""}]


def assemble_business(result, analysis: dict) -> dict:
    """组装商务要求。"""
    biz_text = result.business_requirements
    if not biz_text or not biz_text.strip():
        return {"items": [], "raw": ""}

    items = _parse_requirements_list(biz_text)
    return {
        "items": items,
        "raw": biz_text if not items else "",
    }
