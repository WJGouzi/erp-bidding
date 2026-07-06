## Context

标书生成管线包含三个阶段：分析（analysis_v3）→ 上下文提取（_extract_analysis_context）→ 生成（_build_docx_bytes）。表格问题涉及所有三个阶段：

- **分析阶段**：`_extract_template_tables` 提取表格时不保存 `merge_cells`，导致下游丢失合并信息
- **提取阶段**：`_extract_analysis_context` 中 `raw_tables` 含 None 元素导致 AttributeError
- **生成阶段**：章节标题匹配使用模糊子串匹配，导致表格错误归属到其他章节
- **存储层面**：旧文件以 CHROMA 存储（无 MinIO 备份），缓存删除后无法重新解析
- **模型层面**：`FileStorage` 仍有 `local_path` 字段，但实际已无 LOCAL 存储路径

### 当前架构

```
Upload → FileStorage (MINIO/CHROMA)
  → DocParseCache (解析缓存)
    → analysis_v3.start_analyze_v3
      → phase1_5_format._extract_template_tables (merge_cells 缺失)
      → analysis_data JSON (含 format_requirements)
        → generate._complete_generate
          → _extract_analysis_context
          → _build_docx_bytes._write_outline_item (章节匹配问题)
          → separator page child loop (双重写表逻辑)
```

## Goals / Non-Goals

**Goals:**
- 修复 `_extract_template_tables` 保存 `merge_cells` 数据
- 修复章节标题匹配算法，防止表格错位
- 修复 CHROMA 存储旧文件的重新分析路径
- 修复 `_extract_analysis_context` 的 NoneType 错误
- 移除全部 LOCAL 存储路径和模型字段

**Non-Goals:**
- 不改变分析 JSON schema 结构（仅补充字段）
- 不引入新的存储后端或第三方依赖
- 不改变 `assemble_v3_analysis_data` 函数签名
- 不改动 LLM 分析管线（`llm_extractor.py` 相关）

## Decisions

### D1: merge_cells 存储方式 — 表结构内联字段

**决定**: 在 `_extract_template_tables` 输出的每个表格 dict 中直接添加 `merge_cells` 字段

**理由**:
- 当前表格结构为 `{"headers": [...], "rows": [...]}`，`merge_cells` 是与 `headers`/`rows` 同级的数据
- 下游 `_write_outline_item` 和 separator child 循环已从 `_block.get("merge_cells", [])` 读取该字段
- 无需新增 wrapper 对象或修改下游读取代码

**放弃的方案**:
- 使用 `per_cell` 格式：虽可完整表示合并信息，但 `_build_per_cell` 已存在且未被调用，且下游大部分代码使用 `headers+rows+merge_cells` 三元组
- 新增独立 `merge_cells` 表：过度设计，仅单个字段没必要引入关联表

### D2: 章节匹配算法 — 严格优先+标题清理降级

**决定**: 标题匹配采用「全等 → 去前缀全等 → 标题词元交集 → 子串包含」四级降级

**理由**:
- 当前使用 `title in cc_title or cc_title in title` 纯子串匹配，导致「一、供应商基本情况表」可以错误匹配到「供应商基本情况表」
- 序号前缀（"一、""1.""（一）"）是标题最显著的差异，第一步清理后做全等比较即可覆盖大部分场景
- 四级降级确保不会过度严格导致匹配失败

### D3: CHROMA 文件重新分析 — 缓存保留+主动回退

**决定**: 即使 CHROMA 文件无 MinIO 备份（`_get_file_payload` 返回 None），也不删除缓存，直接从缓存恢复

**理由**:
- `_get_structured_doc_from_cache` 优先从 `DocParseCache` 读取，这是 CHROMA 文件的唯一数据源
- 旧逻辑先删除缓存再尝试重新解析，但 CHROMA 文件无法获取原始字节，导致缓存删了就没了
- 改为先读取缓存，仅当 `_get_file_payload` 有返回值时才删除缓存强制重新解析

**放弃的方案**:
- 在分析入口统一将 CHROMA 记录转为 MINIO：涉及上传逻辑修改，范围过大
- 不删除缓存直接覆盖：`DocParseCache` 已有 `upsert` 逻辑

### D4: LOCAL 移除 — 模型字段标记废弃 + 代码搜索替换

**决定**: 从 `FileStorage` 模型移除 `local_path` 列，搜索替换所有 LOCAL 相关条件分支

**理由**:
- 代码中已无 LOCAL 存储的实际写入路径（`storage.save_bytes` 只写 CHROMA 或 MINIO）
- 遗留的 `local_path` 和 `storage_provider == "LOCAL"` 分支是死代码，影响可读性和维护

## Risks / Trade-offs

- **[旧文件重新分析]** 如果用户上传的文件之前是 `skip_file_storage=True` 上传的（仅 CHROMA），且 DocParseCache 因手动操作已删除，则仍无法重新分析。**缓解**: 提示用户重新上传文件
- **[标题匹配过严格]** 四级降级的全等匹配可能对标题有细微差异（如全角/半角空格）时失败。**缓解**: `_clean_title` 已经处理了常见乱码和空格压缩
- **[merge_cells 数据膨胀]** 每个表格的 merge_cells 信息增加了 analysis_data JSON 的体积。**缓解**: merge_cells 数组很小（每个合并单元格一条记录），影响可忽略
- **[数据库 migration]** 移除 `local_path` 列需要 migration 脚本。**缓解**: 使用 `ALTER TABLE file_storage DROP COLUMN local_path` 简单操作
