## 1. 表格解析模块 — table_parser.py 核心实现

- [x] 1.1 创建 `app/infrastructure/table_parser.py` 模块入口，定义 TableType 枚举（PRELIMINARY/SCORING/PRODUCT_LIST/QUALIFICATION/RESPONSE_FORMAT/GENERIC）
- [x] 1.2 实现 `classify_table()` 函数：基于列数+表头关键词的表格类型判定（支持 DOCX table object 和 文本表格两种输入）
- [x] 1.3 实现前附表 (PRELIMINARY_TABLE) 解析：key-value 提取、多行值合并、特殊字段识别（数字+单位、枚举值）
- [x] 1.4 实现产品清单 (PRODUCT_LIST) 解析：表头映射、合并单元格展开、输出统计摘要（total_items, categories, has_pricing）
- [x] 1.5 实现评分表增强：子维度检测、评分标准原文保留、评分方法行识别
- [x] 1.6 实现 `parse_all_tables(docx_tables)` 入口：遍历 docx 所有表格，分类+提取+返回结构化结果

## 2. 表格解析集成 — 融合到 metadata 管线

- [x] 2.1 在 `phase1_metadata.py` 中导入 table_parser，在 extract_metadata() 中新增表格解析步骤
- [x] 2.2 实现表格结果与 regex 结果的合并逻辑：表格优先（高置信度），regex 结果保留为 fallback
- [x] 2.3 更新 `schemas.py` 的 NULL_METADATA 和 assemble_v3_analysis_data，增加 `tables` 字段存储表格提取结果
- [x] 2.4 更新 `schemas.py` 的 metadata 结构：每个 scalar 字段增加 `_fieldname_meta`（confidence/source/pattern）
- [x] 2.5 更新 `__init__.py` 的 start_analyze_v3() 在 phase1 阶段调用表格解析

## 3. 分包独立分析 — 包内容切分

- [x] 3.1 在 `phase3_scoring.py` 中实现 `split_content_by_package(sections, package_nos)`：按包边界规则切分文档内容
- [x] 3.2 实现包边界检测：章节标题匹配（第X包/包X）、表格标注匹配（(采购包X)）、段落标注匹配（（包X））
- [x] 3.3 实现共享内容（公共部分）的识别和复制：scope="shared" 标记
- [x] 3.4 重构 `extract_packages()`：按包遍历 → 每包独立调用参数统计 → 输出差异化结果

## 4. 分包独立分析 — 逐包策略 + 包间关联

- [x] 4.1 实现 `analyze_package_strategy(pkg_data)`：基于包内容评估难度、竞争、风险
- [x] 4.2 实现 `cross_package_analysis(packages)`：资格重叠检测、最高价值包/最低风险包识别
- [x] 4.3 更新 `_build_strategy_from_phases()`：策略输出按包差异化，每包包含 difficulty/competition/focus/risk 字段
- [x] 4.4 更新 `assemble_v3_analysis_data()`：packages 数组每个元素新增 strategy 和 scoring 字段
- [x] 4.5 更新 `_complete_analysis()` 中技术要求和商务要求的回填：按包归类

## 5. 文档分类器 — 类型识别

- [x] 5.1 在 `phase1_metadata.py` 中实现 `classify_document(file_name, raw_text)`：三层判定（文件名→正文→置信度）
- [x] 5.2 定义 SELECTION/TENDER/NEGOTIATION/INQUIRY 四种类型的规则集（差异化用词表）
- [x] 5.3 实现类型规则集加载逻辑：通用规则 + 类型特定规则覆盖
- [x] 5.4 更新 metadata schema 增加 `document_type` 字段（value/confidence/source）

## 6. 元数据增强 & 死线优先级

- [x] 6.1 实现 extra 字段补全：将已匹配的付款方式、服务期限、配送地点、代理服务费等字段写入 metadata.extra
- [x] 6.2 实现资格/废标条件的优先级标注：must_fix（★条款）/ should_fix（程序违规）/ good_to_know（信息性）
- [x] 6.3 更新 `schemas.py` 的 NULL_METADATA 增加 document_type、tables、_meta 等新字段默认值

## 7. 移除 v2 残留 & 清理

- [x] 7.1 检查 `analysis.py` 中是否还有 v2 降级路径残留，确认 v3-only 路径
- [x] 7.2 简化 `to_dict()` 中的 version 判断逻辑（只检查 v3）
- [x] 7.3 移除 `helpers.py` 中的 `_extract_packages_with_llm` 及相关导入

## 8. 集成测试 & 验证

- [x] 8.1 编写单元测试：table_parser 的每种表格类型识别和提取
- [x] 8.2 编写单元测试：包内容切分（单包/多包/混合内容）
- [x] 8.3 编写单元测试：文档分类器（文件名+正文组合测试）
- [x] 8.4 集成测试：使用 成都海关 和 德阳疾控 两份真实标书文件验证全流程
- [x] 8.5 验证 JSON 输出：确认 budget/extra/document_type/packages 等字段正确填充
