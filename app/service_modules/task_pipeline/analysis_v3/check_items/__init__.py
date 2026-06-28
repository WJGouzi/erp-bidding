"""check-items 门面模块 — 内部模块化组装，对外统一出口。

设计模式：Facade
  GET /tasks/:id/check-items → assemble_check_items() → 复合 JSON

每个子模块从 bidding_analysis_result 表单行读取数据，独立组装。
"""
import json
import logging

from app.domain.models import BiddingAnalysisResult, BiddingCheckItem

logger = logging.getLogger(__name__)


def get_task_id(shared_resource_id: int) -> int:
    """从 shared_resource_id 反查 task_id。"""
    try:
        from app.domain.models import BiddingSharedResource, BiddingTask
        sr = BiddingSharedResource.query.get(shared_resource_id)
        if sr and sr.root_task_id:
            return sr.root_task_id
        # 降级：通过 shared_resource_id 找 task
        task = BiddingTask.query.filter_by(shared_resource_id=shared_resource_id).first()
        if task:
            return task.id
    except Exception:
        pass
    return 0


def assemble_check_items(shared_resource_id: int) -> dict:
    """统一入口：从 bidding_analysis_result 表组装完整 check-items 响应。"""
    result = BiddingAnalysisResult.query.filter_by(
        shared_resource_id=shared_resource_id
    ).first()
    if not result:
        logger.warning("[check_items] analysis_result 不存在 shared_resource_id=%s", shared_resource_id)
        return {}

    analysis = result.safe_analysis_data()

    # 导入各子模块（延迟导入避免循环）
    from .bidding_info import assemble_bidding_info
    from .business import assemble_business
    from .technical import assemble_technical
    from .qualification import assemble_qualification
    from .scoring import assemble_scoring
    from .packages import assemble_packages
    from .checklist import assemble_checklist

    return {
        "task_id": get_task_id(shared_resource_id),
        "bidding_info": assemble_bidding_info(result, analysis),
        "business": assemble_business(result, analysis),
        "technical": assemble_technical(result, analysis),
        "qualification": assemble_qualification(result, analysis),
        "scoring": assemble_scoring(result, analysis),
        "packages": assemble_packages(result, analysis),
    }

# ── 向后兼容：保留旧 generate_check_items 接口 ──
# 原 check_items.py 中的函数签名: generate_check_items(eligibility, scoring, packages)
# 包装为兼容调用（将 eligibility/scoring/packages 放入 analysis dict）
def generate_check_items(eligibility, scoring, packages):
    """兼容旧接口：从 eligibility/scoring/packages 生成核对项。"""
    from .checklist import assemble_checklist
    analysis = {"eligibility": eligibility, "scoring": scoring}
    # 构造一个临时的 result 对象
    class _FakeResult:
        def __init__(self):
            self.shared_resource_id = 0
            self.qualification_requirements = ""
            self.disqualification_items = ""
            self.technical_requirements = ""
            self.business_requirements = ""
            self.overview = ""
    return assemble_checklist(_FakeResult(), analysis)
