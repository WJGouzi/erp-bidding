## 1. 正本定位修正

- [ ] 1.1 helpers.py pre-loop 中 tblpY 从 1400000 改为 406400（约 3949 行）
- [ ] 1.2 helpers.py pre-loop 无模板时 tblpY 从 1400000 改为 406400（约 4118 行）

## 2. 第二封面增加正本

- [ ] 2.1 helpers.py main loop 中第二封面渲染代码前插入"正本"浮动表格（完整复制 pre-loop 正本代码段，含绝对定位设置）
- [ ] 2.2 确认第二封面的 LLM 填充逻辑与第一封面一致（已存在，无需修改）

## 3. 删除 page_margins 死代码

- [ ] 3.1 document_parser.py 移除 page_margins 捕获代码（约 639-653 行）
- [ ] 3.2 analysis_v3/__init__.py 移除 page_margins 注入代码（约 624-631 行）

## 4. 验证

- [ ] 4.1 启动服务并触发分析+生成，确认无报错
- [ ] 4.2 检查生成的 docx 中正本定位正确、两封面均有正本、内容字段已填充
