## MODIFIED Requirements

无 spec 级需求变更。本次变更为纯重构，不改变系统对外行为。

- 删除 `template_tables` 字段不改变分析结果的数据契约
- 渲染逻辑统一从 `template_content` 获取表格数据，输出结果不变
- 表格边框样式从默认变为黑色实线，属 UI 呈现优化
