USE `erp_bidding`;

-- 文件存储表：统一管理所有上传文件（招标文件、主体资料、知识库文档等），
-- 支持本地存储和 MinIO 对象存储两种方式，同时记录向量数据库引用信息。
CREATE TABLE IF NOT EXISTS `file_storage` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `biz_type` VARCHAR(64) NOT NULL COMMENT '业务类型（如：TENDER_FILE=招标文件, SUBJECT_MATERIAL=主体资料, KNOWLEDGE_FILE=知识库文件, BID_RESULT=投标结果文件）',
  `biz_id` BIGINT DEFAULT NULL COMMENT '业务关联ID（关联具体业务记录的主键）',
  `file_name` VARCHAR(255) NOT NULL COMMENT '文件原始名称（含扩展名）',
  `file_ext` VARCHAR(32) DEFAULT NULL COMMENT '文件扩展名（如：.pdf, .docx, .xlsx）',
  `file_size` BIGINT NOT NULL DEFAULT 0 COMMENT '文件大小（字节）',
  `storage_provider` VARCHAR(32) NOT NULL DEFAULT 'LOCAL' COMMENT '存储提供商（LOCAL=本地存储, MINIO=MinIO对象存储）',
  `minio_bucket` VARCHAR(128) DEFAULT NULL COMMENT 'MinIO存储桶名称',
  `minio_object_name` VARCHAR(512) DEFAULT NULL COMMENT 'MinIO对象名称（存储路径+文件名）',
  `local_path` VARCHAR(512) DEFAULT NULL COMMENT '本地存储路径',
  `chroma_tenant` VARCHAR(128) DEFAULT NULL COMMENT 'Chroma向量数据库租户名',
  `chroma_database` VARCHAR(128) DEFAULT NULL COMMENT 'Chroma向量数据库名',
  `chroma_collection` VARCHAR(128) DEFAULT NULL COMMENT 'Chroma向量数据库集合名',
  `chroma_doc_id` LONGTEXT DEFAULT NULL COMMENT 'Chroma向量数据库中文档ID（多个ID用逗号分隔）',
  `deleted_flag` TINYINT NOT NULL DEFAULT 0 COMMENT '逻辑删除标记（0=未删除, 1=已删除）',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_biz_type_biz_id` (`biz_type`, `biz_id`) COMMENT '按业务类型和业务ID查询的联合索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文件存储表：统一管理所有上传文件，支持本地/MinIO存储及向量数据库关联';

-- 标书共享资源表：一份招标文件被解析后产生的共享数据资源，
-- 可被多个标书任务引用复用，避免重复解析。
CREATE TABLE IF NOT EXISTS `bidding_shared_resource` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `root_task_id` BIGINT DEFAULT NULL COMMENT '根任务ID（首次创建该资源的任务ID）',
  `bid_type` VARCHAR(32) NOT NULL COMMENT '投标类型（如：PLAIN=普通公开招标, INVITE=邀请招标, NEGOTIATE=竞争性谈判, QUERY=询价, SINGLE=单一来源）',
  `tender_file_id` BIGINT NOT NULL COMMENT '招标文件ID（关联 file_storage 表）',
  `analysis_status` TINYINT NOT NULL DEFAULT 0 COMMENT '招标文件分析状态（0=未分析, 1=分析中, 2=分析完成, 3=分析失败）',
  `has_package` TINYINT NOT NULL DEFAULT 0 COMMENT '是否有标包/分包（0=无分包, 1=有分包）',
  `selected_package_no` VARCHAR(128) DEFAULT NULL COMMENT '选定投标的标包编号（多包时选择投其中一个）',
  `check_status` TINYINT NOT NULL DEFAULT 0 COMMENT '标书检查状态（0=未检查, 1=检查中, 2=检查完成, 3=检查失败）',
  `catalog_status` TINYINT NOT NULL DEFAULT 0 COMMENT '目录生成状态（0=未生成, 1=生成中, 2=生成完成, 3=生成失败）',
  `catalog_source_type` VARCHAR(64) DEFAULT NULL COMMENT '目录来源类型（AI=AI生成, TEMPLATE=模板生成, MANUAL=手动创建）',
  `reference_count` INT NOT NULL DEFAULT 1 COMMENT '被引用的标书任务数',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_root_task_id` (`root_task_id`) COMMENT '按根任务ID查询的索引',
  KEY `idx_tender_file_id` (`tender_file_id`) COMMENT '按招标文件ID查询的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标书共享资源表：招标文件解析后的共享数据，可被多个投标任务复用';

-- 标书任务主表：一次标书生成任务的完整记录，包含任务配置、进度和状态追踪。
-- 一个任务对应一份投标文件/一个标包的生成过程。
CREATE TABLE IF NOT EXISTS `bidding_task` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `task_name` VARCHAR(255) NOT NULL COMMENT '任务名称',
  `task_origin` VARCHAR(16) NOT NULL COMMENT '任务来源（NEW=新建投标, CHILD=子任务, COPY=复制任务）',
  `parent_task_id` BIGINT DEFAULT NULL COMMENT '父任务ID（子任务关联的父任务，用于拆分生成）',
  `shared_resource_id` BIGINT NOT NULL COMMENT '关联的共享资源ID',
  `tender_file_name` VARCHAR(255) NOT NULL COMMENT '招标文件原始名称',
  `bid_type` VARCHAR(32) NOT NULL COMMENT '投标类型（同共享资源定义）',
  `subject_id` BIGINT DEFAULT NULL COMMENT '投标主体公司ID（关联 subject_company 表）',
  `status` VARCHAR(32) NOT NULL COMMENT '任务状态（QUEUED=排队中, RUNNING=执行中, COMPLETED=已完成, FAILED=失败, CANCELLED=已取消）',
  `progress` INT NOT NULL DEFAULT 0 COMMENT '任务整体进度百分比（0-100）',
  `current_step` VARCHAR(32) NOT NULL COMMENT '当前执行步骤（ANALYSIS=分析, CHECK=检查, CATALOG=目录生成, GENERATE=内容生成, REVIEW=复核）',
  `model_type` VARCHAR(32) DEFAULT NULL COMMENT '使用的AI模型类型',
  `use_knowledge_base` TINYINT NOT NULL DEFAULT 0 COMMENT '是否启用知识库（0=禁用, 1=启用）',
  `knowledge_base_ids` LONGTEXT DEFAULT NULL COMMENT '选中的知识库ID列表（JSON数组格式）',
  `use_product_library` TINYINT NOT NULL DEFAULT 0 COMMENT '是否启用产品库（0=禁用, 1=启用）',
  `catalog_generation_level` VARCHAR(32) DEFAULT NULL COMMENT '目录生成级别（SIMPLE=精简, DETAIL=详细, CUSTOM=自定义）',
  `word_count_level` VARCHAR(32) DEFAULT NULL COMMENT '字数要求级别（MINI=精简, MEDIUM=中等, FULL=完整）',
  `generate_stage_code` VARCHAR(32) DEFAULT NULL COMMENT '生成阶段状态码',
  `generate_stage_message` VARCHAR(255) DEFAULT NULL COMMENT '生成阶段状态描述信息',
  `result_file_id` BIGINT DEFAULT NULL COMMENT '最终生成结果文件ID（关联 file_storage 表）',
  `error_message` VARCHAR(1000) DEFAULT NULL COMMENT '错误信息（任务失败时记录详细原因）',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `deleted_flag` TINYINT NOT NULL DEFAULT 0 COMMENT '逻辑删除标记（0=未删除, 1=已删除）',
  PRIMARY KEY (`id`),
  KEY `idx_shared_resource_id` (`shared_resource_id`) COMMENT '按共享资源ID查询的索引',
  KEY `idx_status` (`status`) COMMENT '按任务状态查询的索引',
  KEY `idx_created_at` (`created_at`) COMMENT '按创建时间查询的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标书任务主表：记录标书生成任务配置、进度与状态';

-- 招标文件附件表：记录招标文件附带的补充材料（如补遗文件、澄清说明等）
CREATE TABLE IF NOT EXISTS `bidding_tender_attachment` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `shared_resource_id` BIGINT NOT NULL COMMENT '关联的共享资源ID',
  `file_id` BIGINT NOT NULL COMMENT '附件文件ID（关联 file_storage 表）',
  `file_name` VARCHAR(255) NOT NULL COMMENT '附件原始文件名',
  `uploaded_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
  PRIMARY KEY (`id`),
  KEY `idx_shared_resource_id` (`shared_resource_id`) COMMENT '按共享资源ID查询的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='招标文件附件表：招标文件的补充材料记录';

-- 标书分析结果表：AI对招标文件进行智能分析后的结构化结果，
-- 包括需求拆解、资格要求、评分标准、废标条款等核心信息。
CREATE TABLE IF NOT EXISTS `bidding_analysis_result` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `shared_resource_id` BIGINT NOT NULL COMMENT '关联的共享资源ID',
  `overview` LONGTEXT COMMENT '招标文件概要信息（项目概况、招标范围、投标人资格等摘要，JSON格式）',
  `requirements` LONGTEXT COMMENT '总体需求汇总（所有要求的汇总，JSON格式）',
  `business_requirements` LONGTEXT COMMENT '商务要求（资质证件、业绩要求、财务要求等，JSON格式）',
  `qualification_requirements` LONGTEXT COMMENT '资格条件（投标人资格、联合体要求等，JSON格式）',
  `technical_requirements` LONGTEXT COMMENT '技术要求（技术方案、工艺标准、验收标准等，JSON格式）',
  `scoring_items` LONGTEXT COMMENT '评分标准（评分项目、分值分布、评分细则，JSON格式）',
  `disqualification_items` LONGTEXT COMMENT '废标条款（可能导致废标的情况列表，JSON格式）',
  `analysis_data` TEXT COMMENT '结构化分析数据JSON（投标人须知/资格审查/商务/技术/评分等结构化对象）',
  `raw_text` LONGTEXT COMMENT '招标文件原始文本内容',
  `effective_text` LONGTEXT COMMENT '招标文件有效正文（去除杂音的纯净文本）',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_shared_resource_id` (`shared_resource_id`) COMMENT '按共享资源ID查询的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标书分析结果表：AI对招标文件的结构化分析数据';

-- 标书核对项表：逐项记录招标文件中的关键要求，供投标人逐条核对和确认，
-- 确保投标文件不遗漏任何重要条款。
CREATE TABLE IF NOT EXISTS `bidding_check_item` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `shared_resource_id` BIGINT NOT NULL COMMENT '关联的共享资源ID',
  `check_key` VARCHAR(128) NOT NULL COMMENT '核对项键名（如：credit_code, bid_bond, qualification）',
  `check_label` VARCHAR(255) NOT NULL COMMENT '核对项显示名称（如：社会信用代码、投标保证金、资质要求）',
  `check_value` LONGTEXT COMMENT '核对项内容/说明（具体要求描述，JSON格式）',
  `confirmed_flag` TINYINT NOT NULL DEFAULT 0 COMMENT '是否已确认（0=未确认, 1=已确认）',
  `sort_no` INT NOT NULL DEFAULT 0 COMMENT '排序序号（数字越小越靠前）',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_shared_resource_id` (`shared_resource_id`) COMMENT '按共享资源ID查询的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标书核对项表：招标文件关键要求逐条核对清单';

-- 标书目录表：投标文件的结构化目录信息，
-- 可由AI自动生成、从模板导入或手动创建。
CREATE TABLE IF NOT EXISTS `bidding_catalog` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `shared_resource_id` BIGINT NOT NULL COMMENT '关联的共享资源ID',
  `catalog_source_type` VARCHAR(64) NOT NULL COMMENT '目录来源类型（AI=AI生成, TEMPLATE=模板生成, MANUAL=手动创建）',
  `template_id` BIGINT DEFAULT NULL COMMENT '引用的目录模板ID（关联 template_catalog 表）',
  `catalog_content` LONGTEXT NOT NULL COMMENT '目录内容（JSON格式，包含章节标题、层级结构等）',
  `confirmed_flag` TINYINT NOT NULL DEFAULT 0 COMMENT '是否已确认（0=未确认, 1=已确认）',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_shared_resource_id` (`shared_resource_id`) COMMENT '按共享资源ID查询的索引',
  KEY `idx_template_id` (`template_id`) COMMENT '按模板ID查询的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标书目录表：投标文件的结构化目录（AI/模板/手动生成）';

-- 标书任务章节表：标书任务中每个章节的独立状态追踪，
-- 支持按章节并行或分批生成，并记录每个章节的生成进展。
CREATE TABLE IF NOT EXISTS `bidding_task_chapter` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `task_id` BIGINT NOT NULL COMMENT '关联的标书任务ID',
  `chapter_no` INT NOT NULL COMMENT '章节序号（从1开始排序）',
  `chapter_title` VARCHAR(255) NOT NULL COMMENT '章节标题',
  `status` VARCHAR(16) NOT NULL DEFAULT 'PENDING' COMMENT '章节生成状态（PENDING=待生成, GENERATING=生成中, COMPLETED=已完成, FAILED=失败）',
  `progress` INT NOT NULL DEFAULT 0 COMMENT '章节进度百分比（0-100）',
  `stage_code` VARCHAR(32) NOT NULL DEFAULT 'QUEUED' COMMENT '阶段代码（进一步细分当前状态，如：QUEUED=排队中, WRITING=撰写中, REVIEWING=复核中）',
  `stage_message` VARCHAR(255) DEFAULT '等待生成' COMMENT '阶段描述信息（如：正在撰写技术方案...）',
  `content_snapshot` LONGTEXT COMMENT '生成内容快照（已生成的内容文本）',
  `error_message` VARCHAR(1000) DEFAULT NULL COMMENT '错误信息（生成失败时的详细原因）',
  `started_at` DATETIME DEFAULT NULL COMMENT '开始生成时间',
  `finished_at` DATETIME DEFAULT NULL COMMENT '完成生成时间',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_task_chapter_no` (`task_id`, `chapter_no`) COMMENT '同一任务下章节序号唯一',
  KEY `idx_task_status` (`task_id`, `status`) COMMENT '按任务ID和状态查询的联合索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标书任务章节表：标书各章节的独立生成进度与状态追踪';

-- 主体公司表：记录作为投标主体的公司/企业信息，
-- 一家公司可以拥有多个知识库和资料文件，是投标业务的核心主体。
CREATE TABLE IF NOT EXISTS `subject_company` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `company_name` VARCHAR(255) NOT NULL COMMENT '公司名称（投标主体全称）',
  `chroma_tenant` VARCHAR(128) DEFAULT NULL COMMENT 'Chroma向量数据库租户名（该公司专属向量空间）',
  `chroma_database` VARCHAR(128) DEFAULT NULL COMMENT 'Chroma向量数据库名',
  `chroma_collection` VARCHAR(128) DEFAULT NULL COMMENT 'Chroma向量表名',
  `credit_code` VARCHAR(128) DEFAULT NULL COMMENT '统一社会信用代码',
  `contact_person` VARCHAR(64) DEFAULT NULL COMMENT '联系人姓名',
  `contact_phone` VARCHAR(64) DEFAULT NULL COMMENT '联系电话',
  `address` VARCHAR(500) DEFAULT NULL COMMENT '公司地址',
  `remark` VARCHAR(1000) DEFAULT NULL COMMENT '备注信息',
  `status` TINYINT NOT NULL DEFAULT 1 COMMENT '状态（0=禁用, 1=启用）',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='主体公司表：投标主体公司/企业信息';

-- 主体资料文件表：记录投标主体公司上传的各类证明资料文件，
-- 如营业执照、资质证书、人员证书、业绩证明等。
CREATE TABLE IF NOT EXISTS `subject_material_file` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `subject_id` BIGINT NOT NULL COMMENT '关联的主体公司ID',
  `material_type` VARCHAR(64) NOT NULL COMMENT '资料类型（如：BUSINESS_LICENSE=营业执照, QUALIFICATION=资质证书, PERSONNEL=人员证书, PERFORMANCE=业绩证明, FINANCE=财务报表）',
  `file_id` BIGINT NOT NULL COMMENT '文件ID（关联 file_storage 表）',
  `file_name` VARCHAR(255) NOT NULL COMMENT '文件原始名称',
  `uploaded_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
  PRIMARY KEY (`id`),
  KEY `idx_subject_id` (`subject_id`) COMMENT '按主体公司ID查询的索引',
  KEY `idx_subject_material_type` (`subject_id`, `material_type`) COMMENT '按主体+资料类型查询的联合索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='主体资料文件表：投标主体的各类证明资料文件';

-- 知识库表：用于构建和管理的RAG知识库，
-- 一个主体公司可以拥有多个不同主题的知识库（如：公司资质库、技术方案库、业绩案例库）。
CREATE TABLE IF NOT EXISTS `knowledge_base` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `subject_id` BIGINT DEFAULT NULL COMMENT '所属主体公司ID（NULL表示全局知识库）',
  `name` VARCHAR(255) NOT NULL COMMENT '知识库名称',
  `description` VARCHAR(1000) DEFAULT NULL COMMENT '知识库描述',
  `chroma_tenant` VARCHAR(128) DEFAULT NULL COMMENT 'Chroma向量数据库租户名',
  `chroma_database` VARCHAR(128) DEFAULT NULL COMMENT 'Chroma向量数据库名',
  `chroma_collection` VARCHAR(128) DEFAULT NULL COMMENT 'Chroma向量数据库集合名',
  `file_count` INT NOT NULL DEFAULT 0 COMMENT '知识库文件数量',
  `total_size` BIGINT NOT NULL DEFAULT 0 COMMENT '知识库文件总大小（字节）',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_subject_id` (`subject_id`) COMMENT '按主体公司ID查询的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识库表：RAG知识库，用于标书生成时检索参考材料';

-- 知识库文件表：知识库内包含的具体文档文件，
-- 文件上传后会被向量化存入 Chroma 数据库用于语义检索。
CREATE TABLE IF NOT EXISTS `knowledge_base_file` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `knowledge_base_id` BIGINT NOT NULL COMMENT '所属知识库ID',
  `file_id` BIGINT NOT NULL COMMENT '文件ID（关联 file_storage 表）',
  `file_name` VARCHAR(255) NOT NULL COMMENT '文件原始名称',
  `file_size` BIGINT NOT NULL DEFAULT 0 COMMENT '文件大小（字节）',
  `reference_enabled` TINYINT NOT NULL DEFAULT 1 COMMENT '是否启用参考（0=禁用引用, 1=允许引用该文件作为参考）',
  `upload_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
  PRIMARY KEY (`id`),
  KEY `idx_knowledge_base_id` (`knowledge_base_id`) COMMENT '按知识库ID查询的索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识库文件表：知识库中的文档文件列表';

-- 模板目录表：预定义的标书目录模板，
-- 用户可基于模板快速生成标准目录结构，提高工作效率。
-- 不同投标类型的模板可不同，如工程类、货物类、服务类各有不同目录规范。
CREATE TABLE IF NOT EXISTS `template_catalog` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `template_name` VARCHAR(255) NOT NULL COMMENT '模板名称',
  `bid_type` VARCHAR(32) NOT NULL COMMENT '适配的投标类型（PLAIN=公开招标, INVITE=邀请招标, NEGOTIATE=竞争性谈判, QUERY=询价, SINGLE=单一来源）',
  `template_desc` VARCHAR(1000) DEFAULT NULL COMMENT '模板描述',
  `catalog_content` JSON NOT NULL COMMENT '目录结构内容（JSON格式，包含层级章节标题、序号等）',
  `use_count` INT NOT NULL DEFAULT 0 COMMENT '使用次数统计',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模板目录表：投标文件目录结构模板';

-- 标书后台任务执行表：记录标书生成过程中的异步后台任务执行记录，
-- 如招标文件分析、内容生成、文件合成等，支持任务进度追踪和取消操作。
CREATE TABLE IF NOT EXISTS `bidding_task_execution` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `task_id` BIGINT NOT NULL COMMENT '关联的标书任务ID',
  `execution_type` VARCHAR(32) NOT NULL COMMENT '执行任务类型（ANALYZE=招标文件分析, GENERATE=内容生成, COMBINE=文件合成, EXPORT=导出结果）',
  `status` VARCHAR(32) NOT NULL DEFAULT 'QUEUED' COMMENT '执行状态（QUEUED=排队中, RUNNING=执行中, COMPLETED=已完成, FAILED=失败, CANCELLED=已取消）',
  `progress` INT NOT NULL DEFAULT 0 COMMENT '执行进度百分比（0-100）',
  `request_payload` LONGTEXT DEFAULT NULL COMMENT '任务请求参数（JSON格式，包含任务的输入数据）',
  `result_payload` LONGTEXT DEFAULT NULL COMMENT '任务执行结果（JSON格式，包含任务的输出数据）',
  `error_message` VARCHAR(1000) DEFAULT NULL COMMENT '错误信息（执行失败时的详细原因）',
  `cancel_requested` TINYINT NOT NULL DEFAULT 0 COMMENT '是否已请求取消（0=未请求, 1=已请求取消）',
  `started_at` DATETIME DEFAULT NULL COMMENT '开始执行时间',
  `finished_at` DATETIME DEFAULT NULL COMMENT '完成执行时间',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_task_execution` (`task_id`, `execution_type`, `status`) COMMENT '按任务ID、执行类型和状态的联合查询索引'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='标书后台任务执行表：异步任务执行记录与进度追踪';


-- ==========================================
-- document-parse-and-multi-recall 变更
-- ==========================================

-- subject_company 增加 short_name 字段
ALTER TABLE `subject_company`
  ADD COLUMN `short_name` VARCHAR(64) DEFAULT NULL COMMENT '公司简称（用作 ChromaDB collection 名）' AFTER `company_name`;

-- file_storage 增加 file_sha256 字段
ALTER TABLE `file_storage`
  ADD COLUMN `file_sha256` VARCHAR(64) DEFAULT NULL COMMENT '文件SHA256（用于缓存校验）' AFTER `file_size`;

-- 文档解析缓存表
CREATE TABLE IF NOT EXISTS `doc_parse_cache` (
  `id`           BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  `file_id`      BIGINT NOT NULL COMMENT '文件ID',
  `file_sha256`  VARCHAR(64) NOT NULL COMMENT '文件SHA256，用于缓存失效判断',
  `parse_version` VARCHAR(16) NOT NULL DEFAULT '1.0' COMMENT '解析器版本，升级后旧缓存失效',
  `parsed_json`  LONGBLOB NOT NULL COMMENT '结构化解析结果（JSON）',
  `chunk_count`  INT NOT NULL DEFAULT 0 COMMENT '切片数量',
  `created_at`   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  UNIQUE KEY `uk_file_id` (`file_id`),
  KEY `idx_sha256` (`file_sha256`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文档解析缓存表：避免重复解析相同文件';

-- 文档切片数据表（含全文索引）
CREATE TABLE IF NOT EXISTS `doc_chunks` (
  `id`           BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
  `file_id`      BIGINT NOT NULL COMMENT '所属文件ID',
  `chunk_index`  INT NOT NULL COMMENT '切片序号',
  `content`      TEXT NOT NULL COMMENT '切片文本内容',
  `section_path` VARCHAR(255) DEFAULT NULL COMMENT '章节路径（如：第一章>1.2）',
  `content_type` VARCHAR(32) NOT NULL DEFAULT 'paragraph' COMMENT '内容类型：heading/paragraph/table/mixed',
  `extra_metadata` JSON DEFAULT NULL COMMENT '扩展元数据',
  `chroma_id`    VARCHAR(128) DEFAULT NULL COMMENT 'ChromaDB中的ID',
  `created_at`   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  FULLTEXT INDEX `ft_content` (`content`) WITH PARSER `ngram`,
  KEY `idx_file_id` (`file_id`),
  KEY `idx_chroma_id` (`chroma_id`),
  UNIQUE KEY `idx_file_chunk` (`file_id`, `chunk_index`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文档切片数据表，支持FULLTEXT关键词检索';
