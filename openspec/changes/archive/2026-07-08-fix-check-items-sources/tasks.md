## 1. EXTRA_LABELS 键名修复

- [x] 1.1 将 `analysis_schema.py` 中 `EXTRA_LABELS` 的 `submission_location` 改为 `bid_submission_location`，`winner_count` 改为 `winner_count_text`，`submission_docs` 改为 `submission_docs_summary`
- [x] 1.2 补充 `file_purchase_price`、`bid_submission_location` 到 `EXTRA_LABELS`
- [x] 1.3 在 `assemble_business` 的 extra 处理中补充 `EXTRA_LABELS` 未覆盖但实际存在的 extra 字段（`winner_count_text`、`submission_docs_summary` 等）

## 2. 重写 assemble_business

- [x] 2.1 实现 `_comprehensive.business_requirements` 结构化列表提取
- [x] 2.2 实现 `table_classification.business_requirements` 表格提取
- [x] 2.3 实现 `table_classification.service_requirements` 服务要求表提取
- [x] 2.4 实现 `metadata.extra` 商务字段提取（遍历 `EXTRA_LABELS` + 补充字段）
- [x] 2.5 实现 DB 列 `result.business_requirements` 兜底
- [x] 2.6 实现文本去重（`seen` set）和优先级排序

## 3. 重写 assemble_technical

- [x] 3.1 实现 `_comprehensive.technical_requirements` 结构化列表提取
- [x] 3.2 实现 `table_classification.tech_requirements` 技术要求表提取
- [x] 3.3 实现 `table_classification.product_lists` 产品清单提取（含规格参数）
- [x] 3.4 实现 DB 列 `result.technical_requirements` 兜底（过滤占位文本）
- [x] 3.5 实现文本去重

## 4. 验证

- [x] 4.1 在现有招标文件上运行 check-items 接口，对比修改前后 business 和 technical 输出
- [x] 4.2 确认多源场景下去重逻辑正确
- [x] 4.3 确认所有结构化源为空时回退到 DB 列行为不变
- [x] 4.4 确认 save_review 前后端兼容
