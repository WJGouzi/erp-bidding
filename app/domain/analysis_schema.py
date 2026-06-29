"""中央数据契约：所有解析模块必须遵守。

设计原则：
  1. 每个提取字段附带置信度（confidence），下游可判断可靠性
  2. 类型明确，避免隐式假设（如 project_code 是 str 而非 dict 或 ""）
  3. 所有默认值集中在一处，消除分散在各文件的 NULL_METADATA
  4. 兼容新旧两种存储格式（dict {value: x} 和 直接值 x）
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  置信度等级
# ═══════════════════════════════════════════════════════════════


class ConfidenceLevel(Enum):
    """标准置信度等级。

    每个等级对应一个数值（0.0~1.0），用于定量比较。
    """

    EXACT = 0.95       # 精确匹配：regex 直接命中 + 表格交叉验证
    HIGH = 0.85        # 高置信度：regex 或表格单独命中
    MEDIUM = 0.70      # 中等：章节提取 + 标题强相关
    LOW = 0.50         # 低：LLM 提取，无规则/表格验证
    UNCERTAIN = 0.30   # 不确定：多源结果不一致
    UNKNOWN = 0.0      # 未提取到

    @classmethod
    def from_value(cls, value: float) -> "ConfidenceLevel":
        """根据数值返回最近的等级。"""
        for level in reversed(cls.__members__.values()):
            if value >= level.value:
                return level
        return cls.UNKNOWN


# ═══════════════════════════════════════════════════════════════
#  字段元数据
# ═══════════════════════════════════════════════════════════════


@dataclass
class FieldMetadata:
    """每个提取字段的元数据包装。

    Attributes:
        value: 提取到的值（extracted value or default）
        confidence: 置信度 0.0~1.0
        source: 提取来源描述（如 "regex:rule2" / "table:预算金额" / "llm"）
        raw_match: 匹配到的原文片段（用于调试和展示）
        fallback_attempted: 提取失败时尝试过的 fallback 路径
    """

    value: Any = None
    confidence: float = 0.0
    source: str = ""
    raw_match: str = ""
    fallback_attempted: List[str] = field(default_factory=list)

    def is_reliable(self, threshold: float = 0.5) -> bool:
        """判断当前字段是否可靠（置信度 >= 阈值）。"""
        return self.confidence >= threshold

    def to_dict(self) -> dict:
        """序列化为 dict，保持向后兼容（旧格式直接返回 value）。"""
        return {
            "value": self.value,
            "confidence": self.confidence,
            "source": self.source,
        }

    def __bool__(self) -> bool:
        """有值且置信度 > 0 时视为 True。"""
        return self.value is not None and self.confidence > 0

    def __str__(self) -> str:
        """直接输出 value 的字符串形式（保持向后兼容）。"""
        return str(self.value) if self.value is not None else ""


# ═══════════════════════════════════════════════════════════════
#  兼容读取函数
# ═══════════════════════════════════════════════════════════════


def safe_read(meta: dict, key: str, default: Any = "") -> Any:
    """兼容新旧两种 metadata 格式的读取函数。

    旧格式: meta["project_code"] = "CG20250099"          (字符串)
    新格式: meta["project_code"] = {"value": "CG20250099"}  (字典)

    也处理嵌套路径:
      meta["budget"]["total"] = 51500
      meta["budget"] = {"total": 51500, ...}
    """
    val = meta.get(key, default)
    if isinstance(val, dict):
        # 新格式：字典
        if "value" in val:
            v = val["value"]
            return v if v is not None else default
        # 嵌套 dict（如 budget: {"total": 51500}）
        return val
    # 旧格式：直接值
    return val if val is not None else default


def safe_read_deep(meta: dict, *keys: str, default: Any = "") -> Any:
    """深层安全读取，支持多级路径。

    Examples:
        safe_read_deep(meta, "budget", "total")  # meta["budget"]["total"]
        safe_read_deep(meta, "key_dates", "bid_deadline")
    """
    current = meta
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, {})
    if isinstance(current, dict) and "value" in current:
        v = current["value"]
        return v if v is not None else default
    return current if current is not None else default


# ═══════════════════════════════════════════════════════════════
#  业务字段映射表（集中一处，消除分散在各文件的手工映射）
# ═══════════════════════════════════════════════════════════════

# extra 字段 → 中文标签映射（按展示顺序）
EXTRA_LABELS: List[Tuple[str, str]] = [
    ("payment_terms", "付款方式"),
    ("service_period", "服务期限"),
    ("delivery_location", "交付地点"),
    ("acceptance_standard", "验收标准"),
    ("pricing_rule", "报价方式"),
    ("after_sale_service", "售后服务"),
    ("warranty_period", "质保期"),
    ("packaging_transport", "包装运输"),
    ("insurance", "保险要求"),
    ("delivery_terms", "交付要求"),
    ("special_declaration", "特别说明"),
    ("agency_fee", "代理服务费"),
    ("submission_location", "递交地点"),
    ("winner_count", "成交数量"),
    ("submission_copies", "份数要求"),
    ("submission_docs", "递交资料"),
]


# ═══════════════════════════════════════════════════════════════
#  数据结构定义
# ═══════════════════════════════════════════════════════════════


@dataclass
class MetadataSchema:
    """元数据 Schema。

    每个字段用 FieldMetadata 包装，带置信度。
    """

    project_name: FieldMetadata = field(default_factory=FieldMetadata)
    project_code: FieldMetadata = field(default_factory=FieldMetadata)
    purchaser_name: FieldMetadata = field(default_factory=FieldMetadata)
    purchaser_contact: FieldMetadata = field(default_factory=FieldMetadata)
    agent_name: FieldMetadata = field(default_factory=FieldMetadata)
    agent_contact: FieldMetadata = field(default_factory=FieldMetadata)

    budget_total: FieldMetadata = field(default_factory=FieldMetadata)
    package_count: FieldMetadata = field(default_factory=FieldMetadata)

    bid_deadline: FieldMetadata = field(default_factory=FieldMetadata)
    bid_opening: FieldMetadata = field(default_factory=FieldMetadata)
    bid_validity_days: FieldMetadata = field(default_factory=FieldMetadata)

    evaluation_method: FieldMetadata = field(default_factory=FieldMetadata)
    bid_type: FieldMetadata = field(default_factory=FieldMetadata)

    # 商务条款（动态扩展，key=field_name, value=FieldMetadata）
    extra: Dict[str, FieldMetadata] = field(default_factory=dict)

    # 原始 metadata dict（保持完整，供需要直接访问的场景）
    _raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "MetadataSchema":
        """从 analysis_data 的 metadata dict 构建。

        兼容新旧两种存储格式。
        """
        schema = cls()

        # 单字段映射
        field_map = {
            "project_name": "project_name",
            "project_code": "project_code",
            "purchaser_name": "purchaser_name",
            "purchaser_contact": "purchaser_contact",
            "agent_name": "agent_name",
            "agent_contact": "agent_contact",
        }

        # 支持旧格式的键名映射
        alt_field_map = {
            "purchaser_name": ("purchaser", "name"),
            "agent_name": ("agent", "name"),
        }

        for schema_field, data_key in field_map.items():
            raw_val = data.get(data_key)
            if isinstance(raw_val, dict) and "value" in raw_val:
                # 新格式：带置信度
                setattr(schema, schema_field, FieldMetadata(
                    value=raw_val.get("value"),
                    confidence=raw_val.get("confidence", 0.85),
                    source=raw_val.get("source", "schema"),
                    raw_match=raw_val.get("raw_match", ""),
                ))
            elif raw_val is not None and raw_val != "":
                # 旧格式：直接值
                setattr(schema, schema_field, FieldMetadata(
                    value=raw_val,
                    confidence=0.7,
                    source="legacy",
                ))

            # 如果主键没读到，尝试替代键（如 purchaser_name 走 purchaser.name）
            if not getattr(schema, schema_field).value and schema_field in alt_field_map:
                path = alt_field_map[schema_field]
                nested = data
                found = True
                for p in path:
                    if isinstance(nested, dict) and p in nested:
                        nested = nested[p]
                    else:
                        found = False
                        break
                if found and nested and (isinstance(nested, str) or (isinstance(nested, dict) and nested.get("value"))):
                    val = nested if isinstance(nested, str) else nested.get("value", "")
                    if val:
                        setattr(schema, schema_field, FieldMetadata(
                            value=val,
                            confidence=0.7,
                            source="legacy:nested",
                        ))

        # Budget
        budget_raw = data.get("budget", 0)
        if isinstance(budget_raw, dict):
            total = budget_raw.get("total", 0) if isinstance(budget_raw, dict) else 0
            schema.budget_total = FieldMetadata(
                value=total,
                confidence=0.85,
                source="schema",
            )
        elif isinstance(budget_raw, (int, float)):
            schema.budget_total = FieldMetadata(
                value=budget_raw,
                confidence=0.7,
                source="legacy:flat",
            )

        # Extra fields
        extra_raw = data.get("extra", {})
        if isinstance(extra_raw, dict):
            for field_key, _ in EXTRA_LABELS:
                val = extra_raw.get(field_key)
                if val is not None and val != "" and val != 0:
                    if isinstance(val, dict):
                        v = val.get("value", "")
                        if v:
                            schema.extra[field_key] = FieldMetadata(
                                value=v,
                                confidence=val.get("confidence", 0.7),
                                source=val.get("source", "extra"),
                            )
                    else:
                        schema.extra[field_key] = FieldMetadata(
                            value=val,
                            confidence=0.7,
                            source="extra:legacy",
                        )

        schema._raw = data
        return schema

    def to_legacy_dict(self) -> dict:
        """输出为旧格式的 dict（兼容现有消费端）。"""
        result = {}
        direct_fields = ["project_name", "project_code"]
        for field in direct_fields:
            fm: FieldMetadata = getattr(self, field, FieldMetadata())
            result[field] = fm.value if fm.value is not None else ""

        # purchaser
        result["purchaser"] = {
            "name": self.purchaser_name.value if self.purchaser_name.value else "",
            "contact": self.purchaser_contact.value if self.purchaser_contact.value else "",
        }
        # agent
        result["agent"] = {
            "name": self.agent_name.value if self.agent_name.value else "",
            "contact": self.agent_contact.value if self.agent_contact.value else "",
        }
        # budget
        result["budget"] = {"total": self.budget_total.value or 0, "packages": {}}
        # extra
        extra = {}
        for field_key, _ in EXTRA_LABELS:
            fm = self.extra.get(field_key)
            if fm and fm.value:
                extra[field_key] = fm.value
        result["extra"] = extra
        # key_dates
        result["key_dates"] = {
            "bid_deadline": self.bid_deadline.value if self.bid_deadline.value else "",
            "bid_opening": self.bid_opening.value if self.bid_opening.value else "",
            "bid_validity_days": self.bid_validity_days.value or 90,
        }
        # 保留其他未映射字段
        for k, v in self._raw.items():
            if k not in result:
                result[k] = v

        return result


# ═══════════════════════════════════════════════════════════════
#  格式要求 Schema（Phase 1.5）
# ═══════════════════════════════════════════════════════════════


@dataclass
class ReqSection:
    """格式要求中定义的必选章节"""
    title: str
    order: int
    required: bool = True
    has_template: bool = False


@dataclass
class TemplateTable:
    """格式要求中定义的模板表格"""
    section_ref: str
    headers: List[str]
    rows: List[List[str]]
    description: str = ""


@dataclass
class FixedText:
    """格式要求中定义的固定文本"""
    section_ref: str
    text: str
    position: str = "start"


@dataclass
class FormatRequirement:
    """招标文件中规定的响应文件格式要求。

    由 Phase 1.5 提取，被 catalog 和 generate 使用。
    """
    chapter_title: str = ""
    required_sections: List[ReqSection] = field(default_factory=list)
    template_tables: List[TemplateTable] = field(default_factory=list)
    fixed_texts: List[FixedText] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def has_complete_structure(self) -> bool:
        """是否有完整的结构定义（3 个以上必选章节）。"""
        required_count = sum(1 for s in self.required_sections if s.required)
        return required_count >= 3


# ═══════════════════════════════════════════════════════════════
#  完整分析结果 Schema
# ═══════════════════════════════════════════════════════════════


@dataclass
class AnalysisSchema:
    """完整的分析结果契约。

    所有模块的输出最终汇聚到这里。
    """

    metadata: MetadataSchema = field(default_factory=MetadataSchema)
    eligibility: dict = field(default_factory=dict)
    scoring: dict = field(default_factory=dict)
    packages: List[dict] = field(default_factory=list)
    format_requirements: Optional[FormatRequirement] = None
    issues: List[str] = field(default_factory=list)

    @classmethod
    def from_analysis_data(cls, analysis_data: dict) -> "AnalysisSchema":
        """从 analysis_data dict 构建。"""
        issues = []
        meta_dict = analysis_data.get("metadata", {})
        try:
            metadata = MetadataSchema.from_dict(meta_dict)
        except Exception as exc:
            issues.append(f"metadata 解析失败: {exc}")
            metadata = MetadataSchema()

        return cls(
            metadata=metadata,
            eligibility=analysis_data.get("eligibility", {}),
            scoring=analysis_data.get("scoring", {}),
            packages=analysis_data.get("packages", []),
            issues=issues,
        )

    def get_metadata_value(self, key: str, default: Any = "") -> Any:
        """安全获取 metadata 字段值（兼容新旧格式）。"""
        fm = getattr(self.metadata, key, None)
        if fm and isinstance(fm, FieldMetadata) and fm.value is not None:
            return fm.value
        # 降级到 _raw
        return safe_read(self.metadata._raw, key, default)


# ═══════════════════════════════════════════════════════════════
#  Value Gate：校验门
# ═══════════════════════════════════════════════════════════════


class ValidationGate:
    """校验门：确保分析结果满足最低质量要求。

    校验内容：
      1. 类型校验：每个字段的类型与 Schema 声明一致
      2. 置信度校验：关键字段 confidence < 阈值时告警
      3. 覆盖率报告：缺失/低置信字段列表
      4. 阻断严重错误：类型不匹配直接抛异常
    """

    # 关键字段及阈值
    CRITICAL_FIELDS: Dict[str, dict] = {
        "project_name": {"min_confidence": 0.3, "label": "项目名称"},
        "project_code": {"min_confidence": 0.3, "label": "项目编号"},
        "purchaser_name": {"min_confidence": 0.3, "label": "采购人"},
        "budget_total": {"min_confidence": 0.3, "label": "预算金额"},
    }

    IMPORTANT_FIELDS: Dict[str, dict] = {
        "agent_name": {"min_confidence": 0.3, "label": "代理机构"},
        "bid_deadline": {"min_confidence": 0.3, "label": "投标截止日期"},
        "evaluation_method": {"min_confidence": 0.3, "label": "评标办法"},
    }

    def validate(self, schema: AnalysisSchema) -> List[str]:
        """执行校验，返回 issues 列表。

        Returns:
            List[str]: 每个元素是一条 issue 描述。
            空列表表示全部通过。
        """
        issues = []

        # 1. 关键字段校验
        for field_name, rules in self.CRITICAL_FIELDS.items():
            fm: Optional[FieldMetadata] = getattr(schema.metadata, field_name, None)
            if fm is None:
                issues.append(f"CRITICAL: {rules['label']}({field_name}) 字段缺失")
                continue
            if not fm.is_reliable(rules["min_confidence"]):
                issues.append(
                    f"LOW_CONFIDENCE: {rules['label']}({field_name}) "
                    f"置信度 {fm.confidence:.2f} < {rules['min_confidence']}, "
                    f"来源: {fm.source}"
                )

        # 2. 重要字段校验
        for field_name, rules in self.IMPORTANT_FIELDS.items():
            fm = getattr(schema.metadata, field_name, None)
            if fm and not fm.is_reliable(rules["min_confidence"]):
                issues.append(
                    f"LOW_CONFIDENCE: {rules['label']}({field_name}) "
                    f"置信度 {fm.confidence:.2f} < {rules['min_confidence']}"
                )

        # 3. Extra 字段覆盖率
        extra = schema.metadata.extra
        found = len([v for v in extra.values() if v and v.value])
        if found == 0:
            issues.append("INFO: 商务条款(extra) 全部未提取到")
        elif found < 3:
            issues.append(f"INFO: 商务条款(extra) 仅提取到 {found} 项")

        # 4. 包数据校验
        if not schema.packages:
            issues.append("WARNING: 未提取到分包信息")
        else:
            is_multi = len(schema.packages) > 1
            for pkg in schema.packages:
                # 单包场景：包名可为空（用户要求）
                if not pkg.get("package_no"):
                    issues.append(f"WARNING: 分包数据不完整: {pkg}")
                elif is_multi and not pkg.get("name"):
                    issues.append(f"WARNING: 多包场景下包名为空: 第{pkg.get('package_no')}包")

        return issues

    def validate_and_raise(self, schema: AnalysisSchema) -> List[str]:
        """执行校验，CRITICAL 级别直接抛异常。

        Returns:
            非 CRITICAL 的 issues 列表。
        """
        issues = self.validate(schema)
        # CRITICAL_FIELDS 上的 LOW_CONFIDENCE 也视为阻断级
        blocking = [i for i in issues if i.startswith("CRITICAL")
                    or (i.startswith("LOW_CONFIDENCE") and any(
                        f"({fn})" in i for fn in self.CRITICAL_FIELDS))]
        if blocking:
            raise ValueError(
                "数据校验未通过:\n  " + "\n  ".join(blocking)
            )
        return issues


# ═══════════════════════════════════════════════════════════════
#  便捷函数
# ═══════════════════════════════════════════════════════════════


def build_overview(metadata: MetadataSchema) -> str:
    """从 metadata 构建 overview 字符串。

    统一一处，消除分散在各文件的拼接逻辑。
    """
    project_name = metadata.project_name.value or ""
    project_code = metadata.project_code.value or ""
    budget_total = metadata.budget_total.value or 0

    parts = [f"项目: {project_name} (编号: {project_code})"]

    if budget_total:
        if budget_total % 10000 == 0:
            parts.append(f"预算: {budget_total // 10000}万元")
        else:
            parts.append(f"预算: {budget_total / 10000:.2f}万元")

    return " | ".join(parts)


def build_business_requirements(metadata: MetadataSchema) -> str:
    """从 metadata.extra 构建商务要求字符串。

    统一一处，消除分散在各文件的拼接逻辑。
    """
    biz_parts = []
    extra = metadata.extra

    for field_key, field_label in EXTRA_LABELS:
        fm = extra.get(field_key)
        if fm and fm.value:
            val = fm.value
            if field_key == "agency_fee":
                biz_parts.append(f"{field_label}：{val}元")
            elif field_key == "service_period" and isinstance(val, (int, float)) and val < 100:
                biz_parts.append(f"{field_label}：{val}天")
            else:
                biz_parts.append(f"{field_label}：{val}")

    if metadata._raw.get("extra", {}).get("business_terms_raw") and not biz_parts:
        biz_parts.append(metadata._raw["extra"]["business_terms_raw"])

    return "\n".join(biz_parts) if biz_parts else "暂未提取到商务要求。"
