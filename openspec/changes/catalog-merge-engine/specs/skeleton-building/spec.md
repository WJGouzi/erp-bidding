# 阶段1：骨架构建 — 规格说明

## 输入

```python
analysis_data.format_requirements = {
    "chapter_title": "第三章 比选申请文件格式",
    "required_sections": [
        {"title": "一、比选函", "order": 1, "required": True, "has_template": False, ...},
        {"title": "1、我方自愿...", "order": 2, "required": True, ...},
        ...
    ],
    "template_tables": [...],
}
```

## 输出

```python
[
    {
        "source": "format_requirements",
        "source_index": 0,
        "title": "一、比选函",
        "description": "",
        "has_template": False,
        "template_tables": [],
        "children": [
            {"source": "format_requirements", "title": "1、我方自愿..."},
            ...
        ]
    },
    ...
]
```

## 解析规则

1. 父级检测：正则 `^[一二三四五六七八九十]+、` 匹配 → 父级节点
2. 子项归属：两个父级索引之间的 items 归属前一个父级
3. 保留元数据：has_template、template_tables 传递给父级
4. description 留空（阶段3填充）

## 边界处理

- required_sections 为空 → 返回 []，触发降级路径
- 只有一个父级 → 全部内容属于该父级
- 父级无子项 → children = []
- order 不连续 → 仍按检测到的顺序排列
