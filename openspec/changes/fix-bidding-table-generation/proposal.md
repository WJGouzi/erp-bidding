## Why

标书生成过程中，表格（特别是资格性文件中的模板表格）存在三大严重问题：

1. **章节错位**：模板表格被生成在错误的章节（如「其他材料」），而非其所属的正确章节
2. **合并单元格丢失**：`merge_cells` 字段始终为空数组，表格没有合并单元格效果
3. **分析管线不可靠**：CHROMA 存储的旧文件无法重新解析（缓存删除后无回退），分析返回空结果导致生成失败

这些问题导致生成的标书严重不符合招标文件格式要求，属于废标风险。

## What Changes

### 修复项

1. **`phase1_5_format.py` — `_extract_template_tables`**
   - 新增 `merge_cells` 字段提取，将 ContentBlock 中的合并单元格信息保存到模板表格输出中
   - 表格结构改为 `headers + rows + merge_cells` 三元组

2. **`helpers.py` — 章节匹配逻辑**
   - 在 separator page 子节点循环中修复标题匹配算法（严格匹配优先，子串匹配降级），防止表格错位
   - 在 `_write_outline_item` 中同样加固标题匹配
   - 新增 `_strict_title_match` 辅助函数，优先全等匹配再降级到包含匹配

3. **`analysis_v3/__init__.py` — `start_analyze_v3` 文件加载逻辑**
   - CHROMA 存储的文件：删除缓存时保留 `_get_structured_doc_from_cache` 作为保底路径
   - 明确将 `_file_payload is None` 与 `_file_payload` 但无缓存两个场景区分开
   - 修复控制流：`if not doc:` 回退段确保正确处理

4. **`helpers.py` — `_extract_analysis_context`**
   - 加固 `raw_tables` 中 None 元素的防御

5. **移除全部 LOCAL 存储路径**
   - `domain/models.py`：移除 `local_path` 字段和相关代码
   - 全局搜索移除 `LOCAL` 存储 provider 的 fallback 路径

### 不变的内容

- 不改变 `skip_file_storage` 配置（已为 `False`）
- 不改变 `BiddingAnalysisResult.analysis_data` JSON schema（仅增加字段）
- 不改变 `assemble_v3_analysis_data` 签名

## Capabilities

### New Capabilities

- `table-merge-preservation`: 在模板表格提取和生成全链路中保留合并单元格信息
- `chapter-accurate-positioning`: 精确的章节定位，确保表格生成在正确的章节
- `storage-provider-minio-only`: 移除 LOCAL 路径，统一使用 MINIO 存储
- `analysis-reliability`: 提高 CHROMA 存储文件的重新分析可靠性

### Modified Capabilities

- 无

## Impact

- **分析管线**: `start_analyze_v3` 中 CHROMA 文件路径控制流修复，影响旧文件重新分析
- **表格提取**: `_extract_template_tables` 新增 `merge_cells`，影响 `format_requirements.required_sections[].template_content[].merge_cells`
- **标书生成**: 章节匹配算法改动，影响所有包含模板表格的章节的定位
- **存储模型**: `FileStorage.local_path` 列移除，需要数据库 migration
- **依赖**: 无新增依赖
