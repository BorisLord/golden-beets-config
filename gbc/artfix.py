"""Pre-import scrub-crash guard -- runs before every import.

A WMA/ASF file with an embedded mime_type=None image crashes beets' `scrub` plugin, and one such file aborts
the WHOLE `beet import`. We strip just the broken image (safe junk metadata; real art is re-fetched by
`fetchart`). Surgical: only WMA actually carrying a mime=None image are written -- this is the ONE source
write gbc makes even in copy/preserve mode. Best-effort: missing mediafile/mutagen just skips the guard.
"""
import importlib.util
import json
import os
from pathlib import Path

from .config import Config
from .logs import get_logger
from .util import write_json

CACHE = "gbc-artfix-cache.json"


def _broken_art(path) -> bool:
    """True if the file carries an embedded image whose mime_type is None (the scrub crasher)."""
    import mediafile
    try:
        return any(getattr(img, "mime_type", None) is None for img in (mediafile.MediaFile(path).images or []))
    except Exception:
        return False


def _strip_wma(path) -> bool:
    """Remove every embedded picture from a WMA/ASF file via mutagen."""
    from mutagen.asf import ASF
    try:
        a = ASF(path)
        for k in [k for k in a if "Picture" in k]:
            del a[k]
        a.save()
        return True
    except Exception:
        return False


def run(cfg: Config, src=None, log=None) -> int:
    """Strip mime=None embedded art from source WMA so scrub can't crash the import. Returns count stripped.
    Cached by path+mtime+size (BEETSDIR/gbc-artfix-cache.json) so repeat/cron runs only parse new/modified WMA."""
    log = log or get_logger("artfix")
    root = str(src) if src else str(cfg.src)
    if importlib.util.find_spec("mediafile") is None or importlib.util.find_spec("mutagen") is None:
        log.warning("mediafile/mutagen absent -> scrub-crash WMA guard skipped")
        return 0
    cpath = cfg.beetsdir / CACHE
    try:
        cache = set(json.loads(cpath.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        cache = set()

    fixed = failed = 0
    for dp, _, files in os.walk(root):
        for fn in files:
            if Path(fn).suffix.lower() != ".wma":
                continue
            p = str(Path(dp) / fn)
            try:
                st = Path(p).stat()
            except OSError:
                continue
            key = f"{int(st.st_mtime)}:{st.st_size}:{p}"
            if key in cache:                       # examined & unchanged -> skip the costly parse
                continue
            if _broken_art(p):                     # not cached: stripping changes the file, so its new key
                if _strip_wma(p):                  #   is re-examined (now clean) and cached next run
                    fixed += 1
                    log.info("artfix: stripped mime=None art -> %s", p)
                else:
                    failed += 1
            else:
                cache.add(key)                     # clean & unchanged -> remember, never re-parse
    write_json(cpath, sorted(cache))                       # atomic (tmp + replace): a crash can't corrupt the cache
    if fixed or failed:
        log.info("=== artfix: %d WMA broken-art stripped (%d unfixable) ===", fixed, failed)
    return fixed
