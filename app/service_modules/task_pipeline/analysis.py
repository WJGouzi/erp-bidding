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
def _get_packages_from_analysis_data(analysis_result):
    """从 v3 analysis_data 中提取包号列表。无 LLM 调用。"""
    if not analysis_result or not analysis_result.analysis_data:
        return []
    try:
        import json as _j
        data = _j.loads(analysis_result.analysis_data) if isinstance(analysis_result.analysis_data, str) else analysis_result.analysis_data
        if not isinstance(data, dict):
            return []
        packages = data.get("packages") or []
        if not isinstance(packages, list):
            return []
        return [{"package_no": str(p["package_no"]), "package_name": p.get("name", "")} for p in packages if p.get("package_no")]
    except Exception:
        return []


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



def _save_v3_check_items(shared_resource_id, check_items):
    """保存 v3 管线产生的核对项。"""
    from .analysis_v3.check_items import generate_check_items
    from ...domain import BiddingCheckItem
    
    # 删除旧的核对项
    BiddingCheckItem.query.filter_by(shared_resource_id=shared_resource_id).delete()
    db.session.flush()
    
    for i, item in enumerate(check_items):
        record = BiddingCheckItem(
            shared_resource_id=shared_resource_id,
            check_key=item.get("check_key", f"v3_item_{i}"),
            check_label=item.get("content") or item.get("check_label", "核对项"),
            check_value=item.get("prep_guide") or item.get("check_value", ""),
            confirmed_flag=False,
            sort_no=i + 1,
        )
        db.session.add(record)

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

    # v3 管线唯一路径（三层分析，零LLM）
    try:
        from .analysis_v3 import start_analyze_v3 as _start_v3_llm_free
        v3_result = _start_v3_llm_free(task, source_texts)
        if v3_result and v3_result.get("analysis_data"):
            v3_data = v3_result["analysis_data"]
            result.analysis_data = json.dumps(v3_data, ensure_ascii=False)
            # 回填旧版本兼容字段（供前端旧接口使用）
            meta = v3_data.get("metadata", {})
            project_name = meta.get("project_name", "")
            project_code = meta.get("project_code", "")
            pur_raw = meta.get("purchaser", "")
            if isinstance(pur_raw, dict):
                purchaser_name = pur_raw.get("name", "")
            elif isinstance(pur_raw, str):
                purchaser_name = pur_raw
            else:
                purchaser_name = ""
            agt_raw = meta.get("agent", "")
            if isinstance(agt_raw, dict):
                agent_name = agt_raw.get("name", "")
            elif isinstance(agt_raw, str):
                agent_name = agt_raw
            else:
                agent_name = ""
            budget_raw = meta.get("budget", {})
            if isinstance(budget_raw, (int, float)):
                budget_total = budget_raw
            elif isinstance(budget_raw, dict):
                budget_total = budget_raw.get("total", 0)
            else:
                budget_total = 0
            pkg_count = meta.get("package_count", 0)
            deadline = meta.get("key_dates", {}).get("bid_deadline", "")
            
            result.overview = f"项目: {project_name} (编号: {project_code})"
            if budget_total:
                result.overview += f" | 预算: {budget_total/10000:.0f}万元"
            if pkg_count:
                result.overview += f" | 共{pkg_count}包"
            if deadline:
                result.overview += f" | 截止: {deadline}"
            
            elig = v3_data.get("eligibility", {})
            all_qual_items = elig.get("qualifications", [])[:10]
            if all_qual_items:
                result.qualification_requirements = json.dumps(all_qual_items, ensure_ascii=False)
            all_disq_items = elig.get("disqualifications", [])[:5]
            if all_disq_items:
                result.disqualification_items = json.dumps(all_disq_items, ensure_ascii=False)
            
            scoring = v3_data.get("scoring", {})
            dims = scoring.get("dimensions", [])
            if dims:
                result.scoring_items = json.dumps(dims, ensure_ascii=False)
            if dims:
                dim_summary = " | ".join(f"{d['name']}: {d['score']}分" for d in dims[:5])
                result.requirements = dim_summary
            
            # 回填商务要求（从 metadata.extra 提取）
            biz_parts = []
            meta = v3_data.get("metadata", {})
            extra = meta.get("extra", {})
            if extra.get("payment_terms"):
                biz_parts.append(f"付款方式：{extra['payment_terms']}")
            if extra.get("service_period"):
                unit = "年" if isinstance(extra['service_period'], int) and extra['service_period'] < 10 else ""
                biz_parts.append(f"服务期限：{extra['service_period']}{unit}")
            if extra.get("delivery_location"):
                biz_parts.append(f"交付地点：{extra['delivery_location']}")
            if extra.get("acceptance_standard"):
                biz_parts.append(f"验收标准：{extra['acceptance_standard']}")
            if extra.get("pricing_rule"):
                biz_parts.append(f"报价方式：{extra['pricing_rule']}")
            if extra.get("special_declaration"):
                biz_parts.append(f"特别说明：{extra['special_declaration']}")
            if extra.get("agency_fee"):
                biz_parts.append(f"代理服务费：{extra['agency_fee']}元")
            result.business_requirements = "\n".join(biz_parts) if biz_parts else "暂未提取到商务要求。"

            # 回填技术要求（从包参数 + ★条款提取）
            tech_parts = []
            pkgs = v3_data.get("packages", [])
            for p in pkgs:
                pname = p.get("name", f"第{p.get('package_no')}包")
                params = p.get("parameters") or {}
                counts = []
                if params.get("starred_count"):
                    counts.append(f"★{params['starred_count']}项")
                if params.get("important_count"):
                    counts.append(f"▲{params['important_count']}项")
                if params.get("general_count"):
                    counts.append(f"一般{params['general_count']}项")
                if counts:
                    tech_parts.append(f"{pname}：技术参数 {'/'.join(counts)}")
                if params.get("core_products"):
                    tech_parts.append(f"{pname}核心产品：{'、'.join(params['core_products'][:5])}")
            result.technical_requirements = "\n".join(tech_parts) if tech_parts else "暂未提取到技术要求。"
            
            # 从表格分类结果补充技术/商务要求（政府采购一体化平台格式）
            tc = v3_data.get("table_classification")
            if tc:
                # 技术要求表
                if result.technical_requirements == "暂未提取到技术要求。":
                    tech_table_parts = []
                    for tr in tc.get("tech_requirements", []):
                        for item in tr.get("items", []):
                            name = item.get("技术要求名称", "")
                            params = item.get("技术参数与性能指标", "")
                            if name and params:
                                tech_table_parts.append(f"  {name}: {params[:100]}")
                    if tech_table_parts:
                        result.technical_requirements = "技术参数要求:\n" + "\n".join(tech_table_parts[:20])
                
                # 商务要求表（含交货时间、交货地点、付款方式等）
                biz_table_parts = []
                for br in tc.get("business_requirements", []):
                    for item in br.get("items", []):
                        name = item.get("商务要求名称", "")
                        content_val = item.get("商务要求内容", "")
                        if name and content_val:
                            biz_table_parts.append(f"  {name}: {content_val[:100]}")
                if biz_table_parts:
                    biz_extra = "\n商务要求（表格）:\n" + "\n".join(biz_table_parts[:15])
                    if result.business_requirements and result.business_requirements != "暂未提取到商务要求。":
                        result.business_requirements += "\n" + biz_extra
                    else:
                        result.business_requirements = biz_extra
                
                # 服务要求表
                srv_table_parts = []
                for sr in tc.get("service_requirements", []):
                    for item in sr.get("items", []):
                        name = item.get("服务要求名称", "")
                        content_val = item.get("服务要求内容", "")
                        if name and content_val:
                            srv_table_parts.append(f"  {name}: {content_val[:100]}")
                if srv_table_parts:
                    srv_extra = "\n服务要求（表格）:\n" + "\n".join(srv_table_parts[:15])
                    if result.business_requirements and result.business_requirements != "暂未提取到商务要求。":
                        result.business_requirements += "\n" + srv_extra
                    else:
                        result.business_requirements = srv_extra

            # 更新 requirements（商务+评分摘要）
            req_parts = []
            if biz_parts:
                req_parts.append(biz_parts[0][:80])
            if dims:
                req_parts.append(f"评分共{len(dims)}项，重点：{dims[0]['name']}({dims[0]['score']}分)")
            result.requirements = " | ".join(req_parts) if req_parts else dim_summary

            # 保存分包列表到独立列
            if pkgs:
                result.packages_json = json.dumps(pkgs, ensure_ascii=False)
                result.package_count = len(pkgs)
            
            # 保存文档分类到独立列
            doc_type = meta.get("document_type", {})
            if isinstance(doc_type, dict):
                result.document_type = doc_type.get("value", "")
            elif isinstance(doc_type, str):
                result.document_type = doc_type
            
            # 生成核对项
            if v3_result.get("check_items"):
                _save_v3_check_items(shared_resource.id, v3_result["check_items"])
            
            # 更新有效文本
            if v3_result.get("effective_text"):
                result.effective_text = v3_result["effective_text"]
            
            logger.info("[analysis] v3 分析完成 task=%s", task_id)
        else:
            logger.error("[analysis] v3 分析返回空结果 task=%s", task_id)
            raise RuntimeError("v3 分析返回空结果")
    except Exception as v3_exc:
        logger.error("[analysis] v3 分析失败 task=%s: %s", task_id, v3_exc)
        raise  # v3-only 路径，不再降级
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
    task.selected_package_no = None
    task.selected_package_name = None

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
    # 优先从 v3 analysis_data 读取包号
    detected_packages = _get_packages_from_analysis_data(analysis_result)
    if not detected_packages:
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
    # 优先从 v3 analysis_data 读取包号（无需 LLM 调用）
    available_packages = _get_packages_from_analysis_data(analysis_result)
    if not available_packages:
        available_packages = _extract_package_numbers(analysis_result.raw_text if analysis_result else "")
    available_nos = {str(item["package_no"]) for item in available_packages}
    if available_packages and str(package_no) not in available_nos:
        raise ValueError("所选包号不在识别结果中")

    shared_resource.selected_package_no = str(package_no)
    task.selected_package_no = str(package_no)
    # 从 packages_json 查出包名
    pkg_name = f"第{package_no}包"
    if analysis_result and analysis_result.packages_json:
        try:
            pkgs = json.loads(analysis_result.packages_json) if isinstance(analysis_result.packages_json, str) else analysis_result.packages_json
            for p in pkgs:
                if str(p.get("package_no", "")) == str(package_no):
                    pkg_name = p.get("name", "") or pkg_name
                    break
        except Exception:
            pass
    task.selected_package_name = pkg_name
    if analysis_result:
        source_texts = _build_shared_resource_analysis_text(task.shared_resource_id, package_no=package_no)
        analysis_result.raw_text = source_texts["raw_text"] or analysis_result.raw_text
        analysis_result.effective_text = source_texts["effective_text"] or _extract_effective_text(analysis_result.raw_text, package_no)
        # v3 已在首次分析中处理所有包，无需重新分析
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
    """获取待人工确认的核对项列表（门面模式）。

    优先从 check_items 子模块组装复合结构，
    降级到传统的 BiddingCheckItem 扁平列表。
    """
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")

    # 新路径：从 v3 check_items 子模块组装
    try:
        from .analysis_v3.check_items import assemble_check_items
        result = assemble_check_items(task.shared_resource_id)
        if result and result.get("bidding_info") is not None:
            logger.info("[check_items] 使用模块化组装 shared_resource_id=%s", task.shared_resource_id)
            return result
    except Exception as exc:
        logger.warning("[check_items] 模块化组装失败，降级: %s", exc)

    # 降级：传统的扁平列表
    items = (
        BiddingCheckItem.query.filter_by(shared_resource_id=task.shared_resource_id)
        .order_by(BiddingCheckItem.sort_no.asc(), BiddingCheckItem.id.asc())
        .all()
    )
    return {"task_id": task.id, "items": [item.to_dict() for item in items]}


def save_review(task_id, data):
    """保存核对后的审核面板数据。

    前端将 GET /check-items 返回的 data 字段整体传入，
    将 6 个 section 写回 bidding_analysis_result.analysis_data。

    可反复调用，第一次调用将任务从 ANALYZED 推进到 CHECKED，
    后续调用（CHECKED 状态）只更新数据不改变状态。
    """
    logger.info("[task] 保存审核面板 task=%s", task_id)
    task = BiddingTask.query.filter_by(id=task_id, deleted_flag=False).first()
    if not task:
        raise LookupError("标书任务不存在")

    shared_resource = BiddingSharedResource.query.filter_by(id=task.shared_resource_id).first()
    if not shared_resource:
        raise LookupError("共享资源不存在")

    result = BiddingAnalysisResult.query.filter_by(
        shared_resource_id=task.shared_resource_id
    ).first()
    if not result:
        raise LookupError("分析结果不存在")

    # 读取现有 analysis_data 并更新
    try:
        analysis = json.loads(result.analysis_data) if result.analysis_data else {}
    except (TypeError, json.JSONDecodeError):
        analysis = {}
    if not isinstance(analysis, dict):
        analysis = {}

    # 将 data 中的 6 个 section 合并到 analysis_data
    section_fields = ["bidding_info", "business", "technical", "qualification", "scoring", "packages"]
    for field in section_fields:
        if field in data and data[field] is not None:
            analysis[field] = data[field]

    result.analysis_data = json.dumps(analysis, ensure_ascii=False)

    # 状态推进：仅第一次（ANALYZED → CHECKED）
    if task.status == "ANALYZED":
        task.status = "CHECKED"
        task.progress = 30
        task.current_step = "catalog"
        shared_resource.check_status = True

    log_operation(
        module="task",
        action="save_review",
        target_type="BiddingTask",
        target_id=task_id,
        task_id=task_id,
        summary="保存审核面板数据",
        detail={"task_id": task_id, "sections": [f for f in section_fields if f in data]},
    )
    db.session.commit()
    return {
        "task_id": task.id,
        "status": task.status,
        "progress": task.progress,
        "current_step": task.current_step,
    }


