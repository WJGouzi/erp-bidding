# 标书内容质量管理 — 设计

> 本文档描述了从"一把梭给 LLM"到"四路分流"的架构升级方案。
> 基于 10 份真实招标文件的模式分析。

---

## 真实招标文件模式分析

分析 10 份招标文件（含政府采购、比选、海关采购等），第三章"比选申请文件格式"呈现规律：

| 模板类别 | 出现频率 | 示例 | 处理方式 |
|---------|---------|------|---------|
| 文本填空 | 100% | 承诺函、授权书、声明函 | 填空引擎 |
| 表格填写 | 90% | 报价一览表、偏离表、业绩表 | 表格引擎 |
| 资格证明清单 | 100% | 第四章 | 资料插入 |
| 自由方案 | 100% | 技术方案、实施计划 | LLM 写作 |

占位符模式多样性：
```
XXX（比选申请人名称）              ← 标准
XXX（法定代表人姓名、职务）         ← 复合字段
XXX（比选编号：XXX）               ← 嵌套
XXX                   （...）      ← 不规则空格
____元（大写：________________）   ← 下划线
比选日期：  年   月   日             ← 隐式空白
```

---

## 改造后：四路分流架构

```
                        ┌──────────────────────────────┐
                        │        章节分类器              │
                        │  _classify_chapter_type()      │
                        │                                │
                        │  TEMPLATE_TEXT  ← 承诺函/声明函等│
                        │  TEMPLATE_TABLE ← 报价表/应答表等│
                        │  QUALIFICATION ← 资格证明文件   │
                        │  FREE_WRITE    ← 技术方案/描述  │
                        └──────────┬───────────────────┘
                                   │
          ┌────────────────────────┼────────────────────┐
          ▼                        ▼                     ▼
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│   填空引擎        │   │   表格填充引擎    │   │   资格证明插入    │
│                  │   │                  │   │                  │
│ LLM识别占位符     │   │ 检测表头列结构    │   │ 提取要求清单      │
│ → 确定性替换      │   │ → docx `<w:tbl>` │   │ → 匹配主体资料    │
│ → diff 校验      │   │ → 数据填充       │   │ → 插扫描件/图片   │
│ → 保留原文       │   │                  │   │ → 标记缺失       │
└──────────────────┘   └──────────────────┘   └──────────────────┘
                                                            │
                                                            ▼
                                                  ┌──────────────────┐
                                                  │   LLM 写作引擎   │
                                                  │  （保留现有逻辑） │
                                                  │  知识库+置信度门控│
                                                  └──────────────────┘
```

---

## 路径 A：填空引擎（LLM 识别 + 确定性替换）

### 流程

```
模板原文:
"本单位XXX（比选申请人名称）参加XXX（项目名称）的比选活动"

         │
         ▼
Step 1: LLM 识别占位符
         Prompt: "找出下面文本中所有需要填写的空白位置，
                  返回JSON数组，每个元素包含raw_text和field_hint"
         → [{"raw": "XXX（比选申请人名称）", "hint": "公司名称"},
            {"raw": "XXX（项目名称）", "hint": "项目名称"}]
         │
         ▼
Step 2: 字段映射
         hint: "公司名称" → SubjectCompany.company_name = "成都智能科技有限公司"
         hint: "项目名称" → analysis_result.project_name = "2024年智能采购系统建设项目"
         │
         ▼
Step 3: 确定性替换（从右向左，避免偏移）
         "本单位成都智能科技有限公司参加2024年智能采购系统建设项目的比选活动"
         │
         ▼
Step 4: 原文锁定校验
         只变了"XXX（比选申请人名称）"→"成都智能科技有限公司"
         和"XXX（项目名称）"→"2024年智能采购系统建设项目"
         其他字符逐位对比一致 → ✅
```

### LLM 识别 prompt 设计

```
你是一个占位符识别助手。你的任务是从招标文件模板中找出所有需要填写的空白位置。

规则：
1. 只识别，不填充，不改写原文
2. 返回 JSON 数组格式
3. 每个元素包含：
   - "raw": 占位符原文
   - "start": 起始字符位置
   - "end": 结束字符位置
   - "hint": 推测的字段含义

示例输入：
"本单位XXX（比选申请人名称）参加XXX（项目名称）的比选活动"
示例输出：
[{"raw": "XXX（比选申请人名称）", "start": 3, "end": 15, "hint": "公司名称"},
 {"raw": "XXX（项目名称）", "start": 17, "end": 27, "hint": "项目名称"}]

注意：如果无法推断 hint，用 "unknown"。
如果文本中没有占位符，返回空数组 []。
```

### 字段映射注册表

| field_hint | 数据源 | 取值方法 |
|-----------|--------|---------|
| 公司名称、申请人名称、单位名称等 | SubjectCompany | company_name |
| 统一社会信用代码 | SubjectCompany | credit_code |
| 法定代表人姓名 | SubjectCompany/材料 | company_name 或材料中提取 |
| 联系电话 | SubjectCompany | contact_phone |
| 联系地址 | SubjectCompany | address |
| 项目名称 | BiddingAnalysisResult | project_name |
| 项目编号 | BiddingAnalysisResult | project_no |
| 招标人、采购人 | BiddingAnalysisResult | bidder_name |
| 代理机构 | BiddingAnalysisResult | agent_name |
| 预算金额 | BiddingAnalysisResult | budget_amount |
| 日期 | 计算 | 当前日期 |

---

## 路径 B：表格填充引擎

### 检测

表格模板特征：
- 章节标题含：报价一览表、偏离表、应答表、业绩表、人员情况表、基本情况表
- 正文含表头列名（如 "序号、名称、数量、单价、总价"）

### 处理

```
识别为表格模板
  │
  ├── 从招标分析结果中提取数据
  │    ├── 报价表 → 从 technical_requirements 中提取产品清单
  │    └── 偏离表 → 提取技术要求条目 + 生成响应情况
  │
  └── 生成 docx 表格
       ├── `<w:tbl>` + Table Grid 样式
       ├── 表头行（加粗）
       └── 数据行
```

---

## 路径 C：资格证明文件插入引擎

### 提取要求清单

从分析结果的 `qualification_requirements` 和 `qualification_review` 中提取：
- "需提供法定代表人或主要负责人授权委托书"
- "需提供营业执照复印件"
- "需提供纳税证明材料"

### 匹配主体资料

```
提取的要求关键词                   主体资料类型
─────────────────────────────────────────
营业执照、法人证书                → BUSINESS_LICENSE
法定代表人身份证明                → LEGAL_PERSON_STATEMENT
授权委托书                       → AUTHORIZATION_LETTER
被授权人身份证                    → AUTHORIZED_PERSON_ID_CARD
纳税证明、社保                   → FINANCIAL_STATEMENT
资质声明函                       → QUALIFICATION_DECLARATION
廉洁承诺书                       → INTEGRITY_COMMITMENT
```

### 处理

```
已上传 → 在 docx 对应位置插入：
         章节标题 + 说明文字 + 扫描件图片/文本摘录

未上传 → 记录到"待人工补齐清单"：
         "法定代表人身份证明：缺少，请上传"
```

---

## 路径 D：LLM 写作引擎（保留现有逻辑）

见 `_generate_chapter_content` 现有实现：
- system_prompt + user_prompt 构造
- 知识库上下文注入
- 主体资料上下文注入
- 约束注入（质量保证模块）

后续增强：置信度门控
- OCR 文本置信度 < 0.7 → 不入 prompt
- 召回相关性 RRF score < 0.3 → 丢弃
- LLM 输出一致性校验 → 生成"可疑内容清单"

---

## 章节分类器设计

```python
def _classify_chapter_type(chapter_title, chapter_desc, tender_text):
    """分类章节类型，决定走哪条处理路径。"""

    combined = f"{chapter_title} {chapter_desc}"

    # 1. 文本模板检测
    TEXT_KEYWORDS = ["承诺函","声明函","授权书","廉洁承诺",
                     "响应函","资格证明","身份证明","授权委托"]
    if any(kw in combined for kw in TEXT_KEYWORDS):
        return "TEMPLATE_TEXT"

    # 2. 表格模板检测
    TABLE_KEYWORDS = ["报价一览表","报价表","偏离表","应答表",
                      "业绩一览表","人员情况表","基本情况表"]
    if any(kw in combined for kw in TABLE_KEYWORDS):
        return "TEMPLATE_TABLE"

    # 3. 资格证明检测
    QUAL_KEYWORDS = ["资格证明","资格审查","资质证明"]
    if any(kw in combined for kw in QUAL_KEYWORDS):
        return "QUALIFICATION"

    # 4. 其他 → LLM 写作
    return "FREE_WRITE"
```

## 数据完整流

```
招标文件上传 → 分析（v3 pipeline）
  → 提取 analysis_data.effective_text
  → 提取 qualification_requirements
  → 提取 technical_requirements, business_requirements
      │
      ▼
目录生成
  → 每章调用 _classify_chapter_type()
      │
      ├── TEMPLATE_TEXT:    填空引擎 → 直接返回填充后文本
      ├── TEMPLATE_TABLE:   表格引擎 → 生成 docx 表格段
      ├── QUALIFICATION:  资格证明 → 返回资料占位指令
      └── FREE_WRITE:      LLM 写作 → _generate_chapter_content()
      │
      ▼
_docx_assembly 阶段
  ├── 普通文本 → _write_formatted_content()
  ├── 表格段   → _write_table_from_data()
  └── 资格证明 → _insert_qualification_documents()
```
