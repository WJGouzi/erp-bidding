# to_dict() v3 路径修复

## 目标
修复 `BiddingAnalysisResult.to_dict()` 在 `version == "v3"` 时跳过独立字段的问题。

## 影响文件
- `app/domain/models.py` — `BiddingAnalysisResult.to_dict()` 的 v3 分支

## 改动内容

在 v3 返回 dict 中补充以下字段（直接从 `self.*` 读取）：

```python
if parsed.get("version") == "v3":
    return {
        "id": self.id,
        "shared_resource_id": self.shared_resource_id,
        "raw_text": self.raw_text or "",
        "effective_text": self.effective_text or "",
        "analysis_data": parsed,
        # 新增：独立字段
        "overview": self.overview or "",
        "requirements": self.requirements or "",
        "business_requirements": self.business_requirements or "",
        "qualification_requirements": self.qualification_requirements or "",
        "technical_requirements": self.technical_requirements or "",
        "scoring_items": self.scoring_items or "",
        "disqualification_items": self.disqualification_items or "",
        # 原有
        "packages_json": _safe_json_load(self.packages_json) if self.packages_json else None,
        "document_type": self.document_type or "",
        "package_count": self.package_count or 0,
        "created_at": self.created_at.isoformat() if self.created_at else None,
        "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }
```

## 验收标准
1. API 返回 `/api/bidding/tasks/{id}/analysis-result` 包含 overview、requirements、business_requirements 等字段
2. 这些字段的内容与数据库一致（来自 `_complete_analysis` 的赋值）
3. 不破坏现有 v3 返回结构（analysis_data 保持不变）
4. `remote_%_summary` 等字段保持原有空值行为

## 不包含
- 不改动数据库 schema
- 不改变 `_complete_analysis` 的写入逻辑
- 不改动 fallback 路径
