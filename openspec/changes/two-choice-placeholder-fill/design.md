## Context

当前标书生成流程中，二选一占位符（如 `（有、无）`）的填充逻辑已经实现，但**渲染阶段存在缺口**。

### 现状（已实现部分）

**分析阶段** (`phase1_5_format.py`):
- `_detect_two_choice_patterns()` 扫描 `template_content` 中的 `（有、无）` / `（是、否）` 模式
- 根据上下文关键词启发式选择正向选项
- 生成结果存入 `analysis_data.two_choice_placeholders`，每条包含：
  - `section_key` — 清洗后的章节标题
  - `raw` — 原始占位符文本（如 `（有、无）`）
  - `selected` — 选定的选项（如 `无`）
  - `text_snippet` — 该段落前 120 字符，用于精确匹配
  - `reason` — 选择理由

**生成阶段** (`helpers.py`):
- `_fill_two_choice_placeholders()` 函数已存在
- 在以下路径已调用：
  - Path C — 传统 TEXT_TEMPLATE 路径（line 2929，带完整上下文）✅
  - `_write_formatted_content` — LLM 文本渲染（line 3646，无 section 上下文，降级到正则关键词猜测）
  - 封面渲染（line 4154、4643、4684 — 实际上封面不需要处理）

### 缺口

**Path A — ContentBlock 渲染路径（line 4398-4402）未调用 `_fill_two_choice_placeholders`**

这是 template_binder 路径的输出渲染位置。当章节有原文模板时：
1. `bind_template()` 检测到模板 → `has_template=True`
2. `fill_content()` 复制模板原文，替换 XXX/___ 占位符，但不对 `（有、无）` 做处理
3. ContentBlocks 被序列化存储
4. 渲染时（line 4398）：`document.add_paragraph(text)` 直接写入原文 → `（有、无）` 原样出现在输出中

### text_snippet 匹配可行性

`text_snippet` 来自分析阶段 `template_content` 的 block_text，ContentBlock 渲染时的 text 来自同一个 `template_content`（经过 `fill_content` 仅替换 XXX/___）。由于 `（有、无）` 周围的原文未被修改，`text_snippet` 在渲染时能精确匹配，保证了：
- 同章节多条二选一时：通过 `text_snippet` 区分不同段落
- 单条时：简单 `text.replace(raw, **selected**)` 即可

## Goals / Non-Goals

**Goals:**
- 在 ContentBlock 段落渲染路径（line 4398）加上 `_fill_two_choice_placeholders` 调用
- 在 ContentBlock 表格单元格渲染路径（line 4420+）加上调用
- 利用分析阶段预存的 `two_choice_placeholders`（含 `text_snippet`）做精确匹配
- 封面渲染路径的二选一调用可以移除（封面无二选一内容）
- `**option**` 加粗标记在 ContentBlock 段落中正确渲染

**Non-Goals:**
- 不修改 `_detect_two_choice_patterns` 分析阶段逻辑
- 不修改 `_fill_two_choice_placeholders` 函数签名
- 不改变 LLM 生成文本的处理（LLM 自行处理二选一）

## Decisions

### Decision 1: 数据源

- **选择**：使用 `analysis_data.two_choice_placeholders` 作为填充依据
- **理由**：分析阶段已根据上下文关键词做了预选，且含 `text_snippet` 可用于精确匹配。避免在渲染阶段重复做关键词猜谜
- 当前的 `_build_docx_bytes` 函数已经在 line 4088-4094 提取了 `_two_choice_raw`，但只用于封面。需要将其提升到更广泛的作用域

### Decision 2: 渲染时替换 vs 生成时替换

- **选择**：在渲染时（`_build_docx_bytes` 内的 `_write_outline_item`）替换
- **理由**：template_binder 路径的 ContentBlocks 在生成阶段（`_generate_chapter_content`）就已序列化返回，后续不再经过文本处理管道。渲染时是最后的拦截点

### Decision 3: 封面调用移除

- **选择**：封面上不会有 `（有、无）` 这类二选一内容，封面渲染的 `_fill_two_choice_placeholders` 调用可以安全移除或保留（无害但冗余）

## Risks / Trade-offs

- **text_snippet 匹配失败**：如果 `fill_content` 替换了 `text_snippet` 内的 XXX 占位符，可能导致 snippet 不能被精确匹配 → 降级到 `text.replace(raw, selected)` 按原始占位符替换
- **同一章节多个相同 raw**：如同一章节出现两次 `（有、无）`，但语义不同 → 需要 `text_snippet` 精确匹配，`>1` 路径已有处理
- **xml 控制字符**：渲染前已通过 `_strip_xml_control_chars` 处理，`text_snippet` 匹配时需要注意两侧都做清洗
