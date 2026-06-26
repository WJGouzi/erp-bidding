from ..core.extensions import db
from ..core.time_utils import utc_now


class FileStorage(db.Model):
    """记录上传文件、生成文件和知识库文件的统一存储元数据。"""

    __tablename__ = "file_storage"

    # 基础标识与业务归属。
    id = db.Column(db.Integer, primary_key=True)
    biz_type = db.Column(db.String(64), nullable=False)
    biz_id = db.Column(db.Integer, nullable=True)

    # 文件自身属性。
    file_name = db.Column(db.String(255), nullable=False)
    file_ext = db.Column(db.String(32), nullable=True)
    file_sha256 = db.Column(db.String(64), nullable=True, comment="文件SHA256，用于缓存校验")
    file_size = db.Column(db.BigInteger, nullable=False, default=0)

    # 物理存储位置，可落本地或 MinIO。
    storage_provider = db.Column(db.String(32), nullable=False, default="LOCAL")
    minio_bucket = db.Column(db.String(128), nullable=True)
    minio_object_name = db.Column(db.String(512), nullable=True)
    local_path = db.Column(db.String(512), nullable=True)

    # 当文件与 Chroma 数据相关时，记录其租户与文档标识。
    chroma_tenant = db.Column(db.String(128), nullable=True)
    chroma_database = db.Column(db.String(128), nullable=True)
    chroma_collection = db.Column(db.String(128), nullable=True)
    chroma_doc_id = db.Column(db.Text, nullable=True)

    # 通用状态字段。
    deleted_flag = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    def to_dict(self):
        """将文件存储记录转换为接口可直接返回的字典。"""

        return {
            "id": self.id,
            "biz_type": self.biz_type,
            "biz_id": self.biz_id,
            "file_name": self.file_name,
            "file_ext": self.file_ext,
            "file_sha256": self.file_sha256,
            "file_size": self.file_size,
            "storage_provider": self.storage_provider,
            "minio_bucket": self.minio_bucket,
            "minio_object_name": self.minio_object_name,
            "local_path": self.local_path,
            "chroma_tenant": self.chroma_tenant,
            "chroma_database": self.chroma_database,
            "chroma_collection": self.chroma_collection,
            "chroma_doc_id": self.chroma_doc_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class BiddingSharedResource(db.Model):
    """保存多个派生任务共享的招标源分析与目录资源。"""

    __tablename__ = "bidding_shared_resource"

    # 共享资源基础标识。
    id = db.Column(db.Integer, primary_key=True)
    root_task_id = db.Column(db.Integer, nullable=True)
    bid_type = db.Column(db.String(32), nullable=False)
    tender_file_id = db.Column(db.Integer, nullable=False)

    # 上游流程状态，派生任务可直接复用。
    analysis_status = db.Column(db.Boolean, nullable=False, default=False)
    has_package = db.Column(db.Boolean, nullable=False, default=False)
    selected_package_no = db.Column(db.String(128), nullable=True)
    check_status = db.Column(db.Boolean, nullable=False, default=False)
    catalog_status = db.Column(db.Boolean, nullable=False, default=False)
    catalog_source_type = db.Column(db.String(64), nullable=True)

    # 引用计数与时间戳。
    reference_count = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    def to_dict(self):
        """将共享资源记录转换为字典。"""

        return {
            "id": self.id,
            "root_task_id": self.root_task_id,
            "bid_type": self.bid_type,
            "tender_file_id": self.tender_file_id,
            "analysis_status": self.analysis_status,
            "has_package": self.has_package,
            "selected_package_no": self.selected_package_no,
            "check_status": self.check_status,
            "catalog_status": self.catalog_status,
            "catalog_source_type": self.catalog_source_type,
            "reference_count": self.reference_count,
        }


class BiddingTenderAttachment(db.Model):
    """保存招标文件附件与共享资源之间的关联关系。"""

    __tablename__ = "bidding_tender_attachment"

    # 附件与共享资源映射关系。
    id = db.Column(db.Integer, primary_key=True)
    shared_resource_id = db.Column(db.Integer, nullable=False, index=True)
    file_id = db.Column(db.Integer, nullable=False)
    file_name = db.Column(db.String(255), nullable=False)

    # 上传时间。
    uploaded_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    def to_dict(self):
        """将招标文件附件记录转换为接口返回结构。"""

        return {
            "id": self.id,
            "shared_resource_id": self.shared_resource_id,
            "file_id": self.file_id,
            "file_name": self.file_name,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
        }


class BiddingTask(db.Model):
    """表示一个面向前端展示的标书任务实例。"""

    __tablename__ = "bidding_task"

    # 任务基础身份与来源关系。
    id = db.Column(db.Integer, primary_key=True)
    task_name = db.Column(db.String(255), nullable=False)
    task_origin = db.Column(db.String(16), nullable=False, default="ORIGINAL")
    parent_task_id = db.Column(db.Integer, nullable=True)
    shared_resource_id = db.Column(db.Integer, nullable=False)

    # 当前任务的投标上下文。
    tender_file_name = db.Column(db.String(255), nullable=False)
    bid_type = db.Column(db.String(32), nullable=False)
    subject_id = db.Column(db.Integer, nullable=True)

    # 前端流程状态与进度展示字段。
    status = db.Column(db.String(32), nullable=False, default="UPLOADED")
    progress = db.Column(db.Integer, nullable=False, default=0)
    current_step = db.Column(db.String(32), nullable=False, default="analyze")

    # 生成阶段配置。
    model_type = db.Column(db.String(32), nullable=True)
    use_knowledge_base = db.Column(db.Boolean, nullable=False, default=False)
    knowledge_base_ids = db.Column(db.Text, nullable=True)
    use_product_library = db.Column(db.Boolean, nullable=False, default=False)
    catalog_generation_level = db.Column(db.String(32), nullable=True)
    word_count_level = db.Column(db.String(32), nullable=True)

    # 生成运行态与结果文件。
    generate_stage_code = db.Column(db.String(32), nullable=True)
    generate_stage_message = db.Column(db.String(255), nullable=True)
    result_file_id = db.Column(db.Integer, nullable=True)
    error_message = db.Column(db.String(1000), nullable=True)

    # 通用时间与逻辑删除字段。
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)
    deleted_flag = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self):
        """将标书任务转换为接口返回结构。"""

        return {
            "task_id": self.id,
            "task_name": self.task_name,
            "task_origin": self.task_origin,
            "parent_task_id": self.parent_task_id,
            "shared_resource_id": self.shared_resource_id,
            "tender_file_name": self.tender_file_name,
            "bid_type": self.bid_type,
            "subject_id": self.subject_id,
            "status": self.status,
            "progress": self.progress,
            "current_step": self.current_step,
            "model_type": self.model_type,
            "use_knowledge_base": self.use_knowledge_base,
            "knowledge_base_ids": self.knowledge_base_ids,
            "use_product_library": self.use_product_library,
            "catalog_generation_level": self.catalog_generation_level,
            "word_count_level": self.word_count_level,
            "generate_stage_code": self.generate_stage_code,
            "generate_stage_message": self.generate_stage_message,
            "result_file_id": self.result_file_id,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class BiddingAnalysisResult(db.Model):
    """保存招标文件分析得到的结构化结果和有效文本。"""

    __tablename__ = "bidding_analysis_result"

    # 分析结果与共享资源绑定。
    id = db.Column(db.Integer, primary_key=True)
    shared_resource_id = db.Column(db.Integer, nullable=False, index=True)

    # 结构化分析输出。
    overview = db.Column(db.Text, nullable=True)
    requirements = db.Column(db.Text, nullable=True)
    business_requirements = db.Column(db.Text, nullable=True)
    qualification_requirements = db.Column(db.Text, nullable=True)
    technical_requirements = db.Column(db.Text, nullable=True)
    scoring_items = db.Column(db.Text, nullable=True)
    disqualification_items = db.Column(db.Text, nullable=True)

    # 结构化分析数据（新版本：投标人须知/资格审查/商务/技术/评分等结构化对象）。
    analysis_data = db.Column(db.Text, nullable=True)

    # 原始全文与按包号裁剪后的有效文本。
    raw_text = db.Column(db.Text, nullable=True)
    effective_text = db.Column(db.Text, nullable=True)

    # 时间戳。
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    def to_dict(self):
        """将分析结果转换为接口返回结构。
        
        优先从 analysis_data JSON 中读取结构化数据（新版本），
        降级到各独立字段（旧版本）。
        """
        import json

        # 尝试从 analysis_data 解析新版本结构化数据
        if self.analysis_data:
            try:
                parsed = json.loads(self.analysis_data)
                if isinstance(parsed, dict) and parsed.get("version") == "v2":
                    return {
                        "id": self.id,
                        "shared_resource_id": self.shared_resource_id,
                        "raw_text": self.raw_text or "",
                        "effective_text": self.effective_text or "",
                        "analysis_data": parsed,
                        "created_at": self.created_at.isoformat() if self.created_at else None,
                        "updated_at": self.updated_at.isoformat() if self.updated_at else None,
                    }
            except (json.JSONDecodeError, TypeError):
                pass

        # 降级到旧版本独立字段
        return {
            "id": self.id,
            "shared_resource_id": self.shared_resource_id,
            "overview": self.overview or "",
            "requirements": self.requirements or "",
            "business_requirements": self.business_requirements or "",
            "qualification_requirements": self.qualification_requirements or "",
            "technical_requirements": self.technical_requirements or "",
            "scoring_items": self.scoring_items or "",
            "disqualification_items": self.disqualification_items or "",
            "raw_text": self.raw_text or "",
            "effective_text": self.effective_text or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class BiddingCheckItem(db.Model):
    """保存分析后待人工确认的核对项。"""

    __tablename__ = "bidding_check_item"

    # 核对项归属与标识。
    id = db.Column(db.Integer, primary_key=True)
    shared_resource_id = db.Column(db.Integer, nullable=False, index=True)
    check_key = db.Column(db.String(128), nullable=False)
    check_label = db.Column(db.String(255), nullable=False)

    # 核对内容与确认状态。
    check_value = db.Column(db.Text, nullable=True)
    confirmed_flag = db.Column(db.Boolean, nullable=False, default=False)
    sort_no = db.Column(db.Integer, nullable=False, default=0)

    # 时间戳。
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    def to_dict(self):
        """将核对项转换为接口返回结构。"""

        return {
            "id": self.id,
            "shared_resource_id": self.shared_resource_id,
            "check_key": self.check_key,
            "check_label": self.check_label,
            "check_value": self.check_value or "",
            "confirmed_flag": self.confirmed_flag,
            "sort_no": self.sort_no,
        }


class BiddingCatalog(db.Model):
    """保存目录候选方案及最终确认结果。"""

    __tablename__ = "bidding_catalog"

    # 目录与共享资源的绑定关系。
    id = db.Column(db.Integer, primary_key=True)
    shared_resource_id = db.Column(db.Integer, nullable=False, index=True)

    # 目录来源和具体内容。
    catalog_source_type = db.Column(db.String(64), nullable=False)
    template_id = db.Column(db.Integer, nullable=True, index=True)
    catalog_content = db.Column(db.Text, nullable=False)
    confirmed_flag = db.Column(db.Boolean, nullable=False, default=False)

    # 时间戳。
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    def to_dict(self):
        """将目录记录转换为接口返回结构。"""

        return {
            "id": self.id,
            "shared_resource_id": self.shared_resource_id,
            "catalog_source_type": self.catalog_source_type,
            "template_id": self.template_id,
            "catalog_content": self.catalog_content,
            "confirmed_flag": self.confirmed_flag,
        }


class BiddingTaskChapter(db.Model):
    """保存标书生成时的章节级状态、进度与内容快照。"""

    __tablename__ = "bidding_task_chapter"

    # 章节基础身份。
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, nullable=False, index=True)
    chapter_no = db.Column(db.Integer, nullable=False)
    chapter_title = db.Column(db.String(255), nullable=False)

    # 章节运行状态。
    status = db.Column(db.String(16), nullable=False, default="PENDING")
    progress = db.Column(db.Integer, nullable=False, default=0)
    stage_code = db.Column(db.String(32), nullable=False, default="QUEUED")
    stage_message = db.Column(db.String(255), nullable=True, default="等待生成")

    # 章节生成结果与错误信息。
    content_snapshot = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.String(1000), nullable=True)

    # 章节运行时间。
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    def to_dict(self):
        """将章节生成记录转换为接口返回结构。"""

        return {
            "id": self.id,
            "task_id": self.task_id,
            "chapter_no": self.chapter_no,
            "chapter_title": self.chapter_title,
            "status": self.status,
            "progress": self.progress,
            "stage_code": self.stage_code,
            "stage_message": self.stage_message,
            "content_snapshot": self.content_snapshot or "",
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SubjectCompany(db.Model):
    """维护投标主体公司及其对应的知识库租户信息。"""

    __tablename__ = "subject_company"

    # 主体基础信息。
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(255), nullable=False)
    short_name = db.Column(db.String(64), nullable=True, comment="公司简称，用作 ChromaDB collection 名")
    # 与现有 Chroma 服务关联所需的租户配置。
    chroma_tenant = db.Column(db.String(128), nullable=True)
    chroma_database = db.Column(db.String(128), nullable=True)
    chroma_collection = db.Column(db.String(128), nullable=True)

    # 主体补充档案信息。
    credit_code = db.Column(db.String(128), nullable=True)
    contact_person = db.Column(db.String(64), nullable=True)
    contact_phone = db.Column(db.String(64), nullable=True)
    address = db.Column(db.String(500), nullable=True)
    remark = db.Column(db.String(1000), nullable=True)

    # 状态与时间戳。
    status = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    def to_dict(self):
        """将主体公司信息转换为字典。"""

        return {
            "id": self.id,
            "company_name": self.company_name,
            "short_name": self.short_name,
            "chroma_tenant": self.chroma_tenant,
            "chroma_database": self.chroma_database,
            "chroma_collection": self.chroma_collection,
            "credit_code": self.credit_code,
            "contact_person": self.contact_person,
            "contact_phone": self.contact_phone,
            "address": self.address,
            "remark": self.remark,
            "status": self.status,
        }


class SubjectMaterialFile(db.Model):
    """保存主体资料与实际文件之间的关联关系。"""

    __tablename__ = "subject_material_file"

    # 主体资料与文件映射关系。
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, nullable=False, index=True)
    material_type = db.Column(db.String(64), nullable=False)
    file_id = db.Column(db.Integer, nullable=False)
    file_name = db.Column(db.String(255), nullable=False)

    # 上传时间。
    uploaded_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    def to_dict(self):
        """将主体资料记录转换为接口返回结构。"""

        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "material_type": self.material_type,
            "file_id": self.file_id,
            "file_name": self.file_name,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
        }


class KnowledgeBase(db.Model):
    """维护知识库基础信息及其所属主体。"""

    __tablename__ = "knowledge_base"

    # 知识库归属与名称。
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, nullable=True, index=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(1000), nullable=True)

    # 知识库在 Chroma 中的连接定位。
    chroma_tenant = db.Column(db.String(128), nullable=True)
    chroma_database = db.Column(db.String(128), nullable=True)
    chroma_collection = db.Column(db.String(128), nullable=True)

    # 聚合统计。
    file_count = db.Column(db.Integer, nullable=False, default=0)
    total_size = db.Column(db.BigInteger, nullable=False, default=0)

    # 时间戳。
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    def to_dict(self):
        """将知识库记录转换为字典。"""

        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "name": self.name,
            "description": self.description,
            "chroma_tenant": self.chroma_tenant,
            "chroma_database": self.chroma_database,
            "chroma_collection": self.chroma_collection,
            "file_count": self.file_count,
            "total_size": self.total_size,
        }


class KnowledgeBaseFile(db.Model):
    """保存知识库文件及其向量化引用状态。"""

    __tablename__ = "knowledge_base_file"

    # 知识库文件基础信息。
    id = db.Column(db.Integer, primary_key=True)
    knowledge_base_id = db.Column(db.Integer, nullable=False, index=True)
    file_id = db.Column(db.Integer, nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.BigInteger, nullable=False, default=0)

    # 向量引用开关与上传时间。
    reference_enabled = db.Column(db.Boolean, nullable=False, default=True)
    upload_time = db.Column(db.DateTime, nullable=False, default=utc_now)

    def to_dict(self):
        """将知识库文件记录转换为接口返回结构。"""

        return {
            "id": self.id,
            "knowledge_base_id": self.knowledge_base_id,
            "file_id": self.file_id,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "reference_enabled": self.reference_enabled,
            "upload_time": self.upload_time.isoformat() if self.upload_time else None,
        }


class TemplateCatalog(db.Model):
    """保存可复用的模板目录配置。"""

    __tablename__ = "template_catalog"

    # 模板基础信息。
    id = db.Column(db.Integer, primary_key=True)
    template_name = db.Column(db.String(255), nullable=False)
    bid_type = db.Column(db.String(32), nullable=False)
    template_desc = db.Column(db.String(1000), nullable=True)

    # 模板目录内容与使用统计。
    catalog_content = db.Column(db.Text, nullable=False)
    use_count = db.Column(db.Integer, nullable=False, default=0)

    # 时间戳。
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    def to_dict(self):
        """将模板目录记录转换为接口返回结构。"""

        return {
            "id": self.id,
            "template_name": self.template_name,
            "bid_type": self.bid_type,
            "template_desc": self.template_desc,
            "catalog_content": self.catalog_content,
            "use_count": self.use_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class OperationLog(db.Model):
    """记录业务操作日志，跟踪关键操作的执行结果。"""

    __tablename__ = "operation_log"

    # 操作编号。
    id = db.Column(db.Integer, primary_key=True)

    # 操作所属模块，如 subject, knowledge_base, template, task, chroma。
    module = db.Column(db.String(64), nullable=False, index=True)

    # 操作动作，如 create, update, delete, upload, analyze, select, confirm, start 等。
    action = db.Column(db.String(64), nullable=False, index=True)

    # 操作目标类型，如 SubjectCompany, KnowledgeBase, BiddingTask 等。
    target_type = db.Column(db.String(64), nullable=True)

    # 操作目标 ID。
    target_id = db.Column(db.Integer, nullable=True, index=True)

    # 操作所属任务 ID（仅任务相关操作时填写）。
    task_id = db.Column(db.Integer, nullable=True, index=True)

    # 操作的简要描述，便于快速阅读。例如 "创建主体公司: 深圳某某科技有限公司"
    summary = db.Column(db.String(500), nullable=True)

    # 操作详情，JSON 格式存储更丰富的上下文信息。
    detail = db.Column(db.Text, nullable=True)

    # 操作结果，默认 SUCCESS；失败时记录 FAILED。
    result = db.Column(db.String(16), nullable=False, default="SUCCESS", index=True)

    # 记录时间。
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now, index=True)

    def to_dict(self):
        """将操作日志转换为接口返回结构。"""

        return {
            "id": self.id,
            "module": self.module,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "task_id": self.task_id,
            "summary": self.summary,
            "detail": self.detail,
            "result": self.result,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class BiddingTaskExecution(db.Model):
    """记录分析、生成等后台执行任务的生命周期。"""

    __tablename__ = "bidding_task_execution"

    # 执行任务基础标识。
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, nullable=False, index=True)
    execution_type = db.Column(db.String(32), nullable=False)

    # 运行态信息。
    status = db.Column(db.String(32), nullable=False, default="QUEUED")
    progress = db.Column(db.Integer, nullable=False, default=0)

    # 输入、输出与异常信息。
    request_payload = db.Column(db.Text, nullable=True)
    result_payload = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.String(1000), nullable=True)
    cancel_requested = db.Column(db.Boolean, nullable=False, default=False)

    # 生命周期时间。
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    def to_dict(self):
        """将后台执行记录转换为接口返回结构。"""

        return {
            "id": self.id,
            "task_id": self.task_id,
            "execution_type": self.execution_type,
            "status": self.status,
            "progress": self.progress,
            "request_payload": self.request_payload,
            "result_payload": self.result_payload,
            "error_message": self.error_message,
            "cancel_requested": self.cancel_requested,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

__all__ = [
    "OperationLog",
    "FileStorage",
    "BiddingSharedResource",
    "BiddingTenderAttachment",
    "BiddingTask",
    "BiddingAnalysisResult",
    "BiddingCheckItem",
    "BiddingCatalog",
    "BiddingTaskChapter",
    "SubjectCompany",
    "SubjectMaterialFile",
    "KnowledgeBase",
    "KnowledgeBaseFile",
    "TemplateCatalog",
    "BiddingTaskExecution",
    "DocParseCache",
    "DocChunk",
]

class DocParseCache(db.Model):
    """文档解析缓存，避免重复解析相同文件。"""

    __tablename__ = "doc_parse_cache"

    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.Integer, nullable=False, comment="文件ID")
    file_sha256 = db.Column(db.String(64), nullable=False, comment="文件SHA256，用于缓存失效判断")
    parse_version = db.Column(db.String(16), nullable=False, default="1.0", comment="解析器版本，升级后旧缓存失效")
    parsed_json = db.Column(db.LargeBinary, nullable=False, comment="结构化解析结果（JSON）")
    chunk_count = db.Column(db.Integer, nullable=False, default=0, comment="切片数量")
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        db.UniqueConstraint("file_id", name="uk_file_id"),
        db.Index("idx_sha256", "file_sha256"),
    )


class DocChunk(db.Model):
    """文档切片数据表，支持 FULLTEXT 关键词检索。"""

    __tablename__ = "doc_chunks"

    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.Integer, nullable=False, comment="所属文件ID")
    chunk_index = db.Column(db.Integer, nullable=False, comment="切片序号")
    content = db.Column(db.Text, nullable=False, comment="切片文本内容")
    section_path = db.Column(db.String(255), nullable=True, comment="章节路径（如：第一章>1.2）")
    content_type = db.Column(db.String(32), nullable=False, default="paragraph", comment="内容类型：heading/paragraph/table/mixed")
    extra_metadata = db.Column(db.JSON, nullable=True, comment="扩展元数据")
    chroma_id = db.Column(db.String(128), nullable=True, comment="ChromaDB 中的 ID")
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    __table_args__ = (
        db.Index("idx_file_id", "file_id"),
        db.Index("idx_chroma_id", "chroma_id"),
        db.Index("idx_file_chunk", "file_id", "chunk_index", unique=True),
    )
