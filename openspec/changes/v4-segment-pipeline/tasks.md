# v4-segment-pipeline 任务

## 阶段 A：解析器三层安检门 ✅

- [x] A1: 加结尾标点拦截（安检门1）— document_parser.py
- [x] A2: 加连续同级别事后扫描（安检门2）— document_parser.py
- [x] A3: 加标题长度阈值（安检门3）— document_parser.py
- [x] A4: 对全部 10 个招标文件做解析回归测试，断言 0 连续空节点组

## 阶段 B：章节索引后处理 ✅

- [x] B1: 实现 clean_section_index 去重函数 — document_parser.py
- [x] B2: 集成到 build_section_index 返回路径 — document_parser.py
- [x] B3: 回归测试，验证空节点彻底消除

## 阶段 C：目录生成重构 ✅

- [x] C1: 定义 BID_SKELETON 8 章标准骨架 — catalog_inference.py
- [x] C2: 实现 HARD 项分级规则（独立/聚合/散落）— catalog_inference.py
- [x] C3: 实现骨架初始化 + 分析数据注入流程 — catalog_inference.py
- [x] C4: 实现空章节 availability 标注 — catalog_inference.py
- [x] C5: 按 bid_type 裁剪骨架 — catalog_inference.py
- [x] C6: 对 10 个文件做目录生成测试
- [x] 测试文件更新（14/14 通过）

## 阶段 D：集成测试

- [ ] D1: 全链路端到端测试（解析 → 分析 → 目录 → 生成）
- [ ] D2: 质量评估报告
