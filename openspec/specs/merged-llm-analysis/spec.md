# merged-llm-analysis Specification

## Purpose
TBD - created by archiving change optimize-analyze-speed. Update Purpose after archive.
## Requirements
### Requirement: 合并分包检测和结构化分析
`_extract_structured_analysis_with_llm` SHALL 在一次 LLM 调用中同时输出分包信息和结构化分析结果。

#### Scenario: 单次调用输出包信息
- **WHEN** 调用 LLM 进行结构化分析
- **THEN** 返回的 JSON SHALL 包含 `has_package` 和 `packages` 字段
- **AND** SHALL 包含原有的 `bidder_notice`、`business_requirements`、`technical_requirements`、`qualification_review`、`scoring_items` 等字段

### Requirement: 长文档分块并行提取
`_extract_structured_analysis_chunked` SHALL 使用 ThreadPoolExecutor 并发执行多个分块的 LLM 提取。

#### Scenario: 分块并发执行
- **WHEN** 招标文本超过 15000 字符触发分块
- **THEN** 各个分块 SHALL 使用 ThreadPoolExecutor(max_workers=3) 并发调用 LLM
- **AND** 所有分块完成后 SHALL 合并结果
- **AND** 合并逻辑与现有实现一致

### Requirement: 清除分段提取串行逻辑
`_complete_analysis` SHALL 不再调用 `_detect_package_info` 进行独立的分包 LLM 调用，因为分包信息已在合并分析中获取。

#### Scenario: 无独立分包检测
- **WHEN** `_complete_analysis` 执行
- **THEN** SHALL NOT 调用 `_detect_package_info`
- **AND** SHALL 直接从合并分析结果中读取 `has_package` 和 `packages` 字段

