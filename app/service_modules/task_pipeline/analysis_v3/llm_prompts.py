"""LLM prompt 模板 — 用于招标文件非结构化信息提取。

每个 prompt 设计原则：
1. 结构化输出（JSON mode）
2. 输入足够小（几百到一两千 token）
3. 任务单一（每个 prompt 只做一件事）
"""

# ========== Phase 1: 元数据提取 ==========

PROMPT_METADATA = """你是一个招标文件解析专家。从以下文档开头提取项目名称、采购人和采购代理机构的完整信息。

文档开头：
{document_excerpt}

以 JSON 格式返回，不要包含其他内容：
{{
  "project_name": "项目全称（如"卫生应急用试剂一批采购项目"），未找到则返回null",
  "project_code": "项目编号/采购项目编号/比选编号，未找到则返回null",
  "purchaser_name": "采购人（招标人/比选人）的全称，未找到则返回null",
  "purchaser_contact": "采购人联系人姓名，未找到则返回null",
  "agent_name": "采购代理机构（招标代理机构）的全称，未找到则返回null",
  "agent_contact": "代理机构联系人姓名，未找到则返回null"
}}"""

PROMPT_BUDGET = """从以下招标信息中提取预算金额，并按采购包拆分。

前附表关键信息：
{table_kv}

以 JSON 格式返回，不要包含其他内容：
{{
  "budget_total": "总预算金额（数字，不含千分位逗号和单位，如1033302）",
  "budget_note": "预算的原始文本描述",
  "packages": [
    {{
      "package_no": "包号（数字）",
      "amount": "该包金额（数字）",
      "note": "该包描述"
    }}
  ]
}}

注意：
- 金额可能包含千分位逗号（如1,033,302.36），去掉逗号
- 金额可能以万元为单位（如1015万元），转换为元
- 如果文中提到了多个采购包，每个包单独列出
- 如果只有一个包，packages 数组里放一个元素
- 没有找到则 budget_total 返回 0，packages 返回空数组"""

# ========== Phase 3: 评分表结构化 ==========

PROMPT_SCORING = """你是一个招标文件解析专家。从以下评分表中提取所有评分维度信息。

评分表内容：
{scoring_text}

以 JSON 格式返回，不要包含其他内容：
{{
  "method": "评标方法名称（如"综合评分法"、"最低评标价法"、"性价比法"等）",
  "total_score": "总分（数字，如100）",
  "dimensions": [
    {{
      "name": "评分因素名称",
      "score": "分值（数字）",
      "weight": "权重百分比（如30%，没有则返回null）",
      "criteria": "评分标准描述",
      "type": "客观/主观（根据评分标准判断，没有明确说明返回null）"
    }}
  ]
}}"""

# ========== Phase 3: 商务要求提取 ==========

PROMPT_BUSINESS = """从以下招标文件章节中提取所有商务要求（非技术类的服务要求，如交货、付款、验收、售后等）。

章节内容：
{section_text}

以 JSON 格式返回，不要包含其他内容：
{{
  "business_requirements": [
    {{
      "name": "要求名称（如"交货时间"、"付款方式"、"验收标准"）",
      "requirement": "具体内容描述",
      "is_star": true/false,
      "importance": "critical/high/normal"
    }}
  ]
}}

注意：
- 只提取商务/服务类要求，不提取技术参数
- ★标记的为 critical 重要性
- ▲标记的为 high 重要性"""

# ========== Phase 3: 技术要求提取 ==========

PROMPT_TECHNICAL = """从以下招标文件章节中提取所有技术要求和技术参数。

章节内容：
{section_text}

以 JSON 格式返回，不要包含其他内容：
{{
  "technical_requirements": [
    {{
      "name": "参数名称或要求标题",
      "requirement": "具体技术参数内容",
      "is_star": true/false,
      "is_arrow": true/false,
      "importance": "critical/high/normal"
    }}
  ]
}}

注意：
- ★标记的为 critical 重要性（实质性要求）
- ▲标记的为 high 重要性（重点扣分项）
- 不提取商务/服务类要求"""
