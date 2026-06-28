## 1. Swagger Model

- [x] 1.1 在 api/tasks.py 中定义 `save_review_model`（fields.Raw，仅支持 `{"data": {...}}` 格式）
- [x] 1.2 移除旧的 `check_confirm_model` 定义

## 2. Service Function

- [x] 2.1 实现 `save_review()` 函数：将 data 中 6 个 section 写回 bidding_analysis_result.analysis_data
- [x] 2.2 支持状态判断：ANALYZED → CHECKED（第一次），CHECKED 只更新不推进
- [x] 2.3 移除旧的 `confirm_check_items()` 和 `_sync_confirmed_items_to_analysis_result()` 函数

## 3. Registration & Wiring

- [x] 3.1 更新 BiddingTaskService 注册 save_review
- [x] 3.2 清理 pipeline.py 和 workflow.py 中对旧 confirm_check_items 的引用

## 4. Cleanup

- [x] 4.1 移除 api/tasks.py 中对旧 check_confirm_model 的引用（如无引用则跳过）
