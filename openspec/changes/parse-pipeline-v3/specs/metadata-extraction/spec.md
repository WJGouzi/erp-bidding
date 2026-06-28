# Metadata Extraction

## 能力说明
从标书文档前部章节提取固定元数据字段，提供给 UI 展示和后续分析使用。

## 输入
- `StructuredDocument.sections[0:4]`（封面、目录、投标邀请、须知前附表）
- `LLMAdapter`

## 输出
```json
{
  "project_name": "四川国际旅行卫生保健中心2026年试剂耗材采购项目",
  "project_code": "ZY20260016ZC-ZJ-A",
  "purchaser": {"name": "", "contact": ""},
  "agent": {"name": "", "contact": ""},
  "budget": {"total": 10150000, "packages": {"1": 2740000}},
  "key_dates": {"bid_deadline": "", "bid_validity_days": 90},
  "evaluation_method": "comprehensive",
  "allow_consortium": false,
  "bid_type": "GOODS"
}
```

## 抽取策略
1. 规则优先（正则匹配项目编号、日期、金额等固定模式）
2. LLM 补充规则覆盖不到的字段
3. temperature=0.0，高确定性
