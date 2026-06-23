"""Shared import lock (filelock): `inbox` (cron) takes it non-blocking and bows out if busy; `run` waits."""
from contextlib import contextmanager

from filelock import FileLock, Timeout

from .config import Config


@contextmanager
def import_lock(cfg: Config, *, blocking: bool = True):
    """Yields True if acquired (released on exit), False if busy (non-blocking only)."""
    cfg.beetsdir.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(cfg.beetsdir / ".import.lock"))
    try:
        lock.acquire(timeout=-1 if blocking else 0)
    except Timeout:
        yield False
        return
    try:
        yield True
    finally:
        lock.release()
