## Why

当前"正本"文字在封面上的定位使用了一个近似的计算值（tblpY=406400 EMU、tblpX 基于页边距推算），用户需要更精确的定位参数：

- 黑色边框顶部距离页面上边 = 640 twip
- 黑色边框右侧距离页面右边 = 640 twip
- "正本"在黑色边框内上下居中、左右居中

同时需要将"正本"的定位改为以右侧和上侧为基准，而非现有的从左侧推算的方式，确保在不同页面宽度下都能保持固定的右上角边距。

## What Changes

- 修改"正本"浮动表格定位参数，使用用户指定的 twip 值精确计算
- tblpY = 640 twip（黑色边框上边缘 → 页面上边缘）
- tblpX 值需通过页面宽度计算（页面宽度 - 边框宽度 - 640 twip），使黑色边框右边缘→页面右边 = 640 twip
- "正本"在边框表格内保持水平居中、垂直居中（现有居中逻辑已验证正确，需保留）
- 黑色边框样式已存在（`_apply_black_solid_borders`），需确认是否满足要求

## Capabilities

### New Capabilities

无（为现有渲染细节调整，不引入新的 spec 级能力）

### Modified Capabilities

无

## Impact

- `app/service_modules/task_pipeline/helpers.py` — 修改"正本"浮动表格的 `tblpX`/`tblpY` 计算逻辑，改为以右侧和上侧为基准
- 如果黑色边框样式需要调整，可能涉及 `_apply_black_solid_borders`
