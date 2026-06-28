# 解析管线优化 — 任务列表

## T1: to_dict v3 路径修复 (P0 🔴)

- [x] 1.1 在 `BiddingAnalysisResult.to_dict()` v3 分支补充 overview、requirements、business_requirements 等 7 个独立字段
- [x] 1.2 验证 API 返回包含这些字段且内容非空

## T2: 表格矩阵分类模块 (P0 🔴)

- [x] 2.1 创建 `app/infrastructure/table_classifier.py`：定义 TABLE_CLASSIFIER_RULES（5 种表格类型）
- [x] 2.2 实现 `classify_table()` 和 `classify_all_tables()` 函数
- [x] 2.3 实现 `_extract_table_data()`：前附表→键值对，产品清单→列表，评分表→维度
- [x] 2.4 在 `start_analyze_v3()` 中调用表格分类，结果传入各 Phase

## T3: 前附表→metadata 融合 (P1 🟡)

- [x] 3.1 在 `phase1_metadata.py` 的 `extract_metadata()` 中集成前附表键值对映射
- [x] 3.2 表格提取值优先于 regex 提取值

## T4: 章节定位规则增强 (P1 🟡)

- [x] 4.1 扩展 `_find_qualification_sections()` 的 title_targets（增加比选特有章节名）
- [x] 4.2 扩展 `_find_scoring_section()` 的 targets（增加评审方法、比选办法等）

## T5: 预设清单双层分类 + 章节模板 (P1 🟡)

- [x] 5.1 `ELIGIBILITY_TEMPLATES` 扩展为 `_BASE` + `bid_type` + `doc_type` 三层合并
- [x] 5.2 `scan_eligibility()` 增加 `doc_type` 参数
- [x] 5.3 新增 `CHAPTER_TEMPLATES` 章节结构模板
- [x] 5.4 `start_analyze_v3()` 传递 doc_type 到 Phase 2

## T6: 技术参数表格提取增强 (P1 🟡)

- [x] 6.1 实现 `_detect_tech_table()` 和 `_parse_tech_table()`
- [x] 6.2 `_find_tech_section()` 两阶段：标题匹配 → 内容表格回退
- [x] 6.3 利用 T2 表格分类结果写入 `packages[].parameters.table_items`

## T7: 商务字段提取率提升 (P2 🟢)

- [x] 7.1 补充 extra 字段的正则变体
- [x] 7.2 budget→pricing_rule 回退推断

## T8: 验证测试 (P2 🟢)

- [x] 8.1 验证表格分类准确率（至少 3 份不同采购方式文档）
- [x] 8.2 验证 metadata 字段填充率提升
- [x] 8.3 验证 to_dict() v3 路径返回独立字段

## T9: 后续优化（未完成，迭代项）

- [ ] 9.1 采购人/代理正则补充：覆盖政府采购一体化平台的"采购人信息"格式（在"投标邀请"章节中）
- [ ] 9.2 前附表金额提取优化："采购预算及最高限价★"等多行文本值中，只提取金额数字（如"1,033,302.36元"）
- [ ] 9.3 评分表合并单元格处理：处理表17等前1-2行为汇总信息、实际数据从第3行开始的结构
- [ ] 9.4 `_merge_preliminary_table` 的 `parse_money` 解析器支持千分位格式（如"1,033,302.36"）
