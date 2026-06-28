## Why

当前标书解析管线将结构化文档拍平成纯文本后按8000字硬切分块，再通过LLM提取到固定5个字段中。这种方式导致：(1)章节逻辑被切碎，跨块线索丢失；(2)固定schema无法适配不同标书类型的差异；(3)JSON解析失败时静默丢数据；(4)没有按标的类型动态调整分析策略的能力。需要重构为三层分析框架，保留固定字段提取的稳定性，同时支持按标书内容动态分析。

## What Changes

- **新增**: `analysis_v3/` 模块，实现三层分析管线（metadata → eligibility → scoring + packages）
- **修改**: `analysis.py` 入口，v3 优先，失败降级到 v2
- **重构**: `BiddingAnalysisResult.analysis_data` JSON schema 从扁平文本升级为结构化树形
- **修复**: `_save_doc_chunks` 中 `extra_metadata` 的二次编码问题
- **修复**: `document_parser.py` 中 `decode("utf-8", errors="ignore")` 改为 `errors="replace"`
- **修复**: `_extract_single_chunk` 中 JSON 解析失败时不重试不回退的问题

## Capabilities

### New Capabilities
- `metadata-extraction`: 从标书前部章节提取项目名称、编号、预算、日期、采购人、代理机构等固定字段
- `eligibility-scan`: 按 bid_type 预设清单对标书中的资格要求、废标条件、★实质性条款进行扫描
- `scoring-breakdown`: 识别并结构化解析评分表，区分客观/主观评分维度，检测子维度
- `package-analysis`: 对分包项目按包号分别统计技术参数（★/▲/一般）和核心产品
- `strategy-generation`: 跨包横向分析，输出竞争策略和资源配置建议

### Modified Capabilities
- `bidding_analysis_result`: analysis_data JSON schema 从扁平文本升级到结构化树形（v3）

## Impact

- `app/service_modules/task_pipeline/analysis.py`: 入口改造，v3优先
- `app/service_modules/task_pipeline/`: 新增 analysis_v3/ 目录
- `app/domain/models.py`: 不改表结构，只升级 JSON schema
- `app/service_modules/chroma_files.py`: 修复 extra_metadata 编码
- `app/infrastructure/document_parser.py`: 修复 decode errors
- `openspec/changes/parse-pipeline-v3/`: 此 change 的文档
