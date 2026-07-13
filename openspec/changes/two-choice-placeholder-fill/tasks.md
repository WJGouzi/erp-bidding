## 1. ContentBlock 渲染路径集成（核心缺口）

- [x] 1.1 将 `two_choice_placeholders` 提取提升到 `_build_docx_bytes` 的更外层作用域，让 `_write_outline_item` 嵌套函数也能访问（当前 line 4088-4094 只对封面提取）
- [x] 1.2 在 ContentBlock 段落渲染处（line 4398-4402），渲染前调用 `_fill_two_choice_placeholders(text, section_title=title, two_choice_fills=_tc_fills_all)`
- [x] 1.3 当返回文本含 `**option**` 标记时，使用 `_add_run_with_bold` 代替 `run.add_run` 渲染加粗
- [x] 1.4 在 ContentBlock 表格单元格渲染处，对单元格文本同样调用 `_fill_two_choice_placeholders`（注意表格中不需要加粗标记，替换后去除 `**`）

## 2. 清理不必要的封面调用

- [x] 2.1 移除以 `_tc_fills_cover` 命名的变量，改为通用的 `_tc_fills_all`（封面和正文共用）
- [x] 2.2 封面渲染处（line 4154、4643、4684）的二选一调用可以移除（封面无 `（有、无）` 内容）

## 3. 加粗渲染一致性

- [x] 3.1 确保 `_add_run_with_bold` 在 ContentBlock 段落渲染时可被调用（已在 `_build_docx_bytes` 内作为嵌套函数定义，可直接使用）

## 4. 测试与验证

- [x] 4.1 验证模板章节（如承诺函、声明函）的 `（有、无）` 被正确替换为 `**无**` 并加粗渲染
- [x] 4.2 验证同章节多个不同二选一时 `text_snippet` 精确匹配生效
- [x] 4.3 运行现有测试确认无回归
