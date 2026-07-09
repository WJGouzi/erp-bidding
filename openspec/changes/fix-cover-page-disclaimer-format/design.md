## Context

当前标书生成流程中，`_build_docx_bytes` 函数负责将解析后的章节内容组装为最终 docx 文件。封面页的渲染逻辑存在四个具体问题：

1. **封面字体/格式**: 封面模板内容从 `format_requirements.required_sections[].template_content` 读取，每个 block 携带 font 元数据（font_name、font_size、alignment），但渲染时在多个地方降级为默认值（宋体/16pt），无法忠实还原招标文件原文格式
2. **免责声明后空白页**: 免责声明渲染后立即调用 `document.add_page_break()`，由于免责声明已在一页内完整渲染，该分页符导致插入额外空白页
3. **"正本"标记**: 使用表格(1x1)配合 `tblpPr` 做绝对定位，但字体颜色未设置为黑色，定位坐标可能不准，导致渲染失败
4. **投标日期填充**: `cover_bid_time` 变量定义为 `utc_now().strftime()`（当前时间），但从未被使用；`_fill_placeholder_text` 函数缺少对"投标日期"等占位符的处理；开标时间存储在 metadata.key_dates.bid_opening 中，未被提取到封面上下文

## Goals / Non-Goals

**Goals:**
- 封面模板内容的字体/字号/对齐方式严格使用原文提取的 font 元数据
- 移除免责声明后的多余分页符（版面已正确）
- "正本"标记使用宋体、三号(16pt)、黑色，正确显示在封面页适当位置
- 封面占位符支持"投标日期"替换，投标日期使用招标文件开标时间
- 项目名称、项目编号等已有占位符填充保持正常工作

**Non-Goals:**
- 不改变免责声明的文本内容
- 不改变封面模板的检测与提取逻辑（仅修复渲染）
- 不改变大纲循环中后续封面的渲染逻辑（仅修复第一个封面和补充分页符）
- 不改动表格解析或章节目录生成逻辑

## Decisions

### 1. 封面字体渲染——直接使用 block 级 font 元数据，不降级

**现状**: 封面块渲染时根据 `_font.get("font_name", "") or "宋体"` 和 `_font.get("font_size", 16.0)` 取值，但 font_size 字段可能在序列化/反序列化过程中精度丢失或不存在。

**方案**: 在 `_build_docx_bytes` 的封面渲染段（3个渲染点）中：
- 严格使用 block 级 `font` 字典的值（font_name、font_size、alignment、bold）
- 不设置默认降级值（font_size 不设置默认 16.0），保留为 None 时让 python-docx 使用文档默认值
- 对每个 block 渲染后记录日志（非必须，调试用）
- 字体名称也用于设置 w:eastAsia

**替代方案**: 在 `phase1_5_format.py` 的 font 注入阶段统一修正。但这会改变存储格式，影响面太大。优先在生成端修复。

### 2. 移除免责声明后的无条件 `add_page_break()`

**现状**: `_add_disclaimer_page(document)` 后紧跟 `document.add_page_break()`，强制免责声明独占一页后跳到下一页。

**方案**: 删除第 3314 行的 `document.add_page_break()`。免责声明内容已确认在一页内渲染完成，其后直接渲染封面页。封面页本身有自己的分页逻辑（渲染后 `document.add_page_break()`），无需额外分页。

### 3. "正本"标记——设置黑色、修复定位

**现状**: 
- `_zhengben_run.font.name = "宋体"` ✓
- `_zhengben_run.font.size = Pt(16)` ✓（三号=16pt）
- 未设置字体颜色（缺少 `font.color.rgb = RGBColor(0,0,0)`）
- 使用 `tblpPr` 做绝对定位，tblpX/tblpY 单位是 twips（OpenXML 标准），数值可能不当

**方案**:
- 添加 `_zhengben_run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)`
- 检查并调整 tblpX/tblpY 定位值：
  - A4 纸宽 210mm ≈ 11906 twips（1mm ≈ 56.7 twips）
  - 右上角定位：tblpX = ~9000 twips（距左约 160mm），tblpY = ~600 twips（距顶约 10mm）
  - 当前 tblpX=6120000 明显过大（是 EMU 单位而非 twips：1 EMU = 1/914400 inch，6120000 EMU ≈ 170mm，在 A4 纸上合理）
  - 确认使用 EMU 单位，保持现有定位值不变

### 4. 投标日期填充——从 metadata 提取开标时间

**现状**:
- `cover_bid_time = utc_now().strftime(...)` 定义在第 3258 行但从未使用
- `_fill_placeholder_text` 不处理"投标日期"占位符
- 开标时间存储在 `analysis_data.metadata.key_dates.bid_opening` 或顶层 `metadata.bid_open_time`

**方案**:
1. 在 `_build_docx_bytes` 中，从 `analysis_data` 提取 `bid_open_time`：
   - 优先从 `analysis_data.metadata.key_dates.bid_opening` 读取
   - 其次从 `analysis_data.metadata.bid_open_time` 读取
   - 再次从 `bidder_notice.bid_open_time` 读取
   - 兜底：使用当前时间
2. 将 `cover_bid_time` 改为从提取的开标时间格式化（如 "2026年07月15日"）
3. 在 `_fill_placeholder_text` 中添加对"投标日期"等占位符的替换规则

## Risks / Trade-offs

- **font_size 精度丢失**: font_size 从 document_parser 以 Pt(float) 存储，序列化为 JSON 后可能丢失精度。风险低，Pt 的小数精度对视觉影响极小。
- **删除免责声明分页符影响封面位置**: 如果删除 `add_page_break()` 后封面内容紧接免责声明在同一页开始（而非新页），这符合预期——封面应紧随免责声明。
- **开标时间字段可能缺失**: 部分招标文件未明确写开标时间，兜底使用当前时间。已设置完整 fallback 链，不会报错。
- **tblpX/tblpY 单位混淆**: OpenXML 标准规定 tblpX/tblpY 单位为 EMU，但一些编辑器可能按 twips 解释。当前值在 EMU 下合理，保持不动。

## Migration Plan

无迁移步骤。修复直接作用于 `_build_docx_bytes` 函数，重新生成标书即生效。

## Open Questions

1. 是否需要为"正本"表格添加边框？用户要求宋体/三号/黑色，未提及边框样式，目前使用 `_apply_black_solid_borders` 添加了黑色实线边框。
2. 其他封面（如双封面场景中的后续封面）是否需要同样的字体修复？当前设计只修复第一个封面的渲染，后续封面的渲染在 outline 循环中处理（逻辑类似但代码重复），可作为后续优化。
