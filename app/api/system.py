import logging; logger = logging.getLogger(__name__)
from flask_restx import Namespace, Resource, reqparse

from ..core.enums import BID_TYPES, CURRENT_STEPS, MATERIAL_TYPES, MODEL_TYPES, TASK_ORIGINS, TASK_STATUSES
from ..core.response import success, page_success
from ..core.response import success
from ..service_modules import BiddingTaskService
from ..domain import OperationLog

health_ns = Namespace("health", description="系统健康检查")
system_ns = Namespace("system", description="系统基础接口")


@health_ns.route("")
class HealthResource(Resource):
    """提供基础健康检查接口。"""

    def get(self):
        """返回服务健康状态，供部署或监控探活使用。"""

        return success({"status": "healthy"})


@system_ns.route("/enums")
class EnumResource(Resource):
    """提供前端需要的固定枚举值集合。"""

    def get(self):
        """返回标书流程中使用的枚举常量。"""

        return success(
            {
                "bid_types": BID_TYPES,
                "task_origins": TASK_ORIGINS,
                "task_statuses": TASK_STATUSES,
                "current_steps": CURRENT_STEPS,
                "material_types": MATERIAL_TYPES,
                "model_types": MODEL_TYPES,
            }
        )


@system_ns.route("/task-runtime")
class TaskRuntimeResource(Resource):
    """提供后台任务线程池的运行快照。"""

    def get(self):
        """返回后台队列并发数和当前活跃任务数。"""

        return success(BiddingTaskService.get_task_runtime_snapshot())


@system_ns.route("/stats")
class TaskStatsResource(Resource):
    """提供标书任务统计概览。"""

    def get(self):
        """返回按状态、标书类型统计的任务数量和今日动态。"""

        return success(BiddingTaskService.get_task_stats())


operation_log_parser = reqparse.RequestParser()
operation_log_parser.add_argument("page_no", type=int, default=1, location="args")
operation_log_parser.add_argument("page_size", type=int, default=20, location="args")
operation_log_parser.add_argument("module", type=str, location="args")
operation_log_parser.add_argument("action", type=str, location="args")
operation_log_parser.add_argument("target_type", type=str, location="args")
operation_log_parser.add_argument("target_id", type=int, location="args")
operation_log_parser.add_argument("task_id", type=int, location="args")


@system_ns.route("/operation-logs")
class OperationLogResource(Resource):
    """提供业务操作日志的分页查询。"""

    @system_ns.expect(operation_log_parser)
    def get(self):
        """按条件分页查询操作日志。"""

        args = operation_log_parser.parse_args()
        query = OperationLog.query
        if args.get("module"):
            query = query.filter_by(module=args["module"])
        if args.get("action"):
            query = query.filter_by(action=args["action"])
        if args.get("target_type"):
            query = query.filter_by(target_type=args["target_type"])
        if args.get("target_id") is not None:
            query = query.filter_by(target_id=args["target_id"])
        if args.get("task_id") is not None:
            query = query.filter_by(task_id=args["task_id"])
        pagination = query.order_by(OperationLog.id.desc()).paginate(
            page=args["page_no"], per_page=args["page_size"], error_out=False
        )
        return page_success(
            [item.to_dict() for item in pagination.items],
            pagination.total,
            args["page_no"],
            args["page_size"],
        )
