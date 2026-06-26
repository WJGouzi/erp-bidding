from .knowledge_bases import (
    delete_knowledge_base,
    delete_knowledge_base_file,
    get_knowledge_base_detail,
    list_knowledge_base_files,
    list_knowledge_bases,
    save_knowledge_base,
    update_knowledge_base_file_reference_status,
    upload_knowledge_base_file,
    upload_knowledge_base_files,
)
from .subjects import (
    delete_subject,
    delete_subject_material,
    get_subject_detail,
    list_subject_materials,
    list_subjects,
    save_subject,
    upload_subject_material,
)
from .templates import (
    delete_template_catalog,
    get_template_catalog_detail,
    list_template_catalogs,
    save_template_catalog,
)
from .common import (
    REQUIRED_SUBJECT_MATERIAL_TYPES,
    TENDER_ALLOWED_EXTENSIONS,
    get_subject_material_completeness,
)
from .pipeline import (
    _prepare_task_chapters,
    batch_delete_tasks,
    cancel_task_execution,
    confirm_catalog,
    confirm_check_items,
    create_derived_task,
    create_original_task,
    delete_task,
    delete_tender_attachment,
    extract_catalog_from_file,
    get_subject_templates,
    download_result_file,
    get_analysis_result,
    get_catalog_options,
    get_check_items,
    get_current_step,
    get_current_task_execution,
    get_generate_chapters,
    get_generate_config,
    get_generate_progress,
    get_packages,
    get_task_stats,
    get_task_detail,
    get_task_executions,
    get_task_runtime_snapshot,
    list_tender_attachments,
    list_tasks,
    recover_background_tasks,
    retry_generate,
    save_generate_config,
    select_package,
    start_analyze,
    start_generate,
    upload_tender_attachment,
)
from .storage import StorageService


class BiddingTaskService:
    """对外聚合任务、主体、知识库和模板相关服务能力。"""

    TENDER_ALLOWED_EXTENSIONS = TENDER_ALLOWED_EXTENSIONS
    REQUIRED_SUBJECT_MATERIAL_TYPES = REQUIRED_SUBJECT_MATERIAL_TYPES

    extract_catalog_from_file = staticmethod(extract_catalog_from_file)
    get_subject_templates = staticmethod(get_subject_templates)
    recover_background_tasks = staticmethod(recover_background_tasks)
    _prepare_task_chapters = staticmethod(_prepare_task_chapters)
    get_task_runtime_snapshot = staticmethod(get_task_runtime_snapshot)
    get_task_executions = staticmethod(get_task_executions)
    get_current_task_execution = staticmethod(get_current_task_execution)
    cancel_task_execution = staticmethod(cancel_task_execution)

    create_original_task = staticmethod(create_original_task)
    delete_task = staticmethod(delete_task)
    batch_delete_tasks = staticmethod(batch_delete_tasks)
    list_tender_attachments = staticmethod(list_tender_attachments)
    upload_tender_attachment = staticmethod(upload_tender_attachment)
    delete_tender_attachment = staticmethod(delete_tender_attachment)
    list_tasks = staticmethod(list_tasks)
    get_task_detail = staticmethod(get_task_detail)
    get_current_step = staticmethod(get_current_step)
    create_derived_task = staticmethod(create_derived_task)
    start_analyze = staticmethod(start_analyze)
    get_analysis_result = staticmethod(get_analysis_result)
    get_packages = staticmethod(get_packages)
    select_package = staticmethod(select_package)
    get_check_items = staticmethod(get_check_items)
    confirm_check_items = staticmethod(confirm_check_items)
    get_catalog_options = staticmethod(get_catalog_options)
    confirm_catalog = staticmethod(confirm_catalog)
    get_generate_config = staticmethod(get_generate_config)
    save_generate_config = staticmethod(save_generate_config)
    start_generate = staticmethod(start_generate)
    retry_generate = staticmethod(retry_generate)
    get_generate_progress = staticmethod(get_generate_progress)
    get_generate_chapters = staticmethod(get_generate_chapters)
    download_result_file = staticmethod(download_result_file)
    get_task_stats = staticmethod(get_task_stats)

    get_subject_material_completeness = staticmethod(get_subject_material_completeness)
    list_subjects = staticmethod(list_subjects)
    get_subject_detail = staticmethod(get_subject_detail)
    save_subject = staticmethod(save_subject)
    delete_subject = staticmethod(delete_subject)
    upload_subject_material = staticmethod(upload_subject_material)
    list_subject_materials = staticmethod(list_subject_materials)
    delete_subject_material = staticmethod(delete_subject_material)

    list_knowledge_bases = staticmethod(list_knowledge_bases)
    get_knowledge_base_detail = staticmethod(get_knowledge_base_detail)
    save_knowledge_base = staticmethod(save_knowledge_base)
    delete_knowledge_base = staticmethod(delete_knowledge_base)
    upload_knowledge_base_file = staticmethod(upload_knowledge_base_file)
    upload_knowledge_base_files = staticmethod(upload_knowledge_base_files)
    list_knowledge_base_files = staticmethod(list_knowledge_base_files)
    update_knowledge_base_file_reference_status = staticmethod(update_knowledge_base_file_reference_status)
    delete_knowledge_base_file = staticmethod(delete_knowledge_base_file)

    list_template_catalogs = staticmethod(list_template_catalogs)
    get_template_catalog_detail = staticmethod(get_template_catalog_detail)
    save_template_catalog = staticmethod(save_template_catalog)
    delete_template_catalog = staticmethod(delete_template_catalog)


__all__ = ["BiddingTaskService", "StorageService"]
