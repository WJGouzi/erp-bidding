# 基于章节结构的商务/技术要求提取

## 目标

用结构化的章节树导航替代 regex 全文搜索，从已解析的文档 sections 中准确定位并提取商务要求、技术要求。

## 为什么

当前做法：在 raw_text 中跑 20+ 条 regex，逐个字段匹配。

```
raw_text → regex(付款方式) → "24"（误匹配章节编号）
raw_text → regex(服务期限) → 未匹配（标书写的是"合同履行期限"）
```

问题：
1. regex 对文本格式敏感，不同标书写法不同
2. 容易误匹配（数字、章节号被当成值）
3. 表格中的商务内容被 flatten 后丢失结构
4. 每份新标书可能要调 regex

**而文档解析器已经解析出章节树，子章节标题就是天然的"字段名"。**

```
第五章 采购项目技术、服务、合同内容条款及商务要求
  └── 六、★商务要求
        ├── (一) 履约时间和地点    → extra.delivery_location + service_period
        ├── (二) 售后服务要求       → extra.after_sale_service
        ├── (三) 付款方式           → extra.payment_terms
        ├── (四) 包装与运输         → extra.packaging_transport
        ├── (五) 保险              → extra.insurance
        └── (六) 其他要求          → extra.other_requirements
```

## 设计

### 提取引擎

```python
def extract_business_from_sections(sections):
    """从文档章节树中提取商务要求。
    
    策略：
      1. 找到标题含"商务要求"的章节
      2. 遍历其子章节
      3. 根据子章节标题关键词归类
      4. 读取子章节内容（段落+表格）作为值
    """
    biz_section = _find_section_by_title(sections, "商务要求")
    if not biz_section:
        return {}  # fallback to regex
    
    result = {}
    for child in biz_section.children:
        title = child.title or ""
        content_text = _section_content_to_text(child)
        
        # 按标题关键词归类
        if any(kw in title for kw in ["付款", "支付", "结算"]):
            result["payment_terms"] = content_text
        elif any(kw in title for kw in ["交货", "交付", "供货", "配送"]):
            result["delivery_terms"] = content_text
        elif any(kw in title for kw in ["服务", "售后", "维修", "质保"]):
            result["after_sale_service"] = content_text
        elif any(kw in title for kw in ["履约时间", "服务期限", "合同期限"]):
            result["service_period"] = content_text
        elif any(kw in title for kw in ["质量", "验收", "标准"]):
            result["quality_acceptance"] = content_text
        # ... 更多映射
    
    return result
```

### 章节查找函数

```python
def _find_section_by_title(sections, keyword):
    """递归在章节树中查找标题含关键词的章节。"""
    for section in sections:
        title = section.title or ""
        if keyword in title:
            return section
        # 递归子章节
        found = _find_section_by_title(section.children, keyword)
        if found:
            return found
    return None
```

### 内容提取函数

```python
def _section_content_to_text(section):
    """提取章节的内容（段落+表格），保留结构。"""
    parts = []
    for block in section.content:
        if block.type == "paragraph" and block.text:
            parts.append(block.text)
        elif block.type == "table":
            # 保持表格结构
            header_line = " | ".join(block.headers) if block.headers else ""
            if header_line:
                parts.append(header_line)
            for row in block.rows:
                parts.append(" | ".join(row))
    return "\n".join(parts)
```

### 集成方式

```python
# 在 _complete_analysis() 或 metadata 提取中：

# 1. 先用章节提取（高精度）
section_extras = extract_business_from_sections(doc.sections)

# 2. 章节提取不到的字段用 regex 补漏
re_extras = extract_extra_via_regex(raw_text)

# 3. 合并：章节结果优先，regex 结果填充缺失字段
final_extras = {**re_extras, **section_extras}
```

## 效果对比

| 字段 | regex 方案 | 章节方案 |
|------|-----------|---------|
| 付款方式 | 搜"付款方式:"，匹配到章节编号"24"❌ | 定位到"(三) 付款方式"子章节，读内容 ✅ |
| 售后服务 | 搜"售后"，碰到合同模板中的"售后服务"❌ | 定位到"售后服务要求"子章节 ✅ |
| 交货地点 | 搜"交货地点"，格式不符未匹配❌ | 定位到"(一) 履约时间和地点"子章节 ✅ |
| 技术要求 | raw_text 中搜参数表，flatten 后丢失❌ | 定位到"技术要求"子章节，读表格 ✅ |

## 验收标准

1. 对 10 份测试标书，章节定位商务章节准确率 ≥ 90%
2. 商务字段填充率不低于 regex 方案
3. 误匹配率降低 80% 以上
4. 表格中的商务内容完整保留结构
