import contextlib
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class FileLockRegistry:
    """Manages per-file threading locks so parallel agents queue behind each other."""

    def __init__(self) -> None:
        self._registry: dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()

    def _get_lock(self, path: str) -> threading.Lock:
        with self._meta_lock:
            if path not in self._registry:
                self._registry[path] = threading.Lock()
            return self._registry[path]

    def acquire(self, path: str | Path) -> None:
        self._get_lock(str(path)).acquire()

    def release(self, path: str | Path) -> None:
        try:
            self._get_lock(str(path)).release()
        except RuntimeError:
            logger.warning("FileLockRegistry: attempted to release an already-unlocked lock for %s", path)

    @contextlib.contextmanager
    def lock(self, path: str | Path):
        self.acquire(path)
        try:
            yield
        finally:
            self.release(path)


_registry = FileLockRegistry()


def get_file_lock_registry() -> FileLockRegistry:
    return _registry