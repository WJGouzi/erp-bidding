import logging; import logging
logger = logging.getLogger(__name__)
from flask_restx import Namespace, Resource, fields, reqparse
from werkzeug.datastructures import FileStorage as WerkzeugFileStorage

from ..core.response import success
from ..service_modules import BiddingTaskService

subject_ns = Namespace("bidding/subjects", description="主体公司与资料库")

subject_list_parser = reqparse.RequestParser()
subject_list_parser.add_argument("page_no", type=int, default=1, location="args")
subject_list_parser.add_argument("page_size", type=int, default=10, location="args")
subject_list_parser.add_argument("keyword", type=str, location="args")
subject_list_parser.add_argument("status", type=int, location="args")

subject_material_upload_parser = reqparse.RequestParser()
subject_material_upload_parser.add_argument("material_type", type=str, required=True, location="form", help="资料类型，可选值: BUSINESS_LICENSE(营业执照), QUALIFICATION_FILE(资质性文件), LEGAL_PERSON_ID_CARD(法人身份证), AUTHORIZATION_LETTER(授权委托书), AUTHORIZED_PERSON_ID_CARD(被授权人身份证), QUALIFICATION_DECLARATION(资质声明函), LEGAL_PERSON_STATEMENT(法定代表人身份证明), FINANCIAL_STATEMENT(财务报表), INTEGRITY_COMMITMENT(廉洁承诺书)")
subject_material_upload_parser.add_argument("file", location="files", type=WerkzeugFileStorage, required=True)

subject_save_model = subject_ns.model(
    "SubjectSaveDto",
    {
        "company_name": fields.String(required=True),
        "chroma_tenant": fields.String(required=False),
        "chroma_database": fields.String(required=False),
        "chroma_collection": fields.String(required=False),
        "credit_code": fields.String(required=False),
        "contact_person": fields.String(required=False),
        "contact_phone": fields.String(required=False),
        "address": fields.String(required=False),
        "remark": fields.String(required=False),
        "status": fields.Boolean(required=False),
    },
)


@subject_ns.route("")
class SubjectListResource(Resource):
    """处理主体公司列表查询与新增。"""

    @subject_ns.expect(subject_list_parser)
    def get(self):
        """分页查询主体公司，并附带资料完整度信息。"""

        args = subject_list_parser.parse_args()
        return BiddingTaskService.list_subjects(
            page_no=args["page_no"],
            page_size=args["page_size"],
            keyword=args.get("keyword"),
            status=args.get("status"),
        )

    @subject_ns.expect(subject_save_model)
    def post(self):
        """创建新的主体公司信息。"""

        return success(BiddingTaskService.save_subject(**(subject_ns.payload or {})), message="主体公司已保存")


@subject_ns.route("/<int:subject_id>")
class SubjectDetailResource(Resource):
    """处理单个主体公司的详情、更新与停用。"""

    def get(self, subject_id):
        """获取主体公司详情以及已上传资料列表。"""

        return success(BiddingTaskService.get_subject_detail(subject_id))

    @subject_ns.expect(subject_save_model)
    def put(self, subject_id):
        """更新指定主体公司的基础信息。"""

        return success(
            BiddingTaskService.save_subject(subject_id=subject_id, **(subject_ns.payload or {})),
            message="主体公司已更新",
        )

    def delete(self, subject_id):
        """停用指定主体公司，而不是物理删除记录。"""

        return success(BiddingTaskService.delete_subject(subject_id), message="主体公司已停用")


@subject_ns.route("/<int:subject_id>/materials")
class SubjectMaterialListResource(Resource):
    """处理主体资料列表查询与文件上传。"""

    def get(self, subject_id):
        """获取主体资料列表及完整度结果。"""

        return success(BiddingTaskService.list_subject_materials(subject_id))

    @subject_ns.expect(subject_material_upload_parser)
    def post(self, subject_id):
        """向指定主体上传一份资料文件。"""

        args = subject_material_upload_parser.parse_args()
        return success(
            BiddingTaskService.upload_subject_material(subject_id, args["material_type"], args["file"]),
            message="主体资料上传成功",
        )


@subject_ns.route("/<int:subject_id>/materials/<int:material_id>")
class SubjectMaterialDeleteResource(Resource):
    """处理单个主体资料的删除。"""

    def delete(self, subject_id, material_id):
        """删除指定主体下的一份资料文件。"""

        return success(BiddingTaskService.delete_subject_material(subject_id, material_id), message="主体资料已删除")
