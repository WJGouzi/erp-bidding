# 封面检测与渲染重构 — 变更方案

## 动机

当前 `format_requirements` 中封面（封面/封皮）相关内容的识别与渲染存在三个核心缺陷：

1. **无 `is_cover` 标记**：`required_sections` 中的条目没有字段标识自身是否为封面，下游（catalog.py、helpers.py）只能靠标题字符串模糊匹配，路径迂回且脆弱
2. **封面被拆成两个独立 section**：封面标签（如"（资格性响应文件封面、封皮）"）和封面内容（如"资 格 性 响 应 文 件"）被解析为两个独立条目，空壳条目标题含"封面"但无内容，内容条目标题无"封面"但有实际模板
3. **封面渲染缺失字体信息**：封面模板渲染时需保留原始文档的字体、字号、对齐等格式，但当前 `template_content` 不存储 font 元数据

## 设计原则

- **标题特征 + 内容特征共同判定**：标题含"封面/封皮"不一定是封面，需结合内容形态（模板式 vs 说明式）综合判断
- **同页合并**：封面指示器（标题含封面、内容为空）与下一个同页 section 合并为一个封面条目
- **`required_sections` 作为唯一源头**：封面信息全部维护在 `required_sections`，移除独立的 `cover_pages` 字段
- **封面原样渲染**：`is_cover=true` 的条目，其 `template_content` 保存原始 font 信息，渲染时直接使用
- **占位符填充**：仅对 `XXX`、`____`、空字符串等占位符内容进行填充，标签文字原样保留
- **零封面兜底**：未识别到任何封面时，走现有自有封面生成逻辑

## 数据流

```
文档解析阶段
  └─ document_parser.py: ContentBlock 增加 font 元数据捕获
      ↓
Phase 1.5 — _extract_required_sections()
  ├─ 封面识别引擎（见下文）
  ├─ 合并封面指示器 + 封面内容 → 一个条目
  ├─ 设 is_cover=true
  ├─ font 信息从 ContentBlock 注入 template_content 各对象
  └─ 标记占位符（placeholder: true + fill_key）
      ↓
format_requirements = {
  required_sections: [
    { title: "...", is_cover: true, template_content: [{type:"text", text:"...", font:{...}, placeholder:true, fill_key:"project_name"}, ...] },
    { title: "...", is_cover: false, ... },
    ...
  ]
  # cover_pages: 移除
}
      ↓
Catalog 构建 — catalog.py _parse_format_tree()
  ├─ 直接从 item 读 is_cover 字段（不再 title 匹配）
  └─ 分册逻辑不变（多封面时第一个作为文档封面）
      ↓
标书渲染 — helpers.py _build_docx_bytes()
  ├─ is_cover=true  → 读取 template_content.font 渲染
  │   └─ placeholder 内容 → 从上下文填充（LLM 填空）；找不到留空
  ├─ is_cover=false → 标准排版（AGENTS.md 规则）
  └─ 无封面兜底 → 自有封面生成
```

## 封面识别引擎

### 输入

`format_requirements` 父章节下的直系子章节列表（`Section` 对象），每个对象有 `title`、`content`（ContentBlock 列表）、`children`、`page_range`。

### 判定流程

```
遍历每个子章节 section:

1. 标题含"封面"或"封皮"?
   ├── 否 → 跳过（不是封面）
   └── 是 → 进入步骤 2

2. 内容形态判定（分析 template_content）:
   统计特征:
   - content 长度（总字符数）
   - 占位符密度（___ / XXX / 留白占 content 比例）
   - 是否含完整句式（"应包含"、"须体现"、"应当"等说明性关键词）
   - 内容块数量
   - 是否完全是标签式短句（"项目名称："、"编号：" 等）

   判别:
   - 模板式特征（标签短句 + 占位符 + 稀疏布局）
     → 标 is_cover=true
   - 说明性特征（长段落 + 完整句式 + 无占位符）
     → 不标封面（保留原样，这是封面要求说明，不是封面模板）
   - 内容为空 或 极少（仅标题）
     → 作为"封面指示器"，进入步骤 3

3. 封面指示器处理（内容为空/极少）:
   看下一个相邻 section（同页检测）:
   ├── 下一个存在 且 page_range 有重叠（同页）
   │   └── 对下一个 section 执行步骤 2 的内容判定
   │       ├── 是模板式 → 合并: 用下一个的 title+content，标 is_cover=true
   │       │   └── 从原始 ContentBlock 提取 font 注入 template_content
   │       └── 是说明性 → 不合并，不标封面
   └── 下一个不存在或跨页 → 不标封面

4. "封面"/"封皮"出现在段落正文中（不在标题）:
   → 忽略，不是封面
```

### 合并规则细节

```
合并时:
- title: 用内容体的 title（"资 格 性 响 应 文 件"），不用指示器的 title（"（资格性响应文件封面、封皮）"）
- template_content: 用内容体的 content
- is_cover: true
- order: 用指示器的 order
- 多 section 连续同页的情况: 合并所有同页且关联的 section 的 content
- 合并后，指示器 section 从 required_sections 中移除
```

## font 元数据捕获

### document_parser.py ContentBlock 新增字段

```python
class ContentBlock:
    def __init__(self, type_="paragraph", text="", level=0):
        # ... 现有字段 ...
        self.font_name = ""         # 字体名称，如"宋体""黑体"
        self.font_size = None       # 字号，单位 Pt，如 16.0
        self.bold = False           # 是否加粗
        self.alignment = None       # 对齐方式: left/center/right
```

捕获时机：解析 docx 时，对每个 paragraph 取其 run 的 `font.name`、`font.size`、`bold` 属性，以及 paragraph 的 `alignment`。

### template_content 的 font 结构

被标记为 `is_cover=true` 时，`template_content` 中的每个 text 块扩展为：

```json
{
  "type": "text",
  "text": "采购项目名称:",
  "font": {
    "name": "宋体",
    "size": 16,
    "bold": false,
    "alignment": "center"
  },
  "placeholder": false
}
```

非封面章节的 template_content 保持现有结构，不加 font 信息。

## 占位符填充

### 占位符识别

各 text 块扫描以下模式：

| 模式 | 示例 | 动作 |
|------|------|------|
| 纯占位符下划线 | `____` / `___________` | 识别，从上下文填充 |
| XXX 占位符 | `XXX` / `XXX（单位名称）` | 识别，替换填充 |
| 空字符串 | `""` | 标记为可选填充 |
| 混合型 | "投标单位（盖章）：XXX" | 部分填充，仅替换 `XXX` |

### 填充上下文来源

优先顺序：
1. `bidder_notice` 中的 `project_name`、`project_no`
2. 主体信息表（`company_name` 等）
3. analysis_data 的 `metadata` 字段
4. LLM 根据上下文推断填充（仅当明确需要时）
5. 找不到 → 留空不处理

## 双封面/多封面处理

### 识别
- 每个封面独立识别并标记 `is_cover=true`
- 它们保持各自在 `required_sections` 中的原始顺序

### 渲染
- **第一个封面** → 渲染为标书文档的正式封面（首页）
- **后续封面** → 保留在章节序列中的自然位置渲染，不做提前或特殊处理
- 第二个封面出现在"供应商认为应提供的其他资料"等前一个章节之后，用封面格式渲染（保留原始字体、占位符填充），**不会被 LLM 扩写**，也不会被当作普通标题

## 渲染逻辑（helpers.py _build_docx_bytes）

```
for each outline item:
  if item.is_cover:
    for each block in item.template_content:
      if block.type == "text":
        text = block.text
        if block.placeholder:
          从上下文填充 text 中的占位符
          找不到则 text 不变（保留原占位符）
        创建 paragraph，应用 block.font 到 run
      if block.type == "table":
        渲染表格（保留原始格式）
  else:
    现有逻辑（标准排版、LLM生成等）
```

## 涉及修改的文件

| 文件 | 修改内容 |
|------|----------|
| `app/infrastructure/document_parser.py` | ContentBlock 新增 font 元数据字段并捕获 |
| `app/service_modules/task_pipeline/analysis_v3/phase1_5_format.py` | 封面识别引擎；合并逻辑；is_cover 标记；font 注入；占位符标记；移除 cover_pages |
| `app/service_modules/task_pipeline/catalog.py` | `_parse_format_tree` 直接读 `is_cover` 字段；简化现有合并逻辑 |
| `app/service_modules/task_pipeline/helpers.py` | `_build_docx_bytes` 封面渲染路径：取 font 信息 + 占位符填充 + 零封面兜底 |
| `app/service_modules/task_pipeline/generate.py` | 可能需导出占位符填充辅助函数 |
| `tests/test_format_cover_detection.py` | 新增测试用例 |

## 测试用例

1. **情形 A**：标题含"封面/封皮" + 内容为空 + 下一个同页有模板内容 → 合并，is_cover=true
2. **情形 B**：标题含"封面" + 内容非空（模板式） → 直接标 is_cover=true
3. **情形 C**：标题含"封面" + 内容为说明性段落 → 不标封面
4. **情形 D**：标题段落正文提到"封面"但不在标题 → 忽略
5. **情形 E**：双封面场景 → 两个 is_cover=true，第一个用作文档封面
6. **情形 F**：无封面场景 → 走自有封面兜底生成
7. **情形 G**：占位符填充 → XXX → 公司名；下划线 → 项目名；混合型 → 部分替换
8. **情形 H**：font 元数据完整性 → template_content 中 font 字段正确
