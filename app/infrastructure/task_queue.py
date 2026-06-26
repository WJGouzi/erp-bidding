from concurrent.futures import ThreadPoolExecutor
from threading import Lock


class TaskQueueManager:
    """统一管理后台线程池及当前挂起任务。"""

    _executor = None
    _max_workers = None
    _lock = Lock()
    _pending_futures = {}

    @classmethod
    def get_executor(cls, max_workers):
        """按配置返回线程池实例，并保证最少 10 个并发工作线程。"""

        normalized_workers = max(10, int(max_workers or 10))
        with cls._lock:
            if cls._executor is None or cls._max_workers != normalized_workers:
                cls._executor = ThreadPoolExecutor(
                    max_workers=normalized_workers,
                    thread_name_prefix="erp-bidding",
                )
                cls._max_workers = normalized_workers
            return cls._executor

    @classmethod
    def submit(cls, max_workers, task_key, fn, *args, **kwargs):
        """提交后台任务并记录 Future，便于后续统计运行态。"""

        executor = cls.get_executor(max_workers)
        future = executor.submit(fn, *args, **kwargs)
        with cls._lock:
            cls._pending_futures[f"{task_key}:{id(future)}"] = future
        future.add_done_callback(lambda done_future: cls._cleanup_future(task_key, done_future))
        return future

    @classmethod
    def _cleanup_future(cls, task_key, future):
        """在线程任务完成后清理挂起记录。"""

        with cls._lock:
            cls._pending_futures.pop(f"{task_key}:{id(future)}", None)

    @classmethod
    def get_runtime_snapshot(cls, configured_max_workers):
        """返回线程池配置与当前活跃任务数量。"""

        cls.get_executor(configured_max_workers)
        with cls._lock:
            active_count = sum(1 for item in cls._pending_futures.values() if not item.done())
        return {
            "max_workers": cls._max_workers,
            "active_count": active_count,
        }
