## Context

经实际数据验证，当前管线的完整数据流如下：

```
解析 docx
  ↓
StructuredDocument (含 sections + 34 tables)
  ├── Phase1: metadata ✓           → metadata.budget.packages 正常
  ├── Phase1.5: format_requirements ✓ → 仅提取模板表（资格性模板正确）
  ├── table_classification 🚫 已移除 → 数据表全丢失
  ├── Phase3: segmented △          → 不提取技术/商务要求
  ├── Assembler → _comprehensive    → technical_requirements = []
  └── check-items                  → technical.items = []
```

**关键约束**：
- `format_requirements` 提取资格性文件模板的路径不动，`phase1_5_format.py` 不修改
- `table_classification` 只恢复数据表分类，不碰模板表
- 不引入 LLM，纯规则驱动

## Goals / Non-Goals

**Goals:**
- 恢复三类数据表分类：技术规格表（"标的名称|规格型号及技术要求"）、采购清单表（"标的名称|★单价限价"）、评分表（"评分因素|分值|评分标准"）
- 从分类结果注入到 `_comprehensive.technical_requirements`、`products`
- 补充评分表遗漏的维度（如"业绩"）
- 修复 `bidding_info` 分包预算降级
- `table_classification` 与 `format_requirements` 两条路径完全独立

**Non-Goals:**
- 不修改 `format_requirements` / `phase1_5_format.py` / 资格性文件模板提取
- 不修改 `segmented.py` 的分段逻辑
- 不修改 API 接口签名和响应 schema
- 不涉及数据库迁移

## Decisions

### Decision 1: 数据表分类器设计

**方案**：复活 `table_classifier.py`，精简为只识别三类数据表，表头规则硬编码：

| 类型 | 表头匹配条件 | 输出路径 |
|---|---|---|
| `TECH_REQUIREMENT` | 同时含"标的名称"+"规格型号" | `table_classification.tech_requirements[]` |
| `PRODUCT_LIST` | 同时含"标的名称"+"单价限价"（或"单价"） | `table_classification.product_lists[]` |
| `SCORING` | 同时含"评分因素"+"分值"+"评分标准" | `table_classification.scoring` |

**为什么用表头匹配而不是上下文推断？**
- docx 中三类数据表的表头非常规范且互斥（如"★单价限价"只出现在采购清单表）
- 表头匹配零成本、零误判、零 LLM
- 旧版 `table_classifier.py` 已有 `TYPE_TECH_REQUIREMENT`、`TYPE_PRODUCT`、`TYPE_SCORING` 三类定义和提取逻辑，可直接复用

**为什么不碰资格性模板？**
- 资格性模板走 `format_requirements` → `phase1_5_format.py`，从"第三章 投标文件格式"章节提取
- 数据表走 `table_classification`，从"第六章 项目技术、服务要求"章节提取
- 表头完全不同（资格性模板表头："招标要求|投标应答|响应情况"，数据表表头："标的名称|规格型号"），不会误分类

### Decision 2: 集成到管线

```
[修改] analysis_v3/__init__.py 中 Phase3 之后：

  原有:
    analysis_data.pop("table_classification", None)  ← 移除
  
  改为:
    # 数据表分类（独立于 format_requirements）
    from app.infrastructure.table_classifier import classify_all_tables
    tc_result = classify_all_tables(doc.tables)
    analysis_data["table_classification"] = {
        "tech_requirements": tc_result.get("tech_requirements", []),
        "product_lists": tc_result.get("product_lists", []),
        "scoring": tc_result.get("scoring", {}),
    }
```

### Decision 3: 注入 `_comprehensive`

在 `assembler.py` 的 `assemble()` 函数末尾（`_basic_merge` 之后）添加：

```
从 table_classification（通过 analysis_data 传入）:
  1. tech_requirements → 追加到 result.technical_requirements（去重）
  2. product_lists → 追加到 result.products
  3. scoring → 补充 result.scoring.dimensions（去重）
```

### Decision 4: 预算降级修复

同此前讨论的三级降级策略，详见 `design.md` 此前版本。

## Risks / Trade-offs

| 风险 | 概率 | 缓解 |
|---|---|---|
| 旧 `table_classifier.py` 代码质量低 | 中 | 只取分类和提取逻辑，去掉废弃/冗余代码 |
| 某些招标文件表格表头不规范，无法匹配 | 低 | 降级行为不变（空=空），不引入新 bug |
| 表头匹配与格式模板匹配混淆 | 极低 | 表头完全不同，且 `format_requirements` 有独立的章节定位逻辑 |
| `_comprehensive` 数据在两次管线运行间不一致 | 中 | `table_classification` 每次重新分类，`_comprehensive` 每次重新组装，幂等 |
