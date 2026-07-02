# v4-segment-pipeline 设计

## 整体架构

```
招标文件 → 分段解析 (逐段独立分析) → LLM 组装器 → comprehensive_analysis.json
                                                             ↓
                        目录生成 (固定骨架 + 动态填充) → 目录树 (含 availability 标记)
                                                                       ↓
                        分段生成 (逐章: HARD/SOFT/FREE 三路路由) → docx (含置信度标记)
```

## 关键原则

1. 强制条款规则优先，LLM 兜底
2. 目录是"生"的不是"套"的 — 但骨架是预定义的
3. 废标条件不写进标书，只作为生成约束
4. 置信度贯穿全程，在最终 docx 中可视化

## 三层修复体系

基于 10 个真实招标文件的测试数据，发现并修复三层问题：

```
Layer 1: 解析器标题误判 (specs/heading-resolution-layer.md)
────────────────────────────────────────────────────────────────────
问题: 文本级标题检测将"内容列举"误判为章节标题 → 产生 92 个空节点
方案: 三道安检门（结尾标点 + 连续检测 + 长度阈值）→ 100% 消除

Layer 2: 章节索引后处理 (specs/heading-resolution-layer.md §3)
────────────────────────────────────────────────────────────────────
问题: 第一层漏网的空节点污染下游
方案: 去重后处理（同名有内容则删空洞）

Layer 3: 目录生成重构 (specs/catalog-generation-layer.md)
────────────────────────────────────────────────────────────────────
问题: HARD项污染顶层目录 / 目录结构不稳定 / 缺少关键章节
方案: 固定 8 章骨架 + 动态填充 + HARD 项分级聚合
```

## 详细规格

- 标题语义解析层: `specs/heading-resolution-layer.md`
- 目录生成层: `specs/catalog-generation-layer.md`

## 阶段规划

| 阶段 | 内容 | 涉及文件 |
|------|------|---------|
| A | 解析器三层安检门 | `document_parser.py` |
| B | 章节索引后处理 | `document_parser.py` + `section_extractor.py` |
| C | 目录骨架 + HARD分级 | `catalog_inference.py` |
| D | 集成测试（10个文件） | 全链路 |
