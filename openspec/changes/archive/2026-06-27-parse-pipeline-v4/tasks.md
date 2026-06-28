## P0: 编码修复（全链路）✅

- [x] 1.1 审计所有 `json.dumps()` 调用，确保全部使用 `ensure_ascii=False`
- [x] 1.2 修复 `_save_parse_cache` 中 `parsed_json` 的字节/字符串混用问题
- [x] 1.3 统一 `document_parser.py` 中所有 `decode()` 使用 `errors="replace"`
- [x] 1.4 新增 `safe_json_loads` + `preprocess_json()` 统一清洗函数

## P0: 零LLM基础解析 — Phase 1 规则增强 ✅

- [x] 2.1 移除 `phase1_metadata.py` 中的 `_llm_extract_remaining`
- [x] 2.2 增强规则集（覆盖标点变体、多格式、换行场景）
- [x] 2.3 多规则交叉验证（同字段多条规则取优先匹配）

## P0: 零LLM基础解析 — Phase 2 生死线扫描 ✅

- [x] 3.1 重写 `phase2_eligibility.py`：移除所有 LLM 调用
- [x] 3.2 增强预设清单模板（GOODS/SERVICE/ENGINEERING 三套模板）
- [x] 3.3 章节智能定位（先标题→后内容→排除目录干扰）

## P0: 零LLM基础解析 — Phase 3 评分拆解 ✅

- [x] 4.1 重写 `phase3_scoring.py`：移除所有 LLM 调用
- [x] 4.2 纯文本表格检测（|分隔/tab分隔/制表符字符）
- [x] 4.3 评分维度解析增强（灵活表头匹配、子维度提取）
- [x] 4.4 纯规则包参数统计（★/▲ 计数）

## P0: 三层专家架构重构 ✅

- [x] 5.1 重写 `analysis_v3/__init__.py`：三层编排（生死线→评分→策略）
- [x] 5.2 分包感知流程
- [x] 5.3 简化 `analysis.py` 入口

## P1: PDF 兼容 + DOCX 表格位置修复 ✅

- [x] 6.1 无OCR时扫描页降级处理（标记而非跳过）
- [x] 6.2 DOCX 表格位置感知分配（修复所有表分配到最后一个章节的 bug）
- [x] 6.3 PDF 文本表格检测方法

## P1: 测试覆盖 ✅

- [x] 7.1 86 单元测试 + 12 集成测试（共98个）
- [x] 7.2 真实 DOCX 管线全通（14章/153项生死线/4维评分/9包检测）

## P1: 策略分析生成 ✅

- [x] 8.1 从三层输出自动生成策略（包优先级 + 撰写重点）
- [x] 8.2 纯规则策略（不依赖LLM）
