"""Pass 1 -- album match import (AcoustID + tags): source -> clean album lib.

Branches on the EFFECTIVE beets import op (via `beetscfg`):
  - source CONSUMED (move / copy+delete): dedup source, carry official sidecars into matched albums, sweep
    the emptied shells.
  - source PRESERVED (copy / reflink / hardlink / symlink / in-place): source is READ-ONLY -- dedup/sidecars/
    prune all move source files, so they're skipped; verified originals are moved later by the reclaim pass.
"""
import tempfile
from pathlib import Path

from .. import artfix, beetscfg, sidecars
from ..beets import run_beet
from ..config import Config
from ..dedup import dedup
from ..logs import get_logger
from ..util import backup_db, count_items, prune_empty_dirs


def _beet_import(cfg: Config, src: Path, reimport: bool, log) -> int:
    inc = "-I" if reimport else "-i"      # -I = noincremental: re-evaluate already-seen (modified) folders
    rc, _ = run_beet(cfg, ["import", "-q", inc, str(src)], passname="import")
    if rc:
        log.error("beet import failed (rc=%d)", rc)
    return rc


def run(cfg: Config, src=None, reimport=False) -> int:
    log = get_logger("import")
    src = Path(src) if src else cfg.src
    if not src.is_dir():
        log.error("source missing: %s", src)
        return 1
    artfix.run(cfg, src=src, log=log)          # strip mime=None WMA art so scrub can't crash beet import
    bi = beetscfg.read_import(cfg)
    backup_db(cfg, "rebuild", log)

    if bi.source_consumed:
        dedup(str(src), str(cfg.dump), True, log)                   # best bitrate kept
        snap = tempfile.NamedTemporaryFile(prefix="sidecars-", suffix=".json", delete=False).name  # noqa: SIM115
        try:
            sidecars.snapshot(str(src), snap, log)                  # snapshot BEFORE import, while source has its audio
            rc = _beet_import(cfg, src, reimport, log)
            sidecars.apply(snap, str(cfg.library), str(cfg.clean), str(cfg.dump), True, log)
            sidecars.prune_shells(str(src), str(cfg.dump), True, log)
            prune_empty_dirs(src)
        finally:
            Path(snap).unlink(missing_ok=True)
    else:
        log.info("source preserved (beets import=%s) -> dedup/sidecars/prune skipped; source untouched", bi.label)
        rc = _beet_import(cfg, src, reimport, log)

    art_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    covers = sum(1 for p in cfg.clean.rglob("cover.*") if p.suffix.lower() in art_exts) if cfg.clean.exists() else 0
    log.info("items: %d | albums: %d | covers: %d",
             count_items(cfg, ["ls"], "import"), count_items(cfg, ["ls", "-a"], "import"), covers)
    return rc
