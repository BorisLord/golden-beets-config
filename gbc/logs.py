"""ONE log: `logs/gbc.log`, append-only, every line tagged `[pass]` + run id. No per-pass files
(separating identical logs was a bug). beets' own import-decisions.log stays separate.

    2026-06-17 08:12:03  run=2a9f  [import]  INFO  ...
"""
import contextvars
import logging
import sys
from pathlib import Path

_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="--------")
_configured = False


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id.get()
        if not hasattr(record, "passname"):
            record.passname = "main"
        return True


def configure(log_dir: Path, run_id: str, *, console: bool = True, level: int = logging.INFO) -> logging.Logger:
    """Configure once; safe to recall (updates run id only)."""
    global _configured
    _run_id.set(run_id)
    logger = logging.getLogger("gbc")
    if _configured:
        return logger
    logger.setLevel(level)
    logger.propagate = False
    logger.addFilter(_ContextFilter())
    fmt = logging.Formatter(
        "%(asctime)s  run=%(run_id)s  [%(passname)s]  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_dir / "gbc.log", mode="a", encoding="utf-8")  # ALWAYS append
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    if console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        logger.addHandler(ch)
    _configured = True
    return logger


def get_logger(passname: str) -> logging.LoggerAdapter:
    return logging.LoggerAdapter(logging.getLogger("gbc"), {"passname": passname})
