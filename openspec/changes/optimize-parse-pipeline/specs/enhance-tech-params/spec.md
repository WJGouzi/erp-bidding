# 技术参数表格提取增强

## 目标
当 `extract_packages()` 在章节标题不含"技术参数"但内容含技术规格表的场景中，通过表格头探测正确识别并提取结构化参数。

## 影响文件
- `app/service_modules/task_pipeline/analysis_v3/phase3_scoring.py` — `extract_packages()` 和 `_find_tech_section()`

## 现状

`_find_tech_section()` 仅通过章节标题关键词定位。但有些标书把技术参数放在混合章节中（如"采购项目技术、服务要求"），标题不含"技术参数"但内容表格包含品名/规格/数量等列，导致漏提取。

## 通用性设计

### 表头关键词的选择依据

`TECH_TABLE_HEADERS` 选取的是**中文采购文档中产品规格表的通用列名**：

| 关键词 | 出现场景 | 通用性 |
|--------|---------|--------|
| 序号 | 任何列表式表格的第一列 | ⭐⭐⭐ |
| 品名/产品名称 | 货物/物资/设备清单 | ⭐⭐⭐ |
| 规格/型号 | 技术规格描述 | ⭐⭐⭐ |
| 数量 | 采购数量 | ⭐⭐⭐ |
| 单位 | 计量单位 | ⭐⭐⭐ |
| 技术参数/技术指标 | 技术要求描述 | ⭐⭐⭐ |

这些关键词不依赖任何特定项目或行业，在任何货物类采购文档的参数表中都会出现。

## 改动内容

### 方案：两阶段探测

**第一阶段：标题匹配（当前逻辑，保留）**

```python
TECH_TITLE_TARGETS = ["技术参数", "技术规格", "技术要求", "技术需求"]
```

**第二阶段：内容表格探测（新增）**

当标题匹配找不到时，遍历章节内容，检测包含技术规格表的表格：

```python
TECH_TABLE_HEADERS = [
    "序号",           # 通用列名
    "品名", "产品名称", "名称",  # 名称类
    "规格", "型号",   # 规格类
    "数量",           # 数量类
    "单位",           # 单位类
    "技术参数", "技术指标",  # 技术类
]
```

检测逻辑（通用，不限文档类型）：
1. 检查章节内容块是否为 `type == "table"`
2. 检查表头是否与 `TECH_TABLE_HEADERS` 有交集（至少 2 列匹配）
3. 检测通过 → 提取表格行数据 → 结构化为 `params.table_items`
4. 检测不通过 → 保留现有行为（params = {}）

### 新增函数

```python
def _detect_tech_table(headers, rows):
    """探测表格是否为技术参数表（通用表头关键词匹配）。"""
    if not headers:
        return False
    matched = sum(1 for h in headers if any(kt in h for kt in TECH_TABLE_HEADERS))
    return matched >= 2

def _parse_tech_table(headers, rows):
    """将技术参数表行解析为结构化条目（通用行转 dict）。"""
    items = []
    for row in rows:
        entry = dict(zip(headers, row + [""] * (len(headers) - len(row))))
        items.append(entry)
    return items
```

### `_find_tech_section()` 修改

两阶段定位：标题匹配优先 → 表格内容探测为 fallback

```python
def _find_tech_section(sections):
    # Phase 1: 标题匹配（现有逻辑）
    for section in sections:
        if _title_matches(section, TECH_TITLE_TARGETS):
            return section
    # Phase 2: 内容表格探测（新增 fallback）
    for section in sections:
        for block in getattr(section, "content", []):
            if block.type == "table" and _detect_tech_table(block.headers, block.rows):
                return section
    return None
```

## 验收标准
1. 包含产品规格表（品名/规格/数量列）的标书，对应包的 `parameters.table_items` 包含结构化数据
2. 不含产品规格表的标书不受影响（params 保持原有行为）
3. 非货物类标书（纯服务/工程）不会误触发（服务类表格列名通常不含"品名/规格/型号"）
4. 标题已含"技术参数"的章节仍然优先通过标题匹配

## 不包含
- 不改动数据库 schema
- 不改动 Phase 1/Phase 2
- 不更改 `packages` JSON 顶层结构，只在 `parameters` 子字段中增加 `table_items`
- 不处理图片/扫描件中的表格（仅处理 python-docx 可识别的原生表格）
