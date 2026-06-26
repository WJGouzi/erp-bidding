"""标书任务分析阶段相关流程，包括分析、分包、核对项读取与确认。"""

import json
import logging; logger = logging.getLogger(__name__)
import re
import time

from flask import current_app

from ...core.extensions import db
from ...domain import (
    BiddingAnalysisResult,
    BiddingCheckItem,
    BiddingSharedResource,
    BiddingTask,
    FileStorage,
)
from .execution import _assert_execution_active, _set_execution_progress, _submit_background_execution
from ..common import log_operation
from .helpers import (
    _build_shared_resource_analysis_text,
    _detect_package_info,
    _extract_effective_text,
    _extract_package_numbers,
    _read_file_text,
)


def _split_analysis_units(text):
    """将文本拆分为适合规则提取的语义片段。"""

    if not text:
        return []
    raw_units = re.split(r"[\r\n]+|(?<=[。；;])", text)
    units = []
    for item in raw_units:
        normalized = re.sub(r"\s+", " ", (item or "").strip())
        if normalized:
            units.append(normalized)
    return units


def _collect_matching_units(units, keywords, limit=4):
    """按关键字筛选片段，并保持原始顺序去重。"""

    matched = []
    seen = set()
    for unit in units:
        if not any(keyword in unit for keyword in keywords):
            continue
        if unit in seen:
            continue
        matched.append(unit)
        seen.add(unit)
        if len(matched) >= limit:
            break
    return matched


def _join_analysis_units(units, fallback_text="", max_length=500):
    """将片段列表拼接为稳定文本，并在缺失时回退。"""

    values = [item.strip() for item in units if item and item.strip()]
    if not values and fallback_text:
        values = [fallback_text.strip()]
    text = "\n".join(values).strip()
    if max_length and len(text) > max_length:
        return text[:max_length].rstrip()
    return text



def _extract_structured_analysis_chunked(full_text, adapter, system_prompt, schema):
    """对超长招标文本进行分块提取，然后合并结果。
    
    每块8000字符，重叠2000字符，逐块调用LLM提取后合并。
    """
    import json
    
    chunk_size = 8000
    overlap = 2000
    chunks = []
    start = 0
    while start < len(full_text):
        end = start + chunk_size
        chunks.append(full_text[start:end])
        start = end - overlap
        if start >= len(full_text):
            break

    merged = {}
    field_order = ["bidder_notice", "business_requirements", "technical_requirements", "qualification_review", "scoring_items"]

    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    def _extract_single_chunk(chunk):
        """提取单个分块，供并发调用。"""
        chunk_prompt = (
            "从以下招标文本中提取结构化信息，严格按照此JSON格式返回（不要markdown、不要解释）：\n"
            + schema + "\n\n"
            "规则：\n"
            "1. bidder_notice 各字段从原文提取具体值，无信息则填空字符串\n"
            "2. business_requirements 提取交货期、付款、质保、售后、验收等所有商务条件原文\n"
            "3. technical_requirements 提取技术参数、规格、性能、配置等所有技术条件原文\n"
            "4. qualification_review.qualification_check 提取营业执照、许可证、财务、社保、信用等资格要求原文\n"
            "5. qualification_review.conformity_check 提取文件格式、签字盖章、有效期等符合性要求原文\n"
            "6. qualification_review.disqualification_items 提取所有废标情形原文\n"
            "7. scoring_items 提取评分办法、评分细则、分值分配等原文\n"
            "8. 字段值保留原文关键信息（数字、名称、日期等），每条1-3句\n"
            "9. 只输出JSON，不要任何其他文字\n\n"
            "招标文本：\n" + chunk
        )
        try:
            raw = adapter.generate_text(
                system_prompt=system_prompt,
                user_prompt=chunk_prompt,
                temperature=0.05,
                max_tokens=3000,
            )
            if raw:
                out = raw.strip()
                if out.startswith("```"):
                    idx = out.find("\n")
                    if idx > 0:
                        out = out[idx+1:]
                if out.endswith("```"):
                    out = out[:-3].strip()
                brace_start = out.find("{")
                brace_end = out.rfind("}")
                if brace_start >= 0 and brace_end > brace_start:
                    out = out[brace_start:brace_end+1]
                    return json.loads(out)
        except Exception as chunk_exc:
            logger.warning("[analysis] 分块提取异常: %s", chunk_exc)
        return None

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_chunk = {executor.submit(_extract_single_chunk, chunk): chunk for chunk in chunks}
        for future in as_completed(future_to_chunk):
            chunk_data = future.result()
            if not chunk_data:
                continue
            for key in field_order:
                if key == "bidder_notice":
                    for sub_key in chunk_data.get(key, {}):
                        val = chunk_data[key].get(sub_key, "")
                        if val and not merged.setdefault(key, {}).get(sub_key):
                            merged[key] = merged.get(key) or {}
                            merged[key][sub_key] = val
                elif key == "qualification_review":
                    for sub_key in chunk_data.get(key, {}):
                        val = chunk_data[key].get(sub_key, "")
                        if val and not merged.setdefault(key, {}).get(sub_key):
                            merged[key] = merged.get(key) or {}
                            merged[key][sub_key] = val
                else:
                    val = chunk_data.get(key, "")
                    if val:
                        existing = merged.get(key, "")
                        if not existing:
                            merged[key] = val
                        elif val not in existing:
                            merged[key] = existing + "\n" + val

    if merged:
        merged["version"] = "v2"
        return merged
    return None


def _extract_structured_analysis_with_llm(text):
    """用大模型从招标文本中提取结构化分析结果，返回 v2 格式 dict。"""
    from ...infrastructure.integrations import LLMAdapter
    import json

    cleaned_text = (text or "").strip()
    if not cleaned_text:
        return None

    adapter = LLMAdapter(
        api_key=current_app.config.get("OPENAI_API_KEY"),
        base_url=current_app.config.get("OPENAI_BASE_URL"),
        default_model=current_app.config.get("OPENAI_MODEL_NAME"),
    )
    if not adapter.is_available():
        logger.warning("[analysis] LLM 不可用，跳过模型分析")
        return None

    system_prompt = "你是招标文件分析专家。从招标文本中提取结构化信息，只输出JSON。"

    schema = (
        '{"version":"v2",'
        '"has_package":false,'
        '"packages":[{"package_no":"","package_name":""}],'
        '"bidder_notice":{"project_name":"","project_no":"","package_no":"","budget":"","tenderee":"","agent":"","field":"","overview":"","for_sme":""},'
        '"business_requirements":"",'
        '"technical_requirements":"",'
        '"qualification_review":{"qualification_check":"","conformity_check":"","disqualification_items":""},'
        '"scoring_items":""}'
    )

    user_prompt = (
        "从以下招标文本中提取结构化信息，严格按照此JSON格式返回（不要markdown、不要解释）：\n"
        + schema + "\n\n"
        "规则：\n"
        "1. bidder_notice 各字段从原文提取具体值，无信息则填空字符串\n"
        "2. business_requirements 提取交货期、付款、质保、售后、验收等所有商务条件原文\n"
        "3. technical_requirements 提取技术参数、规格、性能、配置等所有技术条件原文\n"
        "4. qualification_review.qualification_check 提取营业执照、许可证、财务、社保、信用等资格要求原文\n"
        "5. qualification_review.conformity_check 提取文件格式、签字盖章、有效期等符合性要求原文\n"
        "6. qualification_review.disqualification_items 提取所有废标情形原文\n"
        "7. scoring_items 提取评分办法、评分细则、分值分配等原文\n"
        "8. 字段值保留原文关键信息（数字、名称、日期等），每条1-3句\n"
        "9. has_package 标记是否存在分包，packages 列出所有分包信息（包号+包名称），无分包时 packages 为 []\n"
        "10. 只输出JSON，不要任何其他文字\n\n"
        "招标文本：\n" + cleaned_text
    )

    try:
        raw = adapter.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.05,
            max_tokens=3000,
        )
        if not raw:
            logger.warning("[analysis] LLM 返回空")
            return None

        logger.info("[analysis] LLM 返回 %s 字符", len(raw))

        # 清理响应
        out = raw.strip()
        if out.startswith("```"):
            idx = out.find("\n")
            if idx > 0:
                out = out[idx+1:]
        if out.endswith("```"):
            out = out[:-3].strip()

        # 提取 JSON 对象
        brace_start = out.find("{")
        brace_end = out.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            out = out[brace_start:brace_end+1]
        else:
            return None

        data = json.loads(out)
        data["version"] = "v2"

        # 确保 bidder_notice
        if "bidder_notice" not in data or not isinstance(data["bidder_notice"], dict):
            data["bidder_notice"] = {}
        for f in ["project_name","project_no","package_no","budget","tenderee","agent","field","overview","for_sme"]:
            if f not in data["bidder_notice"] or not str(data["bidder_notice"].get(f,"") or "").strip():
                data["bidder_notice"][f] = ""

        # 确保 qualification_review
        if "qualification_review" not in data or not isinstance(data["qualification_review"], dict):
            data["qualification_review"] = {}
        for f in ["qualification_check","conformity_check","disqualification_items"]:
            if f not in data["qualification_review"] or not str(data["qualification_review"].get(f,"") or "").strip():
                data["qualification_review"][f] = ""

        for f in ["business_requirements","technical_requirements","scoring_items"]:
            if f not in data or not str(data.get(f,"") or "").strip():
                data[f] = ""

        # 判断是否有有效内容
        has = any([
            any(v for v in data.get("bidder_notice",{}).values()),
            data.get("business_requirements",""),
            data.get("technical_requirements",""),
            any(v for v in data.get("qualification_review",{}).values()),
            data.get("scoring_items",""),
        ])
        if not has:
            logger.warning("[analysis] LLM 全空，降级规则")
            return None

        logger.info("[analysis] LLM 结构化分析成功")
        return data
    except Exception as exc:
        logger.warning("[analysis] LLM 异常: %s", exc)
        return None


def _extract_structured_analysis(text):
    """从整份文档或选中包号文本中提取结构化分析结果。优先使用大模型，失败后回退规则匹配。"""

    cleaned_text = (text or "").strip()
    if not cleaned_text:
        return {
            "overview": "暂未提取到项目概述。",
            "requirements": "暂未提取到招标要求。",
            "business_requirements": "暂未提取到商务要求。",
            "qualification_requirements": "暂未提取到资质要求。",
            "technical_requirements": "暂未提取到技术要求。",
            "scoring_items": "暂未提取到评分点。",
            "disqualification_items": "暂未提取到废标项。",
        }

    # 优先用大模型提取
    try:
        from ...infrastructure.integrations import LLMAdapter
        adapter = LLMAdapter(
            api_key=current_app.config.get("OPENAI_API_KEY"),
            base_url=current_app.config.get("OPENAI_BASE_URL"),
            default_model=current_app.config.get("OPENAI_MODEL_NAME"),
        )
        if not adapter.is_available():
            logger.warning("[analysis] LLM 不可用，跳过模型分析")
        else:
            MAX_SINGLE_PASS_CHARS = 12000
            if len(cleaned_text) > MAX_SINGLE_PASS_CHARS:
                logger.info("[analysis] 文档较大(%s字符)，使用分块并行提取", len(cleaned_text))
                schema = (
                    '{"version":"v2",'
                    '"has_package":false,'
                    '"packages":[{"package_no":"","package_name":""}],'
                    '"bidder_notice":{"project_name":"","project_no":"","package_no":"","budget":"","tenderee":"","agent":"","field":"","overview":"","for_sme":""},'
                    '"business_requirements":"",'
                    '"technical_requirements":"",'
                    '"qualification_review":{"qualification_check":"","conformity_check":"","disqualification_items":""},'
                    '"scoring_items":""}'
                )
                system_prompt = "你是招标文件分析专家。从招标文本中提取结构化信息，只输出JSON。"
                llm_result = _extract_structured_analysis_chunked(cleaned_text, adapter, system_prompt, schema)
            else:
                llm_result = _extract_structured_analysis_with_llm(cleaned_text)
            if llm_result is not None:
                logger.info("[analysis] 大模型分析完成")
                return llm_result
    except Exception as exc:
        logger.warning("[analysis] 大模型分析异常，回退规则匹配: %s", exc)

    units = _split_analysis_units(cleaned_text)
    overview_keywords = ["项目概述", "项目名称", "项目背景", "采购内容", "采购范围", "服务内容", "工程概况", "第"]
    requirement_keywords = ["要求", "需求", "参数", "规格", "标准", "功能", "性能", "响应", "采购", "服务", "施工"]
    business_keywords = [
        "商务",
        "交货",
        "供货",
        "交付",
        "工期",
        "服务期",
        "质保",
        "售后",
        "报价",
        "付款",
        "验收",
        "履约",
        "违约",
        "评分",
        "商务条款",
    ]
    qualification_keywords = [
        "资格",
        "资质",
        "营业执照",
        "许可证",
        "法人",
        "信用",
        "财务",
        "社保",
        "纳税",
        "认证",
        "证书",
        "项目经理",
        "人员",
        "安全生产",
    ]
    technical_keywords = [
        "技术要求",
        "技术参数",
        "参数",
        "规格",
        "性能",
        "兼容",
        "功能",
        "配置",
        "接口",
        "标准",
        "响应",
    ]
    scoring_keywords = ["评分", "评审", "分值", "得分", "评分标准", "评标办法", "加分"]
    disqualification_keywords = ["废标", "无效投标", "否决", "不予受理", "无效响应", "资格审查不通过"]

    overview_units = _collect_matching_units(units, overview_keywords, limit=3) or units[:3]
    requirement_units = _collect_matching_units(units, requirement_keywords, limit=5) or units[:5]
    business_units = _collect_matching_units(units, business_keywords, limit=4)
    qualification_units = _collect_matching_units(units, qualification_keywords, limit=4)
    technical_units = _collect_matching_units(units, technical_keywords, limit=5)
    scoring_units = _collect_matching_units(units, scoring_keywords, limit=4)
    disqualification_units = _collect_matching_units(units, disqualification_keywords, limit=4)

    if not business_units:
        business_units = requirement_units[:2]
    if not qualification_units:
        qualification_units = [unit for unit in requirement_units if unit not in business_units][:2] or requirement_units[:2]
    if not technical_units:
        technical_units = [unit for unit in requirement_units if unit not in business_units][:3] or requirement_units[:3]

    return {
        "overview": _join_analysis_units(overview_units, fallback_text=cleaned_text[:200], max_length=300),
        "requirements": _join_analysis_units(requirement_units, fallback_text=cleaned_text[:500], max_length=600),
        "business_requirements": _join_analysis_units(
            business_units,
            fallback_text="暂未提取到明确商务要求。",
            max_length=400,
        ),
        "qualification_requirements": _join_analysis_units(
            qualification_units,
            fallback_text="暂未提取到明确资质要求。",
            max_length=400,
        ),
        "technical_requirements": _join_analysis_units(
            technical_units,
            fallback_text="暂未提取到明确技术要求。",
            max_length=500,
        ),
        "scoring_items": _join_analysis_units(
            scoring_units,
            fallback_text="暂未提取到明确评分点。",
            max_length=400,
        ),
        "disqualification_items": _join_analysis_units(
            disqualification_units,
            fallback_text="暂未提取到明确废标项。",
            max_length=400,
        ),
    }


def _build_check_items(shared_resource_id, analysis_payload):
    """基于结构化分析结果生成待人工确认的核对项。"""

    BiddingCheckItem.query.filter_by(shared_resource_id=shared_resource_id).delete()
    items = [
        ("overview", "项目概述", analysis_payload.get("overview", ""), 1),
        ("requirements", "招标要求", analysis_payload.get("requirements", ""), 2),
        ("business_requirements", "商务要求", analysis_payload.get("business_requirements", ""), 3),
        ("qualification_requirements", "资质要求", analysis_payload.get("qualification_requirements", ""), 4),
        ("technical_requirements", "技术要求", analysis_payload.get("technical_requirements", ""), 5),
        ("scoring_items", "评分点", analysis_payload.get("scoring_items", ""), 6),
        ("disqualification_items", "废标项", analysis_payload.get("disqualification_items", ""), 7),
    ]
    for check_key, check_label, check_value, sort_no in items:
        db.session.add(
            BiddingCheckItem(
                shared_resource_id=shared_resource_id,
                check_key=check_key,
                check_label=check_label,
                check_value=check_value,
                confirmed_flag=False,
                sort_no=sort_no,
            )
        )



def _build_check_items_v2(shared_resource_id, analysis_payload):
    """基于新版本结构化分析数据（v2）生成待人工确认的核对项。"""

    BiddingCheckItem.query.filter_by(shared_resource_id=shared_resource_id).delete()
    items = []

    # 1. 投标人须知
    bn = analysis_payload.get("bidder_notice", {}) or {}
    bn_keys = [
        ("project_name", "项目名称"),
        ("project_no", "项目编号"),
        ("package_no", "包号"),
        ("budget", "预算"),
        ("tenderee", "招标人"),
        ("agent", "招标代理机构"),
        ("field", "项目所属领域"),
        ("overview", "项目概况"),
        ("for_sme", "是否专门面向中小微企业采购"),
    ]
    sort_no = 1
    for key, label in bn_keys:
        val = bn.get(key, "")
        if val and str(val).strip():
            items.append((f"bidder_notice_{key}", label, str(val).strip(), sort_no))
        else:
            items.append((f"bidder_notice_{key}", label, "（暂未提取到" + label + "）", sort_no))
        sort_no += 1

    # 2. 商务要求
    br = analysis_payload.get("business_requirements", "")
    if br and str(br).strip():
        items.append(("business_requirements", "商务要求", str(br).strip(), sort_no))
    else:
        items.append(("business_requirements", "商务要求", "（暂未提取到商务要求）", sort_no))
    sort_no += 1

    # 3. 技术要求
    tr = analysis_payload.get("technical_requirements", "")
    if tr and str(tr).strip():
        items.append(("technical_requirements", "技术要求", str(tr).strip(), sort_no))
    else:
        items.append(("technical_requirements", "技术要求", "（暂未提取到技术要求）", sort_no))
    sort_no += 1

    # 4. 资格审查
    qr = analysis_payload.get("qualification_review", {}) or {}
    qr_keys = [
        ("qualification_check", "资格性审查"),
        ("conformity_check", "符合性审查"),
        ("disqualification_items", "废标项"),
    ]
    for key, label in qr_keys:
        val = qr.get(key, "")
        if val and str(val).strip():
            items.append((f"qualification_{key}", label, str(val).strip(), sort_no))
        else:
            items.append((f"qualification_{key}", label, "（暂未提取到" + label + "）", sort_no))
        sort_no += 1

    # 5. 评分标准
    si = analysis_payload.get("scoring_items", "")
    if si and str(si).strip():
        items.append(("scoring_items", "评分标准", str(si).strip(), sort_no))
    else:
        items.append(("scoring_items", "评分标准", "（暂未提取到评分标准）", sort_no))
    sort_no += 1

    for check_key, check_label, check_value, sort_no in items:
        db.session.add(
            BiddingCheckItem(
                shared_resource_id=shared_resource_id,
                check_key=check_key,
                check_label=check_label,
                check_value=check_value,
                confirmed_flag=False,
                sort_no=sort_no,
            )
        )



def _refresh_analysis_result(result, shared_resource_id, analysis_text, source_files=None):
    """按当前有效分析文本刷新结构化分析结果和核对项。
    
    优先从 LLM 提取新版本结构化数据（v2），
    写入 analysis_data JSON 字段和独立字段，
    同时生成核对项。
    """
    import json

    analysis_payload = _extract_structured_analysis(analysis_text)
    
    logger.info("[analysis] 分析完成, v2=%s, keys=%s",
                analysis_payload.get("version") == "v2" if isinstance(analysis_payload, dict) else False,
                list(analysis_payload.keys()) if isinstance(analysis_payload, dict) else "N/A")
    
    # 检查是否为新版本结构化数据（v2）
    if isinstance(analysis_payload, dict) and analysis_payload.get("version") == "v2":
        if source_files:
            analysis_payload["source_files"] = source_files
        result.analysis_data = json.dumps(analysis_payload, ensure_ascii=False)
        # 同时回填独立字段以保持旧版本兼容
        bn = analysis_payload.get("bidder_notice", {})
        result.overview = bn.get("overview", "")
        result.requirements = ""
        result.business_requirements = analysis_payload.get("business_requirements", "")
        result.qualification_requirements = analysis_payload.get("qualification_review", {}).get("qualification_check", "")
        result.technical_requirements = analysis_payload.get("technical_requirements", "")
        result.scoring_items = analysis_payload.get("scoring_items", "")
        result.disqualification_items = analysis_payload.get("qualification_review", {}).get("disqualification_items", "")
        _build_check_items_v2(shared_resource_id, analysis_payload)
    else:
        # 旧版本规则匹配
        result.overview = analysis_payload.get("overview", "")
        result.requirements = analysis_payload.get("requirements", "")
        result.business_requirements = analysis_payload.get("business_requirements", "")
        result.qualification_requirements = analysis_payload.get("qualification_requirements", "")
        result.technical_requirements = analysis_payload.get("technical_requirements", "")
        result.scoring_items = analysis_payload.get("scoring_items", "")
        result.disqualification_items = analysis_payload.get("disqualification_items", "")
        _build_check_items(shared_resource_id, analysis_payload)


def _normalize_confirmed_check_value(value):
    text = str(value or "").strip()
    if text.startswith("（暂未提取到") and text.endswith("）"):
        return ""
    return text


def _sync_confirmed_items_to_analysis_result(shared_resource_id, existing_items):
    """将人工核对后的值回写到 analysis_result，作为后续目录与生成依据。"""
    result = BiddingAnalysisResult.query.filter_by(shared_resource_id=shared_resource_id).first()
    if not result:
        return

    try:
        analysis_payload = json.loads(result.analysis_data) if result.analysis_data else {}
    except (TypeError, json.JSONDecodeError):
        analysis_payload = {}

    if not isinstance(analysis_payload, dict):
        analysis_payload = {}

    analysis_payload.setdefault("version", "v2")
    analysis_payload.setdefault("bidder_notice", {})
    analysis_payload.setdefault("qualification_review", {})

    bidder_notice = analysis_payload["bidder_notice"]
    qualification_review = analysis_payload["qualification_review"]

    for check_key, record in existing_items.items():
        confirmed_value = _normalize_confirmed_check_value(record.check_value)

        if check_key.startswith("bidder_notice_"):
            bidder_notice[check_key.replace("bidder_notice_", "", 1)] = confirmed_value
            continue
        if check_key == "business_requirements":
            analysis_payload["business_requirements"] = confirmed_value
            continue
        if check_key == "technical_requirements":
            analysis_payload["technical_requirements"] = confirmed_value
            continue
        if check_key == "scoring_items":
            analysis_payload["scoring_items"] = confirmed_value
            continue
        if check_key == "qualification_qualification_check":
            qualification_review["qualification_check"] = confirmed_value
            continue
        if check_key == "qualification_conformity_check":
            qualification_review["conformity_check"] = confirmed_value
            continue
        if check_key == "qualification_disqualification_items":
            qualification_review["disqualification_items"] = confirmed_value
            continue

        # 兼容旧版本核对键
        if check_key == "overview":
            result.overview = confirmed_value
        elif check_key == "requirements":
            result.requirements = confirmed_value
        elif check_key == "qualification_requirements":
            result.qualification_requirements = confirmed_value
        elif check_key == "disqualification_items":
            result.disqualification_items = confirmed_value

    result.analysis_data = json.dumps(analysis_payload, ensure_ascii=False)
    result.overview = bidder_notice.get("overview", "") or result.overview or ""
    result.business_requirements = analysis_payload.get("business_requirements", "")
    result.qualification_requirements = qualification_review.get("qualification_check", "")
    result.technical_requirements = analysis_payload.get("technical_requirements", "")
    result.scoring_items = analysis_payload.get("scoring_items", "")
    result.disqualification_items = qualification_review.get("disqualification_items", "")


def _complete_analysis(task_id, execution_id=None):
    """完成招标文件分析并写入分析、核对和分包结果。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    shared_resource = BiddingSharedResource.query.filter_by(id=task.shared_resource_id).first()
    if not shared_resource:
        raise LookupError("共享资源不存在")
    file_record = FileStorage.query.filter_by(id=shared_resource.tender_file_id, deleted_flag=False).first()
    if not file_record:
        raise LookupError("招标文件不存在")

    delay_seconds = current_app.config.get("ANALYZE_SIMULATE_DELAY", 0)
    if delay_seconds:
        time.sleep(delay_seconds)
    if execution_id:
        _assert_execution_active(execution_id)
        _set_execution_progress(execution_id, 30)

    # ChromaDB 异步入库不阻塞分析流程。
    # 文本直接从本地缓存读取（上传阶段已同步解析并缓存），
    # ChromaDB 入库在后台继续完成，供生成阶段向量检索使用。

    source_texts = _build_shared_resource_analysis_text(shared_resource.id)
    text = source_texts["raw_text"] or _read_file_text(file_record)
    if execution_id:
        _assert_execution_active(execution_id)
        _set_execution_progress(execution_id, 70)
    result = BiddingAnalysisResult.query.filter_by(shared_resource_id=shared_resource.id).first()
    if not result:
        result = BiddingAnalysisResult(shared_resource_id=shared_resource.id)
        db.session.add(result)

    result.raw_text = text
    # effective_text 暂时为空，等 has_package 确定后再设置
    result.effective_text = ""
    _refresh_analysis_result(
        result,
        shared_resource.id,
        result.raw_text,
        source_files=source_texts.get("source_files", []),
    )

    # 从合并的分析结果中读取分包信息，不再独立调用 _detect_package_info
    merged_payload = {}
    try:
        import json as _json
        if result.analysis_data:
            merged_payload = _json.loads(result.analysis_data) if isinstance(result.analysis_data, str) else result.analysis_data
    except Exception:
        pass
    merged_payload = merged_payload if isinstance(merged_payload, dict) else {}
    has_package = merged_payload.get("has_package", False)
    # 确定 has_package 后，无分包时写入有效文本（用于后续核对项提取）
    if not has_package and source_texts.get("effective_text"):
        result.effective_text = source_texts["effective_text"]

    shared_resource.analysis_status = True
    shared_resource.has_package = has_package
    shared_resource.selected_package_no = None

    if has_package:
        task.status = "PACKAGE_PENDING"
        task.current_step = "package_select"
        task.progress = 15
    else:
        task.status = "ANALYZED"
        task.current_step = "check"
        task.progress = 20
    task.error_message = None

    db.session.commit()
    if execution_id:
        _set_execution_progress(execution_id, 100)
    return {
        "task_id": task.id,
        "status": task.status,
        "progress": task.progress,
        "current_step": task.current_step,
        "has_package": shared_resource.has_package,
        "selected_package_no": shared_resource.selected_package_no,
    }


def start_analyze(task_id):
    """启动任务分析阶段，支持后台执行。"""
    logger.info("[task] 启动分析 task=%s", task_id)
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    if task.status not in {"UPLOADED", "FAILED"} or task.current_step != "analyze":
        raise ValueError("当前任务状态不允许启动分析")

    task.status = "ANALYZING"
    task.current_step = "analyze"
    task.progress = 11
    task.error_message = None
    app = current_app._get_current_object()
    if current_app.config.get("ANALYZE_ASYNC", True):
        execution = _submit_background_execution(
            app,
            task.id,
            "ANALYZE",
            _complete_analysis,
            runner_kwargs={"task_id": task.id},
            request_payload={"task_id": task.id},
        )
        db.session.refresh(task)
        return {
            "task_id": task.id,
            "status": task.status,
            "progress": task.progress,
            "current_step": task.current_step,
            "background": True,
            "execution": execution.to_dict(),
        }
    log_operation(
        module="task",
        action="start_analyze",
        target_type="BiddingTask",
        target_id=task_id,
        task_id=task_id,
        summary=f'启动招标文件分析: {task.task_name}',
        detail={"task_name": task.task_name},
    )
    db.session.commit()
    return _complete_analysis(task.id)


def get_analysis_result(task_id):
    """获取任务分析阶段产出的结构化结果。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    result = BiddingAnalysisResult.query.filter_by(shared_resource_id=task.shared_resource_id).first()
    if not result:
        return {
            "task_id": task.id,
            "status": task.status,
            "ready": False,
            "message": "分析结果尚未生成",
        }
    return result.to_dict()


def get_packages(task_id):
    """获取任务识别到的分包结果。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    shared_resource = BiddingSharedResource.query.filter_by(id=task.shared_resource_id).first()
    if not shared_resource:
        raise LookupError("共享资源不存在")
    packages = []
    analysis_result = BiddingAnalysisResult.query.filter_by(shared_resource_id=task.shared_resource_id).first()
    detected_packages = _extract_package_numbers(analysis_result.raw_text if analysis_result else "")
    for pkg in detected_packages:
        package_no = pkg["package_no"]
        package_name = pkg.get("package_name") or f"第{package_no}包"
        packages.append(
            {
                "package_no": package_no,
                "package_name": package_name,
                "selected": str(shared_resource.selected_package_no or "") == str(package_no),
            }
        )
    return {
        "task_id": task.id,
        "has_package": shared_resource.has_package,
        "selected_package_no": shared_resource.selected_package_no,
        "packages": packages,
    }


def select_package(task_id, package_no):
    """保存选中的包号并刷新后续依据文本。"""
    logger.info("[task] 选择包号 task=%s package=%s", task_id, package_no)
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    if task.status != "PACKAGE_PENDING":
        raise ValueError("当前任务无需选择包号")
    shared_resource = BiddingSharedResource.query.filter_by(id=task.shared_resource_id).first()
    if not shared_resource or not shared_resource.has_package:
        raise LookupError("当前任务不存在分包信息")
    if not package_no:
        raise ValueError("包号不能为空")
    analysis_result = BiddingAnalysisResult.query.filter_by(shared_resource_id=task.shared_resource_id).first()
    available_packages = _extract_package_numbers(analysis_result.raw_text if analysis_result else "")
    available_nos = {str(item["package_no"]) for item in available_packages}
    if available_packages and str(package_no) not in available_nos:
        raise ValueError("所选包号不在识别结果中")

    shared_resource.selected_package_no = str(package_no)
    if analysis_result:
        source_texts = _build_shared_resource_analysis_text(task.shared_resource_id, package_no=package_no)
        analysis_result.raw_text = source_texts["raw_text"] or analysis_result.raw_text
        analysis_result.effective_text = source_texts["effective_text"] or _extract_effective_text(analysis_result.raw_text, package_no)
        _refresh_analysis_result(
            analysis_result,
            task.shared_resource_id,
            analysis_result.effective_text or analysis_result.raw_text,
        )
    task.status = "ANALYZED"
    task.progress = 20
    task.current_step = "check"
    log_operation(
        module="task",
        action="select_package",
        target_type="BiddingTask",
        target_id=task_id,
        task_id=task_id,
        summary=f'选择包号: {shared_resource.selected_package_no}',
        detail={"task_id": task_id, "package_no": shared_resource.selected_package_no},
    )
    db.session.commit()
    return {
        "task_id": task.id,
        "status": task.status,
        "progress": task.progress,
        "current_step": task.current_step,
        "selected_package_no": shared_resource.selected_package_no,
    }


def get_check_items(task_id):
    """获取待人工确认的核对项列表。"""
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    items = (
        BiddingCheckItem.query.filter_by(shared_resource_id=task.shared_resource_id)
        .order_by(BiddingCheckItem.sort_no.asc(), BiddingCheckItem.id.asc())
        .all()
    )
    return {"task_id": task.id, "items": [item.to_dict() for item in items]}


def confirm_check_items(task_id, items):
    """保存核对项确认结果并推进流程。"""
    logger.info("[task] 确认核对项 task=%s count=%s", task_id, len(items or []))
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")
    if task.status != "ANALYZED":
        raise ValueError("当前任务状态不允许提交核对结果")
    shared_resource = BiddingSharedResource.query.filter_by(id=task.shared_resource_id).first()
    if not shared_resource:
        raise LookupError("共享资源不存在")

    existing_items = {
        item.check_key: item
        for item in BiddingCheckItem.query.filter_by(shared_resource_id=task.shared_resource_id).all()
    }
    for payload in items or []:
        check_key = payload.get("check_key")
        if not check_key or check_key not in existing_items:
            continue
        record = existing_items[check_key]
        if "check_value" in payload:
            record.check_value = payload.get("check_value") or ""
        record.confirmed_flag = bool(payload.get("confirmed_flag", False))

    if existing_items and not all(item.confirmed_flag for item in existing_items.values()):
        raise ValueError("请先确认全部核对项")

    _sync_confirmed_items_to_analysis_result(task.shared_resource_id, existing_items)
    shared_resource.check_status = True
    task.status = "CHECKED"
    task.progress = 30
    task.current_step = "catalog"
    log_operation(
        module="task",
        action="confirm_check_items",
        target_type="BiddingTask",
        target_id=task_id,
        task_id=task_id,
        summary=f'提交核对项确认: 共{len(items)}项',
        detail={"task_id": task_id, "item_count": len(items)},
    )
    db.session.commit()
    return {
        "task_id": task.id,
        "status": task.status,
        "progress": task.progress,
        "current_step": task.current_step,
    }
