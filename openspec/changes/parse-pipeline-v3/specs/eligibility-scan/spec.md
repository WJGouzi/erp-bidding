# Eligibility Scan

## 能力说明
按 bid_type 预设清单，对标书进行资格要求、废标条件和★实质性条款的扫描。

## 输入
- 全文 `StructuredDocument`
- `bid_type`（GOODS/SERVICE/ENGINEERING）
- `ELIGIBILITY_TEMPLATES` 预设清单
- `LLMAdapter`

## 输出
```json
{
  "qualifications": [{"id": "qual_01", "requirement": "", "found": true, "status": "passed"}],
  "disqualifications": [{"id": "disq_01", "condition": "", "severity": "fatal"}],
  "starred_requirements": [{"id": "star_01", "requirement": "", "verification": ""}]
}
```

## 扫描策略
1. 从 preset checklist 逐项扫描 sections
2. 找到对应原文则标为 "found" 并提取位置
3. 未找到则标为 "attention" 或 "missing"
4. ★标记的条款自动归类到 starred_requirements
