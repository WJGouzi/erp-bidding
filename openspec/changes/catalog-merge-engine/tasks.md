# 目录合并引擎 — 任务列表

## T1: 创建 specs 文档（P0 🔴）

- [ ] 1.1 创建 `specs/skeleton-building/spec.md` — 阶段1骨架构建详细规格
- [ ] 1.2 创建 `specs/scoring-merge/spec.md` — 阶段2评分合并详细规格
- [ ] 1.3 创建 `specs/detail-enrichment/spec.md` — 阶段3详情填充详细规格
- [ ] 1.4 创建 `specs/completeness-validation/spec.md` — 阶段4验证详细规格

## T2: 实现 _parse_format_tree()（P0 🔴）

- [ ] 2.1 从 required_sections 检测父级节点（`一、` `二、` 等）
- [ ] 2.2 将子项分配到对应父级下
- [ ] 2.3 保留 has_template 和 template_tables 元数据
- [ ] 2.4 处理边界：required_sections 为空、单章、无编号子项

## T3: 实现 _infer_skeleton_fallback()（P1 🟡）

- [ ] 3.1 从 document_chapters 关键词匹配推断骨架
- [ ] 3.2 keyword→section_name 映射表（报价、资格、技术、商务、评分）
- [ ] 3.3 章节去重

## T4: 实现 merge_scoring_sections()（P0 🔴）

- [ ] 4.1 实现 `_get_dimensions()` 兼容双格式
- [ ] 4.2 实现 `_is_covered()` 判断覆盖情况
- [ ] 4.3 实现 `_find_insert_position()` 确定插入位置
- [ ] 4.4 主观评分项 → 新增章节（不捏造子项）

## T5: 实现 enrich_section_details()（P1 🟡）

- [ ] 5.1 实现 `_fill_business_children()` 从 business.items 生成子项
- [ ] 5.2 实现 `_fill_tech_description()` 从 technical.items 填充描述
- [ ] 5.3 实现 `_fill_qualification()` 资格项去重+填充
- [ ] 5.4 实现 `_fill_compliance()` 实质性要求填充

## T6: 实现 validate_completeness()（P2 🟢）

- [ ] 6.1 实现 `document_chapters` 逐章验证
- [ ] 6.2 输出覆盖率警告

## T7: 集成到 _build_constrained_requirement_outline()（P0 🔴）

- [ ] 7.1 将合并引擎接入现有入口
- [ ] 7.2 保留旧函数作为降级路径
- [ ] 7.3 标记废弃的旧函数

## T8: 修复 save_review() 数据覆盖问题（P0 🔴）

- [ ] 8.1 在覆盖 `analysis["scoring"]` 时保留 `dimensions` 字段
- [ ] 8.2 或 `_get_dimensions()` 从 business+technical 重建

## T9: 测试验证（P1 🟡）

- [ ] 9.1 用 response_1782747402446.json 测试骨架解析
- [ ] 9.2 用 response_1782749789850.json 测试评分合并
- [ ] 9.3 完整目录输出验证（9大章+2新增+资格+其他）
- [ ] 9.4 降级路径测试（无 format_requirements 场景）
