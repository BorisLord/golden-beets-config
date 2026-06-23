"""The pipeline: import -> albumdedup -> convert -> verify -> acousticbrainz -> qa -> reclaim. `run` + `inbox`
(cron) both call it; only the trigger differs. Ordering is load-bearing: albumdedup FIRST (needs only import
metadata) so later expensive passes skip albums that get quarantined; convert BEFORE verify so every later
pass runs identically on the converted (WMA->Opus, WAV/AIFF->FLAC) files.
"""
from datetime import datetime

from .. import state
from ..config import Config
from ..logs import get_logger
from . import acousticbrainz, albumdedup, convert, import_, qa, reclaim, verify


def run(cfg: Config, *, full: bool = False, src=None, reimport: bool = False) -> int:
    log = get_logger("pipeline")
    wm_old = None if full else state.get_watermark(cfg)
    scope = state.added_query(wm_old)        # qa scope: items added since last run ("" = whole library)
    log.info("pipeline start (%s)%s", "full" if full else "incremental", f" scope={scope}" if scope else "")

    rc = import_.run(cfg, src=src, reimport=reimport)
    if rc:
        # fail-fast: watermark NOT advanced so the next run retries this run's items
        log.error("pipeline ABORTED: import failed (rc=%d) -- watermark NOT advanced, will retry next run", rc)
        return rc
    # every post-import pass is best-effort: a hiccup must never break the import or block the watermark advance
    try:
        albumdedup.run(cfg)
    except Exception:
        log.exception("album dedup pass errored (non-fatal)")
    try:
        rc_conv = convert.run(cfg)
        if rc_conv:
            log.warning("convert returned rc=%d -- some originals were NOT converted (left intact in clean)", rc_conv)
    except Exception:
        log.exception("convert pass errored (non-fatal)")
    wm_new = datetime.now().replace(microsecond=0).isoformat()   # set after import: this run's items are < wm_new

    try:
        verify.run(cfg, scope=scope)
    except Exception:
        log.exception("verify pass errored (non-fatal)")
    try:
        acousticbrainz.run(cfg, scope=scope)
    except Exception:
        log.exception("acousticbrainz pass errored (non-fatal)")
    try:
        qa.run(cfg, scope=scope, cull=True)
    except Exception:
        log.exception("qa pass errored (non-fatal)")
    try:
        reclaim.run(cfg)
    except Exception:
        log.exception("reclaim pass errored (non-fatal)")
    state.set_watermark(cfg, wm_new)
    log.info("pipeline done; watermark -> %s", wm_new)
    return 0
