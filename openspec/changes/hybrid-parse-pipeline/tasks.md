# 实施任务

## Phase 1：规则修复（纯修补，不动架构）

### Task 1: 前附表表头关键词放宽
- [ ] 修改 `app/infrastructure/table_parser.py` 的 `TYPE_SIGNATURES`
- [ ] PRELIMINARY_TABLE 的关键词从 `["内容", "说明与要求"]` 改为 `["说明"]`
- [ ] 增加 optional 列表：`["内容", "说明与要求", "说明和要求", "应知事项", "条款名称", "须知事项"]`
- [ ] 列数限制从 `min_cols=3, max_cols=3` 改为 `min_cols=2, max_cols=4`
- [ ] 验证：10份文档的前附表都能被识别

### Task 2: 分包检测增加 "采购包X" 格式
- [ ] 修改 `app/service_modules/task_pipeline/analysis_v3/phase1_metadata.py` 的 `_extract_rules`
- [ ] 新增规则：`("package_count", r"采购包(\d+)")`
- [ ] 修改 `__init__.py` 的 `_detect_package_count`：没找到显式声明时扫描全文"采购包X"
- [ ] 如果没有找到任何包引用，默认返回 `[1]`（视为单包项目）
- [ ] 验证：成华区2包、成都海关6包都能正确检测

### Task 3: KEY_MAP 扩展 + 模糊匹配
- [ ] 修改 `app/service_modules/task_pipeline/analysis_v3/phase1_metadata.py` 的 `_merge_preliminary_table`
- [ ] 增加 KEY_MAP 条目覆盖"交货"（匹配交货期/交货时间）、"验收"、"付款"等
- [ ] 验证：采购预算及最高限价★ → budget.total

### Task 4: parse_money 支持千分位
- [ ] 修改 `app/service_modules/task_pipeline/analysis_v3/phase1_metadata.py` 的 `parse_money`
- [ ] 先清除千分位逗号再提取数字
- [ ] 验证："1,033,302.36" → 1033302.36

### Task 5: 封面页购买人/代理正则增强
- [ ] 增加 "受 XXX 委托" 模式
- [ ] 增加 "XXX 共同编制" 模式（分割两家公司）
- [ ] 增加 "XXX公司、YYY公司共同编制" 模式（顿号分隔）
- [ ] 验证：资阳/成华区/成都海关 封面页都能提取

## Phase 2：LLM 模块（新增文件）

### Task 6: 创建 LLM 提取基础模块
- [ ] 新建 `app/service_modules/task_pipeline/analysis_v3/llm_extractor.py`
- [ ] 基础函数：`call_llm_json(prompt, max_tokens)` → 调用大模型API → 返回结构化 JSON
- [ ] 错误处理：超时重试1次、非JSON降级
- [ ] 新建 `app/service_modules/task_pipeline/analysis_v3/llm_prompts.py`
- [ ] 存放所有 prompt 模板

### Task 7: LLM 元数据提取（购买人/代理/预算）
- [ ] 实现 `llm_extractor.extract_metadata(doc_text, table_kv)`
- [ ] 输入：文档前3000字 + 前附表KV对
- [ ] 输出：购买人/代理名称、预算+分包分配、关键日期
- [ ] 验证：10份文档的购买人/代理覆盖率达到100%

### Task 8: LLM 评分表结构化
- [ ] 实现 `llm_extractor.extract_scoring(scoring_table_text)`
- [ ] 输入：评分表原始文本
- [ ] 输出：method/total_score/dimensions
- [ ] 验证：有评分表的文档都能提取

### Task 9: LLM 商务/技术要求抽取
- [ ] 实现 `llm_extractor.extract_business(section_text)`
- [ ] 实现 `llm_extractor.extract_technical(section_text)`
- [ ] 输入：对应章节文本
- [ ] 输出：结构化要求列表（含★标记、重要性）

## Phase 3：集成与测试

### Task 10: LLM 结果合并到现有管线
- [ ] 修改 `__init__.py` 的 `start_analyze_v3`
- [ ] 规则 Phase 1 完成后，调用 LLM 增强
- [ ] 实现 `_merge_llm_into_metadata()` 合并逻辑
- [ ] 规则有值 → 保留规则值（规则更精确）
- [ ] 规则空值 + LLM有值 → 用 LLM 值

### Task 11: 10份文档全量回归测试
- [ ] 运行所有10份招标文件通过解析管线
- [ ] 验证关键字段覆盖率 > 90%
- [ ] 记录每份文档的解析耗时和 token 消耗
- [ ] 对比修复前后的差异

### Task 12: 清理遗留问题
- [ ] 确认不再需要 v2 降级路径
- [ ] 删除现有关联的 v2 代码引用
- [ ] 更新 `analysis.py` 中的降级逻辑，失败时报错而不是降级
