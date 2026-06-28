# 目录生成改造 — 设计

## 改造后的数据流

```
Analysis ──▶ analysis_data (含所有包)
                  │
          selected_package_no ──▶ _get_filtered_analysis_data()
                  │                    │
                  │             只取当前包的数据
                  │                    │
                  ▼                    ▼
          BiddingCheckItem ──▶ _build_package_aware_outline()
                  │                    │
                  │         ┌──────────┼──────────┐
                  │         ▼          ▼          ▼
                  │   章节模板    产品推断    评分映射
                  │         │          │          │
                  ▼         ▼          ▼          ▼
              动态生成的 8-12 章目录结构
```

## 模块设计

### 1. `get_catalog_options()` — 注入上下文

```python
def get_catalog_options(task_id):
    task = ...
    analysis_result = ...
    
    # ── 新增：读取包号和确认项 ──
    selected_package_no = getattr(task, "selected_package_no", None)
    check_items = BiddingCheckItem.query.filter_by(
        shared_resource_id=task.shared_resource_id
    ).order_by(BiddingCheckItem.sort_no).all()
    
    # ── 传入两个新参数 ──
    outline = _build_constrained_requirement_outline(
        task, analysis_result,
        generation_level=...,
        selected_package_no=selected_package_no,
        check_items=check_items,
    )
    ...
```

### 2. `_build_constrained_requirement_outline()` — 接收新参数

```python
def _build_constrained_requirement_outline(
    task, analysis_result, generation_level=None,
    selected_package_no=None, check_items=None
):
    # 1. 按包过滤 analysis_data
    analysis_data = _get_filtered_analysis_data(
        analysis_result, selected_package_no)
    
    # 2. 解析确认项（按分类分组）
    confirmed_qualifications = []
    confirmed_compliance = []
    for item in (check_items or []):
        if getattr(item, "confirmed_flag", False):
            # 从 check_key 判断类别: qual_, star_, disq_, score_dim_
            ...
    
    # 3. 动态构建目录
    outline = _build_package_aware_outline(
        task=task,
        analysis_data=analysis_data,
        confirmed_qualifications=confirmed_qualifications,
        confirmed_compliance=confirmed_compliance,
        generation_level=generation_level,
    )
    return {"outline": outline}
```

### 3. `_get_filtered_analysis_data()` — 按包过滤

```python
def _get_filtered_analysis_data(analysis_result, selected_package_no):
    """只保留当前选定包的数据。"""
    analysis_data = safe_loads(analysis_result.analysis_data)
    if not selected_package_no or not analysis_data.get("has_package"):
        return analysis_data                     # 单包场景不过滤
    
    # 只保留当前包
    analysis_data["packages"] = [
        p for p in analysis_data.get("packages", [])
        if str(p.get("package_no")) == str(selected_package_no)
    ]
    analysis_data["package_count"] = len(analysis_data["packages"])
    return analysis_data
```

### 4. `_build_package_aware_outline()` — 动态结构推断

这是核心替换 `_build_dynamic_outline` 硬编码 3 章的新函数。

**推断逻辑**：

```python
def _build_package_aware_outline(task, analysis_data, ...):
    sections = []
    
    # ── 基准章节（所有标书都有） ──
    sections.append(build_bid_letter_section())           # 一、投标函
    sections.append(build_price_section(analysis_data))   # 二、报价部分
    sections.append(build_authorization_section())         # 三、授权书
    
    # ── 资格证明文件（从 check_items 展开） ──
    if confirmed_qualifications:
        sections.append(build_qualification_section(       # 四、资格证明
            confirmed_qualifications))
    
    # ── 实质性要求（从 check_items 展开） ──
    if confirmed_compliance:
        sections.append(build_compliance_section(          # 五、实质性要求
            confirmed_compliance))
    
    # ── 技术部分（根据包内产品数量决定颗粒度） ──
    pkg = get_selected_package(analysis_data)
    item_count = count_package_items(pkg)
    if item_count > 0:
        sections.append(build_tech_section(pkg, item_count)) # 六、技术参数
    
    # ── 商务部分 ──
    sections.append(build_business_section(analysis_data))  # 七、商务要求
    
    # ── 评分响应（从 scoring.dimensions 映射） ──
    scoring = analysis_data.get("scoring", {})
    if scoring.get("dimensions"):
        sections.append(build_scoring_section(scoring))    # 八、评分响应
    
    # ── 售后服务/培训方案 ──
    sections.append(build_service_section())               # 九、售后服务
    
    # ── 业绩和其他 ──
    sections.append(build_performance_section())           # 十、业绩
    sections.append(build_other_section())                 # 十一、其他
    
    return sections
```

**章节推断规则**：

| 条件 | 章节 | 说明 |
|---|---|---|
| 总是有 | 投标函 | 最核心文件 |
| 有产品清单 | 报价部分 | 含分项报价明细表 |
| 总是有 | 法定代表人授权书 | 必备 |
| 有确认的资格项 | 资格证明文件 | 每项展开为子章节 |
| 有确认的实质性要求 | 实质性要求响应 | ★ 项 |
| items > 0 | 技术参数响应 | items 多时展开为每组产品 |
| 总是有 | 商务要求响应 | |
| 有评分维度 | 评分标准响应 | 各维度映射为子章节 |
| 默认 | 售后服务方案 | |
| 默认 | 类似项目业绩 | |
| 默认 | 其他材料 | |

### 5. `_build_check_items_sections()` — 确认项→章节

```python
def _build_qualification_sections(check_items, shared_resource_id):
    """从确认的 check_items 生成资格证明文件的子章节。"""
    items = BiddingCheckItem.query.filter_by(
        shared_resource_id=shared_resource_id
    ).order_by(BiddingCheckItem.sort_no).all()
    
    children = []
    for item in items:
        if item.check_key.startswith("qual_"):
            severity_label = ""
            if not item.confirmed_flag:
                severity_label = "（待准备）"
            children.append({
                "title": f"（{_next_label()}）{item.check_label}{severity_label}",
                "description": item.check_value or "",
            })
        elif item.check_key.startswith("star_"):
            children.append({
                "title": f"（{_next_label()}）{item.check_label}（★实质性要求）",
                "description": item.check_value or "",
            })
    return {
        "title": "四、资格证明文件",
        "description": "根据招标文件资格要求提供以下证明材料",
        "children": children,
    }
```

### 6. 回退策略

当以下条件满足任一，自动回退到当前 3 章硬编码结构：

```python
def _should_fallback_to_legacy(...):
    # 1. 无 analysis_data
    # 2. selected_package_no 为空且 has_package=True
    # 3. check_items 为空列表
    # 四种组合都安全回退
```

## 兼容性

| 场景 | 行为 |
|---|---|
| 新项目（有包号+有确认项） | 动态 8-12 章 |
| 旧项目（无包号） | 回退到 3 章 |
| 单包项目（has_package=False） | 不过滤，直接用全部数据 |
| 无确认项（check_items=[]） | 跳过资格/实质性要求章节 |
| 前端 | JSON 格式不变，零改动 |

## 不变的部分

- `catalog_content` 的 JSON 格式：`{"outline": [{"title", "description", "children"}]}`
- `confirm_catalog()` 不做任何改动
- `extract_catalog_from_file()` Tab2 不受影响
- `get_subject_templates()` Tab3 不受影响
- `refresh_auto_catalog_content()` 逻辑不变
