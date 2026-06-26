BID_TYPES = [
    {"label": "货物类", "value": "GOODS"},
    {"label": "服务类", "value": "SERVICE"},
    {"label": "工程类", "value": "ENGINEERING"},
]

TASK_ORIGINS = [
    {"label": "原始创建", "value": "ORIGINAL"},
    {"label": "再次生成", "value": "DERIVED"},
]

TASK_STATUSES = [
    {"label": "初始化", "value": "INIT"},
    {"label": "上传标书完成", "value": "UPLOADED"},
    {"label": "分析中", "value": "ANALYZING"},
    {"label": "待选择包号", "value": "PACKAGE_PENDING"},
    {"label": "分析完成", "value": "ANALYZED"},
    {"label": "核对完成", "value": "CHECKED"},
    {"label": "目录生成完毕", "value": "CATALOG_CONFIRMED"},
    {"label": "生成标书中", "value": "GENERATING"},
    {"label": "生成完成", "value": "GENERATED"},
    {"label": "已取消", "value": "CANCELLED"},
    {"label": "生成失败", "value": "FAILED"},
]

CURRENT_STEPS = [
    {"label": "上传标书", "value": "upload"},
    {"label": "分析文件", "value": "analyze"},
    {"label": "选择包号", "value": "package_select"},
    {"label": "核对信息", "value": "check"},
    {"label": "确认目录", "value": "catalog"},
    {"label": "生成配置", "value": "generate_config"},
    {"label": "生成标书", "value": "generate"},
    {"label": "完成", "value": "done"},
]

MATERIAL_TYPES = [
    {"label": "营业执照", "value": "BUSINESS_LICENSE"},
    {"label": "资质性文件", "value": "QUALIFICATION_FILE"},
    {"label": "法人身份证", "value": "LEGAL_PERSON_ID_CARD"},
    {"label": "授权委托书", "value": "AUTHORIZATION_LETTER"},
    {"label": "被授权人身份证", "value": "AUTHORIZED_PERSON_ID_CARD"},
    {"label": "资质声明函", "value": "QUALIFICATION_DECLARATION"},
    {"label": "法定代表人身份证明", "value": "LEGAL_PERSON_STATEMENT"},
    {"label": "财务报表", "value": "FINANCIAL_STATEMENT"},
    {"label": "廉洁承诺书", "value": "INTEGRITY_COMMITMENT"},
]

MODEL_TYPES = [
    {"label": "千问 Long", "value": "qwen-long"},
    {"label": "DeepSeek Chat", "value": "deepseek-chat"},
    {"label": "GPT-4o", "value": "gpt-4o"},
    {"label": "GLM-4", "value": "glm-4"},
    {"label": "Claude 3", "value": "claude-3"},
]
