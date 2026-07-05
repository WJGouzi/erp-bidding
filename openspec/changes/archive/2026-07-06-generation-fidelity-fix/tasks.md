# 生成保真修复 — 任务列表

---

## 批次 1：核心保真修复（P0 🔴）

### T1: 目录净化

- [x] 1.1 删除 `catalog.py` 中 `("评分|评选|评审", "评分响应")` 映射规则
- [x] 1.2 删除 `catalog.py` 中硬编码的 `"评审": {...}` 模板推断
- [x] 1.3 删除 `catalog.py` 中 `_classify_check_items()` 的 scoring 分类展开
- [x] 1.4 重构 `_build_package_aware_outline()`：required_sections 为唯一源，其他源降级为后备
- [x] 1.5 删除 `catalog_inference.py` 中 `"评分响应"` 硬编码
- [x] 1.6 新增 `_validate_catalog_against_format()` 校验函数
- [x] 1.7 清理模板库中"评分响应"等多余模板条目

### T2: 模板绑定器（新建模块）

- [x] 2.1 新建 `app/service_modules/task_pipeline/template_binder.py`
- [x] 2.2 定义 `TemplateBinding`、`ContentBlock`、`Placeholder` 数据结构
- [x] 2.3 实现 `bind_template()` — 检测章节是否有模板
- [x] 2.4 实现 `extract_placeholders()` — 从模板中提取占位符
- [x] 2.5 实现 `resolve_fill()` — 按 field_name 从主体/知识库/产品库查询填充值
- [x] 2.6 实现 `fill_content()` — 复制模板并填充占位符（段落）
- [x] 2.7 实现 `fill_table_content()` — 复制表格并填充空缺单元格

### T3: 内容生成接入两阶段

- [x] 3.1 修改 `_generate_chapter_content()` 返回值扩展为支持 ContentBlock
- [x] 3.2 接入 template_binder：优先尝试模板绑定
- [x] 3.3 template_bound 分支：template_binder.fill_content()
- [x] 3.4 no_template 分支：LLM 生成（保持现有逻辑不变）
- [x] 3.5 修改 `_build_chapter_contents_from_records()` 支持传递 content_blocks

---

## 批次 2：质量完善（P1 🟡）

### T4: 缺失归位

- [x] 4.1 删除 `helpers.py` 中 `_write_missing_requirements_page()` 函数
- [x] 4.2 删除 `_build_docx_bytes()` 中对 `_write_missing_requirements_page()` 的调用
- [x] 4.3 在 `_generate_chapter_content()` 返回值中增加 `has_gaps` 和 `gap_details` 字段
- [x] 4.4 docx 组装阶段：每章节写入后如果 has_gaps，追加空行 + 提示

### T5: 封面修复

- [x] 5.1 实现 `_find_cover_template()` — 从 format_requirements 中查找封面模板
- [x] 5.2 实现 `_build_standard_cover()` — 使用招标文件封面模板
- [x] 5.3 实现 `_build_fallback_cover()` — 自有封面模板，填充关键标识字段
- [x] 5.4 修改 `_build_docx_bytes()` 中的封面生成逻辑

### T6: 表格桥梁与混合排版

- [x] 6.1 在 `analysis_data.format_requirements.required_sections[].content_blocks[]` 中定义 table 类型结构
- [x] 6.2 实现 `_write_structured_table()` — 将结构化表格写入 docx Table（含合并单元格）
- [x] 6.3 修改 `_build_docx_bytes()` 中的章节写入逻辑：优先使用 content_blocks
- [x] 6.4 在模板绑定器的 fill_content() 中支持表格填充

---

## 批次 3：安全网与验证（P2 🟢）

### T7: 内容状态检测

- [x] 7.1 实现 `classify_content_state()` 检测函数
- [x] 7.2 在 template_binder.fill_content() 中集成状态检测
- [x] 7.3 EMPTY/PLACEHOLDER 走填充逻辑
- [x] 7.4 FILLED 锁定，跳过

### T8: 测试

- [x] 8.1 单元测试：template_binder — 有模板/无模板检测
- [x] 8.2 单元测试：template_binder — 占位符提取与填充
- [x] 8.3 单元测试：template_binder — 表格填充
- [x] 8.4 单元测试：内容状态检测 — EMPTY/PLACEHOLDER/FILLED 分类
- [x] 8.5 单元测试：目录净化 — 无多余章节
- [x] 8.6 单元测试：缺失归位 — 无独立补齐板块
- [x] 8.7 集成测试：选一份招标文件全流程运行，验证生成结果
- [x] 8.8 回归测试：已有测试全部通过
