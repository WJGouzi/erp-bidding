# 标书内容质量管理 — 任务

## Step 1：填空引擎改造（P0）

### 1.1 章节分类器
- [x] 实现 `_classify_chapter_type()` 函数
  - 返回 `TEMPLATE_TEXT | TEMPLATE_TABLE | QUALIFICATION | FREE_WRITE`
  - 基于标题+关键词+招标原文联合判断

### 1.2 LLM 占位符识别
- [ ] 实现 `_identify_placeholders_via_llm(template_text)` 函数
  - 调用 LLM 识别所有占位符位置 + 字段 hint
  - 返回 `[{"raw": "...", "start": N, "end": N, "hint": "..."}]`
- [ ] 设计 LLM prompt，确保只识别不填充
- [ ] 实现正则兜底：当 LLM 不可用时的降级方案

### 1.3 字段映射与确定性替换
- [x] 实现 `_build_template_field_map()`（已实现）
- [x] 实现 `_fill_template()`（已实现，使用 LLM 识别结果）
- [ ] 实现 `_verify_template_diff()` 替换前后逐位对比校验

### 1.4 集成
- [x] 集成到 `_generate_chapter_content`（已集成）
- [ ] 替换正则 `_extract_placeholders` 为 LLM 识别 + 正则兜底

---

## Step 2：表格填充引擎（P1）

### 2.1 表格模板检测
- [ ] 实现 `_detect_table_template(chapter_title, chapter_desc, tender_text)`
  - 检测表格类章节（报价一览表、偏离表等）
  - 返回 `is_table: bool, table_structure: dict`

### 2.2 表格结构提取
- [ ] 从招标原文中提取表格结构（列名、行数）
- [ ] 从 `technical_requirements` / `business_requirements` 中提取填充数据

### 2.3 docx 表格生成
- [ ] 实现 `_build_table_in_docx(doc, table_data)` 函数
  - 使用 python-docx 生成带样式的真表格
  - 表头加粗 + 网格线
- [ ] 集成到 `_build_docx_bytes` 中

---

## Step 3：资格证明文件插入引擎（P1）

### 3.1 要求清单提取
- [ ] 从 `qualification_requirements` / `qualification_review` 中提取需提交文件清单
- [ ] 实现关键词 → material_type 映射（营业执照→BUSINESS_LICENSE 等）

### 3.2 资料匹配与插入
- [ ] 实现 `_match_qualification_materials(requirements, subject_context)` 函数
  - 对照 SubjectMaterialFile 检查每项是否已上传
  - 已上传 → 准备插入参数
  - 未上传 → 记录缺失项

### 3.3 docx 插入
- [ ] 在 docx 对应位置插入资格证明资料
  - 有扫描件图片 → 插入图片
  - 有文本摘录 → 插入文本
  - 无资料 → 标记"待人工补充"

---

## Step 4：置信度门控（P2）

### 4.1 置信度评分
- [ ] 定义 `ConfidenceScore` 数据结构
- [ ] 各数据源实现置信度计算方法

### 4.2 门控规则
- [ ] OCR 文本置信度 < 0.7 → 不入 prompt
- [ ] 召回相关性 RRF score < 0.3 → 丢弃
- [ ] 字段格式校验（信用代码 18 位等）

### 4.3 LLM 输出校验
- [ ] 承诺一致性检测
- [ ] "可疑内容清单"生成

---

## 交付物清单

| 交付物 | 状态 | 所属 Step |
|--------|------|----------|
| 章节分类器 + 填空引擎 | ✅ 初版 / 🔄 改造中 | Step 1 |
| LLM 占位符识别 | 📝 待实现 | Step 1 |
| 字段映射与替换 | ✅ 已实现 | Step 1 |
| 表格填充引擎 | 📝 待实现 | Step 2 |
| 资格证明文件插入 | 📝 待实现 | Step 3 |
| 置信度门控 | 📝 待实现 | Step 4 |
| docx 控制字符修复 | ✅ 已上线 | 基础设施 |
