# Quality Assurance — Specification

## Overview

构建从招标要求到最终标书的完整质量保证体系。核心是需求追踪矩阵（Requirement Traceability Matrix），在生成前确保所有要求有对应章节，在生成后校验每条要求是否被覆盖、是否有编造。

## Three-Tier Evidence Architecture

```
生成阶段获取证据的优先级:

                        ┌─────────────────────────────────────┐
                        │  第一层: 主体资料（确定性注入）        │
                        │                                     │
                        │  来源: subject_material_file 表       │
                        │  方式: 精确查找，不经过检索            │
                        │  适用: 营业执照、资质证书、法人身份证、  │
                        │        授权委托书、财务报表等           │
                        │                                     │
                        │  规则: 如果需求追踪矩阵标注了           │
                        │  HAS_EVIDENCE 且有 material_file_id,  │
                        │  直接读全文注入，不走知识库              │
                        └──────────────┬──────────────────────┘
                                       │ 无匹配时降级
                                       ▼
                        ┌─────────────────────────────────────┐
                        │  第二层: 知识库（语义检索）            │
                        │                                     │
                        │  来源: doc_chunks (ChromaDB + MySQL) │
                        │  方式: 多路召回（向量 + 关键词）       │
                        │  适用: 行业报告、类似案例、通用参考     │
                        │                                     │
                        │  规则: 第一层无匹配时，走 MultiRecall  │
                        │  引擎从知识库 Chunk 中检索              │
                        └──────────────┬──────────────────────┘
                                       │ 始终执行
                                       ▼
                        ┌─────────────────────────────────────┐
                        │  第三层: 招标文件原文（需求依据）      │
                        │                                     │
                        │  来源: collection=tender             │
                        │  方式: 多路召回                      │
                        │  适用: 招标要求的原文片段             │
                        │                                     │
                        │  规则: 始终执行，作为 Prompt 的       │
                        │  需求依据部分                         │
                        └─────────────────────────────────────┘
```

## Architecture

```
分析阶段                     目录阶段                   生成阶段                    生成后
─────────                   ────────                  ────────                  ────────
招标文件 → 解析
  → 版面解析
  → LLM 逐条提取要求 → atomic_requirement_items (47条)
       │
       │ 与主体已有材料交叉比对:
       │   ├─ REQ-006 ISO9001 → subject_material_file.id=42  ✅ 第一层
       │   ├─ REQ-007 法人代表 → subject_material_file.id=15  ✅ 第一层
       │   ├─ REQ-008 营业执照 → subject_material_file.id=3   ✅ 第一层
       │   ├─ REQ-009 高级工程师 → 无匹配                     ❌ 降级第二层/留空
       │   └─ REQ-010 项目案例 → 无匹配但知识库可能有         🔍 第二层检索
       │
       ▼
  需求追踪矩阵完成:
  每条 requirement 标记了 evidence_source
                                      目录确定
                                      → embedding 语义匹配章节
                                      → 生成 章节-需求 映射表
                                                             Prompt 注入:
                                                               ● 第一层: 主体材料全文直接嵌入
                                                               ● 第二层: MultiRecall 召回结果
                                                               ● 第三层: tender 集合原文片段
                                                               ● 废标硬约束
                                                             LLM 生成正文
                                                                          LLM-as-Judge 校验:
                                                                          ● 逐条检查覆盖率
                                                                          ● 逐条检查编造
                                                                          ● 输出差异报告
```

## Requirements

### R1: 需求追踪矩阵构建（分析阶段）
#### R1.1 原子化提取
- SHALL 将 analysis_data 中的结构化信息拆分为原子级 requirement items
- SHALL 每条 requirement 包含：item_id, 原文, 类型, 级别, 来源文件

#### R1.2 证据状态标注 — 三层优先级
- SHALL 将每条 requirement 与主体已有材料逐条交叉比对：
  - `HAS_EVIDENCE`（第一层）：主体已有对应材料，记录 `material_file_id` 和 `material_type`
  - `KB_AVAILABLE`（第二层）：主体无对应材料，但知识库可能有关联内容
  - `NO_EVIDENCE`：完全无支撑材料

#### R1.3 第一层匹配规则
- SHALL 根据 `material_type` 与 `requirement_type` 的映射表做精确匹配：

| requirement_type | 匹配的 material_type |
|---|---|
| qualification | QUALIFICATION_FILE, BUSINESS_LICENSE |
| qualification_review | QUALIFICATION_FILE, BUSINESS_LICENSE |
| business | FINANCIAL_STATEMENT, INTEGRITY_COMMITMENT |
| legal | LEGAL_PERSON_ID_CARD, LEGAL_PERSON_STATEMENT, AUTHORIZATION_LETTER, AUTHORIZED_PERSON_ID_CARD |

- SHALL 同时通过语义匹配（标题+描述 vs 材料文件名）补充匹配
- SHALL 匹配到的材料文件记录 `file_id`，用于后续全文注入

#### R1.4 废标项特殊处理
- SHALL 废标项标记为 `HARD_CONSTRAINT`
- SHALL 存储到单独列表中，用于全局约束注入

### R2: 章节-需求绑定（目录阶段）
#### R2.1 语义匹配
- SHALL 使用千问 embedding 将章节标题+描述向量化
- SHALL 与每条 requirement 的向量计算余弦相似度
- SHALL 相似度 > 阈值（0.6）的判定为关联

#### R2.2 绑定矩阵
```
┌──────────────────┬──────┬──────┬──────┬──────┬──────┐
│                  │ 要求1 │ 要求2 │ 要求3 │ 要求4 │ 要求5 │
├──────────────────┼──────┼──────┼──────┼──────┼──────┤
│ 第一章 项目概况   │  ✓   │      │      │      │      │
│ 第二章 资质响应   │      │  ✓   │  ✓   │      │      │
│ 第三章 技术方案   │      │      │      │  ✓   │  ✓   │
│ ...              │      │      │      │      │      │
└──────────────────┴──────┴──────┴──────┴──────┴──────┘
```

#### R2.3 未覆盖告警
- SHALL 检查是否有 requirement 未绑定到任何章节
- SHALL 如有未覆盖要求，在 UI 中告警提示

### R3: Prompt 约束注入（生成阶段）
#### R3.1 每章 Prompt 结构
```
系统指令:
  - 你是投标文件编写助手
  - 严格遵守以下约束

本章信息:
  - 章节标题: 第二章 资质要求
  - 章节子项: (一) 企业资质、(二) 人员资质

需要响应的招标要求 (5条):

─── 第一层: 主体已有材料（精确注入，必须使用）───

  ✅ REQ-006 [资质] ISO9001认证
      → 主体已上传: ISO9001认证证书.pdf（文件ID:42）
      → 证书全文: "兹证明XX公司已通过ISO9001:2015质量管理体系认证..."

  ✅ REQ-007 [资质] 营业执照
      → 主体已上传: 营业执照.pdf（文件ID:3）
      → 执照信息: "统一社会信用代码: 91110108MA... 注册资本: 5000万..."

─── 第二层: 知识库检索结果（语义匹配）───

  🔍 REQ-009 [资质] 高级工程师
      → 知识库未匹配到相关材料，请在正文中留空

─── 第三层: 招标文件原文要求 ───

  📋 REQ-010 投标人须具有ISO9001认证
  📋 REQ-011 项目负责人须高级工程师职称

─── 硬约束（全程不可违反）───

  🔴 废标项1: 不得有串标行为
  🔴 废标项2: 投标有效期不足90天

写作约束:
  - [第一层] 主体已有材料 → 必须引用，正文中嵌入证书信息
  - [第二层] 知识库检索到 → 可作为参考引用
  - [第二层] 知识库未检索到 → 仅写"详见...承诺"或留空
  - ❌ 不得编造第一层/第二层之外的资质和能力
```

#### R3.2 第一层材料的全文注入
- SHALL 对 `HAS_EVIDENCE` 的材料读取全文（不截断 800 字）
- SHALL 使用 `_read_file_text()` 获取完整文本
- SHALL 在 Prompt 中以"主体已有材料"区块呈现，明确标注材料来源
- SHALL 在正文中引用时自动标注出处脚注

#### R3.3 结构化输出要求
- SHALL 要求 LLM 输出结构化 JSON：
```json
{
  "content": "正文内容...",
  "citations": [
    {"id": 1, "type": "subject_material", "file_id": 42, "file_name": "ISO9001认证证书.pdf"},
    {"id": 2, "type": "tender", "section": "第三章 > 3.2"}
  ],
  "sub_items_covered": ["（一）企业资质", "（二）人员资质"],
  "word_count": 456
}
```

### R4: 生成后校验
#### R4.1 校验内容
每章生成后 SHALL 校验：
1. **覆盖率**：本章关联的 N 条要求，正文是否逐条覆盖
2. **第一层材料使用检查**：HAS_EVIDENCE 的材料是否被正确引用
3. **编造检测**：NO_EVIDENCE 的要求，正文是否编造了内容
4. **废标检查**：正文是否违反了任何废标项
5. **字数检查**：是否符合 word_count_level 要求
6. **子项完整性**：本章子项是否全部覆盖

#### R4.2 校验方法
- SHALL 使用 LLM-as-Judge：将本章关联的 requirements + 主体材料 + 正文发给校验 LLM
- SHALL 校验 LLM 返回结构化结果：
```json
{
  "chapter_title": "第二章 资质要求",
  "checks": [
    {
      "requirement_id": "REQ-006",
      "evidence_tier": 1,
      "text": "ISO9001认证",
      "covered": true,
      "hallucinated": false,
      "evidence_used": true
    },
    {
      "requirement_id": "REQ-007",
      "evidence_tier": 1,
      "text": "高级工程师",
      "covered": false,
      "hallucinated": true,
      "evidence_used": false,
      "detail": "正文声称'我公司有高级工程师5人'，但第一层无此材料，第二层/第三层也未检索到"
    },
    {
      "requirement_id": "REQ-015",
      "evidence_tier": 2,
      "text": "同类项目业绩",
      "covered": true,
      "hallucinated": false,
      "evidence_used": true
    },
    {
      "requirement_id": null,
      "evidence_tier": null,
      "text": "串标禁止",
      "violated": false
    }
  ],
  "word_count": 456,
  "expected_word_count": "300-500",
  "sub_items_covered": ["（一）企业资质", "（二）人员资质"],
  "sub_items_missing": [],
  "overall": "FAIL"
}
```

#### R4.3 校验结果处理
- `PASS` → 进入下一章或 docx 组装
- `WARN`（覆盖率≥80%但有编造嫌疑）→ 标记给用户审核
- `FAIL`（覆盖率<80%或确认编造）→ 自动重试（调整 prompt 后重新生成）
- 重试超过 2 次仍 FAIL → 标记为人工审核

### R5: 覆盖率报告
#### R5.1 生成后总报告
SHALL 输出可读的覆盖率报告：
```
📊 生成覆盖率报告
───────────────────────────────────
总招标要求: 47条
  第一层(主体材料)覆盖: 15条 ✅ 全部引用
  第二层(知识库)覆盖:   22条 ✅
  第三层(招标要求):    7条 ✅
  未覆盖(留空):        3条 ⚠️

编造检测: 通过 ✅（发现 0 处编造）
废标检查: 通过 ✅（0 项违反）
字数达标: 10/12 章通过

⚠️ 未覆盖要求（留空处理）:
  - REQ-009 高级工程师资质 → 主体未上传，知识库未匹配，已留空

📋 建议:
  - 补充"高级工程师"资质证书后可重新生成，届时将升级为第一层直接注入
```

### R6: 性能与降级
- R6.1 单章校验耗时 SHALL ≤ 5 秒
- R6.2 校验 LLM SHALL 使用轻量模型（如 qwen-turbo）以节约成本
- R6.3 校验失败时 SHALL 至少有 2 次自动重试
- R6.4 重试仍失败时 SHALL 降级为 WARN 状态，不阻塞整体生成流程

## Scenarios

### Scenario: 主体有材料，直接注入
- **GIVEN** REQ-006 "ISO9001认证" 匹配到 subject_material_file.id=42
- **WHEN** 生成"第二章 资质要求"
- **THEN** 第一层材料全文注入 Prompt，不走知识库检索
- **AND** 校验时检查正文是否引用了此材料
- **AND** 如未引用 → WARN，提示"主体已有ISO9001证书但正文未引用"

### Scenario: 主体无材料，走知识库检索
- **GIVEN** REQ-009 "高级工程师" 无匹配的主体材料
- **WHEN** 生成"第二章 资质要求"
- **THEN** 降级到第二层：走 MultiRecall 从知识库检索
- **AND** 知识库也无匹配 → Prompt 中标注 "未检索到匹配材料，请留空"
- **AND** 校验时检查是否编造 → 如编造则 FAIL

### Scenario: 废标项违反
- **GIVEN** 废标项"不得联合体投标"
- **WHEN** 生成了"我公司将与XX公司组成联合体"
- **THEN** 校验 LLM 检测到 violated=true
- **AND** 立即触发重试（注入更强约束）
