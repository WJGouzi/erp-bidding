# 评分表/商务/技术要求提取 — 规格说明

## 所属层级：Phase 3

## 评分表结构化

### 输入

评分表原始文本（从 table_parser 或 table_classifier 得到的表格数据）

### 规则部分（保持现有）

现有 `phase3_scoring.py` 的 `_detect_tech_table` / `_parse_tech_table` 用于产品清单提取。

评分表规则不足，由 LLM 补充。

### LLM 部分（新增）

**Prompt**：
```
你是一个招标文件解析专家。
以下是招标文件中的评分表，请结构化提取所有评分维度。

评分表：
{scoring_table_text}

以 JSON 格式返回：
{
  "method": "综合评分法/最低评标价法/…",
  "total_score": 总分（数字）,
  "dimensions": [
    {
      "name": "评分因素名称",
      "weight": 权重（如30%）,
      "score": 分值（数字）,
      "criteria": "评分标准描述",
      "type": "客观/主观"
    }
  ]
}
```

**输入示例**（来自 2025年检验科试剂耗材项目）：
```
序号 | 评分因素 | 分值 | 评分标准
1 | 报价 | 30 | 满足招标文件要求且投标报价最低的...
2 | 技术参数 | 30 | 完全满足招标文件技术要求得30分...
```

**期望输出**：
```json
{
  "method": "综合评分法",
  "total_score": 100,
  "dimensions": [
    {"name": "报价", "score": 30, "criteria": "满足招标文件要求且投标报价最低的...", "type": "客观"},
    {"name": "技术参数", "score": 30, "criteria": "完全满足招标文件技术要求得30分...", "type": "客观"},
    {"name": "项目实施方案", "score": 15, "criteria": "...", "type": "主观"},
    {"name": "履约能力", "score": 15, "criteria": "...", "type": "主观"},
    {"name": "售后服务", "score": 10, "criteria": "...", "type": "主观"}
  ]
}
```

**覆盖率**：当前 33% → 修复后 90%

## 商务要求提取

### 输入

"商务要求"或"商务条款"相关章节的文本

### 规则部分

现有正则覆盖常见字段：
```python
DELIVERY_LOCATION = r"(?:交货地点|配送地点|服务地点)[：:]\s*(.+)"
SERVICE_PERIOD = r"(?:交货期限|服务期限|交付时间)[：:]\s*(.+)"
PAYMENT_TERMS = r"(?:付款方式|支付方式|结算方式)[：:]\s*(.+)"
```

### LLM 部分

**Prompt**：
```
从以下招标文件章节中提取所有商务要求（非技术类的服务要求）。

章节文本：
{section_text}

以 JSON 格式返回商务要求列表：
{
  "business_requirements": [
    {
      "name": "要求名称（如交货时间、付款方式）",
      "requirement": "具体内容",
      "is_star": true/false,  // 是否为★实质性要求
      "importance": "critical/high/normal"
    }
  ]
}
```

## 技术要求提取

### 输入

"技术要求"、"技术参数"、"项目技术"相关章节

### 规则部分

现有 `_detect_tech_table` 覆盖产品清单表格提取。

### LLM 部分

**Prompt**：
```
从以下招标文件章节中提取所有技术要求/技术参数。

章节文本：
{section_text}

以 JSON 格式返回：
{
  "technical_requirements": [
    {
      "name": "参数名称",
      "requirement": "具体参数内容",
      "is_star": true/false,
      "is_arrow": true/false,  // 是否为▲重点扣分项
      "importance": "critical/high/normal"
    }
  ]
}
```
