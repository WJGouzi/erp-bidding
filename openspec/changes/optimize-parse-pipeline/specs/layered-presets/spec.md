# 预设清单双层分类 (bid_type × document_type)

## 目标
将资格扫描模板从 `bid_type` 单层分类扩展为 `bid_type × document_type` 双层分类，使同一采购类型下不同采购模式的模板差异化。

## 影响文件
- `app/service_modules/task_pipeline/analysis_v3/phase2_eligibility.py` — `ELIGIBILITY_TEMPLATES` 扩展 + `scan_eligibility()` 签名
- `app/service_modules/task_pipeline/analysis_v3/__init__.py` — `start_analyze_v3()` 传递 `doc_type` 到 Phase 2

## 设计原则

### 模板内容的三层通用性分级

```
通用性         层级          示例
⭐⭐⭐    全局通用      营业执照、财务报告、纳税社保、信用记录
⭐⭐      文档类型通用   招标→投标保证金   比选→代理服务费
⭐        采购内容通用   货物→经营许可    工程→施工资质
⚪        项目特定      （不应进预设模板）
```

**规范：ELIGIBILITY_TEMPLATES 中只允许 ⭐⭐⭐ 和 ⭐⭐ 级别的条款。** ⭐ 级别（如"配送能力"）属于采购内容分类（GOODS/SERVICE/ENGINEERING），在现有 bid_type 模板中已存在。⚪ 级别（项目特有）不进入预设。

### 双层分类的维度含义

```
第一层 bid_type（采购内容）→ 第二层 document_type（采购方式）
     GOODS               ×         TENDER
     SERVICE             ×         SELECTION
     ENGINEERING         ×         NEGOTIATION
                                 INQUIRY

例如：GOODS_TENDER = 货物公开招标
     GOODS_SELECTION = 货物比选
     SERVICE_NEGOTIATION = 服务竞争性谈判
```

每层的关注点：
- **bid_type 层**：采购物的本质属性（货物要经营许可、工程要施工资质、服务要人员资质）
- **document_type 层**：采购流程的结构差异（招标要保证金、比选要服务费、询价要一次性报价）

## 改动内容

### 1. 模板 KEY 变更

```python
# 当前：
ELIGIBILITY_TEMPLATES = {
    "GOODS": { ... },
    "SERVICE": { ... },
    "ENGINEERING": { ... },
}

# 目标：扩展为复合 KEY，每个复合模板 = 基础模板 + 采购方式特有条款
ELIGIBILITY_TEMPLATES = {
    # ── 全局基础条款（所有标书共有） ──
    "_BASE": {
        "通用资格": ["营业执照", "独立承担民事责任", "法人资格", "合法注册"],
        "财务状况": ["财务报告", "审计报告", "资产负债表", "利润表", "现金流量表"],
        "纳税社保": ["纳税", "税收", "社会保障", "社保", "完税"],
        "信用记录": ["信用中国", "政府采购网", "失信被执行人", "重大税收违法"],
    },
    
    # ── 货物基础模板 ──
    "GOODS": {
        "特定资格": ["经营许可", "备案", "许可证", "资质证书", "注册证"],
        "★实质性": ["★", "实质性要求", "不满足"],
        "废标条件": ["废标", "无效投标", "拒收", "不予受理", "否决", "投标无效"],
        "联合体": ["联合体", "联合体投标"],
        "业绩要求": ["业绩", "类似项目", "类似业绩"],
    },
    
    # ── 服务基础模板 ──
    "SERVICE": {
        "特定资格": ["资质", "许可证", "认证", "专业资质"],
        "人员资格": ["人员", "项目负责人", "从业资格", "专业技术人员"],
        "★实质性": ["★", "实质性要求", "不满足"],
        "废标条件": ["废标", "无效投标", "拒收", "不予受理", "否决", "投标无效"],
        "联合体": ["联合体", "联合体投标"],
        "业绩要求": ["业绩", "类似项目", "类似业绩"],
    },
    
    # ── 工程基础模板 ──
    "ENGINEERING": {
        "特定资格": ["施工资质", "建筑业", "安全生产许可", "安全生产许可证"],
        "项目经理": ["项目经理", "项目负责人", "注册建造师", "项目管理人员"],
        "★实质性": ["★", "实质性要求", "不满足"],
        "废标条件": ["废标", "无效投标", "拒收", "不予受理", "否决", "投标无效"],
        "联合体": ["联合体", "联合体投标"],
        "业绩要求": ["业绩", "类似项目", "类似业绩", "工程业绩"],
    },
    
    # ── 采购方式特有条款（叠加到基础模板之上） ──
    # TENDER（公开招标）特有
    "TENDER": {
        "投标保证金": ["投标保证金", "投标担保", "保证金"],
        "履约保证金": ["履约保证金", "履约保函", "履约担保"],
        "投标有效期": ["投标有效期"],
        "标书费用": ["招标文件售价", "文件售价", "标书费"],
    },
    
    # SELECTION（比选/遴选）特有
    "SELECTION": {
        "代理服务费": ["代理服务费", "代理费", "服务费"],
        "中选/成交": ["中选", "成交", "比选"],
    },
    
    # NEGOTIATION（竞争性谈判）特有
    "NEGOTIATION": {
        "谈判轮次": ["谈判", "谈判小组"],
        "最终报价": ["最终报价", "最后报价", "二次报价"],
    },
    
    # INQUIRY（询价）特有
    "INQUIRY": {
        "报价要求": ["一次性报价", "报价单", "报价表"],
        "最低价成交": ["最低价", "最低评标价"],
    },
    
    # ── bid_type × document_type 复合模板（可覆盖上述默认值） ──
    # 这一层可选：仅在有特殊需求时定义，不定义则自动组合 base + doc_type
}
```

### 2. 模板合并逻辑

```python
def _get_template(bid_type, doc_type):
    """双层模板获取 + 自动合并。"""
    # 1. 先取复合模板（最高优先级）
    composite_key = f"{bid_type}_{doc_type}"
    if composite_key in ELIGIBILITY_TEMPLATES:
        return ELIGIBILITY_TEMPLATES[composite_key]
    
    # 2. 自动合并 base + bid_type + doc_type
    template = {}
    # 全局基础
    template.update(ELIGIBILITY_TEMPLATES.get("_BASE", {}))
    # bid_type 层
    template.update(ELIGIBILITY_TEMPLATES.get(bid_type, {}))
    # doc_type 层
    template.update(ELIGIBILITY_TEMPLATES.get(doc_type, {}))
    
    return template
```

### 3. 函数签名修改

```python
def scan_eligibility(sections, bid_type, doc_type="TENDER"):
    """执行生死线扫描 — 纯关键词匹配，零LLM。
    
    Args:
        sections: StructuredDocument.sections
        bid_type: "GOODS" / "SERVICE" / "ENGINEERING"
        doc_type: "TENDER" / "SELECTION" / "NEGOTIATION" / "INQUIRY"
    """
    template = _get_template(bid_type, doc_type)
    # ... 原有逻辑不变
```

### 4. 调用链路修改

```python
# start_analyze_v3() 中
metadata = extract_metadata(...)  # Phase 1

doc_type_val = metadata.get("document_type", {}).get("value", "TENDER")
# ... Phase 1 结束后
eligibility = scan_eligibility(sections, bid_type, doc_type_val)  # 传入 doc_type
```

## 通用性验证

| 条款 | 通用性 | 依据 |
|------|--------|------|
| 营业执照 | ⭐⭐⭐ 全局通用 | 任何采购项目都需要 |
| 财务报告 | ⭐⭐⭐ 全局通用 | 政府采购法规定 |
| 纳税社保 | ⭐⭐⭐ 全局通用 | 政府采购法规定 |
| 信用记录 | ⭐⭐⭐ 全局通用 | 近三年无违法记录是基本要求 |
| 投标保证金 | ⭐⭐ 招标特有 | 招标投标法 |
| 代理服务费 | ⭐⭐ 比选特有 | 比选采购工作规范 |
| 谈判轮次 | ⭐⭐ 谈判特有 | 政府采购非招标方式管理办法 |
| 一次性报价 | ⭐⭐ 询价特有 | 政府采购非招标方式管理办法 |

## 验收标准
1. 比选文件能扫到"代理服务费"等采购方式特有条款
2. 公开招标文件能扫到"投标保证金"、"履约保函"等
3. 复合模板未定义时自动从 base + bid_type + doc_type 合并
4. 项目特定内容（如"配送能力"）不出现在预设模板中
5. 不影响现有已解析文档的兼容性

## 不包含
- 不改动数据库 schema
- 不增加新的 bid_type 分类
- 不从实际标书内容推导项目特有关键词
