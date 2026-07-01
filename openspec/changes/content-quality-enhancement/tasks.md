# 标书内容质量管理 — 任务（重构版）

> 更新日期: 2026-07-02 | 基于全链路代码审查 + 原则确认

---

## 核心原则

1. **主体优先**：主体信息表已有数据直接使用，不做置信度校验
2. **三级递进**：主体→知识库→留白一页，逐级查找
3. **表格原样**：招标文件的表格完整复制到生成文档，再填充
4. **纯LLM识别**：占位符识别只用LLM，不用正则
5. **固定格式不降级**：承诺函不可走LLM改写
6. **全量表格内容**：报价一览表完整复制，产品信息匹配填充

---

## 已完成的修复

| 任务 | 文件 | 说明 |
|------|------|------|
| ✅ 字体设置 | `helpers.py` | 正文仿宋小四、H1宋体二号、H2宋体四号 |
| ✅ product_lists 产品库链路 | `helpers.py` | `_build_product_context()` + `_extract_analysis_context()` |
| ✅ 报价表使用 product_lists | `helpers.py` | `_extract_table_data_from_analysis()` |
| ✅ 置信度不阻断 | `helpers.py` | 仅打标签不清除主体资料文本 |
| ✅ XML 控制字符全覆盖 | `helpers.py` | 5处新增 `_strip_xml_control_chars()` |
| ✅ 封面字段多级兜底 | `helpers.py` | project_name/project_no 三层回退 |
| ✅ 目录 children title 兜底 | `catalog.py` | `_build_qualification_section()` 新增文本匹配 |

---

## P0 — 核心架构修复

### Z01 三级递进查找：主体→知识库→留白

**问题**：资格证明文件生成只查主体材料，不查知识库，空内容不真正分页

**涉及**：
- `helpers.py:_generate_qualification_content()` — 增加 `knowledge_contexts` 参数
- `helpers.py:_build_leaf_response_bindings()` — 增加知识库 evidence 路径
- `helpers.py:_EMPTY_PAGE_MARKER` — 改为分页符机制
- `helpers.py:_build_docx_bytes()` — 处理 `_EMPTY_PAGE_MARKER` 时插入分页符
- `generate.py:_complete_generate()` — 传递 `knowledge_contexts` 给资格章节

**验收标准**：
- [ ] 资格项先在主体材料中查找
- [ ] 主体没有 → 去知识库检索
- [ ] 知识库也没有 → 留白一页（分页符 + 章节标题 + 空白说明）
- [ ] 下个章节从下一页开始

---

### Z02 产品列表修复：表格原样 + 产品库填充

**问题**：
1. `_extract_table_data_from_analysis()` 硬编码字段名，两个解析系统的输出不兼容
2. 报价一览表没有"原样复制表格"机制
3. 产品信息没有走产品库匹配填充

**涉及**：
- `helpers.py:_extract_table_data_from_analysis()` — 使用统一字段映射表
- `helpers.py` — 新增全局 `PRODUCT_COLUMN_MAP` 统一字段映射
- `helpers.py` — 新增 `_match_products_from_library()` 产品库匹配
- `table_parser.py:_map_product_headers()` — 使用统一映射表
- `table_classifier.py:_extract_table_data()` — 使用统一映射表
- `helpers.py:_extract_analysis_context()` — 保留原始表格结构（headers + rows）
- `helpers.py:_build_docx_bytes()` — 新增 `_write_table_from_data()` 支持原样表格

**验收标准**：
- [ ] 招标文件中的报价一览表完整复制到生成文档
- [ ] 表头、列数、行数与原始表格一致
- [ ] 产品名称能匹配到产品库信息
- [ ] 匹配到的规格、单价等信息填充到对应空白
- [ ] 匹配不到的产品列保持空白

---

### Z03 纯LLM占位符识别

**问题**：`_identify_placeholders_via_llm()` 同时使用 LLM + 正则兜底，正则无法穷尽所有格式

**涉及**：
- `helpers.py:_identify_placeholders_via_llm()` — 移除正则兜底，只保留 LLM 识别
- `helpers.py:_fallback_extract_placeholders()` — 标记为废弃
- `helpers.py:_FALLBACK_PLACEHOLDER_PATTERNS` — 移除
- LLM prompt 增加泛化识别能力（覆盖方括号、尖括号、隐式空白等）

**验收标准**：
- [ ] 占位符识别完全依赖 LLM
- [ ] 正则仅做 start/end 位置修正，不做兜底提取
- [ ] LLM 能识别 XXX（）、______、【】、<> 等格式
- [ ] LLM 无法识别时，直接保留原文，不降级到正则

---

### Z04 承诺函等固定格式不可降级

**问题**：`_fill_template()` 中 `_verify_template_diff()` 失败后降级到 LLM 生成，有废标风险

**涉及**：
- `helpers.py:_fill_template()` — 移除降级逻辑
- `helpers.py:_verify_template_diff()` — 不通过时仅日志告警 + 保留原文

**验收标准**：
- [ ] 承诺函填充失败 → 保留原文，占位符保持 `______` 状态
- [ ] 不调用 LLM 改写
- [ ] 日志记录 "章节XX有X个占位符未填充"
- [ ] 不阻断整体生成流程

---

## P1 — 数据链路修复

### Z05 分析阶段数据完整传递

**问题**：`analysis_data` 中的结构化数据（`eligibility.qualifications[]`, `table_classification.product_lists[]` 等）没有被生成阶段充分利用

**涉及**：
- `helpers.py:_extract_analysis_context()` — 验证所有结构化字段正确提取
- `helpers.py:_build_generation_coverage_snapshot()` — 对 QUALIFICATION 类型章节特殊处理（不检查正文内容，检查附件证据）
- `helpers.py:_build_docx_bytes()` — cover 字段使用 `bidder_notice` 数据

**验收标准**：
- [ ] `analysis_data.eligibility.qualifications[]` 被生成阶段正确读取
- [ ] 30项遗漏问题得到解决
- [ ] 封面标的名称、编号正确填充

---

### Z06 主体公司名称

**问题**：`SubjectCompany.company_name` 存的是 "erp"（系统注册名）

**涉及**：
- `domain/models.py` / 主体管理前端 — 增加 "投标用公司全称" 字段
- `helpers.py:_build_subject_material_context()` — 读取全称字段

**验收标准**：
- [ ] 用户可在主体管理设置投标用全称
- [ ] 标书生成时使用全称填充公司名称占位符

---

## P2 — 增强优化

### Z07 主体附件内容读取验证

- [ ] 检查 `_read_file_text()` 在各存储模式下是否正常工作
- [ ] 检查 `StorageService.read_bytes()` → `DocumentParser.parse_bytes()` 链路
- [ ] 检查 ChromaDB 降级路径是否正常

### Z08 正文格式增强

- [ ] 正文内容去重（`_split_generated_sections_by_titles()`）
- [ ] 业绩表插入"请根据实际情况填写"提示
- [ ] 偏离表从 `tech_requirements` 提取逐条数据

---

## 修复优先级

```
P0 ─── Z01 三级递进查找 ─── 资格项覆盖率的根本问题
  ├── Z02 产品列表修复  ─── 报价表内容缺失的根本问题
  ├── Z03 纯LLM占位符   ─── 占位符识别质量
  └── Z04 固定格式不降级 ─── 废标风险

P1 ─── Z05 分析数据传递 ─── 30项遗漏问题
  └── Z06 公司全称     ─── 基础数据问题

P2 ─── Z07 附件内容验证 ─── 主体资料可信度
  └── Z08 正文格式增强 ─── 美观性
```
