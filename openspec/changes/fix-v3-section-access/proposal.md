# 修复 v3 管线章节解析问题

Status: ✅ Complete

## Root Cause

文档解析器 `_parse_docx_structured()` 创建虚拟 `__root__` Section（level 0），
所有真实章节通过 `stack[-1].children.append()` 附加为 `__root__` 的子节点，
导致 `doc.sections` 永远为空。
`to_dict()` 序列化空 sections → 缓存 JSON 丢失所有章节结构 → v3 从缓存加载时得到空文档。

此外正则规则缺少 `采购项目名称`、`比选人` 等常见变体。

## Changes Made

### 1. `document_parser.py` — 修复章节嵌套
- 当栈中仅剩 `__root__` 时，新章节直接加入 `doc.sections` 而非 `__root__.children`
- 修复标题样式和文本检测两处相同的逻辑

### 2. `phase1_metadata.py` — 增强正则规则
- 新增 `采购项目名称`、`比选人`、`比选代理机构` 匹配
- 新增 `。` 边界字符、`据实结算` 预算模式、`递交比选申请书截止时间` 等

### 3. `__init__.py` — raw_text 回退机制
- 元数据提取：无章节时自动取 raw_text 前 80 行
- 分包检测：传递 raw_text 作为回退文本
- 生死线扫描/评分：章节为空时用 raw_text 构建临时章节

## Verification
- ✅ 86 个单元测试全部通过
- ✅ 德阳疾控文档：project_name/purchaser/agent/package_count(=3) 正确提取
- ✅ 成都海关文档：project_name/budget(1015万)/package_count(=9) 正确提取
