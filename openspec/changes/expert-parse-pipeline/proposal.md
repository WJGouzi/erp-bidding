# 专家级解析管线重构：移除预设矩阵，改用固定必查+动态章节提取

## Why

### 当前方案的根本问题

当前 `phase2_eligibility.py` 使用 `ELIGIBILITY_TEMPLATES` 矩阵：
```python
# 伪代码示意
template = _BASE + bid_type(GOODS) + doc_type(TENDER)
# 问题：每新增一个组合就要评估是否需要改模板
```

基于 10 份真实标书的验证发现，这个方案有三个不可扩展的问题：

#### 问题一：预设矩阵无法覆盖所有组合

| bid_type | doc_type | 状态 |
|---------|---------|------|
| GOODS | TENDER | ✅ 碰巧能用 |
| GOODS | SELECTION | ✅ 碰巧能用 |
| GOODS | NEGOTIATION | ❌ 缺"竞争性磋商"→ 回退 TENDER |
| SERVICE | TENDER | ❓ 无人验证 |
| SERVICE | SELECTION | ❓ 无人验证 |
| SERVICE | NEGOTIATION | ❓ 无人验证 |
| ENGINEERING | 任意 | ❓ 无人验证 |

每次新增一个 doc_type（如"竞争性磋商"），都要：
1. 改 `classify_document()` 加关键词
2. 评估要不要加新的模板条目
3. 人工确认不会影响现有分类

**这本质上是一个组合爆炸问题。** 即使当前 3×4=12 种组合，已经难以覆盖。

#### 问题二：模板关键词与实际标书脱节

`GOODS` 模板包含了 `"医疗器械": ["医疗器械", "经营许可证", "注册证"]` 这个子类。但：
- 不是所有货物类标书都是医疗器械（还有 IT 设备、办公用品、仪器设备...）
- 当标书不是医疗器械时，这个子类就是"噪声"
- 当标书是 IT 设备时，却没有对应的"软件著作权""系统集成资质"等关键词

**根源：用 bid_type 推断标书内容是不准确的。** 应该直接从文档中读。

#### 问题三：doc_type 既做分类又做驱动

```
classify_document() 识别 doc_type
        ↓
doc_type 驱动模板选择
        ↓
模板错了 → 资格检查项错了 → 整个 Phase 2 输出偏差
```

doc_type 应该只是"展示信息"，不应该做逻辑驱动。

### 专家处理标书的实际方式

作为标书撰写专家，处理一份新标书时：

```
1. 不关心这是"货物+招标"还是"服务+比选"
2. 直接翻目录，找"供应商资格要求"章节
3. 读这个章节，看具体要求了什么
4. 分类：营业执照→通用，许可证→特定，★→实质性
5. 再加法律规定的必查项（政府采购法22条）
```

**关键：专家不依赖"预设模板"，而是依赖"章节定位能力"。**

## Capabilities

### `statutory-checklist` — 法规固定检查清单（P0）
移除 ELIGIBILITY_TEMPLATES 矩阵。将法律法规硬性要求的检查项移出到独立 YAML 配置文件。这些项与标书类型无关，所有政府采购标书都需要。

### `dynamic-section-extraction` — 动态章节提取（P0）
强化章节定位机制，从文档中动态提取资格要求。不依赖 bid_type 和 doc_type 预设。

### `content-signal-classification` — 内容信号归类（P1）
从定位的章节文本中检测信号词，自动归类为"特定资质""实质性条款""业绩要求"等。

### `doc-type-display-only` — 文档类型仅做展示（P1）
修复 classify_document() 缺失"竞争性磋商"的问题。doc_type 只用于 UI 展示，不驱动任何逻辑。

## Files to Modify

```
DELETE app/service_modules/task_pipeline/analysis_v3/phase2_eligibility.py  # 重建
CREATE app/service_modules/task_pipeline/analysis_v3/phase2_extractor.py    # 新：动态章节提取
CREATE config/presets/statutory_checklist.yaml                              # 新：法规固定清单
CREATE config/presets/signal_words.yaml                                     # 新：信号词配置
MODIFY app/service_modules/task_pipeline/analysis_v3/phase1_metadata.py     # 修复classify_document
MODIFY app/service_modules/task_pipeline/analysis_v3/__init__.py            # 集成新Phase2
MODIFY app/service_modules/task_pipeline/analysis_v3/schemas.py             # 输出结构调整
```

### 差距五：商务/技术字段提取依赖 regex，频繁误匹配

当前用 20+ 条 regex 从 raw_text 提取商务字段：

| 字段 | regex 问题 |
|------|-----------|
| 付款方式 | 抓到章节编号"24"（误匹配） |
| 交货地点 | 标书写"合同履行期限"，regex 未匹配（漏匹配） |
| 技术要求 | 参数表在表格中，flatten 后丢失结构 |
| 售后服务 | 合同模板中的"售后服务"干扰（误匹配） |

而文档解析器已经输出了结构化章节树，子章节标题就是天然的"字段名"。用章节导航替代 regex 全文搜索，可以彻底解决这些问题。

## New Capability

### `section-based-extra` — 基于章节结构的商务/技术要求提取（P0）
利用已解析的章节树，通过章节标题导航定位"付款方式""交货地点""售后服务"等子章节，直接读取其内容。
