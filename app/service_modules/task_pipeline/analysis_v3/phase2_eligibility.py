"""Phase 2 (v1) — 已迁移到 phase2_extractor.py（v2）。

此文件保留为向后兼容的再导出模块。
所有新代码应使用 phase2_extractor.scan_eligibility_v2()。

移除内容（迁移到 config/presets/ + phase2_extractor.py）：
  - ELIGIBILITY_TEMPLATES → statutory_checklist.yaml + signal_words.yaml
  - _get_template() → 不再需要
  - CHAPTER_TEMPLATES → 不再需要
  - _find_qualification_sections() → _find_qualification_sections_v2()（评分机制）
"""

import logging

from .phase2_extractor import scan_eligibility_v2

logger = logging.getLogger(__name__)


def scan_eligibility(sections, bid_type=None, doc_type=None):
    """向后兼容的入口 — 实际委托给 scan_eligibility_v2()。

    Args:
        sections: StructuredDocument.sections
        bid_type: 不再使用（保留参数保持兼容）
        doc_type: 不再使用（保留参数保持兼容）

    Returns:
        dict: 资格检查结果
    """
    if bid_type or doc_type:
        logger.info(
            "[phase2_eligibility] 收到弃用参数 bid_type=%s, doc_type=%s，已忽略",
            bid_type, doc_type,
        )
    return scan_eligibility_v2(sections)
