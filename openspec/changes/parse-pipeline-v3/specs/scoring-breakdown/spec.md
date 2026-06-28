# Scoring Breakdown

## 能力说明
识别并结构化解析标书的评分表，输出评分维度树。

## 输入
- 包含"评标办法""评分""评审"等关键词的 section
- `LLMAdapter`

## 输出
```json
{
  "method": "comprehensive",
  "total_score": 100,
  "dimensions": [
    {"name": "报价", "score": 30, "type": "objective"},
    {"name": "配送方案", "score": 32, "type": "subjective", "sub_dimensions": [...]}
  ]
}
```

## 解析策略
1. 在 sections 中搜索评分相关章节
2. 优先解析表格结构（ContentBlock.type == "table"）
3. 无表格时用 LLM 从文本中提取
4. 检测评分类型（客观/主观/半客观）
5. 主观评分检测子维度
