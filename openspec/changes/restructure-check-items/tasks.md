# check-items 接口重构 — 任务列表

## T1: 创建 check_items 模块目录 (P0 🔴)

- [x] 1.1 创建 `app/service_modules/task_pipeline/analysis_v3/check_items/` 目录
- [x] 1.2 创建 `__init__.py` 含 `assemble_check_items()` 统一入口
- [x] 1.3 在 `BiddingAnalysisResult` 模型上添加 `safe_analysis_data()` 辅助方法

## T2: 实现投标人须知组装模块 (P0 🔴)

- [x] 2.1 创建 `bidding_info.py`
- [x] 2.2 实现 `assemble_bidding_info()`：从 `analysis_data.metadata` + `overview` 提取
- [x] 2.3 覆盖字段：project_name, project_code, package_no, budget, purchaser, agency, domain, summary, sme_only, dark_bid, bid_deadline, bid_bond, bid_open_time

## T3: 实现商务/技术要求组装模块 (P0 🔴)

- [x] 3.1 创建 `business.py` + `technical.py`
- [x] 3.2 实现 `assemble_business()` / `assemble_technical()`
- [x] 3.3 支持结构化列表 + 降级按行拆分

## T4: 实现资格审查组装模块 (P1 🟡)

- [x] 4.1 创建 `qualification.py`
- [x] 4.2 实现三 Tab 分组：qualification_items / compliance_items / rejection_items
- [x] 4.3 从 `analysis_data.eligibility` + `qualification_requirements` + `disqualification_items` 提取

## T5: 实现评分标准组装模块 (P1 🟡)

- [x] 5.1 创建 `scoring.py`
- [x] 5.2 实现按商务/技术分组
- [x] 5.3 保留 method / total_score / price_weight 等元信息

## T6: 实现分包信息组装模块 (P1 🟡)

- [x] 6.1 创建 `packages.py`
- [x] 6.2 从 `packages_json` + `package_count` 组装

## T7: 实现扁平化 checklist 模块 (P1 🟡)

- [x] 7.1 创建 `checklist.py`
- [x] 7.2 聚合资格/评分/废标的待确认项
- [x] 7.3 支持 category / severity / content / prep_guide / confirmed 字段
- [x] 7.4 从 BiddingCheckItem 表读取 confirmed 状态

## T8: 改造 get_check_items() 为门面模式 (P0 🔴)

- [x] 8.1 改造 `analysis.py` 的 `get_check_items()`
- [x] 8.2 新路径：调用 `assemble_check_items()`，失败则降级
- [x] 8.3 验证返回结构完整

## T9: 更新需求文档 2.4 节 (P0 🔴)

- [x] 9.1 修改 `需求文档.md` 2.4.1 的接口顺序图
- [x] 9.2 补充 `check-items` 返回结构说明
- [x] 9.3 确保与设计一致
