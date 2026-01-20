"""模块名称：并发锁管理器

模块目的：为同键资源提供一致的互斥访问机制。
主要功能：
- 进程内按键串行化访问
- 多进程/多 worker 之间的文件级互斥
使用场景：共享资源写入、缓存更新、任务去重等。
关键组件：`KeyedMemoryLockManager`、`KeyedWorkerLockManager`
设计背景：不同运行模式下需要统一的“按键锁”抽象。
注意事项：`KeyedWorkerLockManager` 仅允许字母数字与下划线键名。
"""

import re
import threading
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock
from platformdirs import user_cache_dir


class KeyedMemoryLockManager:
    """基于键的内存锁管理器（进程内）。"""

    def __init__(self) -> None:
        self.locks: dict[str, threading.Lock] = {}
        self.global_lock = threading.Lock()

    def _get_lock(self, key: str):
        """按键获取锁对象并延迟初始化。"""
        with self.global_lock:
            if key not in self.locks:
                self.locks[key] = threading.Lock()
            return self.locks[key]

    @contextmanager
    def lock(self, key: str):
        """上下文形式获取并释放指定键的锁。"""
        lock = self._get_lock(key)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()


class KeyedWorkerLockManager:
    """跨 worker 的文件锁管理器。"""

    def __init__(self) -> None:
        # 注意：缓存目录用于跨进程共享锁文件。
        self.locks_dir = Path(user_cache_dir("langflow"), ensure_exists=True) / "worker_locks"

    @staticmethod
    def _validate_key(key: str) -> bool:
        """校验键名仅包含字母数字与下划线。

        Parameters:
        s (str): The string to validate.

        返回：
            bool：合法则为 True，否则 False。
        """
        pattern = re.compile(r"^\w+$")
        return bool(pattern.match(key))

    @contextmanager
    def lock(self, key: str):
        """获取跨进程锁；非法键直接抛 `ValueError`。"""
        if not self._validate_key(key):
            msg = f"Invalid key: {key}"
            raise ValueError(msg)

        lock = FileLock(self.locks_dir / key)
        with lock:
            yield
