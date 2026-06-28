# 文档类型仅做展示

## 目标
修复 `classify_document()` 缺失"竞争性磋商"的问题。doc_type 只用于 UI 展示，不驱动任何逻辑。

## 具体设计

### 修复 classify_document()
在 `type_keywords` 中增加"竞争性磋商"关键词：

```python
"NEGOTIATION": [
    "竞争性谈判公告", "竞争性谈判文件", "谈判邀请",
    "竞争性磋商公告", "竞争性磋商文件", "磋商邀请", "磋商公告",
    "磋商文件",
]
```

文件名判定增加"竞争性磋商"：

```python
if "竞争性谈判" in file_basename or "竞争性磋商" in file_basename:
    filename_type = "NEGOTIATION"
```

### 解耦 doc_type

当前：`scan_eligibility(sections, bid_type, doc_type)` → 模板选择依赖 doc_type
改为：`scan_eligibility_v2(sections)` → 不需要 doc_type 参数

### 保留 doc_type
- 在 metadata 中保留 document_type 字段
- API 返回中保留 document_type
- UI 展示"文档类型：竞争性磋商"等信息

## 验收标准
1. "竞争性磋商"文件正确识别为 NEGOTIATION
2. doc_type 不影响资格提取结果
3. API 返回的 document_type 值正确
