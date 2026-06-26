import logging; import logging
logger = logging.getLogger(__name__)
from flask import request
from flask_restx import Namespace, Resource, fields, reqparse
from werkzeug.datastructures import FileStorage as WerkzeugFileStorage

from ..core.response import success
from ..service_modules import BiddingTaskService

kb_ns = Namespace("bidding/knowledge-bases", description="知识库管理")

kb_list_parser = reqparse.RequestParser()
kb_list_parser.add_argument("page_no", type=int, default=1, location="args")
kb_list_parser.add_argument("page_size", type=int, default=10, location="args")
kb_list_parser.add_argument("keyword", type=str, location="args")
kb_list_parser.add_argument("subject_id", type=int, location="args")

kb_file_upload_parser = reqparse.RequestParser()
kb_file_upload_parser.add_argument("file", location="files", type=WerkzeugFileStorage, required=False)
kb_file_upload_parser.add_argument("files", location="files", type=WerkzeugFileStorage, action="append", required=False)

kb_save_model = kb_ns.model(
    "KnowledgeBaseSaveDto",
    {
        "subject_id": fields.Integer(required=False),
        "name": fields.String(required=True),
        "description": fields.String(required=False),
        "chroma_tenant": fields.String(required=False),
        "chroma_database": fields.String(required=False),
        "chroma_collection": fields.String(required=False),
    },
)

kb_file_reference_status_model = kb_ns.model(
    "KnowledgeBaseFileReferenceStatusDto",
    {
        "reference_enabled": fields.Boolean(required=True),
    },
)


@kb_ns.route("")
class KnowledgeBaseListResource(Resource):
    """处理知识库列表查询与新增。"""

    @kb_ns.expect(kb_list_parser)
    def get(self):
        """分页查询知识库，可按主体和关键字过滤。"""

        args = kb_list_parser.parse_args()
        return BiddingTaskService.list_knowledge_bases(
            page_no=args["page_no"],
            page_size=args["page_size"],
            subject_id=args.get("subject_id"),
            keyword=args.get("keyword"),
        )

    @kb_ns.expect(kb_save_model)
    def post(self):
        """创建新的知识库配置。"""

        return success(BiddingTaskService.save_knowledge_base(**(kb_ns.payload or {})), message="知识库已保存")


@kb_ns.route("/<int:knowledge_base_id>")
class KnowledgeBaseDetailResource(Resource):
    """处理单个知识库的详情、更新与删除。"""

    def get(self, knowledge_base_id):
        """获取知识库详情和其下文件列表。"""

        return success(BiddingTaskService.get_knowledge_base_detail(knowledge_base_id))

    @kb_ns.expect(kb_save_model)
    def put(self, knowledge_base_id):
        """更新指定知识库的配置。"""

        return success(
            BiddingTaskService.save_knowledge_base(knowledge_base_id=knowledge_base_id, **(kb_ns.payload or {})),
            message="知识库已更新",
        )

    def delete(self, knowledge_base_id):
        """删除指定知识库及其关联文件。"""

        return success(BiddingTaskService.delete_knowledge_base(knowledge_base_id), message="知识库已删除")


@kb_ns.route("/<int:knowledge_base_id>/files")
class KnowledgeBaseFileListResource(Resource):
    """处理知识库文件列表查询与上传。"""

    def get(self, knowledge_base_id):
        """获取指定知识库下的文件列表。"""

        keyword = request.args.get("keyword")
        return success(BiddingTaskService.list_knowledge_base_files(knowledge_base_id, keyword=keyword))

    @kb_ns.expect(kb_file_upload_parser)
    def post(self, knowledge_base_id):
        """向知识库上传单个或多个文件并写入 Chroma 向量集合。"""

        kb_file_upload_parser.parse_args()
        file_list = []
        file_list.extend([item for item in request.files.getlist("files") if item])
        file_list.extend([item for item in request.files.getlist("file") if item])
        if len(file_list) <= 1:
            return success(
                BiddingTaskService.upload_knowledge_base_file(knowledge_base_id, file_list[0] if file_list else None),
                message="知识库文件上传成功",
            )
        return success(
            BiddingTaskService.upload_knowledge_base_files(knowledge_base_id, file_list),
            message="知识库文件批量上传成功",
        )


@kb_ns.route("/<int:knowledge_base_id>/files/<int:knowledge_base_file_id>")
class KnowledgeBaseFileDeleteResource(Resource):
    """处理单个知识库文件的删除。"""

    @kb_ns.expect(kb_file_reference_status_model)
    def put(self, knowledge_base_id, knowledge_base_file_id):
        """更新知识库文件是否允许在生成时被引用。"""

        return success(
            BiddingTaskService.update_knowledge_base_file_reference_status(
                knowledge_base_id,
                knowledge_base_file_id,
                (kb_ns.payload or {}).get("reference_enabled"),
            ),
            message="知识库文件引用状态已更新",
        )

    def delete(self, knowledge_base_id, knowledge_base_file_id):
        """删除知识库中的单个文件及对应向量数据。"""

        return success(
            BiddingTaskService.delete_knowledge_base_file(knowledge_base_id, knowledge_base_file_id),
            message="知识库文件已删除",
        )
