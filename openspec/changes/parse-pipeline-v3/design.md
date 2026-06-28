## Context

当前解析管线在 `analysis.py` 中实现，核心逻辑是 `_extract_structured_analysis_chunked()`：将全文按8000字符硬切，每块独立LLM提取固定5个字段，然后合并。存在三个根本性问题：

1. **文本硬切破坏逻辑单元** — 资格条件可能跨块，各块LLM看不到完整上下文
2. **固定schema无法适配** — 所有标书类型（GOODS/SERVICE/ENGINEERING）共用同一套5字段输出
3. **JSON解析失败静默丢数据** — 不重试、不回退、不通知

同时存在编码问题：`_save_doc_chunks` 将 `json.dumps` 后的字符串塞进 `db.JSON` 列导致二次编码，`document_parser` 的 `decode("utf-8", errors="ignore")` 静默丢弃字符。

## Goals / Non-Goals

**Goals:**
- 建立三层分析管线（metadata → eligibility → scoring + packages），替换当前单层扁平抽取
- v3 优先、v2 降级的平滑迁移路径，不破坏现有功能
- 修复编码问题和JSON解析容错
- 保持 `BiddingAnalysisResult` 表结构不变（只升级 `analysis_data` JSON schema）

**Non-Goals:**
- 不改动前端展示逻辑（前端按新JSON的字段路径消费即可）
- 不改动目录生成逻辑（本change只输出解析结果，目录消费留待后续）
- 不重写 `document_parser.py`（只修编码一行）
- 预设清单不做完整定义（只搭骨架框架，内容随实际标书迭代）

## Decisions

### D1: 新模块独立于旧代码，不侵入修改

```
app/service_modules/task_pipeline/
├── analysis.py          ← 改入口，v3优先，v2降级
├── analysis_v3/         ← 全新模块
│   ├── __init__.py
│   ├── phase1_metadata.py
│   ├── phase2_eligibility.py
│   ├── phase3_scoring.py
│   ├── schemas.py
│   └── check_items.py
```

理由：旧代码耦合度高（400行 analysis.py + 2500行 helpers.py），在旧文件里改容易出回归。新模块独立起步，等验证稳定后再考虑逐步取代。

### D2: Phase 1 规则提取优先，LLM 补充

元数据提取（项目编号、预算等）模式固定，先走正则规则。规则覆盖不到的字段再走LLM（temperature=0.0）。避免大模型"编造"元数据。

### D3: Phase 3 按章节切分而非按字数

复用 `StructuredDocument.sections` 的树形结构。每个 section 作为一个 LLM 调用单元，携带 parent 标题上下文。不再使用 8000 字符硬切。

### D4: JSON schema 从扁平文本升级为树形结构

- 旧版 v2：5个文本字段 + 版本号
- 新版 v3：metadata / eligibility / scoring / packages 子树，每层字段短且结构化
- 长文本内容（技术参数原文）保留在 `effective_text` 字段

### D5: JSON 解析增加容错层

在 `json.loads()` 之前做预处理：去掉 trailing comma、去掉控制字符。解析失败时先重试一次（告诉 LLM "格式有误请重新输出"），再失败则走 fallback 返回部分结果。

## Risks / Trade-offs

- [风险] Phase 3 逐章节调用LLM可能比旧方案更多次调用 → 但每节文本更短，总token数相近，且精度更高
- [风险] Phase 2 预设清单定义不全 → 先搭框架，清单内容通过迭代实际标书扩充
- [风险] v2 降级路径不执行新编码修复 → 但如果是降级，说明v3有问题，此时降级返回的旧格式至少能工作
- [风险] extra_metadata 编码修复需要确认 ChromaDB 端是否兼容 → 修复方向是从 Python 端发正确的 Unicode，ChromaDB HTTP API 应兼容

## Migration Plan

1. 创建 `analysis_v3/` 模块和文件结构
2. 实现 Phase 1（metadata extraction）
3. 实现 Phase 3（scoring + packages extraction）
4. 实现 Phase 2 骨架（eligibility scan）
5. 实现 `__init__.py` 编排入口 + v2 降级
6. 修改 `analysis.py` 入口指向 v3
7. 修复编码问题（chroma_files.py + document_parser.py）
8. 验证：跑一通旧标书，确认 v3 能产出有效结果
