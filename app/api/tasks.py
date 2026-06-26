import logging; import logging
logger = logging.getLogger(__name__)
from flask import Response
from flask_restx import Namespace, Resource, fields, reqparse
from werkzeug.datastructures import FileStorage as WerkzeugFileStorage

from ..core.response import success
from ..service_modules import BiddingTaskService

task_ns = Namespace("bidding/tasks", description="标书任务")

upload_parser = reqparse.RequestParser()
upload_parser.add_argument("bid_type", type=str, required=True, location="form", help="标书类型，可选值: GOODS(货物类), SERVICE(服务类), ENGINEERING(工程类)")
upload_parser.add_argument("task_name", type=str, required=False, location="form")
upload_parser.add_argument("file", location="files", type=WerkzeugFileStorage, required=True, help="招标文件")

attachment_upload_parser = reqparse.RequestParser()
attachment_upload_parser.add_argument("file", location="files", type=WerkzeugFileStorage, required=True, help="招标附件文件")

list_parser = reqparse.RequestParser()
list_parser.add_argument("page_no", type=int, default=1, location="args")
list_parser.add_argument("page_size", type=int, default=10, location="args")
list_parser.add_argument("keyword", type=str, location="args")
list_parser.add_argument("status", type=str, location="args", help="任务状态过滤，可选值: INIT(初始化), UPLOADED(上传标书完成), ANALYZING(分析中), PACKAGE_PENDING(待选择包号), ANALYZED(分析完成), CHECKED(核对完成), CATALOG_CONFIRMED(目录生成完毕), GENERATING(生成标书中), GENERATED(生成完成), CANCELLED(已取消), FAILED(生成失败)")
list_parser.add_argument("bid_type", type=str, location="args", help="标书类型过滤，可选值: GOODS(货物类), SERVICE(服务类), ENGINEERING(工程类)")
list_parser.add_argument("subject_id", type=int, location="args")
list_parser.add_argument("date_from", type=str, location="args")
list_parser.add_argument("date_to", type=str, location="args")
list_parser.add_argument("sort_by", type=str, location="args", help="排序字段，可选值: created_at, updated_at, task_name, status, bid_type")
list_parser.add_argument("sort_order", type=str, location="args", help="排序方向，可选值: asc(升序), desc(降序)")

derive_model = task_ns.model(
    "DeriveTaskRequest",
    {
        "task_name": fields.String(required=False, description="新任务名称"),
        "subject_id": fields.Integer(required=False, description="主体公司 ID"),
        "model_type": fields.String(required=False, description="模型类型，可选值: qwen-long(千问 Long), deepseek-chat(DeepSeek Chat), gpt-4o(GPT-4o), glm-4(GLM-4), claude-3(Claude 3)"),
        "use_knowledge_base": fields.Boolean(required=False, description="是否启用知识库"),
        "use_product_library": fields.Boolean(required=False, description="是否启用产品库"),
        "catalog_generation_level": fields.String(required=False, description="目录生成程度，可选值: LOW(简洁直达), MEDIUM(兼顾常规), HIGH(详细展开)"),
        "word_count_level": fields.String(required=False, description="字数等级，可选值: SHORT(少 约2.5万字), MEDIUM(常规篇幅 约6万字), LONG(详尽篇幅)"),
    },
)

package_select_model = task_ns.model(
    "PackageSelectRequest",
    {"package_no": fields.String(required=True, description="选择的包号")},
)

check_confirm_model = task_ns.model(
    "CheckConfirmRequest",
    {
        "items": fields.List(
            fields.Nested(
                task_ns.model(
                    "CheckConfirmItem",
                    {
                        "check_key": fields.String(required=True, description="核对项标识"),
                        "check_value": fields.String(required=False, description="核对值"),
                        "confirmed_flag": fields.Boolean(required=True, description="是否确认"),
                    },
                )
            ),
            required=True,
            description="核对项列表",
        )
    },
)

catalog_confirm_model = task_ns.model(
    "CatalogConfirmRequest",
    {
        "catalog_content": fields.Raw(required=True, description="目录内容（用户编辑确认后的最终目录）。\n格式: {\"outline\": [{\"title\": \"一、章节标题\", \"children\": [{\"title\": \"（一）子标题\"}]}]}"),
    },
)

generate_config_model = task_ns.model(
    "GenerateConfigRequest",
    {
        "subject_id": fields.Integer(required=True, description="主体公司ID"),
        "model_type": fields.String(required=True, description="模型类型，可选值: qwen-long(千问 Long), deepseek(deepSeek-v4-flash)"),
        "use_knowledge_base": fields.Boolean(required=False, description="是否启用知识库"),
        "knowledge_base_ids": fields.List(fields.Integer, required=False, description="知识库ID列表"),
        "use_product_library": fields.Boolean(required=False, description="是否启用产品库"),
        "catalog_generation_level": fields.String(required=False, description="目录生成程度，可选值: LOW(简洁直达), MEDIUM(兼顾常规), HIGH(详细展开)"),
        "word_count_level": fields.String(required=False, description="字数等级，可选值: SHORT(600-900字), MEDIUM(常规篇幅), LONG(详尽篇幅)"),
    },
)

generate_retry_model = task_ns.model(
    "GenerateRetryRequest",
    {
        "retry_all": fields.Boolean(required=False, description="是否整本重生成，true 时忽略 chapter_nos"),
        "chapter_nos": fields.List(
            fields.Integer,
            required=False,
            description="需要重试的章节编号列表，为空时默认重试未成功章节",
        ),
    },
)

task_batch_delete_model = task_ns.model(
    "TaskBatchDeleteRequest",
    {
        "task_ids": fields.List(
            fields.Integer,
            required=True,
            description="需要批量删除的标书任务ID列表",
        )
    },
)


@task_ns.route("/upload-tender")
class UploadTenderResource(Resource):
    """处理招标文件上传并创建原始标书任务。"""

    @task_ns.expect(upload_parser)
    def post(self):
        """上传招标文件并返回新建任务信息。"""

        args = upload_parser.parse_args()
        data = BiddingTaskService.create_original_task(
            file_storage=args["file"],
            bid_type=args["bid_type"],
            task_name=args.get("task_name"),
        )
        return success(data, message="上传成功")


@task_ns.route("/<int:task_id>/attachments")
class TaskAttachmentListResource(Resource):
    """处理招标文件附件列表查询与上传。"""

    def get(self, task_id):
        """返回任务对应共享招标源下的附件列表。"""

        return success(BiddingTaskService.list_tender_attachments(task_id))

    @task_ns.expect(attachment_upload_parser)
    def post(self, task_id):
        """向指定标书任务上传一份招标附件。"""

        args = attachment_upload_parser.parse_args()
        return success(BiddingTaskService.upload_tender_attachment(task_id, args["file"]), message="招标附件上传成功")


@task_ns.route("/<int:task_id>/attachments/<int:attachment_id>")
class TaskAttachmentDeleteResource(Resource):
    """处理单个招标文件附件删除。"""

    def delete(self, task_id, attachment_id):
        """删除任务所属共享招标源下的一份附件。"""

        return success(BiddingTaskService.delete_tender_attachment(task_id, attachment_id), message="招标附件已删除")


@task_ns.route("")
class TaskListResource(Resource):
    """处理标书任务分页列表查询。"""

    @task_ns.doc(params={
        "page_no": "页码，从 1 开始，默认 1",
        "page_size": "每页条数，默认 10",
        "keyword": "搜索关键词，按任务名称模糊匹配",
        "status": "任务状态过滤。可选值: INIT(初始化), UPLOADED(上传标书完成), ANALYZING(分析中), PACKAGE_PENDING(待选择包号), ANALYZED(分析完成), CHECKED(核对完成), CATALOG_CONFIRMED(目录生成完毕), GENERATING(生成标书中), GENERATED(生成完成), CANCELLED(已取消), FAILED(生成失败)",
        "bid_type": "标书类型过滤。可选值: GOODS(货物类), SERVICE(服务类), ENGINEERING(工程类)",
        "subject_id": "主体公司ID过滤。从 GET /api/bidding/subjects 获取 id",
        "date_from": "创建日期范围（开始），格式: YYYY-MM-DD",
        "date_to": "创建日期范围（结束），格式: YYYY-MM-DD",
        "sort_by": "排序字段。可选值: created_at(创建时间), updated_at(更新时间), task_name(任务名称), status(状态), bid_type(标书类型)",
        "sort_order": "排序方向。可选值: asc(升序), desc(降序)",
    })
    def get(self):
        """按分页条件查询标书任务列表。"""

        args = list_parser.parse_args()
        return BiddingTaskService.list_tasks(
            page_no=args["page_no"],
            page_size=args["page_size"],
            keyword=args.get("keyword"),
            status=args.get("status"),
            bid_type=args.get("bid_type"),
            subject_id=args.get("subject_id"),
            date_from=args.get("date_from"),
            date_to=args.get("date_to"),
            sort_by=args.get("sort_by"),
            sort_order=args.get("sort_order"),
        )


@task_ns.route("/<int:task_id>")
class TaskDetailResource(Resource):
    """提供单个标书任务详情。"""

    def get(self, task_id):
        """返回任务基础信息、配置与结果摘要。"""

        return success(BiddingTaskService.get_task_detail(task_id))

    def delete(self, task_id):
        """删除单个标书任务及其可清理的关联资源。"""

        return success(BiddingTaskService.delete_task(task_id), message="标书任务已删除")


@task_ns.route("/batch-delete")
class TaskBatchDeleteResource(Resource):
    """批量删除多个标书任务。"""

    @task_ns.expect(task_batch_delete_model)
    def post(self):
        """批量删除多个任务，并按引用关系清理共享资源。"""

        payload = task_ns.payload or {}
        return success(BiddingTaskService.batch_delete_tasks(payload.get("task_ids")), message="标书任务已批量删除")


@task_ns.route("/<int:task_id>/current-step")
class TaskCurrentStepResource(Resource):
    """提供任务当前流程步骤信息。"""

    def get(self, task_id):
        """返回任务当前状态、步骤与进度。"""

        return success(BiddingTaskService.get_current_step(task_id))


@task_ns.route("/<int:task_id>/derive")
class TaskDeriveResource(Resource):
    """基于既有任务创建派生标书任务。"""

    @task_ns.expect(derive_model)
    def post(self, task_id):
        """复用上游成果并生成一个新的主体投标任务。"""

        payload = task_ns.payload or {}
        data = BiddingTaskService.create_derived_task(
            source_task_id=task_id,
            task_name=payload.get("task_name"),
            subject_id=payload.get("subject_id"),
            model_type=payload.get("model_type"),
            use_knowledge_base=payload.get("use_knowledge_base", False),
            use_product_library=payload.get("use_product_library", False),
            catalog_generation_level=payload.get("catalog_generation_level"),
            word_count_level=payload.get("word_count_level"),
        )
        return success(data, message="再次生成任务创建成功")


@task_ns.route("/<int:task_id>/analyze")
class TaskAnalyzeResource(Resource):
    """启动招标文件分析流程。"""

    def post(self, task_id):
        """发起后台分析任务。"""

        return success(BiddingTaskService.start_analyze(task_id), message="分析任务已启动")


@task_ns.route("/<int:task_id>/analysis-result")
class TaskAnalysisResultResource(Resource):
    """获取分析阶段产出的结构化结果。"""

    def get(self, task_id):
        """返回概述、要求和原始文本等分析结果。"""

        return success(BiddingTaskService.get_analysis_result(task_id))


@task_ns.route("/<int:task_id>/packages")
class TaskPackageResource(Resource):
    """获取任务识别到的分包信息。"""

    def get(self, task_id):
        """返回是否存在分包以及可选包号。"""

        return success(BiddingTaskService.get_packages(task_id))


@task_ns.route("/<int:task_id>/packages/select")
class TaskPackageSelectResource(Resource):
    """确认任务后续采用的分包范围。"""

    @task_ns.expect(package_select_model)
    def post(self, task_id):
        """保存选中的包号并更新有效分析文本。"""

        payload = task_ns.payload or {}
        return success(BiddingTaskService.select_package(task_id, payload.get("package_no")), message="包号选择成功")


@task_ns.route("/<int:task_id>/check-items")
class TaskCheckItemResource(Resource):
    """获取分析后需要人工确认的核对项。"""

    def get(self, task_id):
        """返回当前任务的核对项列表。"""

        return success(BiddingTaskService.get_check_items(task_id))


@task_ns.route("/<int:task_id>/check-items/confirm")
class TaskCheckConfirmResource(Resource):
    """提交核对项确认结果。"""

    @task_ns.expect(check_confirm_model)
    def post(self, task_id):
        """保存人工确认后的核对项状态。"""

        payload = task_ns.payload or {}
        return success(BiddingTaskService.confirm_check_items(task_id, payload.get("items")), message="核对完成")


@task_ns.route("/<int:task_id>/catalog-options")
class TaskCatalogOptionsResource(Resource):
    """获取可供选择的目录来源方案。"""

    def get(self, task_id):
        """返回模板目录、分析目录等候选目录方案。"""

        return success(BiddingTaskService.get_catalog_options(task_id))


@task_ns.route("/<int:task_id>/catalog-confirm")
@task_ns.doc(
        description='template_id 可选值: GOODS(货物类), SERVICE(服务类), ENGINEERING(工程类)。\n ' \
        'catalog_content 格式: {"outline": [{"title": "章节标题", "description": "章节说明(可选)"}]}'
    )
class TaskCatalogConfirmResource(Resource):
    """确认任务最终使用的投标目录。"""

    @task_ns.expect(catalog_confirm_model)
    def post(self, task_id):
        """保存目录选择结果并进入生成配置阶段。"""

        payload = task_ns.payload or {}
        catalog_content = payload.get("catalog_content")
        if not catalog_content:
            return {"code": 1, "message": "catalog_content 不能为空"}, 400
        data = BiddingTaskService.confirm_catalog(
            task_id,
            catalog_content,
        )
        return success(data, message="目录确认完成")


@task_ns.route("/<int:task_id>/generate-config")
class TaskGenerateConfigResource(Resource):
    """查询或保存生成标书所需配置。"""

    def get(self, task_id):
        """读取当前任务的生成配置。"""

        return success(BiddingTaskService.get_generate_config(task_id))

    @task_ns.expect(generate_config_model)
    @task_ns.doc(
        description='subject_id 主体公司ID过滤。从 GET /api/bidding/subjects 获取 id。\n ' \
        'model_type 模型类型，可选值: qwen-long(千问 Long), deepseek(deepSeek-v4-flash)。 \n' \
        'use_knowledge_base 是否使用知识库. \n' \
        'knowledge_base_ids 知识库ID列表。\n' \
        'use_product_library 是否使用产品库 \n ' \
        'catalog_generation_level 目录生成程度，可选值: LOW(简洁直达), MEDIUM(兼顾常规), HIGH(详细展开) \n' \
        'word_count_level 字数等级，可选值: SHORT(少 约2.5万字), MEDIUM(常规篇幅 约6万字), LONG(详尽篇幅)' 

    )
    def post(self, task_id):
        """保存主体、模型、知识库与产品库等生成参数。"""

        payload = task_ns.payload or {}
        data = BiddingTaskService.save_generate_config(
            task_id=task_id,
            subject_id=payload.get("subject_id"),
            model_type=payload.get("model_type"),
            use_knowledge_base=payload.get("use_knowledge_base", False),
            knowledge_base_ids=payload.get("knowledge_base_ids"),
            use_product_library=payload.get("use_product_library", False),
            catalog_generation_level=payload.get("catalog_generation_level"),
            word_count_level=payload.get("word_count_level"),
        )
        return success(data, message="生成配置已保存")


@task_ns.route("/<int:task_id>/generate/start")
class TaskGenerateStartResource(Resource):
    """启动整本标书生成流程。"""

    def post(self, task_id):
        """发起后台生成任务。"""

        return success(BiddingTaskService.start_generate(task_id), message="生成任务已启动")


@task_ns.route("/<int:task_id>/generate/retry")
class TaskGenerateRetryResource(Resource):
    """对失败章节或整本任务发起重试生成。"""

    @task_ns.expect(generate_retry_model)
    def post(self, task_id):
        """按章节范围提交重试生成请求。"""

        payload = task_ns.payload or {}
        data = BiddingTaskService.retry_generate(
            task_id,
            payload.get("chapter_nos"),
            payload.get("retry_all", False),
        )
        return success(data, message="重试生成任务已启动")


@task_ns.route("/<int:task_id>/generate/progress")
class TaskGenerateProgressResource(Resource):
    """查询标书生成阶段的整体进度。"""

    def get(self, task_id):
        """返回任务百分比进度、阶段和重试建议。"""

        return success(BiddingTaskService.get_generate_progress(task_id))


@task_ns.route("/<int:task_id>/generate/chapters")
class TaskGenerateChaptersResource(Resource):
    """查询章节级生成结果。"""

    def get(self, task_id):
        """返回每个章节的状态、进度和错误信息。"""

        return success(BiddingTaskService.get_generate_chapters(task_id))


@task_ns.route("/<int:task_id>/executions")
class TaskExecutionListResource(Resource):
    """查询任务的后台执行记录列表。"""

    def get(self, task_id):
        """返回分析或生成等后台执行历史。"""

        return success(BiddingTaskService.get_task_executions(task_id))


@task_ns.route("/<int:task_id>/execution/current")
class TaskExecutionCurrentResource(Resource):
    """查询任务当前最新的一条后台执行记录。"""

    def get(self, task_id):
        """返回正在进行或最近一次执行的后台任务记录。"""

        return success(BiddingTaskService.get_current_task_execution(task_id))


@task_ns.route("/<int:task_id>/execution/cancel")
class TaskExecutionCancelResource(Resource):
    """提交后台执行取消请求。"""

    def post(self, task_id):
        """标记当前后台任务为取消中。"""

        return success(BiddingTaskService.cancel_task_execution(task_id), message="后台任务取消请求已提交")


@task_ns.route("/<int:task_id>/download")
class TaskDownloadResource(Resource):
    """下载已生成完成的标书文件。"""

    def get(self, task_id) -> Response:
        """返回 docx 文件下载响应。"""

        return BiddingTaskService.download_result_file(task_id)

catalog_from_file_parser = reqparse.RequestParser()
catalog_from_file_parser.add_argument(
    "file", location="files", type=WerkzeugFileStorage, required=True, help="用于参考目录格式的投标文件（doc/docx/pdf）"
)


@task_ns.route("/<int:task_id>/catalog-from-file")
class TaskCatalogFromFileResource(Resource):
    """上传投标文件并提取其目录结构（对应前端 Tab2：按参考格式生成）。"""

    @task_ns.expect(catalog_from_file_parser)
    def post(self, task_id):
        """上传一份已有的投标文件，提取其中的目录/大纲结构。"""

        args = catalog_from_file_parser.parse_args()
        return success(
            BiddingTaskService.extract_catalog_from_file(task_id, args["file"]),
            message="目录提取成功",
        )


@task_ns.route("/<int:task_id>/subject-templates")
class TaskSubjectTemplatesResource(Resource):
    """获取任务对应标书类型的可用模板列表（对应前端 Tab3：按模板库生成）。"""

    def get(self, task_id):
        """返回模板库中与当前任务标书类型匹配的模板列表。"""

        return success(BiddingTaskService.get_subject_templates(task_id))
