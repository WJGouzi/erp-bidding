# 按章节逐项提取设计方案

## 1. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Pipeline Orchestrator                     │
│  (读取文档 → 按章节分派 → 逐章提取 → 合并 → 后处理)        │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
      ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
      │ Chapter 1    │ │ Chapter 2    │ │ Chapter 3-8  │
      │ Extractor    │ │ Extractor    │ │ Extractors   │
      │ (公告/邀请)   │ │ (须知前附表)  │ │ (各自逻辑)    │
      └──────────────┘ └──────────────┘ └──────────────┘
              │               │               │
              └───────────────┼───────────────┘
                              ▼
                    ┌──────────────────┐
                    │    Merger        │
                    │  (合并 + 去重)    │
                    └──────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │  analysis_data   │
                    └──────────────────┘
```

## 2. 章节 -> 提取器映射

### 标准章节定义

以《政府采购法实施条例》为标准模板，8章结构映射：

| 标准章节名 | 别名 | 提取器 | 产出块 |
|-----------|------|--------|--------|
| 投标邀请/比选公告 | 采购公告 | `InvitationExtractor` | metadata |
| 投标人须知 | 比选须知 | `InstructionsExtractor` | 前附表 + 规则 |
| 资格条件 | 供应商资格条件 | `EligibilityExtractor` | 资格清单（已有） |
| 资格证明材料 | — | `DocListExtractor` | 提交文件清单 |
| 技术要求 | 项目技术/服务要求 | `RequirementExtractor` | 服务/技术参数 |
| 投标文件格式 | 响应文件格式 | `FormatExtractor` | 材料清单 |
| 评标办法 | 评审方法 | `ScoringExtractor` | 评分表（已有） |
| 合同模板 | 采购合同 | `ContractExtractor` | 合同要点 |

### 章节识别策略

```python
CHAPTER_MAP = {
    "invitation":  ["投标邀请", "比选公告", "磋商邀请", "招标公告", "询价通知"],
    "instructions": ["投标人须知", "比选须知", "供应商须知", "磋商须知"],
    "eligibility":  ["资格条件", "供应商资格", "投标人资格"],
    "eligibility_docs": ["资格证明材料", "应当提供的资格"],
    "requirements": ["技术", "服务", "采购项目", "商务要求"],
    "formats":      ["投标文件格式", "响应文件格式", "申请文件格式"],
    "scoring":      ["评标办法", "评审方法", "评审办法", "评分方法"],
    "contract":     ["合同模板", "采购合同", "合同条款"],
}
```

### 提取器接口

```python
class ChapterExtractor(ABC):
    """每个章节提取器的基类"""
    
    @abstractmethod
    def extract(self, section: Section, doc_text: str) -> dict:
        """从该章节提取结构化数据"""
        pass
    
    @property
    @abstractmethod
    def output_schema(self) -> dict:
        """该提取器输出的 JSON schema"""
        pass
```

## 3. 各提取器的具体设计

### 3.1 InvitationExtractor（第一章：投标邀请/比选公告）

**当前捕获字段：** project_name, project_code, purchaser, agent

**应补充：**
- `bid_opening` — 开标时间（当前为空）
- `file_purchase_start/end` — 标书购买时间
- `file_purchase_location` — 获取地点
- `file_purchase_price` — 文件售价
- `bid_submission_location` — 投标文件递交地点
- `contact_info` — 联系人详细信息
- `special_declaration` — 特殊声明（如"未达到采购限额"）

**提取方式：** 正则规则（对第一章全文应用）

### 3.2 InstructionsExtractor（第二章：须知前附表）

**当前捕获字段：** evaluation_method, allow_consortium, bid_security_required,
performance_security_pct, bid_validity_days

**应补充（从须知前附表的表格提取）：**
- `中标人数量` — 按包（采购包1：5家，采购包2：3家…）
- `比选保证金` — 金额或"不收取"
- `履约保证金` — 金额或比例
- `代理服务费` — 收费标准和金额
- `验收方式及标准` — 如"财库〔2016〕205号"
- `报价要求` — 一次性报价/多轮报价
- `联合体` — 是否允许（已有但需确认）
- 其他表格行

**提取方式：** 表格解析（识别须知前附表的"序号 | 内容 | 说明与要求"表）

### 3.3 DocListExtractor（第四章：资格证明材料）

**当前状态：** 未实现，资格证明文件列表与资格条件混在一起

**应提取：**
- 需要提交的资格证明文件清单
- 每个文件的格式要求（原件/复印件/扫描件）
- 是否需要盖章/公证

**提取方式：** 列表项提取（识别"一二三四"或"1234"编号列表）

### 3.4 RequirementExtractor（第五章：技术/服务要求）

**当前状态：** 完全未实现

**应提取：**
- `service_period` — 服务期限/交货期限
- `delivery_location` — 配送/交货地点
- `payment_terms` — 付款方式/付款条件
- `acceptance_standard` — 验收标准
- `warranty` — 质保期
- `after_sale_service` — 售后服务要求
- `training` — 培训要求
- `technical_parameters` — 技术参数/规格（特别是对货物类）
- `★条款` — 该章节的★实质性要求（已有部分）
- 货物/服务清单（表格）

**提取方式：** 
- 通用字段：正则规则扫描整章
- 货物清单：表格解析
- ★条款：★前缀扫描

### 3.5 FormatExtractor（第六章：投标文件格式）

**当前状态：** 完全未实现

**应提取：**
- 需要提交的响应文件清单（比选申请函、报价函、承诺函…）
- 正/副本数量要求（1正2副）
- 装订/密封要求
- 签字盖章要求
- 电子文档要求

**提取方式：** 关键词 + 上下文提取

### 3.6 ScoringExtractor（第七章：评标办法）

**当前状态：** 已有实现，需增强

**应增强：**
- 评分维度名称的完整性（当前截断）
- 子维度/评分细则的映射
- 评分标准的详细描述
- 按包评分（分包项目各包评分标准不同时）

### 3.7 ContractExtractor（第八章：合同模板）

**当前状态：** 未实现

**应提取：**
- 合同关键条款摘要
- 违约责任
- 争议解决方式
- 合同有效期

## 4. 包感知设计

分包项目的核心挑战：信息在章节内按包划分，但章节结构不变。

### 处理策略

```python
class PackageAwarePipeline:
    """自动检测分包并分发提取"""
    
    def run(self, doc, raw_text):
        # 1. 检测是否分包（已有）
        package_nos = detect_packages(raw_text)
        
        if not package_nos:
            # 无分包：整体提取
            return self._extract_single(raw_text)
        
        # 2. 提取公共信息（适用于所有包）
        common = self._extract_common(raw_text)
        
        # 3. 按包提取（识别"采购包X:"前缀）
        per_package = {}
        for pkg_no in package_nos:
            pkg_text = self._get_package_context(raw_text, pkg_no)
            per_package[pkg_no] = self._extract_package(pkg_text, pkg_no)
        
        # 4. 合并
        return merge(common, per_package)
```

### 包信息提取

对于每个包，提取：
- `package_name` — 包名（"试剂耗材配送服务"）
- `budget` — 分包预算（如有）
- `winner_count` — 拟招入供应商数量
- `special_qualifications` — 该包专用资格条件
- `starred_clauses` — 该包★条款
- `scoring` — 该包评分标准（如按包分别评分）
- `parameters` — 技术参数（已有框架）

## 5. 与现有 v3 架构的集成

### 方式一：渐进增强（推荐）

保持现有 v3 的三层框架，在每层内部按章节细化：

```
v3 管线 (保留)                     增强（新增）
────────────────────────────────────────────────
Layer 1: 元数据提取                  InvitationExtractor
  → 固定字段规则                     InstructionsExtractor（前附表部分）
  
Layer 2: 生死线扫描                  继承复用（资格条件/★条款）
  → 资格关键词匹配                   

Layer 3: 评分 + 分包                 ScoringExtractor（增强）
  → 表格解析                        RequirementExtractor（新增）
                                     FormatExtractor（新增）
                                     ContractExtractor（新增）
```

### 方式二：全新管线

完全替换 v3 为章节驱动管线，更清晰但改动更大。
建议先方式一迭代，验证后考虑方式二。

## 6. analysis_data 结构扩展

```json
{
  "version": "v3",
  "pipeline_status": "completed",
  
  "metadata": { /* 现有元数据 + 新增字段 */ },
  "eligibility": { /* 现有 */ },
  "scoring": { /* 现有 + 增强 */ },
  "packages": [ /* 现有 + 增强 */ ],
  "strategy": { /* 现有 */ },
  
  "chapter_details": {
    "invitation": {
      "file_purchase_period": "2025年06月13日至06月17日",
      "file_purchase_location": "德阳市汇通大厦A栋23楼",
      "file_purchase_price": 400,
      "bid_submission_location": "四川中宸项目管理有限公司开标室",
      "contact_info": {
        "purchaser_contact": "邬老师 / 0838-2518393",
        "agent_contact": "周女士 / 0838-2301555"
      },
      "special_declaration": "本项目未达到政府采购限额"
    },
    "instructions_table": [
      {"seq": 1, "item": "确定邀请公开比选的供应商数量和方式", "value": "3家及以上；公告方式"},
      {"seq": 4, "item": "联合体", "value": "不允许联合体"},
      {"seq": 5, "item": "比选方法", "value": "综合评分法"},
      {"seq": 6, "item": "成交供应商的数量", "value": "包1:5家,包2:3家,包3:3家"},
      {"seq": 7, "item": "比选保证金", "value": "不收取"},
      {"seq": 8, "item": "履约保证金", "value": "不收取"}
    ],
    "requirements": {
      "service_period": "3年",
      "delivery_location": "德阳市疾病预防控制中心指定地点",
      "payment_terms": "交货验收合格后，付款时间由双方协商确定",
      "acceptance_standard": "财库〔2016〕205号",
      "has_starred_clauses": true
    },
    "submission_docs": [
      "比选申请函",
      "法定代表人身份证明书",
      "比选申请书签署授权委托书",
      "报价表",
      "资格证明文件"
    ]
  }
}
```

## 7. 优先级建议

### Phase 1（当前迭代已完成的问题修复）
- [x] 文档解析器 __root__ 章节嵌套修复
- [x] 正则规则增强（采购项目名称、比选人、据实结算）
- [x] raw_text 回退机制

### Phase 2（推荐下一步）
1. InstructionsExtractor — 须知前附表表格解析（获取评标方法、保证金、中标人数量等）
2. InvitationExtractor 增强 — 补充开标时间、标书购买时间等
3. RequirementExtractor — 服务期限、付款、验收（需确认是否需要额外字段）

### Phase 3（后续迭代）
4. FormatExtractor — 提交文件清单
5. ContractExtractor — 合同要点
6. PackageAwarePipeline — 包级提取增强
