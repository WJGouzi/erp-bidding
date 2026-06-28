# 内容信号归类

## 目标
从定位的章节文本中检测信号词，自动归类为"特定资质""实质性条款""业绩要求"等。

## 具体设计

### 信号词配置
`config/presets/signal_words.yaml`

### 分类类型
- 特定资格：许可证、资质证书、注册证、经营许可、备案、认证
- 业绩要求：业绩、类似项目、类似业绩、成功案例
- 联合体：联合体、联合体投标、联合体协议
- 保证金：投标保证金、履约保证金、投标担保
- 实质性条款：★、实质性要求、必须、不得
- 废标信号：投标无效、废标、拒收、否决、不予受理

### 归类逻辑
```python
def _classify_by_signal(text, signals):
    for category, keywords in signals.items():
        if any(kw in text for kw in keywords):
            return category
    return None
```

### 严重级别
- fatal：废标条件、★实质性条款
- critical：投标保证金、特定许可证缺失
- normal：通用资格、业绩要求

## 验收标准
1. 信号词配置文件可独立修改，不影响代码
2. 新增分类类型不需要改代码
3. 分类准确率 ≥ 85%
