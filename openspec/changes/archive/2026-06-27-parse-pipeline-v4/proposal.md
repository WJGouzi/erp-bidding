## Why

parse-pipeline-v3 上线验证后暴露以下问题：

1. **编码问题**：doc_parse_cache 和 doc_chunks 表中的中文文本出现乱码，`ensure_ascii=True` 的陈旧写法未彻底清理
2. **依赖 LLM 做基础解析**：Phase 1 用 LLM 补充元数据、Phase 2 用 LLM 扫描、Phase 3 用 LLM 分析包参数 — 导致每次分析需 2-3 次 LLM 调用，速度慢且不稳定（JSON解析错误频发）
3. **固定模板 vs 动态解析**：当前解析是"固定清单扫一遍"，而专家会先看生死线、再逐章节拆评分点、最后横向定策略
4. **分包感知不足**：分包检测后只记录包号，没有按包独立处理生死线和评分
5. **PDF 支持不完整**：现有 fitz 解析骨架但章节结构化提取不完善
6. **表格解析单一**：只支持 python-docx 的 Grid 表格，文本型表格（用空格/制表符对齐的）无法解析

## What Changes

### 核心改造
- **零LLM基础解析**：Phase 1 去掉 `_llm_extract_remaining`，全部规则提取；Phase 2 去掉 LLM 参数，纯关键词匹配；Phase 3 去掉 LLM 包参数分析，纯文本统计
- **三层专家架构重构**：`analysis_v3/` 模块按"生死线→拆解→策略"重写
- **分包感知处理**：按包号分别执行 Phase 2+3

### 修复
- **全链路编码修复**：所有 json.dumps 加 `ensure_ascii=False`，StructuredDocument.to_json() 统一编码，_save_parse_cache 存储链路清理
- **JSON 预处理加固**：统一 `_preprocess_json()` 函数处理 trailing comma 和控制字符
- **document_parser 编码统一**：全部 `errors="replace"`，移除 `errors="ignore"`

### 新增能力
- **纯文本表格检测**：识别用空格/制表符对齐的文本表格
- **目录生成**：从解析结果自动生成文档目录结构
- **PDF 结构化增强**：完善 fitz 的章节树构建
- **DOC 兼容加固**：降级解析的异常处理

### 测试
- 单元测试：encoding、JSON容错、纯规则模式
- 集成测试：真实DOCX + PDF

## Impact

- `app/service_modules/task_pipeline/analysis_v3/` — 全部模块重写
- `app/service_modules/task_pipeline/analysis.py` — v3入口简化（不再需要v2降级）
- `app/infrastructure/document_parser.py` — 编码修复 + PDF增强
- `app/service_modules/chroma_files.py` — 编码链路修复（已部分修）
- `tests/analysis_v3/` — 增补测试
- `app/domain/models.py` — 不改表结构

## Risk

- 零LLM解析可能导致部分非标准标书提取内容减少 — 但第一次确保"有内容"比"可能有错"更重要，LLM可保留作为Phase3可选增强
- PDF 结构化依赖 fitz 的文本布局分析，扫描件页仍需 OCR 降级 — 当前保留 OCR 通路且不作为阻塞项
