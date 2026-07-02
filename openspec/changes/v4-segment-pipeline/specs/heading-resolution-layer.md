# 标题语义解析层 — 设计规格

## 概述

解决解析器将"内容列举"误判为"章节标题"的问题，从根本上消除空节点对下游的污染。

---

## 一、问题定义

### 1.1 三道误判模式

```
模式A: "文件组成"列举                                优先级: P0
────────────────────────────────────────────────────────────
文档特征: "3.1 比选文件包括下列内容：第一章 比选公告..."
误判路径: `^第X章` 正则命中 → 创建 level-1 空章节
判定: 招标文件在正文中列举自己的章节构成
产出: 7 个空节点

模式B: 中文编号条款 (一、二、三)                      优先级: P0
────────────────────────────────────────────────────────────
文档特征: "一、坚持客观、公平、公正原则..."
误判路径: `^[一二三四五六七八九十]+、` 正则命中
判定: 正文中的条件/声明/制度描述
产出: 每文件 3-4 个空节点

模式C: 数字编号条款 (1. 2. 3.)                      优先级: P0
────────────────────────────────────────────────────────────
文档特征: "1.具有独立承担民事责任的能力；"
误判路径: `^\d+[、，,．.]` 正则命中
判定: 正文中的资格条件/要求条款列表
产出: 每文件 38-42 个空节点（最严重）
```

### 1.2 受影响文件

| 文件 | 空节点数 | 模式分布 |
|------|---------|---------|
| 组织研磨器采购项目 | 42 | C(39) + B(3) |
| 采购文件(传染病检测) | 39 | C(38) + B(1) |
| 德阳疾控比选文件 | 7 | A(7) |
| 成都海关2026 | 4 | B(4) |
| **合计** | **92** | **三层覆盖** |

### 1.3 污染链

```
误判假标题 → 加入 section_index → 作为正式分段 → 分析引擎空转 → 目录推断多出无效章节 → 生成阶段浪费
                                  → 占据子节点位置 → 真内容被推到错误层级
```

---

## 二、方案设计：三层安检门

### 2.1 第一道：结尾标点拦截（命中率最高）

**规则**: 若 Normal 样式的文本以句号/分号/冒号结尾，判定为内容段落，不创建章节。

但需要排除"纯中文编号标题"——如果文本是 `^[一二三四五六七八九十]+[、，]` 格式且 ≤15 字（例如"一、项目概况。"），即使有结尾标点也保留标题资格。因为此类文本在 Normal 样式中极可能是有结尾标点的真标题。

```
匹配模式:
  text.endswith(('。', '；', '：', ';', ':'))

例外条件（仍保留标题资格）:
  re.match(r'^[一二三四五六七八九十]+[、，]', text)
  AND len(text) <= 15
  AND 不包含"：""。"之外的句子成分

作用范围: 只在 text_heading > 0 且 heading_level == 0 的路径中生效
```

**覆盖效果**: 消掉模式 C 全部 39 个空节点（"1.具有独立承担民事责任的能力；"）

**代码位置**: `document_parser.py` → `_parse_docx_structured()` → `text_heading > 0 and heading_level == 0` 分支内

```python
# 在如下位置插入
if text_heading > 0 and heading_level == 0:
    # 安检门1: 结尾标点 → 判定为内容段落
    # 例外: 纯中文编号标题（一、二、三…）即使有标点也保留
    _is_chinese_num_title = bool(
        re.match(r'^[一二三四五六七八九十]+[、，]', text)
        and len(text) <= 15
    )
    if text.endswith(('。', '；', '：', ';', ':')) and not _is_chinese_num_title:
        block = ContentBlock(ContentBlock.TYPE_PARAGRAPH, text)
        current_section.content.append(block)
        continue
    
    # 原有逻辑
    if "\t" in text:
        block = ContentBlock(ContentBlock.TYPE_PARAGRAPH, text)
        current_section.content.append(block)
        continue
    ...
```

### 2.2 第二道：事后扫描降级（替代循环内缓冲）

**决策变更**: review 后从"循环中滑动缓冲"改为"解析完成后一次性扫描"。理由是：

```
旧方案（已废弃）:
  - 在 for 循环中维护 heading_buffer
  - 看到第3个同级命中时降级前2个
  - 问题：混合级别列举、缓冲清空逻辑复杂、状态泄漏风险

新方案（采纳）:
  - 解析完成后，遍历 section_index
  - 发现连续 3+ 个同级别且全无内容的节点 → 批量降级为内容
  - 实现简单，无状态管理，不影响解析循环
```

**规则**: 解析完成后检测 section 树。如果发现满足以下条件的节点组：

```
条件:
  1. 同一父节点下
  2. 同 level
  3. 连续（索引相邻）
  4. 数量 ≥ 3
  5. 每个节点 content 都为空，且标题不包含真正章节关键字（第X章）

→ 这组节点全部判定为"列举列表"，从章节树中移除
→ 所有标题作为段落文本重新加入父节点的 content 中
```

**覆盖效果**: 消掉模式 A 全部 7 个空节点（连续 8 个"第X章"列举）

**实现伪代码**:

```python
def _cleanup_fake_headings(sections):
    """事后扫描：移除连续同层级的假标题"""
    
    def _scan_children(parent_section):
        children = parent_section.children
        if not children:
            return
        
        i = 0
        while i < len(children):
            # 找连续同级别空节点组
            j = i
            while (j < len(children) 
                   and children[j].level == children[i].level
                   and len(children[j].content) == 0
                   and not _is_real_chapter(children[j].title)  # 第X章单独检查
                   and len(children[j].children) == 0):
                j += 1
            
            count = j - i
            if count >= 3:
                # 批量降级：标题变段落，加入父节点内容
                for k in range(i, j):
                    block = ContentBlock(
                        ContentBlock.TYPE_PARAGRAPH,
                        children[k].title
                    )
                    parent_section.content.append(block)
                # 从章节树中移除
                del children[i:j]
            else:
                # 不够3个，保持原样，递归处理子节点
                for k in range(i, j):
                    _scan_children(children[k])
                i = j
        
        # 递归处理每个子节点
        for child in children:
            _scan_children(child)
    
    # 从顶层开始
    for section in sections:
        _scan_children(section)
```

### 2.3 第三道：标题长度阈值（消除声明型误判）

**规则**: 文本标题检测命中的 Normal 段落，如果原文长度 > **40** 字（review 后从 30 调整），判定为内容段落。

```
阈值: 40 字符（中文）
调整说明: 30 字边界存在风险。"第五章 采购需求、技术规格、商务…及其他要求"可达 30+ 字
         40 字为常见真实招标标题留足余量

依据: 对测试文档的统计，真实标题最长 26 字
      模式B误判的 "一、坚持客观、公平、公正原则，按照法律制度…" 35+ 字
```

**覆盖效果**: 消掉模式 B 全部 4 个空节点（成都海关的声明、组织研磨器的格式说明）

**实现**:
```python
if text_heading > 0 and heading_level == 0:
    # 安检门3: 标题过长 → 判定为内容段落（≥40字）
    if len(text) >= 40:
        block = ContentBlock(ContentBlock.TYPE_PARAGRAPH, text)
        current_section.content.append(block)
        continue
```

### 2.4 三扇安检门联合效果

```
安检门      | 模式A(7) | 模式B(4) | 模式C(39) | 合计(50)
───────────┼─────────┼─────────┼──────────┼─────────
标点结尾    |    -    |    -    |   39     |   39     ✅
事后扫描    |    7    |    -    |    -     |    7     ✅
长度阈值    |    -    |    4    |    -     |    4     ✅
──────────────────────────────────────────────────────
合计消掉    |    7    |    4    |   39     |   50     ✅ 100%
```

---

## 三、章节索引后处理（第二层兜底）

### 3.1 去重策略

**review 调整**: 去重 key 从 title 改为 `(title, level, parent_id)` 三元组。防止同名但不同位置的真章节被误删。

```python
def clean_section_index(index: list) -> list:
    """清洗章节索引：删除冗余的空节点"""
    
    # Step 1: 构建有内容章节的标识集合
    # key = (title, level, parent_id) 三元组
    content_keys = set()
    content_ids = set()
    for entry in index:
        if _has_real_content(entry):
            key = (entry["title"], entry["level"], entry.get("parent_id"))
            content_keys.add(key)
            content_ids.add(entry["id"])
    
    # Step 2: 删除无内容的重复节点
    # "第一章 比选公告" level=1 parent=None 出现两次，
    # 一个有内容一个没内容 → 删掉没内容的
    clean = []
    for entry in index:
        key = (entry["title"], entry["level"], entry.get("parent_id"))
        should_drop = (
            entry["title"] and                                # 有标题
            key in content_keys and                           # 同名同级同父有内容版本
            entry["id"] not in content_ids                    # 但本节点无内容
        )
        if not should_drop:
            clean.append(entry)
    
    return clean


def _has_real_content(entry: dict) -> bool:
    """判断章节节点是否有真实内容"""
    has_content = bool(entry.get("content"))  # 直接内容
    has_children = bool(entry.get("children_ids"))  # 有子节点
    page_range = entry.get("page_range", []) or []
    has_pages = len(page_range) >= 2 and page_range[1] > page_range[0]  # 跨页
    return has_content or has_children or has_pages
```

### 3.2 触发时机

在每次 `build_section_index()` 返回前自动调用清洗，调用方无需感知。

---

## 四、测试验证

### 4.1 解析器回归测试

对全部 10 个招标文件运行解析，断言：

```
1. section_index 中无 content=0 & children=0 的条目
2. 真实章节（Heading 1/2 样式创建）均保留
3. 章节层级关系与原文一致
4. 无章节数量异常（不影响文档覆盖率）
```

### 4.2 执行命令

```bash
python3 -c "
from app.infrastructure.document_parser import DocumentParser
parser = DocumentParser()
# 逐个文件验证解析结果无空节点
"
```

### 4.3 预期质量指标

| 指标 | 当前值 | 目标值 |
|------|-------|-------|
| 空节点率 | 40% (4/10 文件) | 0% |
| 误判章节数 | 92 | 0 |
| 章节还原准确率 | ~85% | 99%+ |
| 真实结构损失率 | — | 0% (不误删真实标题) |

---

## 五、风险与边界

### 5.1 安检门1 误杀风险

以句号结尾的真实标题罕见但存在，主要在 Normal 样式中。"一、项目概况。"这种标题被拦截后，会丢失一个节的结构信息。

修复措施：安检门1加了"纯中文编号标题例外"，`^[一二三四五六七八九十]+[、，]` 且 ≤15 字的即使有结尾标点也不拦截。

### 5.2 事后扫描的边界

扫描只在"连续 3+ 同级别空节点"时触发，对以下模式天然免疫：

```
第一章 比选公告           ← 有内容（非空）
(大量正文...)
第二章 比选须知           ← 有内容（非空）
```

因为节点有内容或子节点，`_has_real_content` 返回 True，不会被扫描判定为假标题。

### 5.3 安检门3 的 40 字阈值

| 检核标题 | 长度 | 判定 |
|---------|:---:|:----:|
| "第一章 比选公告" | 8字 | ✅ 真实标题，保留 |
| "一、采购项目基本情况" | 10字 | ✅ 保留 |
| "第五章 采购项目技术、服务、采购合同内容条款及其他商务要求" | 26字 | ✅ 保留 |
| "第五章 采购需求、技术规格、商务要求及和其他要求" | 30字 | ✅ 保留 |
| "一、坚持客观、公平、公正原则，按照法律制度规定和委托代理协议的约定办理政府采购事宜，规范代理行为" | 50字 | ❌ 拦截 |

最长真实标题 26 字，40 字阈值安全。

---

## 六、与现有流程的集成

```
修改前:                             修改后:
parser → raw_index → segmented    parser → 安检门过滤 → clean_index → 事后扫描 → segmented
                                                         ↓
                                                     后处理去重 → final_index
```

