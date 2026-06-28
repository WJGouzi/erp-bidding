# Package Analysis

## 能力说明
对分包项目按包号分别分析技术参数，统计 ★/▲/一般 参数数量，识别核心产品。

## 输入
- 包含技术要求的 section
- package_nos 列表
- `LLMAdapter`

## 输出
```json
{
  "package_no": 1,
  "name": "口岸传染病病原体检测试剂",
  "budget": 2740000,
  "parameters": {
    "total_items": 200,
    "starred_count": 2,
    "important_count": 5,
    "general_count": 193,
    "core_products": ["多重虫媒病原体检测试剂盒"]
  }
}
```

## 解析策略
1. 检测是否分包（从 metadata 或 sections 标题判断）
2. 按包号定位对应 section
3. 每个包独立统计参数等级
4. 无分包时整体分析
