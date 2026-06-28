# Tasks

## 修复文档解析器章节嵌套
- [x] 修复 `_parse_docx_structured()` 中 `__root__` 吞没章节的 bug
  - 修改点：标题检测和文本标题检测两处 `stack[-1].children.append` → 当栈中仅剩 `__root__` 时直接 `doc.sections.append`

## 增强正则规则
- [x] 新增 `采购项目名称` 匹配（原仅匹配`项目名称`或`采购名称`）
- [x] 新增 `比选人`、`比选代理机构` 匹配变体
- [x] 新增 `。` 作为名称提取的边界字符
- [x] 新增 `据实结算` 预算模式
- [x] 新增 `递交比选申请书截止时间`、`评审方法` 等模式

## 新增 raw_text 回退机制
- [x] `_text_from_first_sections()` 接受 `raw_text` 参数，无章节时取 raw_text 前80行
- [x] `_text_from_all_sections()` 接受 `raw_text` 参数，无章节时返回 raw_text
- [x] `_detect_package_count()` 传递 raw_text 给文本提取函数
- [x] `start_analyze_v3()` 从 source_texts 读取 raw_text，章节为空时构建临时章节

## 验证
- [x] 86 个现有单元测试全部通过
- [x] 德阳疾控文档：project_name/ purchaser/ agent/ package_count(=3) 全部正确提取
- [x] 成都海关文档：project_name/ project_code/ agent/ budget(1015万)/ package_count(=9) 全部正确提取
- [x] 序列化/反序列化测试通过
