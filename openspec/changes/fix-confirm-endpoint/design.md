## Context

当前 POST /check-items/confirm endpoint 的 Swagger 模型 `save_review_model` 未定义，service 函数 `save_review()` 未实现，导致接口不可用。旧的 `check_confirm_model` 和 `confirm_check_items()` 基于 BiddingCheckItem 扁平表 + check_key/confirmed_flag 模式，与新版的 check-items 结构化返回格式不匹配。

## Goals / Non-Goals

**Goals:**
- 定义 `save_review_model`，仅支持 `{"data": {完整data结构}}` 格式
- 实现 `save_review()` 将 6 个 section 写回 `bidding_analysis_result.analysis_data`
- 支持重复调用（不限制必须一次全量确认）
- 移除旧的相关代码

**Non-Goals:**
- 不改动 GET /check-items 的返回结构
- 不改动前端数据传递方式
- 不做旧格式兼容

## Decisions

1. **输入格式**：仅支持 `{"data": { ... }}"` 包装格式，不做裸露字段兼容
2. **写入位置**：6 个 section 写入 `bidding_analysis_result.analysis_data` 的对应字段，维持 analysis_data 作为单体 JSON 存储
3. **状态推进**：第一次调用将任务从 ANALYZED → CHECKED；后续调用（CHECKED 状态）只更新数据不改变状态
4. **不再使用 BiddingCheckItem 表**：旧表保留但不再写入或读取
5. **Swagger 模型**：使用 `fields.Raw` 定义 data 字段，避免重复定义每个子字段

## Risks / Trade-offs

- [Low] 旧 `confirm_check_items` 被移除后，任何还在用旧格式的外部调用会失败 → 已确认无旧调用方
- [Low] BiddingCheckItem 表变为死表 → 后可考虑清理
