# 目录生成改造 — 任务列表

## T1: 为 `get_catalog_options` 注入包号和确认项 (P0 🔴)

- [x] 1.1 在 `get_catalog_options()` 中查询 `task.selected_package_no`
- [x] 1.2 在 `get_catalog_options()` 中查询 `BiddingCheckItem`（按 shared_resource_id 过滤）
- [x] 1.3 将两个新参数传入 `_build_constrained_requirement_outline()`

## T2: 新增 `_get_filtered_analysis_data()` 辅助函数 (P0 🔴)

- [x] 2.1 在 `catalog.py` 中新增函数，按 `selected_package_no` 过滤 `analysis_data["packages"]`
- [x] 2.2 单包场景（`has_package=False` 或 `None`）不走过滤逻辑
- [x] 2.3 返回过滤后的 analysis_data dict

## T3: 新增 `_build_package_aware_outline()` 动态结构推断 (P0 🔴)

- [x] 3.1 替换 `_build_dynamic_outline()` 的 3 章硬编码
- [x] 3.2 实现基准章节生成：投标函、报价部分、授权书
- [x] 3.3 实现资格证明文件章节（从 check_items 展开）
- [x] 3.4 实现实质性要求章节（从 check_items 展开）
- [x] 3.5 实现技术参数响应章节（根据包内产品数量决定颗粒度）
  - items=0: 跳过
  - items=1-5: 一个子章节"技术参数偏离表"
  - items>5: 分"总偏离表"+"产品分组详细响应"
- [x] 3.6 实现评分标准响应章节（从 scoring.dimensions 映射）
- [x] 3.7 实现售后服务、业绩、其他等默认章节

## T4: 实现回退策略 (P1 🟡)

- [x] 4.1 `_should_fallback_to_legacy()` 条件判断
- [x] 4.2 无包号/无确认项时调用原有的 `_build_dynamic_outline()` 3 章结构
- [x] 4.3 确保缓存逻辑兼容（目录重新生成时按新结构，旧 3 章缓存视为过期）

## T5: 更新 `_build_constrained_requirement_outline()` (P0 🔴)

- [x] 5.1 接收新参数：`selected_package_no`, `check_items`
- [x] 5.2 调用 `_get_filtered_analysis_data()` 获取当前包数据
- [x] 5.3 调用 `_build_package_aware_outline()` 替代原有 3 章逻辑
- [x] 5.4 `_build_auto_catalog_content` 也传入 `selected_package_no`

## T6: 更新 `_build_dynamic_outline_with_llm()` 注入包上下文 (P2 🟢)

- [x] 6.1 在 LLM 提示词中注入 `selected_package_no` 和包名
- [x] 6.2 LLM 生成的目录也将聚焦于当前包

## T7: 验证和测试 (P1 🟡)

- [x] 7.1 多包场景：选择包号后只生成该包的目录
- [x] 7.2 单包场景：回退到不过滤逻辑
- [x] 7.3 确认项全空场景：跳过资格/实质性要求章节
- [x] 7.4 前端无报错：JSON 格式保持向后兼容
- [ ] 7.5 启动服务验证实际运行
