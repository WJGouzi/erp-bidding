# 目录合并引擎 — 设计文档

## 一、架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                   目录合并引擎                                 │
│                                                             │
│  build_catalog(analysis_data, classified_items) → outline   │
│                                                             │
│  阶段1: build_base_skeleton()                                │
│    ├─ _parse_format_tree()        ← format_requirements      │
│    └─ _infer_skeleton_fallback()  ← document_chapters        │
│                                                             │
│  阶段2: merge_scoring_sections()                             │
│    ├─ _classify_coverage()        ← 客观项→已有章节           │
│    ├─ _create_subjective_section() ← 主观项→新增章节          │
│    └─ _find_insert_position()     ← 关键词定位                  │
│                                                             │
│  阶段3: enrich_section_details()                             │
│    ├─ _fill_business_children()   ← business.items            │
│    ├─ _fill_tech_children()       ← technical.items           │
│    ├─ _fill_qualification()       ← check_items.qualification │
│    └─ _fill_compliance()          ← check_items.compliance    │
│                                                             │
│  阶段4: validate_completeness()                               │
│    └─ _check_chapter_coverage()   ← document_chapters         │
│                                                             │
│  输出: outline[]                                              │
└─────────────────────────────────────────────────────────────┘
```

## 二、阶段1：构建基础骨架

### 2.1 主路径：有 format_requirements

从 `format_requirements.required_sections` 解析层级树。

**检测规则**：
- 标题以 `一、` `二、` ... `十、` 开头 → 父级节点
- 标题以 `1.` `1、` `2.` `2、` 开头 → 归属于最近父级的子项
- 无编号文本 → 归属于最近父级的子项

```python
def _parse_format_tree(required_sections):
    """解析格式要求为目录树"""
    cn_pat = re.compile(r'^[一二三四五六七八九十]+、')
    
    # 找所有父级索引
    parent_indices = [i for i, s in enumerate(required_sections) 
                      if cn_pat.match(s['title'])]
    
    tree = []
    for idx, p_idx in enumerate(parent_indices):
        parent = required_sections[p_idx]
        next_p = parent_indices[idx+1] if idx+1 < len(parent_indices) else len(required_sections)
        children = required_sections[p_idx+1:next_p]
        
        tree.append({
            "source": "format_requirements",
            "source_index": p_idx,
            "title": parent["title"],           # 原文，不修改
            "has_template": parent.get("has_template", False),
            "template_tables": parent.get("template_tables", []),
            "children": [
                {
                    "source": "format_requirements",
                    "title": c["title"],         # 原文，不修改
                } for c in children
            ],
            "description": "",  # 阶段3填充
        })
    
    return tree
```

### 2.2 降级路径：无 format_requirements

当招标文件没有"申请文件格式"章节时，从 `document_chapters` + `scoring` 推断骨架。

```python
def _infer_skeleton(analysis_data):
    """无格式要求时的降级骨架"""
    chapters = analysis_data.get("document_chapters", [])
    scoring = analysis_data.get("scoring", {})
    
    skeleton = []
    
    # 从章节标题推断必需章节
    chapter_section_map = [
        ("报价|报价格", "报价函"),
        ("资格", "资格证明文件"),
        ("技术|参数|采购需求", "技术响应"),
        ("商务|合同", "商务响应"),
        ("评分|评选|评审", "评分响应"),
    ]
    
    seen = set()
    for ch in chapters:
        for keyword, section_name in chapter_section_map:
            if re.search(keyword, ch) and section_name not in seen:
                skeleton.append({
                    "source": "inferred",
                    "title": section_name,
                    "description": "",
                    "children": [],
                })
                seen.add(section_name)
    
    return skeleton
```

## 三、阶段2：合并评分维度

### 3.1 核心原则

| 评分维度类型 | 处理方式 | 理由 |
|------------|--------|------|
| objective（客观） | 标记已有章节覆盖 | 报价→报价一览表，业绩→业绩表 |
| subjective（主观） | 新增撰写章节 | 专家读方案打分 |
| 合计/总计行 | 跳过 | 汇总信息，非评审项 |

### 3.2 覆盖判定

```python
def _is_covered(skeleton, dim_name, dim_score):
    """判断评分维度是否已被骨架覆盖"""
    # 定义显式映射（不同表述但指向同一内容）
    explicit_map = {
        "报价": ["报价一览表", "报价表", "报价部分"],
        "供应商业绩": ["类似项目业绩", "业绩一览表", "业绩"],
        "业绩": ["类似项目业绩", "业绩一览表"],
    }
    
    # 1. 显式映射检查
    expected_sections = explicit_map.get(dim_name, [dim_name])
    for node in skeleton:
        for expected in expected_sections:
            if expected in node["title"]:
                return True, node
    
    # 2. 标题包含检查
    for node in skeleton:
        if dim_name in node["title"]:
            return True, node
    
    return False, None
```

### 3.3 新增章节的插入位置

```python
def _find_insert_position(skeleton, dim_name):
    """确定新增章节的插入位置"""
    # 取维度名的前2-3个字作为关键词
    keywords = [dim_name[:2], dim_name[:3]]
    
    candidates = []
    for i, node in enumerate(skeleton):
        for kw in keywords:
            if kw and kw in node["title"]:
                candidates.append(i + 1)  # 插入到该章节之后
    
    if candidates:
        # 取最靠前的匹配位置
        return min(candidates)
    
    # 无匹配：插入到"其他材料"之前
    for i, node in enumerate(skeleton):
        if "其他" in node["title"]:
            return i
    return len(skeleton)
```

### 3.4 新增章节的 description

```python
def _build_scoring_section_description(dim):
    """评分驱动章节的desc只说明目的，不写评分标准"""
    name = dim.get("name", "")
    score = dim.get("score", 0)
    # 只写"根据本项目需求撰写XXX方案"
    # 不写"40分 - 评分标准：方案完整可行"
    return f"根据本项目采购需求，编制{name}"
```

## 四、阶段3：填充章节详情

### 4.1 business items → 商务偏离表子项

```python
def _fill_business_children(skeleton, business_items):
    """从实际business items动态生成商务偏离表子项"""
    if not business_items:
        return
    
    keyword_section_map = [
        ("付款", "付款方式响应"),
        ("交付地点", "交货地点"),
        ("交付要求|交货时间", "交货时间"),
        ("验收", "验收方案"),
        ("售后", "售后服务承诺"),
        ("质保", "质保期承诺"),
        ("报价方式", "报价方式说明"),
    ]
    
    children = []
    for item in business_items:
        content = item.get("content", "")
        for keyword, title in keyword_section_map:
            if re.search(keyword, content):
                children.append({
                    "source": "business_items",
                    "title": title,
                    "description": content[:80],
                })
                break
    
    # 找到商务偏离表节点，填充
    for node in skeleton:
        if "商务" in node["title"] and "偏离" in node["title"]:
            node["children"] = children
            break
```

### 4.2 technical items → 技术偏离表描述

```python
def _fill_tech_description(skeleton, technical_items, packages):
    """从technical items和产品清单填充技术偏离表描述"""
    # 统计产品数量
    product_count = 0
    product_names = []
    for pkg in packages or []:
        table_items = pkg.get("parameters", {}).get("table_items", [])
        for item in table_items:
            name = item.get("采购产品名称", "")
            if name:
                product_count += 1
                product_names.append(name)
    
    if product_count == 0 and technical_items:
        product_count = len(technical_items)
    
    for node in skeleton:
        if "技术" in node["title"] and "偏离" in node["title"]:
            if product_count > 0:
                node["description"] = f"共{product_count}种产品，逐项响应技术参数要求"
            break
```

### 4.3 资格项填充

```python
def _fill_qualification(skeleton, classified_items):
    """
    资格项填充规则：
    1. 骨架中已有"资格"节点 → 填入children（去重后）
    2. 骨架中无"资格"节点但classified_items有资格项 → 新增
    """
    qual_items = classified_items.get("qualification", [])
    if not qual_items:
        return
    
    # 去重：按requirement前20字
    seen = set()
    deduped = []
    for item in qual_items:
        key = item.get("requirement", "")[:20]
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    
    # 找到或创建资格节点
    qual_node = None
    for node in skeleton:
        if "资格" in node["title"]:
            qual_node = node
            break
    
    if qual_node:
        qual_node["children"] = [
            {
                "source": "qualification",
                "title": f"（{i+1}）{item.get('requirement','')[:60]}",
                "description": item.get("material", "")[:100],
            }
            for i, item in enumerate(deduped)
        ]
```

### 4.4 符合性/实质性要求填充

```python
def _fill_compliance(skeleton, classified_items):
    """实质性要求填入或新增章节"""
    comp_items = classified_items.get("compliance", [])
    if not comp_items:
        return
    
    # 查找已有"实质性要求"节点
    comp_node = None
    for node in skeleton:
        if "实质性" in node["title"]:
            comp_node = node
            break
    
    if comp_node:
        comp_node["children"] = [
            {"source": "compliance", "title": item.get("check_label", ""), "description": item.get("check_value", "")[:80]}
            for item in comp_items
        ]
```

## 五、阶段4：完整性验证

```python
def validate_completeness(outline, document_chapters):
    """
    验证目录覆盖了源文档所有章节。
    对每个document_chapter，检查是否有对应section。
    输出警告而非阻塞。
    """
    chapter_section_map = [
        ("比选邀请", ["比选函"]),
        ("须知", ["比选函"]),       # 须知内容隐含在比选函
        ("申请文件格式", []),       # 格式要求=目录本身
        ("资格证明", ["资格证明"]),
        ("比选项目及要求", ["报价一览表", "商务", "技术", "偏离表"]),
        ("评选办法", ["服务方案", "售后保障", "评分"]),
        ("合同", []),              # 参考，不生成响应章节
    ]
    
    warnings = []
    for ch in document_chapters:
        matched = False
        for keyword, expected_sections in chapter_section_map:
            if keyword in ch:
                if not expected_sections:
                    matched = True  # 不需对应章节
                    break
                for node in outline:
                    for expected in expected_sections:
                        if expected in node["title"]:
                            matched = True
                            break
                    if matched:
                        break
                break
        
        if not matched and ch not in ("目录",):
            warnings.append(f"章节 '{ch}' 在目录中无明确对应")
    
    return warnings
```

## 六、主流程编排

```python
def build_catalog(analysis_data, classified_items):
    """目录合并引擎主入口"""
    
    # 阶段1：基础骨架
    skeleton = build_base_skeleton(analysis_data)
    
    # 阶段2：合并评分维度
    scoring = analysis_data.get("scoring", {})
    if isinstance(scoring, dict):
        skeleton = merge_scoring_sections(skeleton, scoring)
    
    # 阶段3：填充详情
    enrich_section_details(skeleton, analysis_data, classified_items)
    
    # 阶段4：编号+收尾
    outline = assign_numbers(skeleton)
    
    # 验证
    chapters = analysis_data.get("document_chapters", [])
    warnings = validate_completeness(outline, chapters)
    if warnings:
        logger.info("[catalog] 覆盖警告: %s", warnings)
    
    return outline
```

## 七、边界情况处理

### 7.1 format_requirements 部分存在（只有几章）
按检测到的父级节点生成骨架，不完整部分由阶段2/3补充。

### 7.2 scoring.dimensions 为空
阶段2无操作，只保留骨架。不在目录中生成评分相关章节。

### 7.3 新旧数据格式兼容
```python
def _get_dimensions(scoring):
    """兼容 analyze 格式和 check-items 格式"""
    dims = scoring.get("dimensions", [])
    if dims:
        return dims
    # 兼容 check-items 格式：从 business + technical 合并
    dims = []
    for group in ["business", "technical"]:
        for item in scoring.get(group, []):
            dims.append({
                "name": item.get("name", ""),
                "score": item.get("score", 0),
                "type": item.get("type", "objective"),
            })
    return [d for d in dims if "合计" not in d.get("name", "")]
```

### 7.4 资格项为空的处理
classified_items 中 qualification 为空时，若 document_chapters 仍有"第四章资格证明文件"，骨架中保留空节点，标记为"待用户确认"。

## 八、与现有代码的关系

### 保留
- `_build_catalog_description()` — 文本裁剪工具函数
- `_get_filtered_analysis_data()` — 分包过滤
- `_classify_check_items()` — 确认项分类
- `_build_constrained_requirement_outline()` — 作为兼容入口

### 废弃
- `_build_package_aware_outline()` → 被合并引擎替代
- `_build_bid_letter_section()` → 从 format_requirements 解析
- `_build_price_section()` → 由 format_requirements"报价一览表"替代
- `_build_authorization_section()` → 由 format_requirements"授权书"替代
- `_build_tech_section()` → 由 format_requirements"技术偏离表"替代
- `_build_business_section()` → 由 format_requirements"商务偏离表"替代
- `_build_scoring_section()` → 被阶段2合并逻辑替代
- `_build_service_section()` → 被评分维度新增章节替代
- `_build_performance_section()` → 由 format_requirements"业绩表"替代
- `_build_qualification_section()` → 被阶段3填充逻辑替代
- `_build_compliance_section()` → 被阶段3填充逻辑替代

### 新增
- `_parse_format_tree()` — 解析格式要求为目录树
- `_infer_skeleton_fallback()` — 降级推断
- `merge_scoring_sections()` — 评分合并
- `enrich_section_details()` — 详情填充
- `validate_completeness()` — 完整性验证
- `_get_dimensions()` — 兼容双格式

## 九、数据源参考（本份标书示例）

```
format_requirements 贡献（9大章）：
  一、比选函
  二、法定代表人/单位负责人授权书
  三、承诺函
  四、报价一览表（含22种产品模板）
  五、商务要求偏离表（含偏离表模板）
  六、比选申请人基本情况表（含复杂模板）
  七、比选申请人类似项目业绩一览表（含业绩模板）
  八、技术、服务要求偏离表
  九、比选申请人本项目管理、技术、服务人员情况表

scoring.dimensions 贡献（4维）：
  报价30分 → objective → 已覆盖（四、报价一览表）
  供应商业绩10分 → objective → 已覆盖（七、业绩一览表）
  服务方案40分 → subjective → 新增章节
  售后保障方案20分 → subjective → 新增章节

business.items 贡献（7条）：
  付款方式、交付地点、验收标准、报价方式、售后服务、质保期、交付要求
  → 填入五、商务要求偏离表 的子项

technical.items 贡献（35项，含22种产品）：
  → 填入八、技术、服务要求偏离表的description

check_items.qualification 贡献（13项，去重后约10项）：
  → 填入资格证明文件章节
```
