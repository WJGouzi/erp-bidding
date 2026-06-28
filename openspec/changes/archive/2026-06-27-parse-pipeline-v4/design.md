# 设计文档

## 总体架构

```
文件上传
   │
   ▼
document_parser.py (DOCX/PDF/DOC)
   │ 统一 StructuredDocument
   ▼
analysis_v3/start_analyze_v3()
   │
   ├── Phase 1: 规则元数据（纯规则，零LLM）
   │   ├── 正则规则集匹配（含多位置交叉验证）
   │   └→ 输出：metadata dict
   │
   ├── 分包检测 → 按包分流（或无分包则整体）
   │
   ├── Phase 2: 生死线扫描（纯规则）
   │   ├── 章节智能定位
   │   ├── 预设模板关键词匹配
   │   └→ 输出：eligibility dict
   │
   ├── Phase 3: 评分拆解（纯规则）
   │   ├── 表格优先（Docx表格 + 文本表格启发式）
   │   ├── 段落匹配得分点规则
   │   └→ 输出：scoring dict + packages dict
   │
   └── Phase 4: 策略分析（可选LLM）
       ├── 跨包/跨章节整合
       └→ 输出：strategy dict
```

## 编码规范

所有与 DB 交互的 JSON 序列化必须遵循：
```python
json.dumps(data, ensure_ascii=False)  # 必须加 ensure_ascii=False
```

`parsed_json` 字段为 LargeBinary，存储的是 UTF-8 编码的 bytes：
```python
# 写入
parsed_json = json.dumps(data, ensure_ascii=False).encode("utf-8")
# 读取
data = json.loads(cached.parsed_json.decode("utf-8"))
```

## JSON 预处理规范

所有 LLM 输出在经过 json.loads 之前，必须调用统一清洗函数：

```python
def _preprocess_json(text: str) -> str:
    """清洗 JSON 字符串：去除 trailing comma、控制字符、BOM 等。"""
    text = text.strip()
    # 去掉 BOM
    if text.startswith("\ufeff"):
        text = text[1:]
    # 去除控制字符（保留换行和 tab）
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # 去除 trailing comma 在 } 和 ] 前
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)
    return text
```

## 预设清单模板设计

按 bid_type 分三类模板，每类下分"通用资格""特定资格"等类别：

- **GOODS**: 通用资格 + 财务状况 + 纳税社保 + 信用记录 + 经营许可 + ★实质性 + 废标条件
- **SERVICE**: 通用资格 + 财务状况 + 纳税社保 + 信用记录 + 专业资质 + 人员资格 + ★实质性 + 废标条件
- **ENGINEERING**: 通用资格 + 财务状况 + 纳税社保 + 信用记录 + 施工资质 + 项目经理 + 安全生产 + ★实质性 + 废标条件

## 分包感知

```
无分包 → 整体执行 Phase 2 → Phase 3 → 单份分析数据
有分包 → 逐个包执行 Phase 2 → 逐个包执行 Phase 3 → 合并结果
```

## 纯文本表格检测

检测规则：
1. 连续行中每行都包含 "|" 或 "\t"
2. 或连续行中列对齐（空格数一致）
3. 或连续行以 "─┌┐└┘├┤┬┴┼" 等制表符字符开头（ASCII art 表格）
