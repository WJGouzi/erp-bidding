# 专家级解析管线 — 设计文档

---

## 总体架构

```
                    start_analyze_v3(task, source_texts)
                              │
                 ┌────────────┴────────────┐
                 │  第0步：文档结构解析      │
                 │  (已有，不变)             │
                 └────────────┬────────────┘
                              │
                 ┌────────────┴────────────┐
                 │  第1步：表格矩阵分类      │
                 │  (已有，不变)             │
                 └────────────┬────────────┘
                              │
                 ┌────────────┴────────────┐
                 │  第2步：元数据提取        │
                 │  (已有 + 修复classify)   │
                 └────────────┬────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
  第3步: 法规固定清单    第4步: 章节定位       第5步: 动态提取
  (config文件加载)       (强化评分机制)       (从定位章节提取内容)
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                              ▼
                    第6步: 内容归类 + 合并
                              │
                              ▼
                    第7步: 评分拆解(已有)
                              │
                              ▼
                    第8步: 策略分析(已有)
```

---

## 第3步：法规固定清单（新建）

### 设计原则

不再使用 `ELIGIBILITY_TEMPLATES` 的 `_BASE + bid_type + doc_type` 矩阵。

改为 **一个独立于标书类型的固定清单**，仅包含法律法规硬性要求的检查项。

### 配置文件

```yaml
# config/presets/statutory_checklist.yaml
# 政府采购法第二十二条 + 通用法规要求的资格条件
# 这些是所有政府采购标书都必须满足的，与 bid_type/doc_type 无关
# 修改此文件需要法律合规审核

statutory_items:
  - id: "stat_01"
    category: "通用资格"
    requirement: "具有独立承担民事责任的能力（营业执照/法人证书）"
    law_ref: "政府采购法第二十二条第一款"
    severity: "fatal"

  - id: "stat_02"
    category: "通用资格"
    requirement: "具有良好的商业信誉和健全的财务会计制度（财务报告/审计报告）"
    law_ref: "政府采购法第二十二条第二款"
    severity: "fatal"

  - id: "stat_03"
    category: "纳税社保"
    requirement: "具有依法缴纳税收和社会保障资金的良好记录"
    law_ref: "政府采购法第二十二条第四款"
    severity: "fatal"

  - id: "stat_04"
    category: "信用记录"
    requirement: "参加政府采购活动前三年内，在经营活动中没有重大违法记录"
    law_ref: "政府采购法第二十二条第五款"
    severity: "fatal"

  - id: "stat_05"
    category: "信用记录"
    requirement: "未被列入'信用中国'失信被执行人、重大税收违法案件当事人名单"
    law_ref: "财库[2016]125号"
    severity: "fatal"

  - id: "stat_06"
    category: "信用记录"
    requirement: "未被列入'中国政府采购网'政府采购严重违法失信行为记录名单"
    law_ref: "财库[2016]125号"
    severity: "fatal"

  - id: "stat_07"
    category: "通用资格"
    requirement: "法定代表人授权书（如由授权代表参与）"
    law_ref: "通用格式要求"
    severity: "normal"

  - id: "stat_08"
    category: "通用资格"
    requirement: "供应商单位及其现任法定代表人/主要负责人无行贿犯罪记录"
    law_ref: "通用要求"
    severity: "fatal"
```

### 加载方式

```python
# phase2_extractor.py

import yaml
from pathlib import Path

_statutory_cache = None

def _load_statutory_checklist():
    global _statutory_cache
    if _statutory_cache is not None:
        return _statutory_cache
    path = Path(__file__).parent.parent.parent.parent / "config" / "presets" / "statutory_checklist.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _statutory_cache = data.get("statutory_items", [])
    return _statutory_cache
```

### 与文档匹配

每个 stat 项在文档中验证是否提及：

```python
def _verify_statutory_items(sections, statutory_items):
    """验证法规固定项在文档中是否有对应条款。"""
    full_text = _get_full_text(sections)
    results = []
    for item in statutory_items:
        # 提取关键词（如"独立承担民事责任"）
        keywords = _extract_keywords(item["requirement"])
        found = any(kw in full_text for kw in keywords)
        results.append({
            **item,
            "found": found,
            "status": "passed" if found else "attention",
            "source": "statutory_checklist",
        })
    return results
```

---

## 第4步：章节定位强化

### 当前问题

`_find_qualification_sections()` 用关键词列表匹配章节标题：

```python
title_targets = [
    "资格要求", "资质要求",
    "第四章", "第五章",
    # ... 大量关键词 ...
]
```

**这种方案有两个缺陷：**
1. 标题关键词列表需要人工维护，总有遗漏
2. 无法处理标题不标准的情况（如"申请人的资格要求"→当前无此关键词）

### 强化方案：章节评分机制

借鉴 `_find_scoring_section()` 已有的打分方法，扩展到资格章节定位：

```python
def _find_qualification_sections_v2(sections):
    """v2: 评分机制替代关键词列表。

    对每个章节计算"资格相关性得分"。
    得分 = 标题匹配分 + 内容关键密度分 - 干扰章节扣分
    """

    def _score(node):
        score = 0
        title = node.title or ""

        # ── 标题加分 ──
        title_signals = [
            # 高置信度（≥8分）
            ("资格要求", 10), ("供应商资格", 10), ("投标人资格", 10),
            ("资格审查", 10), ("资格性审查", 10),
            ("申请人资格", 10),
            # 中置信度（5-7分）
            ("投标人须知", 7), ("供应商须知", 7), ("比选须知", 7),
            ("资质要求", 6), ("供应商条件", 6),
            ("第四章", 5), ("第五章", 5),  # 章节号作为信号
            # 低置信度（2-3分）
            ("资格证明", 3), ("资质", 2),
        ]
        for signal, points in title_signals:
            if signal in title:
                score += points

        # ── 标题扣分（干扰章节） ──
        noise_signals = ["目录", "TOC", "前附表", "合同模板", "合同草案",
                         "响应文件格式", "评标办法", "评分"]
        for ns in noise_signals:
            if ns in title:
                score -= 5

        # ── 内容密度分 ──
        content_text = _section_to_text(node)
        density_signals = ["营业执照", "资格", "资质", "许可证",
                          "财务报告", "纳税", "社保", "信用中国"]
        density_hits = sum(1 for s in density_signals if s in content_text)
        score += min(density_hits, 10)  # 最多加10分

        return score

    # 对所有章节评分，取最高分章节及其子章节
    scored = []
    for section in sections:
        s = _score(section)
        if s > 0:
            scored.append((s, section))

    scored.sort(key=lambda x: -x[0])
    top_score = scored[0][0] if scored else 0

    # 只要得分在最高分80%以上，都作为资格章节包含
    threshold = top_score * 0.8
    result = [s for s_score, s in scored if s_score >= threshold and s_score >= 5]

    return result
```

### 优点

- 不需要人工维护标题关键词列表
- 天然处理所有采购方式（招标/比选/磋商/谈判）
- 通过内容密度分自动识别"有实料"的章节
- 通过信号词权重的加减，自动排除干扰章节

---

## 第5步：动态提取

### 从定位的章节中提取具体内容

```python
def _extract_requirements_from_sections(qual_sections):
    """从资格章节中提取具体的资格要求。

    不使用预设模板，直接从文本中检测。
    检测到的内容自动归类。
    """
    all_text = "\n".join(_section_to_text(s) for s in qual_sections)

    # 信号词配置（从 YAML 加载）
    signals = _load_signal_words()

    results = {"qualifications": [], "disqualifications": [], "starred": []}

    # 按行扫描
    for line in all_text.split("\n"):
        line = line.strip()
        if not line or len(line) < 5:
            continue

        # 检测分类
        category = _classify_by_signal(line, signals)
        if not category:
            continue

        entry = {
            "requirement": line[:200],
            "category": category,
            "severity": _detect_severity(line),
        }

        if _is_disqualification(line):
            entry["status"] = "attention"
            results["disqualifications"].append(entry)
        elif _is_starred(line):
            entry["status"] = "attention"
            results["starred"].append(entry)
        else:
            entry["status"] = "passed"
            results["qualifications"].append(entry)

    return results


def _classify_by_signal(text, signals):
    """根据信号词分类文本属于哪个类别。

    signals = {
        "特定资格": ["许可证", "资质证书", "注册证", "经营许可", "备案"],
        "业绩要求": ["业绩", "类似项目", "类似业绩", "成功案例"],
        "联合体": ["联合体", "联合体投标", "联合体协议"],
        "保证金": ["投标保证金", "履约保证金", "投标担保"],
        # ...
    }
    """
    for category, keywords in signals.items():
        if any(kw in text for kw in keywords):
            return category
    return None
```

### 信号词配置文件

```yaml
# config/presets/signal_words.yaml
# 内容归类信号词
# 从定位的章节文本中检测信号词，自动将内容归类
# 此文件可以随时扩展，不需要改代码

classification_signals:
  特定资格:
    - "许可证"
    - "资质证书"
    - "注册证"
    - "经营许可"
    - "备案"
    - "认证"
    - "许可"
    - "执业"
    - "安全生产"
    - "医疗器械"

  业绩要求:
    - "业绩"
    - "类似项目"
    - "类似业绩"
    - "成功案例"
    - "履约能力"
    - "过往"

  联合体:
    - "联合体"
    - "联合体投标"
    - "联合体协议"

  保证金:
    - "投标保证金"
    - "履约保证金"
    - "投标担保"
    - "保证金金额"

  实质性条款:  # 需要结合 ★ 符号一起判断
    - "实质性要求"
    - "必须"
    - "不得"

  废标信号:  # 用于检测废标条件
    - "投标无效"
    - "废标"
    - "拒收"
    - "否决"
    - "不予受理"
```

---

## 第6步：内容归类与合并

### 合并规则

```python
def _build_eligibility(sections):
    """组装最终资格检查结果。"""

    # 1. 法规固定清单（直接从配置加载，不依赖文档）
    statutory = _verify_statutory_items(sections, _load_statutory_checklist())

    # 2. 章节定位
    qual_sections = _find_qualification_sections_v2(sections)

    if qual_sections:
        # 3. 从章节动态提取
        dynamic = _extract_requirements_from_sections(qual_sections)
    else:
        # 4. 无资格章节时的 fallback
        dynamic = {"qualifications": [], "disqualifications": [], "starred": []}
        # 在全部文本中搜废标和★条款
        dynamic["disqualifications"] = _fallback_scan(sections, _load_signal_words())

    # 5. 去重合并
    all_quals = statutory + dynamic["qualifications"]
    all_quals = _deduplicate_by_text(all_quals)

    return {
        "summary": {
            "statutory_items": len(statutory),
            "dynamic_items": len(dynamic["qualifications"]),
            "disqualifications": len(dynamic["disqualifications"]),
            "starred": len(dynamic["starred"]),
        },
        "qualifications": all_quals,
        "disqualifications": dynamic["disqualifications"],
        "starred_requirements": dynamic["starred"],
    }
```

---

## doc_type 修复

### 当前问题

`classify_document()` 缺少"竞争性磋商"关键词：

```python
type_keywords = {
    "SELECTION": ["比选公告", "比选文件", ...],
    "TENDER": ["招标公告", "招标文件", ...],
    "NEGOTIATION": ["竞争性谈判公告", "竞争性谈判文件", ...],  # ← 只有谈判，没有磋商
    "INQUIRY": ["询价公告", "询价通知书", ...],
}
```

### 修复方案

```python
type_keywords = {
    "SELECTION": ["比选公告", "比选文件", "比选邀请", "比选须知"],
    "TENDER": ["招标公告", "招标文件", "投标邀请", "投标须知", "公开招标"],
    "NEGOTIATION": [
        "竞争性谈判公告", "竞争性谈判文件", "谈判邀请",
        "竞争性磋商公告", "竞争性磋商文件", "磋商邀请", "磋商公告",  # ← 新增
    ],
    "INQUIRY": ["询价公告", "询价通知书", "询价邀请"],
}
```

同时文件名层级增加"竞争性磋商"：

```python
if "比选" in file_basename:
    filename_type = "SELECTION"
elif "竞争性谈判" in file_basename or "竞争性磋商" in file_basename:  # ← 新增
    filename_type = "NEGOTIATION"
elif "询价" in file_basename:
    filename_type = "INQUIRY"
```

**但关键变化是：doc_type 只用于 UI 展示，不驱动任何逻辑。**

```python
# 在 start_analyze_v3() 中：
# Phase 2 不再接收 doc_type
eligibility = scan_eligibility_v2(sections)  # ← 不需要 doc_type 参数
# doc_type 仅保存在 metadata 中用于展示
```

---

## 文件组织结构

```
openspec/changes/expert-parse-pipeline/
├── proposal.md
├── design.md
├── specs/
│   ├── statutory-checklist/
│   │   └── spec.md              # 法规固定清单配置
│   ├── dynamic-section-extraction/
│   │   └── spec.md              # 章节定位 + 动态提取
│   ├── content-signal-classification/
│   │   └── spec.md              # 信号词归类
│   └── doc-type-display-only/
│       └── spec.md              # 修复classify + 只做展示
└── tasks.md

config/presets/
├── statutory_checklist.yaml      # 法规固定清单
└── signal_words.yaml             # 信号词配置

app/service_modules/task_pipeline/analysis_v3/
├── phase2_eligibility.py         # DELETE: 整个文件重建
├── phase2_extractor.py           # CREATE: 动态章节提取（新模块）
```

---

## 兼容性

### 向后兼容

| 维度 | 兼容性 |
|------|--------|
| 输出 JSON schema | 向后兼容，字段名不变 |
| 数据库字段 | 不变 |
| API 返回格式 | 不变 |
| UI 展示 | 不变（但 doc_type 更准确） |

### 过渡策略

1. 先完成新的 `phase2_extractor.py` 模块
2. 在新的管线中并行跑新旧两个 Phase 2，对比输出
3. 验证新输出不劣于旧输出后，切换到新管线
4. 删除旧 `phase2_eligibility.py`

---

## 基于章节结构的商务/技术要求提取

### 背景

当前 `phase1_metadata.py` 用 20+ 条 regex 在 raw_text 中提取商务字段（付款方式、交货地点等），存在频繁误匹配和漏匹配问题。解决方案与 Phase 2 的改动思路一致：**利用已解析的章节树结构，通过章节导航替代全文搜索**。

### 章节树结构

文档解析器已输出结构化章节树，每个章节有 title + level + content + children：

```
第五章 采购项目技术、服务、合同内容条款及商务要求    ← level=1, title="第五章..."
  ├── 一、项目概述                                      ← level=2
  ├── 二、★采购内容                                     ← level=2
  ├── 三、技术要求                                       ← level=2
  │     ├── 1. 组织研磨器                                ← level=3（子章节）
  │     └── 2. 光照培养箱                                ← level=3
  ├── 四、履约能力要求                                   ← level=2
  ├── 五、★质量要求                                      ← level=2
  └── 六、★商务要求                                      ← level=2
        ├── (一) 履约时间和地点                            ← level=3
        │     └── content: [paragraph("交货时间: 合同签订后30日内"), ...]
        ├── (二) 售后服务要求
        │     └── content: [paragraph("质保期3年"), ...]
        ├── (三) 付款方式
        │     └── content: [paragraph("验收合格后支付95%"), ...]
        ├── (四) 包装与运输
        ├── (五) 保险
        └── (六) 其他要求
```

### 章节标题 → 业务字段映射

```python
SECTION_TO_EXTRA = [
    # (标题关键词, extra字段名, 优先级)
    (["付款", "支付", "结算"], "payment_terms", 1),
    (["交货", "交付", "供货", "配送地点"], "delivery_location", 1),
    (["交货时间", "供货期", "合同履行期限"], "service_period", 1),
    (["服务期限", "服务期", "合同期限"], "service_period", 2),
    (["售后", "维修", "质保"], "after_sale_service", 1),
    (["质量", "验收", "验收标准"], "acceptance_standard", 1),
    (["包装", "运输"], "packaging_transport", 1),
    (["保险"], "insurance", 1),
    (["履约地点", "服务地点"], "delivery_location", 2),
    (["报价", "价格", "费用"], "pricing_rule", 1),
    (["特别说明", "其他要求", "其他"], "special_declaration", 1),
]
```

### 提取流程

```
输入: doc.sections (章节树)
         │
         ▼
1. 找"商务要求"章节
   _find_section_by_title(sections, "商务要求")
         │
   找到? ───否──→ fallback 到 regex 方案
         │
         ▼
2. 遍历子章节
   for child in biz_section.children:
         │
         ▼
3. 子章节标题匹配 SECTION_TO_EXTRA 映射表
   title_keywords → extra_field_name
         │
         ▼
4. 读取子章节内容（段落+表格）
   _section_content_to_text(child)
         │
         ▼
5. 合并结果
   final = {**regex_results, **section_results}  # 章节结果优先
```

### 技术原理图

```
                           商务要求章节
                               │
                    ┌──────────┼──────────┐
                    │          │          │
               子章节1      子章节2     子章节3
              (付款方式)   (交货地点)   (售后服务)
                    │          │          │
              读取content:  读取content: 读取content:
              paragraphs    paragraphs   paragraphs
              + tables      + tables     + tables
                    │          │          │
                    ▼          ▼          ▼
              payment_terms  delivery_loc  after_sale
              = "验收合格后  = "德阳市    = "质保期3年,
                30日内支付"   疾控中心"      响应2小时"
```

### 集成方式

```python
# 在 metadata 提取中增加新的章节提取步骤
def extract_metadata(meta_text, file_name="", table_results=None, sections=None):
    # ... 现有 regex 提取逻辑 ...
    
    # 新增：章节提取（sections 不为空时）
    section_extras = {}
    if sections:
        section_extras = extract_business_from_sections(sections)
    
    # 合并：章节结果优先
    for key, value in section_extras.items():
        if value:  # 非空值覆盖 regex 结果
            extra[key] = value
    
    return metadata
```

### 预期收益

1. **误匹配消除**：不再有 regex 抓到无关数字/章节号的问题
2. **表格支持**：商务章节中的表格（如"商务要求表"）完整保留行列结构
3. **零维护**：新增标书不需要调 regex，章节结构自动适配
4. **覆盖率提升**：目前 regex 只覆盖 10+ 字段，章节方案可覆盖所有子章节
