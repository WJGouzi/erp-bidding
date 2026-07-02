# 目录生成层 — 设计规格

## 概述

重建 catalog_inference.py 的目录生成逻辑，从"分析数据驱动"进一步演进为"固定骨架 + 动态填充"模式，
解决 HARD 项污染顶层目录、目录结构不稳定、缺少关键章节等问题。

---

## 一、问题定义

### 1.1 当前目录生成的三大缺陷

```
缺陷1: HARD 项污染顶层目录
────────────────────────────────────────────────────────────────────────
现状: catalog_inference 将所有 mandate_items 插入为顶层章节
结果: CG文件 → 11章（前7章都是来自"响应文件格式"的 HARD 子项）
      成都海关 → 30+ 章（各种承诺函、声明函全变成独立章节）
根因: 没有区分"独立章节HARD项"和"子章节HARD项"

缺陷2: 目录结构不稳定
────────────────────────────────────────────────────────────────────────
现状: 目录章节完全由分析数据的字段空/非空决定
结果: 同类型标书可能生成不同结构的目录
      缺少统一定位的响应框架

缺陷3: 缺少关键章节
────────────────────────────────────────────────────────────────────────
现状: 没有"投标函"、"授权书"等强制格式的默认章节
结果: 这些关键项只在被 mandate_classifier 检测到时才出现
      漏检则整章消失
```

### 1.2 根因分析

```
catalog_inference.py 当前逻辑:
  skeleton = []
  for each 分析字段:
      if 字段非空:
          skeleton.append(对应章节)
  return skeleton  # ← 完全被数据驱动，没有固定框架

问题在于: 分析数据的质量直接决定目录质量
          分析数据错了 → 目录跟着错
          分析数据漏了 → 相应章节直接消失
```

---

## 二、方案设计：固定骨架 + 动态填充

### 2.1 核心架构

目录生成采用**三级优先级**，确保不违反招标文件明确要求：

```
P0: 招标文件明确指定的响应结构（最高优先级）
    来源: 招标文件中"响应文件组成"章节的显式要求
    行为: 严格遵循原文顺序和内容，不增不减

P1: 8 章标准投标骨架（默认优先级）
    来源: 预定义 + bid_type 裁剪
    行为: 按分析数据动态填充子项
    
P2: 硬编码兜底（不应走到这里）
    来源: 检测到 0 条需求
    行为: 单一"综合响应"章节
```

### 2.2 预定义的投标骨架

```python
BID_SKELETON = [
    {
        "id": "quotation",
        "title": "报价部分",
        "mandate_level": "HARD",
        "fill_strategy": "TEMPLATE",
        "description": "报价函、报价一览表、分项报价明细表",
        "required": True,
        "detection_key": "quotation",
    },
    {
        "id": "auth_and_declare",
        "title": "法定代表人授权书及声明函",
        "mandate_level": "HARD",
        "fill_strategy": "TEMPLATE",
        "description": "法定代表人授权书、声明函、承诺函等强制格式文件",
        "required": True,
        "detection_key": "mandate_aggregate",
    },
    {
        "id": "qualification",
        "title": "资格证明文件",
        "mandate_level": "SOFT",
        "fill_strategy": "QUALIFICATION",
        "description": "营业执照、资质证书、许可证等资格证明材料",
        "required": True,
        "detection_key": "eligibility",
    },
    {
        "id": "technical",
        "title": "技术方案",
        "mandate_level": "FREE",
        "fill_strategy": "KB_FIRST",
        "description": "技术参数响应、产品配置方案",
        "required": True,
        "detection_key": "technical",
    },
    {
        "id": "business",
        "title": "商务条款响应",
        "mandate_level": "FREE",
        "fill_strategy": "KB_FIRST",
        "description": "付款方式、交货期、质保等商务条款应答",
        "required": False,
        "detection_key": "business",
    },
    {
        "id": "scoring",
        "title": "评分标准响应",
        "mandate_level": "FREE",
        "fill_strategy": "KB_FIRST",
        "description": "针对评分细则的逐项响应",
        "required": False,
        "detection_key": "scoring",
    },
    {
        "id": "service",
        "title": "售后服务及培训方案",
        "mandate_level": "FREE",
        "fill_strategy": "KB_FIRST",
        "description": "售后服务承诺、技术培训及应急响应方案",
        "required": False,
        "detection_key": "service",
    },
    {
        "id": "other_commitments",
        "title": "其他承诺及补充材料",
        "mandate_level": "FREE",
        "fill_strategy": "MANUAL",
        "description": "其他未归类的承诺函、声明及补充文件",
        "required": False,
        "detection_key": "catch_all",
        "max_children": 5,  # review 后新增：设上限，防止垃圾桶爆炸
    },
]
```

### 2.3 三步生成流程

```
Step 1: 检测招标文件是否指定了响应结构（P0 优先级）
────────────────────────────────────────────
从 comprehensive_json 中搜索"响应文件组成""响应文件格式"
"投标文件的组成"等关键章节。

如果找到:
  → 直接按该章节指定的格式提取目录结构
  → 跳过 Step 2，进入 Step 3 后处理

如果没找到:
  → 走 Step 2 默认骨架

Step 2: 骨架初始化 + 分析数据注入（P1 默认优先级）
────────────────────────────────────────────
从 BID_SKELETON 创建完整目录树
此时所有 required=True 的章节已在骨架中

将 comprehensive_json 的各项注入骨架：

  skeleton[quotation]       ← 报价函、报价一览表、分项报价明细表
  skeleton[auth_declare]    ← 授权书、声明函、承诺函（聚合）
  skeleton[qualification]   ← 营业执照、许可证…（从主体信息表）
  skeleton[technical]       ← 技术参数、产品配置（从知识库匹配）
  skeleton[business]        ← 付款、交货、质保（从产品库匹配）
  skeleton[scoring]         ← 各评分维度及分值
  skeleton[service]         ← 售后服务要求
  skeleton[catch_all]       ← 未归属的其他强制项（上限 5）

Step 3: 后处理
────────────────────────────────────────────
  a. required=False 且无注入内容 → 从目录中移除该章节
  b. 空章节(required=True 但无可填充子项) → 标注 availability=empty
  c. catch_all 子项 > 5 → 触发告警日志，不静默添加
  d. 子项保留原文顺序（review 调整）
  e. 输出最终目录树
```

### 2.4 关键差异对比

```
                        当前方案                       新方案
                        ────────                       ──────
骨架来源            analysis_data 字段空/非空         三级优先级: P0 原文 > P1 骨架 > P2 兜底
HARD项处理         全部当顶层章节                     按来源分级: 独立章→顶层 / 格式子项→聚合 / 列表→引用
章节稳定性          不稳定(同类型每次可能不同)         稳定(P0 精准 / P1 稳定 / P2 兜底)
空章节处理          不出现(无数据直接消失)             保留(标注 availability=empty)
catch_all           无此概念                         有，上限 5 项
子项顺序            无序                             保留原文顺序
```

---

## 三、HARD 项分级规则

### 3.1 分级依据

```
HARD 项来源          | 目录层级    | 示例
─────────────────────┼───────────┼────────────────────────
独立章节             | 顶层章节    | "第六章 响应文件格式" 整体
   (来源于            |            | → 展开为 auth_declare 的子项列表
    独立 Heading 1）  |            |
                     |            |
子章节/内容列举      | 聚合子项    | "3、承诺函"（在"响应文件格式"
   (来源于            |            |  下的子项）
   子层级的 Normal)   |            | → 归入 auth_and_declare 的子项
                     |            |
单纯条款描述        | 不进入目录  | "1.具有独立承担民事责任的能力；"
   (经过安检门        | (仅作为    | → 只作为资格条件引用
    降级的内容文本)   |  内容引用)  |
```

### 3.2 聚合规则

当检测到多个 HARD 项（承诺函、声明函等）时，将它们**聚合**到 `auth_and_declare` 这一个顶层章节下。

```
聚合前的子项顺序: 按原文出现顺序排列
3、承诺函
4、中小企业声明函
7、残疾人福利性单位声明函
12、知识产权声明函

聚合后的子项序: 保留原文顺序
auth_and_declare:
  ├── 承诺函
  ├── 中小企业声明函
  ├── 残疾人福利性单位声明函
  └── 知识产权声明函
```

**review 调整**: 子项顺序保留原文出现顺序。

### 3.3 分级实现

```python
def _categorize_hard_items(hard_items: list, section_index: list) -> dict:
    """将 HARD 项按来源章节层次分成三级"""
    
    groups = {
        "top_level": [],     # 来自独立顶层章节 → 成为骨架内的子项
        "aggregated": [],    # 来自格式章节的子项 → 聚合入 auth_declare
        "scattered": [],     # 散落在正文中 → 归入 catch_all（可触发警告）
    }
    
    for item in hard_items:
        seg_id = item.get("segment_id")
        title = item.get("title", "")
        
        # 判断来源层级
        category = _determine_hard_category(item, section_index)
        groups[category].append(item)
    
    # scattered 超限告警
    if len(groups["scattered"]) > 5:
        logger.warning(
            "[catalog] 散落HARD项 %d 个超过上限5，请检查 mandate_classifier 质量",
            len(groups["scattered"])
        )
    
    return groups


def _determine_hard_category(item: dict, section_index: list) -> str:
    """单条 HARD 项判定
    
    策略:
      1. segment_id 有效 → 查来源章节层次
      2. segment_id 无效 → 文本相似度匹配已知章节
      3. 都不可用 → 归入 scattered
    """
    # 策略1: 通过 segment_id
    seg_id = item.get("segment_id")
    if seg_id and section_index:
        source = _find_section_by_id(seg_id, section_index)
        if source:
            if source["level"] <= 1:
                return "top_level"
            else:
                return "aggregated"
    
    # 策略2: 文本相似度（segment_id 缺失时的兜底）
    title = item.get("title", "")
    if _is_format_item(title):
        return "aggregated"
    if _is_scattered_item(title):
        return "scattered"
    
    return "scattered"


def _is_format_item(title: str) -> bool:
    """判断是否是格式章节子项的关键词匹配"""
    format_keywords = [
        "承诺函", "声明函", "授权书", "证明书",
        "报价表", "报价函", "报价一览表",
        "中小企业", "残疾人", "监狱企业",
        "知识产权", "3C", "本国产品",
    ]
    return any(kw in title for kw in format_keywords)
```

---

## 四、子项填充策略

### 4.1 报价部分

```python
children = []
children.append({"title": "报价函", "fill_strategy": "TEMPLATE"})
children.append({"title": "报价一览表", "fill_strategy": "TEMPLATE"})
if analysis_data.get("products"):
    children.append({
        "title": "分项报价明细表",
        "fill_strategy": "TEMPLATE",
        "description": f"含 {len(products)} 项产品",
    })
```

### 4.2 法定代表人授权书及声明函

```python
children = []
for item in hard_groups["aggregated"]:       # 原文顺序遍历
    children.append({
        "title": item["title"],
        "fill_strategy": "TEMPLATE",
        "description": item.get("description", ""),
    })
# 始终保留的稳定项（未检测到时也保留）
if not any("授权" in c["title"] for c in children):
    children.insert(0, {"title": "法定代表人授权书", "fill_strategy": "TEMPLATE"})
if not any("承诺" in c["title"] for c in children):
    children.append({"title": "承诺函", "fill_strategy": "TEMPLATE"})
```

### 4.3 资格证明文件

```python
children = []
for q in qualifications:
    children.append({
        "title": q["requirement"][:60],
        "fill_strategy": "SUBJECT_DATA",       # 主体信息表填充
        "description": q["requirement"][:200],
        "confidence": "NA",                    # 主体信息表数据视为确定项
    })
if not children:
    children.append({
        "title": "资格证明材料",
        "description": "待人工补充",
        "fill_strategy": "MANUAL",
    })
```

### 4.4 技术方案

```python
children = []
for i, req in enumerate(technical_requirements[:10]):
    children.append({
        "title": f"技术要求 {i+1}",
        "description": req["requirement"][:200],
        "fill_strategy": "KB_FIRST",
        "confidence": _match_confidence(req),
    })
if not children:
    children.append({
        "title": "技术方案响应",
        "description": "无匹配资料，待补充",
        "fill_strategy": "MANUAL",
    })
```

---

## 五、输出格式

### 5.1 统一目录节点格式

```python
{
    "id": "auth_and_declare",
    "chapter_no": 2,
    "title": "法定代表人授权书及声明函",
    "mandate_level": "HARD",
    "fill_strategy": "TEMPLATE",
    "source": "mandate_aggregate",
    "description": "共 5 项强制格式文件",
    "required": True,
    "priority": "P1",                         # P0 / P1 / P2
    "availability": {
        "status": "filled",                   # filled / partial / empty / pending
        "confidence": "HIGH",                 # HIGH / MEDIUM / LOW / NA
        "pending_items": [],                  # 待补充的具体内容列表
        "warning": "",                        # 用户可见的提示文字
    },
    "children": [
        {
            "title": "法定代表人授权书",
            "fill_strategy": "TEMPLATE",
            "mandate_level": "HARD",
        },
        {
            "title": "承诺函",
            "fill_strategy": "TEMPLATE",
            "mandate_level": "HARD",
        },
    ],
}
```

### 5.2 availability 状态定义

| status | 含义 | 填充方式 |
|--------|------|---------|
| filled | 全部子项有内容，可直接生成 | 正常走填充路由 |
| partial | 部分子项有内容，部分缺失 | 缺失项标注 "待补充" |
| empty | required=True 但无数据 | 整章留白，标 "无匹配资料" |
| pending | 等待外部数据（如主体信息表未提交） | 等待确认后填充 |

---

## 六、默认骨架的适应性

### 6.1 按 bid_type 裁剪

```
招标类型 | 报价 | 授权声明 | 资格 | 技术 | 商务 | 评分 | 售后 | 其他
货物采购 |  ✅  |    ✅   |  ✅  |  ✅  |  ✅  |  ✅  |  ✅  |  ⬜
服务采购 |  ✅  |    ✅   |  ✅  |  ✅  |  ✅  |  ✅  |  ✅  |  ⬜
工程采购 |  ✅  |    ✅   |  ✅  |  ✅  |  ✅  |  ⬜  |  ⬜  |  ✅
比选     |  ✅  |    ✅   |  ✅  |  ✅  |  ✅  |  ✅  |  ⬜  |  ✅
单一来源 |  ✅  |    ✅   |  ✅  |  ⬜  |  ✅  |  ⬜  |  ⬜  |  ✅
```

### 6.2 特殊说明

```
比选:
  - 有评分表 → 保留"评分标准响应"
  - 通常无售后服务 → "售后服务"裁剪掉

询价:
  - 无需"技术方案"
  - 核心在"报价部分"
  - 无评分表 → "评分标准响应"裁剪掉
```

---

## 七、测试验证

### 7.1 目录生成测试

对全部 10 个招标文件运行目录推断，验证：

```
1. 目录章节数稳定（同类型标书差异不超过 ±2 章）
2. 无 HARD 项独立成章导致目录膨胀
3. 关键章节（报价、资格、技术）始终存在
4. 空节点不产生目录项
5. 检测到的所有强制格式都出现在正确位置
6. catch_all 子项不超过 5
```

### 7.2 预期输出对照

```
德阳疾控比选文件 (目前 11 章 → 目标 7-8 章):
  现有: [报价部分] [授权声明] [资格证明] [技术方案] [商务响应] [评分响应] [+5个误HARD子项]
  目标: 同前 6 章 + [其他承诺]（聚合误HARD项）= 7 章 ✅

组织研磨器 (目前 8 章 → 目标 6-7 章):
  现有: 8 章（含空节点产生的虚假技术/商务项）
  目标: 消除空节点后，骨架驱动 = 6-7 章 ✅

政府采购首批 (目前 4 章 → 目标 6-7 章):
  现有: 缺报价、缺授权声明章节
  目标: 骨架包含报价/授权，覆盖率 100% ✅
```

### 7.3 评估指标

| 指标 | 当前值 | 目标值 |
|------|-------|-------|
| 章节数稳定性 | 变幅大(4-30+) | 标准类型 ±2 章 |
| HARD污染率 | 40%文件有 | 0% |
| 关键章节覆盖率 | ~60% | 100% |
| 空项标注率 | 0%（直接消失） | 100%（标注 availability） |
| catch_all 超限率 | 无此概念 | ≤5 项/文件 |

---

## 八、实现优先级

```
P0（必须，建完即生效）:
  □ 定义 BID_SKELETON 8 章骨架
  □ P0 检测：招标文件是否指定了响应结构
  □ 实现 HARD 项三级分级（segment_id + 文本相似度双策略）
  □ 骨架初始化 + 分析数据注入流程
  □ 空章节 availability 标注
  □ required=False 的隐藏逻辑
  □ catch_all 上限 5 项 + 超限告警

P1（重要）:
  □ 按 bid_type 裁剪骨架
  □ 子项顺序保留原文出现顺序
  □ children 填充上限（每个章节≤15子项）

P2（优化）:
  □ 子项 HARD > SOFT > FREE 排序（同级内）
  □ 不可达章节的智能警告
  □ 目录结构可视化预览
```

