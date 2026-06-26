import logging; import logging
logger = logging.getLogger(__name__)
from flask_restx import Namespace, Resource, fields, reqparse

from ..core.response import success
from ..service_modules import BiddingTaskService

template_ns = Namespace("bidding/template-catalogs", description="模板目录库")

template_list_parser = reqparse.RequestParser()
template_list_parser.add_argument("page_no", type=int, default=1, location="args")
template_list_parser.add_argument("page_size", type=int, default=10, location="args")
template_list_parser.add_argument("keyword", type=str, location="args")
template_list_parser.add_argument("bid_type", type=str, location="args", help="标书类型过滤，可选值: GOODS(货物类), SERVICE(服务类), ENGINEERING(工程类)")

template_save_model = template_ns.model(
    "TemplateCatalogSaveDto",
    {
        "template_name": fields.String(required=True, description="模板目录名称"),
        "bid_type": fields.String(required=True, description="标书类型，可选值: GOODS(货物类), SERVICE(服务类), ENGINEERING(工程类)"),
        "template_desc": fields.String(required=False, description="模板描述"),
        "catalog_content": fields.Raw(
            required=True,
            description='目录结构内容。必须包含 outline 数组，每个元素必须有 title 字段。格式: {"outline": [{"title": "章节标题", "description": "章节说明(可选)"}]}'
        ),
    },
)


@template_ns.route("")
class TemplateCatalogListResource(Resource):
    """处理模板目录列表查询与新增。"""

    @template_ns.expect(template_list_parser)
    def get(self):
        """分页查询模板目录，可按标书类型和关键字筛选。"""

        args = template_list_parser.parse_args()
        return BiddingTaskService.list_template_catalogs(
            page_no=args["page_no"],
            page_size=args["page_size"],
            bid_type=args.get("bid_type"),
            keyword=args.get("keyword"),
        )

    @template_ns.expect(template_save_model)
    @template_ns.doc(
        description='bid_type 可选值: GOODS(货物类), SERVICE(服务类), ENGINEERING(工程类)。\n catalog_content 格式: {"outline": [{"title": "章节标题", "description": "章节说明(可选)"}]}'
    )
    def post(self):
        """创建新的模板目录。"""

        return success(BiddingTaskService.save_template_catalog(**(template_ns.payload or {})), message="模板目录已保存")


@template_ns.route("/<int:template_id>")
class TemplateCatalogDetailResource(Resource):
    """处理单个模板目录的查询、更新与删除。"""

    def get(self, template_id):
        """获取指定模板目录详情。"""

        return success(BiddingTaskService.get_template_catalog_detail(template_id))

    @template_ns.expect(template_save_model)
    @template_ns.doc(
        description='更新指定模板目录。bid_type 可选值: GOODS(货物类), SERVICE(服务类), ENGINEERING(工程类)。catalog_content 格式: {"outline": [{"title": "章节标题", "description": "章节说明(可选)"}]}'
    )
    def put(self, template_id):
        """更新指定模板目录。"""

        return success(
            BiddingTaskService.save_template_catalog(template_id=template_id, **(template_ns.payload or {})),
            message="模板目录已更新",
        )

    def delete(self, template_id):
        """删除指定模板目录。"""

        return success(BiddingTaskService.delete_template_catalog(template_id), message="模板目录已删除")
