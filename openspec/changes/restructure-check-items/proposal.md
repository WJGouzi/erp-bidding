# check-items 接口重构：门面模式 + 内部模块化

## Why

当前 `GET /api/bidding/tasks/{id}/check-items` 返回的数据存在三个结构性问题：

### 问题 1：信息严重不全

当前只返回 9 条扁平记录（4 条资格 + 5 条评分），但前端的 ReviewWorkspace 需要展示以下内容：

| UI 章节 | check-items 当前状态 |
|---------|-------------------|
| 投标人须知（项目名称/编号/预算/招标人/代理机构/领域/概况/中小企业/暗标） | ❌ 无 |
| 商务要求（条款列表） | ❌ 无 |
| 技术要求（条款列表） | ❌ 无 |
| 资格审查（资格性审查/符合性审查/废标项） | ⚠️ 仅 4 条资格要求 |
| 评分标准（商务评分+技术评分+评分标准描述） | ⚠️ 仅评分维度名称 |
| 分包信息 | ❌ 无 |

### 问题 2：扁平结构无法支撑 UI

当前 9 条记录打平在一个数组里，没有层级、没有分类。但 ReviewWorkspace 需要分 Tab 渲染（资格性审查/符合性审查/废标项/商务评分/技术评分），扁平结构迫使前端自己分类，容易出错。

### 问题 3：字段混用

`check_label` 和 `check_value` 的定义模糊（label 里塞了全文，value 里也塞了全文），两者几乎无差异，导致 UI 显示困难。

## 方案

采用**门面模式（Facade）+ 内部模块化**设计：

```
┌─────────────────────────────────────────────────────────────┐
│  GET /tasks/:id/check-items  (统一对外接口)                  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  check-items 组装器                                    │   │
│  │  ├─ assemble_bidding_info()  ← 从 analysis_data 提取   │   │
│  │  ├─ assemble_business()      ← 从 business_requirements │ │
│  │  ├─ assemble_technical()     ← 从 technical_requirements│ │
│  │  ├─ assemble_qualification() ← 从 eligibility + 独立字段│ │
│  │  ├─ assemble_scoring()       ← 从 scoring + 独立字段    │ │
│  │  ├─ assemble_packages()      ← 从 packages_json         │ │
│  │  └─ assemble_checklist()     ← 扁平化待确认项（原有）   │ │
│  └──────────────────────────────────────────────────────┘   │
│                                                             │
│  数据源: bidding_analysis_result 表单行查询                 │
└─────────────────────────────────────────────────────────────┘
```

### 各子模块职责

| 模块 | 数据来源 | 输出结构 |
|------|---------|---------|
| `bidding_info` | `analysis_data.metadata` + `overview` | 结构化对象（含 project_name/code/budget/purchaser/agency 等） |
| `business` | `business_requirements` | 列表（含 content + source_section） |
| `technical` | `technical_requirements` | 列表（含 content + source_section） |
| `qualification` | `analysis_data.eligibility` + `qualification_requirements` + `disqualification_items` | 分三组：qualification / compliance / rejection |
| `scoring` | `analysis_data.scoring` + `scoring_items` | 含 method/total_score + business/technical 分组 |
| `packages` | `packages_json` | 列表（含 package_no/name/budget/parameters） |
| `checklist` | 各模块提取的待确认项 | 扁平列表（含 category/severity/content/confirmed_flag） |

## Scope

- 改动范围：`app/service_modules/task_pipeline/analysis_v3/` 及 `app/service_modules/task_pipeline/analysis.py`
- 不涉及：数据库表结构变更、前端代码
- check-items 接口的 URL 和请求方式不变，仅返回结构升级
