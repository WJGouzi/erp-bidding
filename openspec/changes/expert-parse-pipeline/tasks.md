# 专家级解析管线 — 任务列表

## T1: 创建法规固定清单配置 (P0 🔴)

- [x] 1.1 创建 `config/presets/statutory_checklist.yaml`，包含至少 8 项法规固定检查项
- [x] 1.2 每项包含 id、category、requirement、law_ref、severity
- [x] 1.3 不依赖任何 bid_type 或 doc_type 预设

## T2: 创建信号词配置 (P0 🔴)

- [x] 2.1 创建 `config/presets/signal_words.yaml`，包含至少 6 个分类的信号词
- [x] 2.2 新增分类不需要改代码

## T3: 实现法规清单加载与匹配 (P0 🔴)

- [x] 3.1 在 `phase2_extractor.py` 实现 `_load_statutory_checklist()`，启动时加载 YAML
- [x] 3.2 实现 `_verify_statutory_items()`，在文档中验证每项是否提及
- [x] 3.3 未提及的 stat 项标记为 "attention"

## T4: 实现章节定位 v2 (P0 🔴)

- [x] 4.1 在 `phase2_extractor.py` 实现 `_find_qualification_sections_v2()`，评分机制
- [x] 4.2 标题信号词加权（高/中/低三级权重）
- [x] 4.3 干扰章节扣分机制
- [x] 4.4 内容关键密度加分
- [x] 4.5 80% 阈值筛选资格章节集

## T5: 实现动态提取与归类 (P0 🔴)

- [x] 5.1 实现 `_extract_requirements_from_sections()`，从资格章节按行扫描
- [x] 5.2 实现 `_classify_by_signal()`，用 signal_words.yaml 归类
- [x] 5.3 实现废标条件和 ★ 条款检测

## T6: 实现合并逻辑 (P1 🟡)

- [x] 6.1 实现 `_build_eligibility()`，合并 statutory + dynamic
- [x] 6.2 实现 fallback 扫描（无资格章节时全文搜索）
- [x] 6.3 去重

## T7: 修复 classify_document() (P1 🟡)

- [x] 7.1 在 `phase1_metadata.py` 中增加"竞争性磋商"关键词
- [x] 7.2 文件名判定增加"竞争性磋商"

## T8: 集成到 start_analyze_v3() (P1 🟡)

- [x] 8.1 新 Phase 2 不再接收 doc_type 参数
- [x] 8.2 新旧两版并行跑，对比输出
- [x] 8.3 验证新输出不劣于旧输出后切换到新版

## T9: 清理旧代码 (P2 🟢)

- [x] 9.1 删除 `ELIGIBILITY_TEMPLATES` 所有内容
- [x] 9.2 删除 `_get_template()` 函数
- [x] 9.3 删除 `CHAPTER_TEMPLATES`（不再需要）
- [x] 9.4 删除旧 `_find_qualification_sections()` 及其 title_targets

## T10: 验证测试 (P2 🟢)

- [x] 10.1 对 10 份测试标书验证资格章节定位准确率 ≥ 90%
- [x] 10.2 验证分类准确率 ≥ 85%
- [x] 10.3 验证 doc_type 不影响资格提取结果
- [x] 10.4 验证 API 返回的资格清单字段完整

## T11: 章节提取商务字段 (P0 🔴)

- [x] 11.1 在 `phase1_metadata.py` 实现 `_find_section_by_title()` 递归章节查找
- [x] 11.2 实现 `_section_content_to_text()` 章节内容提取（段落+表格）
- [x] 11.3 定义 `SECTION_TO_EXTRA` 章节标题→extra字段映射表（至少10个映射）
- [x] 11.4 实现 `extract_business_from_sections()` 主函数
- [x] 11.5 集成到 `extract_metadata()`：章节结果优先，regex 结果补漏

## T12: 章节提取技术要求 (P1 🟡)

- [x] 12.1 实现 `extract_technical_from_sections()`，定位"技术要求"子章节
- [x] 12.2 从技术子章节中提取参数表（完整保留表格结构）
- [x] 12.3 集成到 `_complete_analysis()` 回填 technical_requirements

## T13: 验证测试 (P2 🟢)

- [x] 13.1 对 10 份测试标书验证商务字段填充率
- [x] 13.2 验证误匹配率降低 ≥ 80%
- [x] 13.3 验证表格内容完整保留
