# 目录生成改造 — 设计

> 本文档描述了从"打补丁"到"建体系"的架构升级方案。
> 核心思想：用数据契约 + 置信度系统 + 校验门，取代隐式假设和静默失败。

---

## 现状问题：隐式假设链

```
当前系统存在 N 条隐式假设，每条都可能静默失败：

phase1_metadata.py         下游 reader
  project_code = ""  ────→  meta.get("project_code", "")  
  (字符串)                    (期望字符串)                 ✅ 巧合一致

  KEY_MAP:
  project_code → metadata["project_code"]["value"]  
  (期望字典)    但实际 project_code = ""                ❌ 静默丢弃!  

analysis.py                 业务字段
  biz_parts 只查7个字段 ──→  section_extractor 能提16个  ❌ 漏字段

  result.overview 写死 ──→  analysis_data 后续更新       ❌ 数据不一致

schema.py                  phase1_metadata.py
  NULL_METADATA            _build_metadata 初始化
  project_name = ""         project_name = ""            ✅ 巧合一致
  (但 KEY_MAP 期望 dict)    (但 KEY_MAP 期望 dict)        ❌ 双重静默失败
```

每条箭头都是一个断裂点，且全部**静默**——没有告警、没有日志、没有错误。

---

## 改造后架构：契约驱动 + 置信度标记

```
                        ┌──────────────────────────────┐
                        │      Central Schema          │
                        │  (唯一数据契约，所有模块遵守)  │
                        │                              │
                        │  metadata.project_name:      │
                        │    type: str                 │
                        │    required: true            │
                        │    confidence: 0.0~1.0       │
                        │    source: "regex|llm|table" │
                        └────────────┬─────────────────┘
                                     │
          ┌──────────────────────────┼──────────────────────────┐
          ▼                          ▼                          ▼
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│  Phase 1        │       │  Phase 2        │       │  Phase 1.5     │
│  元数据提取     │       │  资格扫描       │       │  格式要求提取   │
│                 │       │                 │       │  (新增)         │
│  输出：带置信度 │       │  输出：带置信度 │       │                 │
│  的 metadata    │       │  的 eligibility │       │  输出：结构化   │
│                 │       │                 │       │  模板约束       │
└────────┬────────┘       └────────┬────────┘       └────────┬────────┘
         │                        │                        │
         ▼                        ▼                        ▼
         ┌──────────────────────────────────────────────────────┐
         │              Validation Gate（新增）                  │
         │                                                      │
         │  1. 类型校验：每个字段的类型与 Schema 声明一致        │
         │  2. 置信度阈值：关键字段 confidence < 0.5 时告警     │
         │  3. 覆盖率报告：缺失/低置信字段列表                  │
         │  4. 阻断严重错误：类型不匹配直接抛异常（不再静默）    │
         │                                                      │
         │  输出：validated_data + issues[]                      │
         └──────────────────────┬───────────────────────────────┘
                                ▼
              ┌─────────────────────────────────────┐
              │      分析结果存储（analysis_data）    │
              │                                     │
              │  消除所有 legacy 副本字段，改为      │
              │  实时计算的 property 视图            │
              └──────────────┬──────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
    catalog 生成        check-items API     内容生成
    (读 schema)        (读 schema)         (读 schema)
    + 格式约束          + 置信度展示        + 模板注入

```

---

## 一、Central Schema：中央数据契约

### 1.1 字段定义

每个字段附带类型、是否必须、置信度阈值：

```python
# app/domain/analysis_schema.py
"""中央数据契约：所有解析模块必须遵守"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum

class ConfidenceLevel(Enum):
    HIGH = 0.9      # 规则精确匹配（如 regex 直接命中）
    MEDIUM = 0.7    # 多源交叉验证（regex + 表格都提取到）
    LOW = 0.4       # 单源不确定匹配（如 LLM 提取，置信不足）
    UNKNOWN = 0.0   # 未提取到

@dataclass
class FieldMetadata:
    """每个提取字段的元数据包装"""
    value: Any = None
    confidence: float = 0.0
    source: str = ""           # "regex:rule2" / "table:预算金额" / "llm"
    raw_match: str = ""        # 匹配到的原文片段
    fallback_attempted: List[str] = field(default_factory=list)

@dataclass
class MetadataSchema:
    # ── 必须字段（confidence < 0.5 时阻断流程） ──
    project_name: FieldMetadata = field(default_factory=FieldMetadata)
    project_code: FieldMetadata = field(default_factory=FieldMetadata)
    purchaser: FieldMetadata = field(default_factory=FieldMetadata)
    agent: FieldMetadata = field(default_factory=FieldMetadata)
    
    # ── 重要字段（confidence < 0.3 时告警） ──
    budget_total: FieldMetadata = field(default_factory=FieldMetadata)
    bid_deadline: FieldMetadata = field(default_factory=FieldMetadata)
    bid_type: FieldMetadata = field(default_factory=FieldMetadata)
    
    # ── 商务条款（动态扩展） ──
    extra: Dict[str, FieldMetadata] = field(default_factory=dict)

@dataclass
class AnalysisSchema:
    """完整的分析结果契约"""
    metadata: MetadataSchema = field(default_factory=MetadataSchema)
    eligibility: Dict[str, List[Dict]] = field(default_factory=dict)
    scoring: Dict[str, Any] = field(default_factory=dict)
    packages: List[Dict] = field(default_factory=list)
    format_requirements: Optional[Dict] = None  # Phase 1.5
    issues: List[str] = field(default_factory=list)  # 校验报告
```

### 1.2 为什么用 dataclass 而非 dict

| 特性 | 当前 dict 方案 | Schema 方案 |
|---|---|---|
| 类型约束 | 无（任何字段可放任何类型） | 编译时类型检查 |
| 字段可见性 | 隐式（不知道有哪些字段） | 显式（所有字段一目了然） |
| 默认值 | 分散在多个文件的多个 dict 中 | 集中在一处定义 |
| 空值语义 | 无法区分"没提取到"和"值为空" | confidence=0 明确表示未提取 |

---

## 二、Confidence System：置信度系统

### 2.1 每字段的置信度标注

当前做法：
```python
# 旧：无声失败
meta["project_code"] = ""  # 为空，但不知道是没提取到还是值为空
```

新做法：
```python
# 新：带置信度
meta["project_code"] = FieldMetadata(
    value="CG20250099",
    confidence=0.95,
    source="regex:rule2",
    raw_match="比选编号：CG20250099"
)
```

### 2.2 置信度判定规则

| 提取方式 | 基础置信度 | 提升条件 | 最终置信度 |
|---|---|---|---|
| Regex 精确匹配 | 0.8 | + 表格交叉验证 | 0.95 |
| | | + 规则优先级最高 | 0.9 |
| 表格 KV 匹配 | 0.7 | + 与 regex 一致 | 0.95 |
| | | + 单独命中 | 0.7 |
| LLM 增强 | 0.5 | + 规则/表格未命中 | 0.5 |
| | | + 多轮验证一致 | 0.7 |
| 章节提取 | 0.6 | + 章节标题强相关 | 0.8 |
| | | + 子章节内容匹配 | 0.7 |
| 未提取到 | 0.0 | 记录所有尝试过的 fallback | 0.0 |

### 2.3 下游消费策略

```python
def get_catalog_options(task_id):
    schema = AnalysisSchema.from_dict(analysis_data)
    
    # 关键字段置信度检查
    REQUIRED_CONFIDENCE = {
        "project_name": 0.5,    # < 0.5 时生成"待补充"标记
        "project_code": 0.5,
        "packages": 0.3,        # 包数据要求略低
    }
    
    issues = schema.validate(thresholds=REQUIRED_CONFIDENCE)
    if issues:
        # 记录问题但不阻断（让用户决定）
        logger.warning("[catalog] 数据质量问题: %s", issues)
    
    # 置信度影响目录描述
    if schema.metadata.project_name.confidence < 0.5:
        section["description"] += "（项目名称待确认）"
```

---

## 三、Validation Gate：校验门

### 3.1 校验时机

```
解析完成 → Validation Gate → 存入 analysis_data → 下游消费
              ↑
         必须通过，不可跳过
```

### 3.2 校验规则

```python
class ValidationGate:
    """校验门：确保分析结果满足最低质量要求"""
    
    CRITICAL_FIELDS = {
        "project_name": {"min_confidence": 0.3, "type": str},
        "project_code": {"min_confidence": 0.3, "type": str},
        "purchaser": {"min_confidence": 0.3, "type": str},
        "budget_total": {"min_confidence": 0.3, "type": (int, float)},
    }
    
    def validate(self, schema: AnalysisSchema) -> List[str]:
        issues = []
        for field_name, rules in self.CRITICAL_FIELDS.items():
            field = getattr(schema.metadata, field_name, None)
            if field is None:
                issues.append(f"CRITICAL: {field_name} 字段缺失")
                continue
            
            # 类型校验
            if not isinstance(field.value, rules["type"]):
                issues.append(
                    f"TYPE_MISMATCH: {field_name} 期望 {rules['type'].__name__}, "
                    f"实际 {type(field.value).__name__} = {repr(field.value)}"
                )
            
            # 置信度校验
            if field.confidence < rules["min_confidence"]:
                issues.append(
                    f"LOW_CONFIDENCE: {field_name} "
                    f"confidence={field.confidence} < {rules['min_confidence']}, "
                    f"source={field.source}"
                )
        
        return issues
```

### 3.3 阻断 vs 告警

| 严重级别 | 条件 | 处理方式 |
|---|---|---|
| CRITICAL | 类型不匹配 / 必须字段缺失 | 阻断流程，抛异常，记录原文上下文 |
| WARNING | 置信度低于阈值 | 写入 issues 列表，消费端可选展示 |
| INFO | 字段提取方式、fallback 路径 | 仅日志 |

---

## 四、消除 Legacy 副本

### 4.1 当前的问题

```sql
-- BiddingAnalysisResult 表有 N 个独立字段
-- 每个都是 analysis_data 的手工快照
-- 没有机制保证它们与 analysis_data 一致
overview         VARCHAR   -- 从 metadata 手工拼
business_requirements TEXT  -- 从 metadata.extra 手工拼
technical_requirements TEXT  -- 从 packages 手工拼
scoring_items    TEXT       -- 从 scoring 手工拼
packages_json    TEXT       -- 从 packages 手工拼
```

### 4.2 修复方案

逐步淘汰 legacy 字段，改为实时计算：

```python
# Phase 1: 读取端改为计算属性
class BiddingAnalysisResult:
    @property
    def overview(self):
        """实时从 analysis_data 计算，无需存储"""
        data = self.safe_analysis_data()
        meta = data.get("metadata", {})
        
        # 兼容新旧两种 metadata 格式
        project_name = self._safe_read(meta, "project_name", "")
        project_code = self._safe_read(meta, "project_code", "")
        budget = self._safe_read(meta, "budget", {})
        if isinstance(budget, dict):
            budget_total = budget.get("total", 0)
        else:
            budget_total = budget
        
        parts = [f"项目: {project_name} (编号: {project_code})"]
        if budget_total:
            # 格式化逻辑只此一处
            if budget_total % 10000 == 0:
                parts.append(f"预算: {budget_total // 10000}万元")
            else:
                parts.append(f"预算: {budget_total / 10000:.2f}万元")
        return " | ".join(parts)
    
    @property
    def business_requirements(self):
        """实时计算，消除副本"""
        data = self.safe_analysis_data()
        meta = data.get("metadata", {})
        extra = meta.get("extra", {}) if isinstance(meta, dict) else {}
        biz_parts = []
        for field_key, field_label in EXTRA_LABELS:
            val = extra.get(field_key, {})
            if isinstance(val, dict):
                v = val.get("value", "")
            else:
                v = val
            if v:
                biz_parts.append(f"{field_label}：{v}")
        return "\n".join(biz_parts) if biz_parts else "暂未提取到商务要求。"
    
    @staticmethod
    def _safe_read(meta, key, default=""):
        """兼容新旧两种格式：dict {value: x} 和 直接值 x"""
        val = meta.get(key, default)
        if isinstance(val, dict):
            return val.get("value", default)
        return val if val else default
```

### 4.3 迁移计划

| 步骤 | 操作 | 兼容性 |
|---|---|---|
| 1 | 新增 `@property` 视图 | 旧代码继续读 DB 字段 |
| 2 | 消费端逐步改用 property | 无感切换 |
| 3 | DB 字段标记 deprecated | 写入停用 |
| 4 | 清理 DB 字段 | 下一版本 |

---

## 五、Phase 1.5：格式要求提取（新增）

### 5.1 定位

```
Phase 1 (元数据) → Phase 1.5 (格式要求) → Phase 2 (资格) → Phase 3 (评分)
                     ↑
                 全新模块
```

### 5.2 提取内容

```python
@dataclass
class FormatRequirement:
    """招标文件中规定的响应文件格式要求"""
    chapter_title: str                    # "第三章 比选申请文件格式"
    required_sections: List[ReqSection]   # 必须包含的章节列表
    template_tables: List[TemplateTable]  # 固定模板表格
    fixed_texts: List[FixedText]          # 固定文字要求
    
@dataclass
class ReqSection:
    title: str                            # "响应函"
    order: int                            # 在格式章节中的序号
    required: bool = True                 # 是否必须
    has_template: bool = False            # 是否有固定模板
    
@dataclass
class TemplateTable:
    section_ref: str                      # 所属章节
    headers: List[str]                    # 表头
    rows: List[List[str]]                 # 模板行
    description: str = ""                 # 表格说明

@dataclass
class FixedText:
    section_ref: str                      # 所属章节
    text: str                             # 固定文本内容
    position: str = "start"               # 章节中的位置
```

### 5.3 提取策略

```
三层递进：

1. 目录扫描
   在文档目录中定位 "第三章 比选申请文件格式" 等章节
   记录其页码和所有子章节标题 → required_sections 雏形

2. 章节内容提取
   定位到具体章节后，提取：
   - 模板表格（python-docx 原生表格 → 结构化 headers/rows）
   - 固定文本段落（"响应函应包含以下内容：..."）
   - 文件清单（"须提交以下文件："）

3. 约束归并
   required_sections 与 catalog 生成的章节列表对比：
   - 格式要求有但 catalog 没有 → 追加
   - catalog 有但格式要求没有 → 标记"额外内容"
   - 两者都有但顺序不同 → 以格式要求为准
```

### 5.4 校验规则

```python
def validate_against_format(catalog_outline, format_req):
    """校验生成的目录是否符合格式要求"""
    violations = []
    
    # 必选章节完整性
    req_titles = {s.title for s in format_req.required_sections if s.required}
    cat_titles = {s["title"] for s in catalog_outline}
    missing = req_titles - cat_titles
    if missing:
        violations.append(f"缺少格式要求的必选章节: {missing}")
    
    # 章节顺序
    for i, req_s in enumerate(format_req.required_sections):
        if req_s.title in cat_titles:
            cat_idx = next(i for i, s in enumerate(catalog_outline) 
                          if s["title"] == req_s.title)
            if cat_idx != req_s.order - 1:
                violations.append(
                    f"章节顺序不符: {req_s.title} "
                    f"应为第{req_s.order}章, 实际为第{cat_idx+1}章"
                )
    
    return violations
```

---

## 六、数据流全景

```
原始文档
    │
    ▼
DocumentParser → StructuredDocument
    │
    ├── Phase 1: 元数据提取（规则+表格+LLM）
    │   └── 输出: MetadataSchema（带置信度）
    │
    ├── Phase 1.5: 格式要求提取（新增）
    │   └── 输出: FormatRequirement（结构化模板约束）
    │
    ├── Phase 2: 资格扫描
    │   └── 输出: Eligibility（带置信度）
    │
    ├── Phase 3: 评分+包参数
    │   └── 输出: Scoring + Packages（带置信度）
    │
    ├── Validation Gate
    │   ├── 类型校验
    │   ├── 置信度校验
    │   └── 输出: AnalysisSchema + issues[]
    │
    ├── 存储: analysis_data (JSON)
    │   └── legacy 字段: 改为 property 视图
    │
    ├── 目录生成
    │   ├── 读 schema.metadata → 投标函描述
    │   ├── 读 schema.packages → 报价部分
    │   ├── 读 schema.eligibility → 资格/实质性要求章节
    │   ├── 读 schema.scoring → 评分响应章节
    │   ├── 读 schema.format_requirements → 格式约束校验
    │   └── 置信度不足时追加标注
    │
    └── 内容生成
        ├── 读 schema.format_requirements.template_tables
        ├── 读 schema.format_requirements.fixed_texts
        └── 注入生成提示词作为硬约束
```

---

## 七、目录生成的最终决策逻辑

```python
def build_catalog(schema: AnalysisSchema) -> List[Section]:
    sections = []
    format_req = schema.format_requirements
    
    # Step 1: 以格式要求为骨架（如有）
    if format_req:
        for req_sec in format_req.required_sections:
            section = build_section_from_format(req_sec)
            sections.append(section)
    
    # Step 2: 从分析数据补充（格式未覆盖的章节）
    if not format_req or not format_req.has_complete_structure:
        # 用原来的动态推断逻辑补充
        dynamic_sections = infer_from_analysis(schema)
        sections = merge_with_format(sections, dynamic_sections)
    
    # Step 3: 置信度标注
    for section in sections:
        if schema.metadata.project_name.confidence < 0.5:
            section["note"] = "项目名称待确认"
    
    # Step 4: 格式校验（阻断严重违规）
    violations = validate_against_format(sections, format_req)
    if any(v.startswith("缺少格式要求的必选章节") for v in violations):
        raise CatalogValidationError(violations)
    
    return sections
```

---

## 八、兼容性与迁移

| 阶段 | 现有数据 | 新产生的数据 | 消费端 |
|---|---|---|---|
| 当前 | project_code = "" (字符串) | project_code = {"value": "", "confidence": 0} | 兼容读取 |
| 迁移期 | 混合格式 | 新格式 | `_safe_read` 兼容 |
| 完成后 | 全部新格式 | 新格式 | 直接索引 |

**向后兼容策略**：所有读取操作通过 `_safe_read()` 函数，自动识别新旧两种格式。

```python
def _safe_read(meta, key, default=""):
    """新旧格式兼容读取"""
    val = meta.get(key, default)
    if isinstance(val, dict):
        return val.get("value", default)
    return val if val else default
```

---

## 九、不变的部分（保持向后兼容）

- API 返回的 JSON 格式不变（字段名、结构）
- `confirm_catalog()` 不做任何改动
- `extract_catalog_from_file()` Tab2 不受影响
- `get_subject_templates()` Tab3 不受影响
- 前端零改动

---

## 十、特殊字符前缀处理（新增 — 探索发现）

### 10.1 问题根因

招标文件中的章节标题经常带有特殊装饰符号作为标记，如：

```
★二、商务要求
◆三、技术要求  
●四、资格要求
▲五、其他要求
```

当前文档解析器的文本标题检测模式使用 `^` 锚点匹配，这些特殊符号阻塞了匹配：

```python
# document_parser.py 第247行
(2, r'^[一二三四五六七八九十零〇]+[、，,．.]')   # → "★二、商务要求" 不匹配
```

导致整个章节被当作普通段落，章节树中不存在该节点 → 后续所有提取逻辑都无法找到它。

### 10.2 设计方案：通用剥离层

在标题检测前增加一个通用前导字符剥离步骤，不依赖字符白名单：

```
原始文本: "★二、商务要求"
    ↓ 剥离前导非实质性字符（正则: ^[^\w\u4e00-\u9fff\d]+）
剥离后文本: "二、商务要求"  →  匹配 heading 模式，level=2
    ↓
章节标题仍然保留原文: "★二、商务要求"
```

适用场景：
- document_parser.py 的 text_heading_patterns 检测
- section_extractor.py 的 find_section_by_title 关键词匹配
- 未来任何需要按标题搜索的逻辑

设计原则：
- **以"实质性内容"识别标题，而非"外观"**：只看去掉装饰符后是否匹配
- **剥离仅用于匹配，不改变原文**：标题存储仍保留原始文本
- **不罗列字符**：用 `\W` 的补集做通用匹配，不维护白名单

```python
def _strip_heading_prefix(text: str) -> str:
    """剥离标题前导装饰字符，保留标题实质内容。
    
    剥离规则：去掉开头的连续非中文、非英文、非数字字符。
    示例: "★二、商务要求" → "二、商务要求"
          "●1.技术要求"   → "1.技术要求"
          "【重要】三、须知" → "三、须知"
    """
    return re.sub(r'^[^\w\u4e00-\u9fff\d]+', '', text)
```

### 10.3 影响范围

| 文件 | 改动点 | 影响 |
|------|--------|------|
| `document_parser.py` | text_heading_patterns 匹配前调用 `_strip_heading_prefix` | 所有带前缀的标题都能正确识别为章节 |
| `section_extractor.py` | `find_section_by_title` 匹配前调用 `_strip_heading_prefix` | 商务/技术章节能按关键词找到 |
| `phase3_scoring.py` | `_find_tech_section` 匹配前调用 | 技术章节能正确识别 |
| `phase1_metadata.py` | metadata 提取中的章节搜索 | 元数据提取更完整 |

---

## 十一、双线并行提取（新增 — 探索发现）

### 11.1 问题根因

`llm_extractor.py` 中已经实现了 `extract_business()` 和 `extract_technical()` 函数，但它们在 `analysis_v3/__init__.py` 中**被导入但从未被调用**（第30-31行），属于死代码。

同时，规则提取路径（`section_extractor.py`）虽然速度快，但过于机械——只按章节标题搜索，文档结构稍有变化（如带★前缀）就找不到。

### 11.2 设计方案：规则优先 + LLM 兜底

```
阶段1: 规则提取（快速、精确、零成本）
  └─ section_extractor.extract_business_from_sections()
  └─ 输出: extra dict（结构化字段）
  └─ 若找到 ANY 字段 → confidence=HIGH，跳过 LLM

阶段2: LLM 增强（仅在规则结果为空时触发）
  └─ llm_extractor.extract_business(section_text)
  └─ 输出: business_requirements list
  └─ 若找到 → confidence=MEDIUM（LLM 可能有幻觉）

阶段3: 合并策略
  └─ 规则有结果 → 使用规则结果
  └─ 规则为空 → 使用 LLM 结果
  └─ 两者都空 → "暂未提取到" + confidence=0
  └─ 两者都有 → 优先规则（高置信度），LLM 作为补充
```

### 11.3 调用点

在 `analysis_v3/__init__.py` 的管线中，Phase 1 (metadata 提取) 之后、Phase 2 (资格扫描) 之前，增加调用：

```python
# 在 phase1_metadata 提取 metadata 之后
# 规则提取（已在 phase1_metadata 中调用 section_extractor）
rule_biz = metadata.get("extra", {})

# LLM 兜底：当规则提取结果为空时
if not rule_biz:
    logger.info("[analysis_v3] 规则未提取到商务要求，启用 LLM 兜底")
    # 找到商务要求相关章节的原文
    biz_section_text = _find_business_section_text(sections, raw_text)
    if biz_section_text:
        llm_biz = llm_extract_business(biz_section_text)
        if llm_biz:
            # 存入 metadata.extra（格式化为与规则一致的字段）
            metadata.setdefault("extra", {}).update(...)
```

同理对技术要求。

### 11.4 章节文本定位策略

LLM 需要传入相关章节的原文，而不是整个文档。所以在调用前需要先定位：

```python
def _find_business_section_text(sections, raw_text) -> str:
    """找到最可能是商务要求的章节的原文。
    
    策略:
      1. 从章节树中找标题含"商务"的章节 → 取原文
      2. 找不到 → 从全文搜索含"交货""付款""验收"等关键词的段落的上下文
      3. 还找不到 → 返回空（LLM 不调用）
    """
```

---

## 十二、文档感知的目录生成（新增 — 探索发现）

### 12.1 问题根因

当前 `_build_package_aware_outline()` 硬编码了 11 个章节，且与招标文件的实际结构无关：

```
一、投标函与报价        ← 永远有
二、开标一览表          ← 永远有
三、法定代表人授权书     ← 永远有
四、资格证明文件        ← 条件有（取决于确认项是否有）
...
六、技术参数响应        ← 永远有（即使文档没有技术章节）
七、商务要求响应        ← 永远有（即使商务要求提取为空）
九、售后服务及培训方案   ← 永远有（这份标书没有售后要求）
十、类似项目业绩        ← 永远有（这份标书没有业绩要求）
```

标书专家的思维是：**响应文件的目录 = f(招标文件的目录 + 投标策略)**。

### 12.2 设计方案：招标结构 → 响应映射

核心思想：从招标文件的章节结构中推断响应文件需要哪些章节。

```
输入: 招标文件的章节树（DocumentParser 输出）
   │
   ▼
逐章分类映射:

  招标章节                             响应章节
  ──────────                         ──────────
  "第一章 比选邀请"    → INVITATION  → "投标函"
  "第二章 比选申请人须知" → INSTRUCTION → (商务/资格映射到后续章节)
  "第三章 比选申请文件格式" → FORMAT  → 直接照搬，列为响应章节
  "第四章 资格证明文件" → QUALIFICATION → "资格证明材料"
  "第五章 比选项目及要求" → REQUIREMENT → "技术参数响应" + "报价明细"
  "第六章 评选办法"   → SCORING    → "评分标准响应"
  "第七章 合同主要条款" → CONTRACT   → "商务条款承诺"

  输出: 响应目录骨架（按响应文件习惯重新排序）
```

### 12.3 章节类型推断策略

对招标文件的每个章节目录项，根据标题关键词推断章节类型：

```python
CHAPTER_TYPE_RULES = [
    # (关键词, 章节类型, 响应章节标题)
    (["比选邀请", "投标邀请", "招标公告"], "INVITATION", "投标函"),
    (["申请文件格式", "投标文件格式", "响应文件格式"], "FORMAT", None),
    # FORMAT 类型的章节不直接映射为响应章节，而是作为格式约束
    (["资格证明", "资格审查"], "QUALIFICATION", "资格证明文件"),
    (["项目及要求", "技术参数", "技术规格", "采购需求"], "REQUIREMENT", "技术参数响应"),
    (["评选办法", "评标办法", "评审办法", "评分"], "SCORING", "评分标准响应"),
    (["合同条款", "合同主要"], "CONTRACT", "商务条款承诺"),
    (["投标人须知", "比选申请人须知", "供应商须知"], "INSTRUCTION", "商务要求响应"),
]
```

### 12.4 目录生成决策逻辑

```python
def build_document_aware_catalog(task, analysis_result, check_items):
    """从招标文件章节结构生成响应文件目录。"""
    sections = []
    chapter_types = classify_chapters( bidding_doc_sections )
    
    # Step 1: FORMAT 章节 → 提取格式要求作为骨架
    format_chapters = [c for c in chapter_types if c.type == "FORMAT"]
    if format_chapters:
        format_req = extract_format_requirements(bidding_doc_sections)
        for req_sec in format_req.required_sections:
            sections.append(build_from_format(req_sec))
    
    # Step 2: 非 FORMAT 章节 → 映射为响应章节
    for chapter in chapter_types:
        if chapter.type == "FORMAT":
            continue  # 已在 Step 1 处理
        if chapter.type == "INSTRUCTION":
            continue  # 须知类映射到商务/资格，不单独成章
        response_section = map_to_response(chapter, analysis_result, check_items)
        if response_section:
            sections.append(response_section)
    
    # Step 3: 补充格式要求中列示但未覆盖的章节
    # (如招标文件仅列出"响应函、报价表、授权书"三个格式要求，
    #  自动补充资格/技术/评分等章节)
    supplement_sections = infer_from_analysis(analysis_result, check_items)
    sections = merge_with_supplement(sections, supplement_sections)
    
    # Step 4: 编号 & 校验
    sections = apply_numbering(sections)
    return {"outline": sections}
```

### 12.5 单包包名处理

当项目只有一个包、且包名无法从原文提取时：

```python
# phase3_scoring.py extract_packages()
# 修改点：单包场景、pkg_name_map 为空、section title 不是描述性名称时
if len(package_nos) == 1:
    pkg_name = (pkg_name_map or {}).get(pkg_no, "")
    # 只在明确提取到时使用section title
    if not pkg_name:
        section_title = getattr(section, "title", "") or ""
        # 过滤掉"第X章"类章节标题、过滤过长的文本
        if section_title and len(section_title) < 20 and not re.match(r'^第[一二三四五六七八九十]', section_title):
            pkg_name = section_title
    # 如果仍然为空，留空而非回退到错误内容
```

---

## 十三、变更优先级总览

结合新发现和已有设计，整理整体优先级：

| 优先级 | 变更 | 影响范围 | 工作量评估 |
|--------|------|---------|-----------|
| **P0** | ★ 特殊字符前缀处理 | document_parser + section_extractor + phase3_scoring | 小（3处改动） |
| **P0** | 双线并行提取激活 | llm_extractor (激活死代码) + analysis_v3/__init__ | 中（2处改动） |
| **P0** | 单包包名留空 | phase3_scoring.py + check_items/bidding_info.py | 小（2处改动） |
| **P1** | 文档感知目录生成 | catalog.py（重构 _build_package_aware_outline） | 大（核心重构） |
| **P1** | 置信度系统 | 多个文件（已有设计T1-T4） | 大 |
| **P1** | 格式要求管线化 | phase1_5_format + catalog（已有设计T5/T8） | 中 |

P0 先行，P1 后续。

