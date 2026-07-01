# 标书内容质量管理 — 架构设计

> 更新日期: 2026-07-02 | 基于原则讨论重构

---

## 核心原则

| 原则 | 描述 |
|------|------|
| **主体优先** | 主体信息表已有数据直接使用，不做置信度校验 |
| **三级递进** | 主体→知识库→留白一页，逐级查找 |
| **表格原样** | 招标文件的表格完整复制到生成文档，再填充 |
| **纯LLM识别** | 占位符识别只用LLM，不用正则兜底 |
| **固定格式不降级** | 承诺函等固定模板填不了就留空，不走LLM改写 |
| **全量表格内容** | 报价一览表完整复制，产品信息匹配填充 |

---

## 架构总览

```
招标文件 (.docx)
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│                      分析阶段                                │
│  analysis_v3 pipeline                                       │
│  → analysis_data (JSON)                                     │
│     ├─ metadata (项目信息)                                   │
│     ├─ eligibility (资格要求)                                │
│     ├─ table_classification (表格分类 + 产品清单)             │
│     ├─ scoring (评分)                                        │
│     └─ packages (分包)                                       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                      生成阶段                                │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                章节分类器                            │    │
│  │  _classify_chapter_type()                           │    │
│  │  ① TEMPLATE_TEXT → 填空 (承诺函/声明函)              │    │
│  │  ② TEMPLATE_TABLE → 表格复制+填充 (报价一览表)       │    │
│  │  ③ QUALIFICATION → 三级递进 (主体→知识库→留白)      │    │
│  │  ④ FREE_WRITE → LLM 写作 (技术方案/描述)             │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    DOCX 组装                                 │
│  _build_docx_bytes()                                        │
│  → 封面 (bidder_notice 数据)                                │
│  → 目录                                                    │
│  → 正文 (分章节)                                            │
│  → 表格 (原样复制)                                          │
│  → 分页 (留白章节)                                          │
│  → 字体 (仿宋小四/宋体二号/宋体四号)                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 路径A：填空引擎 (TEMPLATE_TEXT)

### 流程

```
模板原文:
"本单位XXX（比选申请人名称）参加XXX（项目名称）的比选活动"

         │
         ▼
Step 1: LLM 识别占位符 (纯LLM，无正则兜底)
         Prompt: 泛化识别所有占位符格式
         → [{"raw": "XXX（比选申请人名称）", "start": 3, "end": 16, "hint": "公司名称"},
            {"raw": "XXX（项目名称）", "start": 19, "end": 28, "hint": "项目名称"}]
         │
         ▼
Step 2: 字段映射 (field_map → _TEMPLATE_FIELD_MAP)
         "公司名称" → subject.company_name
         "项目名称" → bidder_notice.project_name
         │
         ▼
Step 3: 确定性替换 (从右向左)
         "本单位成都智能科技有限公司参加2024年智能采购系统建设项目的比选活动"
         │
         ▼
Step 4: 原文锁定校验 (仅占位符被替换，原文不变)
         ✅ → 返回填充文本
         ❌ → 日志告警 + 返回填充后的文本 (保留未填充占位符)
             不降级到 LLM 生成 (防止改写固定格式)
```

### 关键规则

- **不可降级**：承诺函/声明函等固定格式，填不了就保留 `______`
- **字段映射表**：hint 关键词 → 数据源 → 取值方法
- **数据源优先级**：主体表直接读取 > 分析结果 > 计算值（如日期）

---

## 路径B：表格引擎 (TEMPLATE_TABLE)

### 流程

```
Step 1: 从分析阶段提取原始表格结构
         读取: analysis_data.table_classification.product_lists[].items[]
         字段名统一通过 PRODUCT_COLUMN_MAP 映射
         
Step 2: 在 docx 中重建表格
         - 表头行: 保持原标题和列数
         - 数据行: 完整复制招标文件的每一行
         
Step 3: 从产品库匹配填充
         对每个 product.name 检索产品库:
         - Chroma product_library 集合
         - LLM embedding 匹配
         - 取 score 最高的匹配结果
         
Step 4: 填充匹配到的字段到对应列空白
         匹配不到 → 保持空白
```

### 统一字段映射表

```python
PRODUCT_COLUMN_MAP = {
    "name": ["品名", "名称", "产品名称", "试剂名称", "货物名称",
             "商品名", "采购产品名称", "标的名称", "产品名"],
    "spec": ["规格", "规格型号", "型号", "技术规格", "参数",
             "★规格参数", "技术参数与性能指标", "规格参数"],
    "brand": ["品牌", "生产厂家", "厂家", "制造商"],
    "qty": ["数量", "需求量", "预估数量", "采购量", "★数量"],
    "unit": ["单位", "计量单位", "★计量单位"],
    "unit_price": ["单价", "预算单价", "最高限价", "★单价最高限价",
                   "单价最高限价"],
    "total_price": ["总价", "金额", "合计"],
    "产地": ["产地", "来源"],
    "备注": ["备注", "说明"],
}
```

---

## 路径C：资格证明文件插入引擎 (QUALIFICATION)

### 三级递进查找

```
对每个资格要求项:
  (如 "具有独立承担民事责任的能力（营业执照/法人证书/执业许可证）")
  
  Level 1 ─── 主体材料匹配 ──────────┐
    SubjectMaterialFile:              │
    type=BUSINESS_LICENSE             │
    → 有: 插入文档                     │  ← 找到即返回
    → 无: ↓                          │
                                      │
  Level 2 ─── 知识库检索 ────────────┤
    ChromaDB:                         │
    query="营业执照 法人证书"          │
    → 有: 插入文档                     │
    → 无: ↓                          │
                                      │
  Level 3 ─── 留白一页 ─────────────┤
    → 插入分页符                      │
    → 章节标题 + "本节无内容"         │
    → 日志记录缺失                    │
                                     │
  每个等级找到后即返回，不继续下级查找
```

### 分页符实现

```python
_EMPTY_PAGE_MARKER = "[[EMPTY_PAGE]]"

# _build_docx_bytes() 中的处理:
if content.strip() == _EMPTY_PAGE_MARKER:
    doc.add_page_break()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("（本节无内容）")
    run.font.color.rgb = RGBColor(180, 180, 180)
    run.font.size = Pt(14)
    doc.add_page_break()  # 下个章节从下一页开始
```

---

## 路径D：LLM 写作引擎 (FREE_WRITE)

保留现有逻辑，增强点：

- **置信度门控仅应用于知识库召回片段**，不应用于主体数据
- `_build_subject_material_context()` 不做置信度过滤
- `_filter_low_confidence_subject_materials()` 仅打标签，不清除文本

---

## 占位符识别 (纯LLM模式)

### 不再使用

| 组件 | 原因 | 替代方案 |
|------|------|---------|
| `_fallback_extract_placeholders()` | 正则无法穷尽占位符格式 | LLM 泛化识别 |
| `_FALLBACK_PLACEHOLDER_PATTERNS` | 同上 | 同上 |

### LLM Prompt 设计

```
你是一个占位符识别助手。
找出下面文本中所有需要填写的空白位置。

规则：
1. 只识别，不填充，不改写原文
2. 返回 JSON 数组格式
3. 每个元素包含：raw(占位符原文), start(起始字符位置), end(结束位置), hint(推测字段含义)

识别所有格式的占位符：
- XXX（字段名）格式：XXX（比选申请人名称）
- 下划线格式：______
- 混合格式：法定代表人：__________
- 隐式空白：比选日期：  年   月   日
- 方括号格式：【】
- 任何看起来需要填写的空白位置
```

### 保留的正则用途

```python
# 仅用于修正 LLM 返回的位置偏移量
def _correct_placeholder_positions(text, placeholders):
    """用正则重算 start/end，确保位置准确。"""
    for ph in placeholders:
        raw = ph.get("raw", "")
        # 在原文中查找 raw 的实际位置
        idx = text.find(raw, ph.get("start", 0))
        if idx >= 0:
            ph["start"] = idx
            ph["end"] = idx + len(raw)
    return placeholders
```

---

## 数据完整性检查

### 分析阶段 → 生成阶段数据传递

```
analysis_data JSON
  ├── bidder_notice.project_name     → 封面标的名称
  ├── bidder_notice.project_no       → 封面项目编号
  ├── eligibility.qualifications[]   → 资格项清单
  ├── table_classification.product_lists[].items[]
  │                                   → 报价表数据
  ├── format_requirements            → 目录骨架
  └── scoring.dimensions[]           → 评分维度

每个字段必须在 _extract_analysis_context() 中被正确提取，
并在对应的生成路径中被使用。
```

### 覆盖检查修正

对 QUALIFICATION 类型的章节，`_build_generation_coverage_snapshot()` 不应检查正文内容是否覆盖，而应检查：
1. 对应主体材料是否已上传
2. 知识库是否有匹配片段
3. 留白标记是否正确插入
