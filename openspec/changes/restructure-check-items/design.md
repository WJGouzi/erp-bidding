# check-items 内部模块化设计

## 架构

```
app/service_modules/task_pipeline/
├── analysis_v3/
│   ├── __init__.py                    # start_analyze_v3()
│   ├── phase1_metadata.py             # 元数据提取
│   ├── phase2_extractor.py            # 资格+商务提取
│   ├── phase3_scoring.py              # 评分+分包提取
│   ├── check_items/                   # ← 新增目录
│   │   ├── __init__.py                # assemble_check_items() 统一入口
│   │   ├── bidding_info.py            # 投标人须知组装
│   │   ├── business.py                # 商务要求组装
│   │   ├── technical.py               # 技术要求组装
│   │   ├── qualification.py           # 资格审查组装
│   │   ├── scoring.py                 # 评分标准组装
│   │   ├── packages.py                # 分包信息组装
│   │   └── checklist.py               # 待确认项扁平清单
│   └── check_items.py                 # ← 现有，保留兼容或移除
├── analysis.py                        # 分析入口 + get_check_items() 改造
```

## 模块接口

### `check_items/__init__.py` — 统一入口

```python
def assemble_check_items(shared_resource_id: int) -> dict:
    """从 bidding_analysis_result 表组装完整 check-items 响应。"""
    result = BiddingAnalysisResult.query.filter_by(
        shared_resource_id=shared_resource_id
    ).first()
    if not result:
        return None

    analysis = result.safe_analysis_data()  # 安全解析 JSON

    return {
        "task_id": get_task_id(shared_resource_id),
        "bidding_info": assemble_bidding_info(result, analysis),
        "business": assemble_business(result, analysis),
        "technical": assemble_technical(result, analysis),
        "qualification": assemble_qualification(result, analysis),
        "scoring": assemble_scoring(result, analysis),
        "packages": assemble_packages(result, analysis),
        "checklist": assemble_checklist(result, analysis),
    }
```

### `bidding_info.py` — 投标人须知

```python
def assemble_bidding_info(result, analysis) -> dict:
    meta = analysis.get("metadata", {})
    return {
        "project_name": meta.get("project_name", ""),
        "project_code": meta.get("project_code", ""),
        "package_no": _get_current_package_no(result),
        "budget": {
            "total": meta.get("budget", 0),
            "note": meta.get("budget_note", ""),
        },
        "purchaser": meta.get("purchaser", ""),
        "agency": meta.get("agent", ""),
        "domain": meta.get("domain", ""),
        "summary": result.overview or "",
        "sme_only": meta.get("sme_only", False),
        "dark_bid": meta.get("dark_bid", False),
        "bid_deadline": meta.get("bid_deadline", ""),
        "bid_bond": meta.get("bid_bond", ""),
        "bid_open_time": meta.get("bid_open_time", ""),
    }
```

### `business.py` — 商务要求

```python
def assemble_business(result, analysis) -> dict:
    biz = result.business_requirements or ""
    # 尝试解析为结构化列表，否则按行拆分
    items = _parse_requirements_list(biz)
    return {
        "items": items,
        "raw": biz if not items else None,
    }
```

### `technical.py` — 技术要求

同上模式。

### `qualification.py` — 资格审查（三 Tab）

```python
def assemble_qualification(result, analysis) -> dict:
    eligibility = analysis.get("eligibility", {})
    return {
        "qualification_items": _extract_qual_items(eligibility, result),
        "compliance_items": _extract_compliance_items(eligibility),
        "rejection_items": _extract_rejection_items(eligibility, result),
    }
```

### `scoring.py` — 评分标准

```python
def assemble_scoring(result, analysis) -> dict:
    scoring = analysis.get("scoring", {})
    dims = scoring.get("dimensions", [])
    return {
        "method": scoring.get("method", ""),
        "total_score": scoring.get("total_score", 0),
        "price_weight": scoring.get("price_weight", 0),
        "business": [d for d in dims if d.get("type") in ("biz", "商务")],
        "technical": [d for d in dims if d.get("type") in ("tech", "技术")],
    }
```

### `packages.py` — 分包信息

```python
def assemble_packages(result, analysis) -> dict:
    pkgs = result.packages_json or []
    if isinstance(pkgs, str):
        pkgs = json.loads(pkgs)
    return {
        "has_packages": len(pkgs) > 1,
        "current_package": _get_selected_package(result),
        "packages": pkgs,
    }
```

### `checklist.py` — 待确认清单（扁平化）

```python
def assemble_checklist(result, analysis) -> list:
    """从各模块提取需要人工确认的项，扁平输出。"""
    items = []
    # 从资格提取
    for q in _get_qual_items(result, analysis):
        items.append({
            "category": "qualification",
            "severity": "critical",
            "content": q["requirement"],
            "prep_guide": q.get("material", ""),
            "confirmed": _get_confirmed_flag(result.shared_resource_id, q["id"]),
        })
    # 从评分提取
    for d in _get_scoring_dims(analysis):
        items.append({
            "category": "scoring",
            "severity": "normal",
            "content": f"{d['name']}（{d['score']}分）",
            "prep_guide": d.get("criteria", ""),
            "confirmed": _get_confirmed_flag(result.shared_resource_id, d["name"]),
        })
    # 从废标提取
    for d in _get_disqual_items(result):
        items.append({
            "category": "must_pass",
            "severity": "fatal",
            "content": d["requirement"],
            "prep_guide": "",
            "confirmed": _get_confirmed_flag(result.shared_resource_id, d["id"]),
        })
    return items
```

## analysis.py 改造

### get_check_items() 流程

```python
def get_check_items(task_id):
    task = BiddingTask.query.get(task_id)
    if not task:
        return None
    
    sr_id = task.shared_resource_id
    if not sr_id:
        return None
    
    # 新路径：从 v3 模块组装
    from .analysis_v3.check_items import assemble_check_items
    result = assemble_check_items(sr_id)
    if result:
        return result
    
    # 降级：旧路径（兼容）
    return _legacy_get_check_items(task_id)
```

### 原 BiddingCheckItem 表

保留 `BiddingCheckItem` 表的 `confirmed_flag` 功能，`POST /check-items/confirm` 接口行为不变。新 check-items 返回的 `checklist` 中的 `confirmed` 字段从此表读取。

## 向后兼容

- `GET /check-items` 的 URL 不变，仅返回结构升级
- 旧的 `items` 数组保留在 `checklist` 字段中（兼容前端已有逻辑）
- `POST /check-items/confirm` 接口不变
