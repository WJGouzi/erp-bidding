## 1. 编码修复（P0 快速修复）

- [x] 1.1 修复 `chroma_files.py` 中 `extra_metadata` 的二次编码问题
- [x] 1.2 修复 `document_parser.py` 中 `decode("utf-8", errors="ignore")` 为 `errors="replace"`
- [x] 1.3 修复 `analysis.py` 中 `_extract_single_chunk` JSON 解析失败时增加预处理和容错

## 2. Phase 1: 元数据提取

- [x] 2.1 创建 `analysis_v3/__init__.py` 和 `phase1_metadata.py`
- [x] 2.2 实现元数据规则提取（项目编号、预算、日期等）
- [x] 2.3 实现元数据 LLM 补充提取
- [x] 2.4 编写 `schemas.py` 定义 JSON schema 和组装逻辑

## 3. Phase 3: 得分点拆解 + 分包分析

- [x] 3.1 实现 `phase3_scoring.py` 的评分表解析（表格优先）
- [x] 3.2 实现评分维度提取（检测★/▲/一般、主观/客观、子维度）
- [x] 3.3 实现分包技术参数统计 `extract_packages()`

## 4. Phase 2: 生死线扫描（骨架）

- [x] 4.1 实现 `phase2_eligibility.py` 的预设清单模板
- [x] 4.2 实现扫描逻辑（关键词匹配 + LLM 确认）

## 5. v3 入口编排

- [x] 5.1 实现 `analysis_v3/__init__.py` 的 `start_analyze_v3()` 总入口
- [x] 5.2 修改 `analysis.py` 的 `start_analyze()` 入口，v3 优先、v2 降级
- [x] 5.3 实现 `check_items.py` 从 eligibility + scoring 生成核对项
