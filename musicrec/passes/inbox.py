"""The cron door. Same pipeline as `run`, but triggered by a drop: take the import lock (bow out if busy),
skip if nothing NEW to import (parse beet's --pretend plan -- which it writes to STDERR, hence the old
gate bug), debounce until the drop finished copying, then run the pipeline.
"""
import re
import time
from pathlib import Path

from ..beets import run_beet
from ..lock import import_lock
from ..logs import get_logger
from . import pipeline


def _dir_size(path) -> int:
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())


def has_new(plan: str) -> bool:
    """True if beet's --pretend plan lists something to import. beets writes the plan to STDERR, so the
    text must be captured from stderr (the old bash gate read stdout -> always empty -> never imported)."""
    return bool(re.search(r"(?m)^(Album|Singleton):", plan))


def _debounce(cfg, interval: int = 20) -> None:
    prev = -1
    while True:
        cur = _dir_size(cfg.src)
        if cur == prev:
            return
        prev = cur
        time.sleep(interval)


def run(cfg) -> int:
    log = get_logger("inbox")
    with import_lock(cfg, blocking=False) as got:
        if not got:
            log.info("another import in progress -> exit")
            return 0
        if not cfg.src.exists() or not any(cfg.src.iterdir()):
            log.info("source empty -> exit")
            return 0
        _, plan = run_beet(cfg, ["import", "-q", "-i", "--pretend", str(cfg.src)],
                           passname="inbox", echo_lines=False)
        if not has_new(plan):
            log.info("nothing new to import -> exit")
            return 0
        _debounce(cfg)
        return pipeline.run(cfg)
