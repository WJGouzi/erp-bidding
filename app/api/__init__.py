from .knowledge_bases import kb_ns
from .subjects import subject_ns
from .system import health_ns, system_ns
from .tasks import task_ns
from .templates import template_ns


def register_namespaces(api):
    """向 Flask-RESTX 实例注册系统、任务、主体、知识库与模板命名空间。"""

    api.add_namespace(health_ns, path="/health")
    api.add_namespace(system_ns, path="/system")
    api.add_namespace(task_ns, path="/bidding/tasks")
    api.add_namespace(subject_ns, path="/bidding/subjects")
    api.add_namespace(kb_ns, path="/bidding/knowledge-bases")
    api.add_namespace(template_ns, path="/bidding/template-catalogs")


__all__ = ["register_namespaces"]
