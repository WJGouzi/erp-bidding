## bid_type_label_map 作用域修复

### 当前代码

```python
def _generate_chapter_content(...):
    try:
        ...  # 模板绑定
    except Exception as _exc:
        logger.warning(...)
        bid_type_label_map = {"GOODS": "货物类", ...}  # ← 在 except 块内
    bid_type_label = bid_type_label_map.get(...)       # ← 在 except 块外引用
```

### 修复后

```python
def _generate_chapter_content(...):
    bid_type_label_map = {"GOODS": "货物类", ...}  # ← 移到 try 之前
    
    try:
        ...  # 模板绑定
    except Exception as _exc:
        logger.warning(...)
    
    bid_type_label = bid_type_label_map.get(...)       # ← 正常使用
```

### 文件
- `app/service_modules/task_pipeline/helpers.py` 第 2795 行附近
