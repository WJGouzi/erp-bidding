## 移除 v2 降级路径 ✅

- [x] 1.1 修复 `BiddingAnalysisResult.to_dict()` — `version=="v2"` → `version in ("v2", "v3")`
- [x] 1.2 改造 `_complete_analysis()` — 去掉 v3→v2 降级，v3 失败直接报错
- [x] 1.3 增强 v3 兼容字段写入 — `overview` 现在包含项目名/编号/预算/包数/截止日期，`qualification_requirements` 从 3→10 条，`requirements` 填写评分摘要
- [x] 1.4 `select_package` — v3 已有全部分包数据，跳过 v2 再分析
