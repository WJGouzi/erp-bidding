## Why

确认接口 POST /check-items/confirm 的 Swagger 模型和 service 函数均未完整实现，导致前端无法调用。同时旧的 confirm_check_items 逻辑基于 BiddingCheckItem 扁平表（check_key + confirmed_flag），与新版结构化 check-items 返回格式不匹配。

## What Changes

- **BREAKING**: 删除旧的 `check_confirm_model` Swagger 模型
- **BREAKING**: 删除旧的 `confirm_check_items()` 函数及其关联的 `_sync_confirmed_items_to_analysis_result()`
- 新增 `save_review_model` Swagger 模型，仅支持 `{"data": {完整 data 结构}}` 格式
- 新增 `save_review()` 服务函数，将 6 个 section 写回 bidding_analysis_result 表
- 支持重复调用（任务状态为 ANALYZED 或 CHECKED 均可）
- 注册 `save_review` 到 BiddingTaskService
- 更新 BiddingAnalysisResult.to_dict() 增加校验逻辑
- 更新 api/tasks.py 中的 Swagger 模型引用

## Capabilities

### New Capabilities
- `confirm-review`: 确认接口，接受完整 data 结构并写回分析结果表，支持反复调用

### Modified Capabilities
- 无（本次仅修复现有 endpoint，不新增能力）

## Impact

- `app/api/tasks.py`：Swagger 模型定义、endpoint 模型引用
- `app/service_modules/task_pipeline/analysis.py`：移除旧函数，新增 save_review
- `app/service_modules/__init__.py`：注册 save_review
- `app/domain/models.py`：BiddingAnalysisResult 增加校验工具方法
