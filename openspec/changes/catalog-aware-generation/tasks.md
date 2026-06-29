# 目录生成改造 — 任务列表

## 架构层（P0 🔴）

### T1: 定义 Central Schema 数据契约
- [ ] 1.1 创建 `app/domain/analysis_schema.py`，定义 MetadataSchema / AnalysisSchema
- [ ] 1.2 定义 FieldMetadata（value + confidence + source + raw_match）
- [ ] 1.3 定义 ConfidenceLevel 枚举（HIGH / MEDIUM / LOW / UNKNOWN）
- [ ] 1.4 定义 _safe_read() 兼容读取函数

### T2: 实现 Validation Gate
- [ ] 2.1 实现 ValidationGate 类，含类型校验 + 置信度校验
- [ ] 2.2 CRITICAL 级别阻断流程（抛异常、记录原文上下文）
- [ ] 2.3 WARNING 级别写入 issues 列表，不阻断
- [ ] 2.4 集成到 analysis_v3 管线的输出端

### T3: 消除 Legacy 副本字段
- [ ] 3.1 BiddingAnalysisResult.overview 改为 @property 实时计算
- [ ] 3.2 BiddingAnalysisResult.business_requirements 改为 @property
- [ ] 3.3 BiddingAnalysisResult.technical_requirements 改为 @property
- [ ] 3.4 BiddingAnalysisResult.scoring_items 改为 @property
- [ ] 3.5 BiddingAnalysisResult.packages_json 改为 @property
- [ ] 3.6 统一预算格式化逻辑到一处

## 提取层（P0 🔴）

### T4: Phase 1 元数据提取改造 — 置信度标注
- [ ] 4.1 _rule_extract 输出带 confidence + source 的 FieldMetadata
- [ ] 4.2 _build_metadata 初始化使用 MetadataSchema（统一各处 NULL_METADATA）
- [ ] 4.3 _merge_preliminary_table 输出带置信度的字段
- [ ] 4.4 section_extractor 输出带置信度的字段
- [ ] 4.5 LLM 增强合并时保留/调整置信度
- [ ] 4.6 修复 KEY_MAP 与 _build_metadata 初始化不一致（project_code/project_name）
- [ ] 4.7 提取失败时记录 fallback_attempted

### T5: Phase 1.5 格式要求提取（新增模块）
- [ ] 5.1 创建 `app/service_modules/task_pipeline/analysis_v3/phase1_5_format.py`
- [ ] 5.2 实现目录扫描：定位"第三章 比选申请文件格式"等章节
- [ ] 5.3 实现模板表格提取（python-docx 原生表格 → 结构化 headers/rows）
- [ ] 5.4 实现固定文本提取
- [ ] 5.5 输出: FormatRequirement dataclass
- [ ] 5.6 集成到 analysis_v3 管线

### T6: Phase 2/3 置信度标注
- [ ] 6.1 eligibility.qualifications 每条带 confidence
- [ ] 6.2 scoring.dimensions 每条带 confidence
- [ ] 6.3 packages[].parameters 每条带 confidence
- [ ] 6.4 extract_packages 单包回退逻辑改进（已有）

## Catalog 层（P1 🟡）

### T7: 目录生成集成置信度
- [ ] 7.1 低置信度字段在目录描述中追加"待确认"标记
- [ ] 7.2 catalog-options API 返回 issues 列表（供前端展示）

### T8: 目录生成集成格式约束
- [ ] 8.1 实现 validate_against_format() 校验函数
- [ ] 8.2 格式要求的必选章节缺失时阻断并报告
- [ ] 8.3 格式要求的章节顺序与生成顺序对比
- [ ] 8.4 catalog-options 响应增加 format_violations 字段

### T9: 已有功能修复
- [x] 9.1 预算格式化 .0f → .2f + 整除判断
- [x] 9.2 商务要求字段全量遍历（7→16个）
- [x] 9.3 包名优先级修复（pkg_name_map > section title > 兜底）
- [x] 9.4 前序文本覆盖 80→150 行
- [x] 9.5 LLM excerpt 5000→8000
- [x] 9.6 单包自动跳过 PACKAGE_PENDING
- [x] 9.7 project_code/project_name KEY_MAP 初始化不一致修复
- [x] 9.8 所有下游 reader 兼容新旧格式

## 生成层（P2 🟢）

### T10: 内容生成集成格式模板
- [ ] 10.1 _generate_chapter_content 注入 FormatRequirement 信息
- [ ] 10.2 优先使用模板表格替代 LLM 自由生成表格
- [ ] 10.3 固定文本作为硬约束注入提示词

## 测试（P1 🟡）

### T11: 测试
- [ ] 11.1 测试 Central Schema 类型校验
- [ ] 11.2 测试 Validation Gate 阻断/告警逻辑
- [ ] 11.3 测试置信度计算逻辑
- [ ] 11.4 测试 Phase 1.5 格式提取
- [ ] 11.5 测试格式约束校验
- [ ] 11.6 更新现有测试适配新 schema
- [ ] 11.7 启动服务验证实际运行

---

## 新增任务（2026-06-29 探索发现）

### T12: 特殊字符前缀处理（P0 🔴）
- [ ] 12.1 实现 `_strip_heading_prefix()` 通用函数
- [ ] 12.2 `document_parser.py` text_heading_patterns 匹配前调用
- [ ] 12.3 `section_extractor.py` find_section_by_title 匹配前调用
- [ ] 12.4 `phase3_scoring.py` _find_tech_section 匹配前调用
- [ ] 12.5 `phase1_metadata.py` 章节搜索调用
- [ ] 12.6 单元测试：★●■▲ 等前缀的标题匹配

### T13: 双线并行提取（P0 🔴）
- [ ] 13.1 实现 `_find_business_section_text()` 章节定位函数
- [ ] 13.2 实现 `_find_technical_section_text()` 章节定位函数
- [ ] 13.3 在 `analysis_v3/__init__.py` 中激活 `llm_extract_business()` 调用
- [ ] 13.4 在 `analysis_v3/__init__.py` 中激活 `llm_extract_technical()` 调用
- [ ] 13.5 实现规则+LLM 合并策略（规则优先，LLM 兜底）
- [ ] 13.6 单元测试：规则空 + LLM 有、规则有 + LLM 空、两者都有

### T14: 单包包名留空（P0 🔴）
- [ ] 14.1 `phase3_scoring.py` extract_packages() 单包场景包名处理
- [ ] 14.2 `check_items/bidding_info.py` _get_current_package_info() 包名为空时处理
- [ ] 14.3 单元测试：单包无包名、单包有包名、多包包名

### T15: 文档感知目录生成（P1 🟡）
- [ ] 15.1 实现 `classify_chapters()` 招标章节类型推断
- [ ] 15.2 实现 `map_to_response()` 招标章节 → 响应章节映射
- [ ] 15.3 实现 `build_document_aware_catalog()` 新目录生成入口
- [ ] 15.4 章节类型规则表维护（INVITATION / FORMAT / QUALIFICATION / REQUIREMENT / SCORING / CONTRACT / INSTRUCTION）
- [ ] 15.5 格式要求章节照搬逻辑（FORMAT 类型→直接插入响应目录）
- [ ] 15.6 补充章节推断（格式未覆盖时的后备）
- [ ] 15.7 集成到 `get_catalog_options()` 调用链
- [ ] 15.8 向后兼容：老路径保留为降级方案
- [ ] 15.9 单元测试：多种招标文件结构测试

### T16: 集成测试（P1 🟡）
- [ ] 16.1 启动服务验证实战（CG20250099 文档全流程）
- [ ] 16.2 验证目录生成包含第三章格式要求
- [ ] 16.3 验证目录不含文档没有的章节
- [ ] 16.4 验证商务/技术要求已提取
- [ ] 16.5 验证单包包名留空
