# Confirm Review

## Input Format

POST `/api/bidding/tasks/{task_id}/check-items/confirm`

Body 必须使用 `{"data": {完整 data 结构}}` 包装格式，不兼容裸露字段方式：

```json
{
  "data": {
    "task_id": 6,
    "bidding_info": { ... },
    "business": { ... },
    "technical": { ... },
    "qualification": { ... },
    "scoring": { ... },
    "packages": { ... }
  }
}
```

- `task_id` 在 body 中忽略（已在 URL 中）
- 不支持旧 `check_confirm_model` 的 `items` 数组格式

## Behavior

1. 将 data 中的 6 个 section 写入 `bidding_analysis_result.analysis_data` JSON 的对应字段
2. 第一次调用将任务状态从 ANALYZED 推进到 CHECKED
3. 后续调用（CHECKED 状态）只更新数据，不重复改变状态
4. 支持反复调用
5. 不生成 checklist、不写入 BiddingCheckItem 表

## Response

```json
{
  "code": 0,
  "message": "审核面板保存完成",
  "data": {
    "task_id": 6,
    "status": "CHECKED"
  }
}
```
