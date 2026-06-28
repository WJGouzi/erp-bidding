# Tasks

## Phase 2: 核心增强 ✅

### 1. InvitationExtractor 增强 ✅
- [x] bid_opening: 新增 `:HH:MM` 格式（如"14:30"）
- [x] file_purchase_start/end: 标书获取起止时间
- [x] file_purchase_price: 标书售价
- [x] bid_submission_location: 投标地点
- [x] special_declaration: 特殊声明（"未达到采购限额"）
- [x] evaluation_method: 表格行格式（"比选方法 | 综合评分法"）

### 2. InstructionsExtractor ✅
- [x] 须知前附表表格行解析
- [x] 中标人数量（按包）
- [x] 验收标准（财库〔2016〕205号）
- [x] 报价要求（一次性报价/据实结算）
- [x] 保证金/履约保证金增强（"不收取"检测）

### 3. RequirementExtractor ✅
- [x] service_period: 服务期限（年/月/天）
- [x] delivery_location: 配送/交货地点
- [x] payment_terms: 付款方式
- [x] acceptance_standard: 验收标准
- [x] pricing_rule: 报价规则

## Phase 3: 完善优化 ✅

### 4. PackageAwarePipeline 增强 ✅
- [x] 包名从 raw_text 提取（"第1包：试剂耗材配送服务"）
- [x] 传递给 extract_packages 使用
- [x] 回退机制（有命名用命名，无命名用"第X包"）

### 5. FormatExtractor ✅
- [x] 提交材料清单提取
- [x] 正副本数量提取
- [x] 包专用资格条件提取

### 6. 额外增强 ✅
- [x] agency_fee 中文数字支持（"陆佰"→600）
- [x] submission_copy_detail（正本壹份/副本贰份）
- [x] pkg_special_qual 按包专用资格

## 验证 ✅
- [x] 86 个单元测试全部通过
- [x] 德阳疾控文档全字段正确提取
- [x] 成都海关文档回归测试通过
