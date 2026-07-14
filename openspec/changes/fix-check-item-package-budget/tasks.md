## 1. 恢复数据表分类器

- [x] 1.1 重构 `table_classifier.py`，精简为只识别三类数据表（TECH_REQUIREMENT、PRODUCT_LIST、SCORING），按表头匹配，不碰资格性模板
- [x] 1.2 在 `analysis_v3/__init__.py` 中移除 `analysis_data.pop("table_classification")`，在 Phase3 之后调用分类器写入 `analysis_data.table_classification`
- [x] 1.3 验证三类表在成都海关 docx 上正确识别：技术规格表（9个表649行）、采购清单表（9个表654行）、评分表

## 2. 打通 `_comprehensive` 组装

- [x] 2.1 在 `assembler.py` 的 `assemble()` 中从 `table_classification.tech_requirements` 提取数据，合并到 `_comprehensive.technical_requirements[]`
- [x] 2.2 从 `table_classification.product_lists` 提取数据，合并到 `_comprehensive.products[]`
- [x] 2.3 从 `table_classification.scoring` 补充缺少的评分维度到 `_comprehensive.scoring.dimensions[]`

## 3. 修复分包预算

- [x] 3.1 修改 `bidding_info.py` 的 `_extract_budget()`，增加从 `budget.packages[str(no)]` 取包预算的降级逻辑
- [x] 3.2 修改 `analysis.py` 的 `get_packages()`，返回每个包的 `budget` 字段

## 4. 验证 check-items 模块

- [x] 4.1 确认 `technical.py` 的 `_collect_from_comprehensive` 路径在 `_comprehensive` 有数据后能正确读取
- [x] 4.2 在 `technical.py` 添加各数据源条目数日志
- [x] 4.3 确认 `scoring` 模块的 `technical` 数组包含完整的评分维度（含"业绩"等）
- [x] 4.4 确认 `format_requirements` 和资格性模板提取未受影响
