# 标书解析管线重构：表格解析 + 分包独立分析 + 策略理解

## Why

当前 v3 解析管线能提取基础元数据（项目名、编号、采购人等），但对于标书撰写专家的实际工作流程存在三个结构性差距：

### 差距一：表格是最大盲点
标书中的关键业务信息（前附表、评审表、产品清单）80% 在表格中，但当前管线仅靠正则扫描 flattern 后的文本，大量数据丢失。
- **德阳疾控案例**：前附表的"比选方法：综合评分法""代理服务费：600元/家/包""不收取保证金"等信息全部未提取
- **通用影响**：预算数据（在前附表或表格中）、成交供应商数量、评标方法这些"一票否决"级信息在线性文本中难以定位

### 差距二：分包信息停留在"数字"层面
包号检测正确（如德阳疾控检测到 3 个包），但每个包的独立分析几乎为零：
- 包名仅显示"第1包"，未从"第1包：试剂耗材配送服务"中提取真实名称
- 每包的特殊资格条件未单独归集（包2/3需要危化品许可证，包1不需要）
- 策略分析对每个包输出相同的"资格要求复杂"，未体现包间差异
- 各包的评分侧重点、竞争格局、风险评估缺失

### 差距三：无"标书类型"驱动的上下文理解
不同采购方式（招标/比选/竞争性谈判/询价/单一来源）的用词习惯、结构布局完全不同，但当前所有文档按同一套规则解析：
- 招标文件用"采购人/招标代理机构"，比选文件用"比选人/比选代理机构"
- 招标文件有"投标保证金"，比选文件可能是"比选保证金"或"不收取"
- 评审方法在不同类型文件中出现在不同位置

## Capabilities

### `table-parser` — 通用表格解析引擎
新增模块，专门解析标书中各类表格：
- 自动识别表格类型（前附表/评分表/产品清单/资质表/响应格式表）
- 前附表提取键值对（如"比选方法 → 综合评分法"）
- 产品清单提取结构化数据（品名、规格、数量）
- 评分表增强（子维度拆解、评分标准原文保留）
- 提取结果融合到 metadata，优先于 regex

### `per-package-analysis` — 分包独立分析
每个包作为独立分析单元：
- 按包切分文档内容（包1/包2/包3 + 公共部分）
- 每包独立执行资格扫描、参数统计、策略分析
- 包间关联分析（哪些条件共享？哪些包竞争少利润高？）
- 策略输出按包差异化（难度评估、竞争格局、推荐重点）

### `document-classifier` — 文档分类驱动解析
前置文档分类步骤：
- 识别采购方式（招标/比选/竞争性谈判/询价/单一来源）
- 加载对应解析规则集（不同用词、不同结构）
- 信息标注置信度和原文出处
- "死线"优先级标注（真"一票否决" vs "可补救"）

## Changes

### 1. 新增表格解析模块
- `app/infrastructure/table_parser.py` — 通用表格类型识别和键值对提取
- 整合到 analysis_v3 管线（phase1_metadata 中调用）
- 支持原生 DOCX 表格和文本表格两种输入

### 2. 分包分析重构
- `phase3_scoring.py` 的 `extract_packages()` 重构为逐包分析
- 新增包内容切分逻辑（`_split_content_by_package`）
- 新增包间关联分析（`_cross_package_analysis`）
- `strategy` 输出按包差异化

### 3. 文档分类前置
- 新增 `_classify_document()` 函数
- 扩展 metadata schema 增加 `document_type` 字段
- 规则集按类型加载（同一字段多种表述）
- confidence 和 source 标注

### 4. 元数据增强
- metadata 增加 `document_type`、`confidence`、`source` 字段
- extra 字段补全（付款方式、服务期限、配送地点等已提取但未传出）
- 表格提取结果优先于 regex 提取

### 5. 移除 v2 残留
- 确认 `analysis.py` 中 v2 回退彻底移除
- `to_dict()` 中 version 判断简化

## Files to Modify

```
CREATE app/infrastructure/table_parser.py          # 通用表格解析引擎
MODIFY app/service_modules/task_pipeline/analysis_v3/__init__.py
MODIFY app/service_modules/task_pipeline/analysis_v3/phase1_metadata.py
MODIFY app/service_modules/task_pipeline/analysis_v3/phase3_scoring.py
MODIFY app/service_modules/task_pipeline/analysis_v3/schemas.py
MODIFY app/service_modules/task_pipeline/analysis.py
MODIFY app/domain/models.py
MODIFY app/service_modules/task_pipeline/analysis_v3/check_items.py
```
