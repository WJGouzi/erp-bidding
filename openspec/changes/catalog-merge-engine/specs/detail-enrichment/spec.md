# 阶段3：详情填充 — 规格说明

## 输入

```python
business_items = [
    {"content": "付款方式：按批次据实结算...", "source_section": ""},
    {"content": "交付地点：成都市疾控中心...", ...},
    ...
]
technical_items = [
    {"content": "黄热病毒核酸实时荧光PCR检测试剂盒A", ...},
    ...
]
packages = [{... table_items: [...]}]
classified_items = {
    "qualification": [...],
    "compliance": [...],
    "disqualification": [...],
    "scoring": [...],
}
```

## 输出（修改后的 skeleton）

各节点的 children 和 description 被填充。

### 3.1 商务偏离表子项

从 business.items 按关键词匹配生成子项：

| 关键词 | 子项标题 | 说明 |
|-------|---------|------|
| 付款 | 付款方式响应 | 取匹配项 content[:80] 作 desc |
| 交付地点 | 交货地点 | |
| 交付要求|交货时间 | 交货时间 | |
| 验收 | 验收方案 | |
| 售后 | 售后服务承诺 | |
| 质保 | 质保期承诺 | |
| 报价方式 | 报价方式说明 | |

一个 business item 匹配多个关键词时，只取第一个匹配。

### 3.2 技术偏离表描述

从 packages.table_items 统计产品数量，填充到 description：

```python
"description": f"共{product_count}种产品，逐项响应技术参数要求"
```

无产品数据时，用 technical.items 数量。

### 3.3 资格项填充

去重规则：按 `requirement[:20]` 去重。

填充规则：
- 骨架已有"资格"节点 → 填入 children
- 骨架无"资格"节点但 items 存在 → 新增节点，插在比选函之后

### 3.4 实质性要求填充

同资格项模式，查找或新增"实质性要求"节点。
