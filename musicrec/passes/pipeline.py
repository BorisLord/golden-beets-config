"""The pipeline: import -> qa. `run` (manual) and `inbox` (cron) both call this; only the trigger differs.

beets does the heavy lifting natively DURING `beet import` (auto: yes): match, scrub, fetchart, embedart,
lastgenre, ftintitle, replaygain. The import pass adds dedup (before) + sidecars (after); qa is a
read-only audit. Fail-fast: if import fails, the watermark is NOT advanced (next run retries). The
watermark scopes qa to items added since the last successful run (whole library on first run / --all).
"""
from datetime import datetime

from .. import state
from ..logs import get_logger
from . import import_, qa


def run(cfg, *, full: bool = False, src=None) -> int:
    log = get_logger("pipeline")
    wm_old = None if full else state.get_watermark(cfg)
    scope = state.added_query(wm_old)        # qa scope: items added since last run ("" = whole library)
    log.info("pipeline start (%s)%s", "full" if full else "incremental", f" scope={scope}" if scope else "")

    rc = import_.run(cfg, src=src)           # match + scrub + art + genres + ftintitle + replaygain (beets auto)
    if rc:
        log.error("pipeline ABORTED: import failed (rc=%d) -- watermark NOT advanced, will retry next run", rc)
        return rc
    wm_new = datetime.now().replace(microsecond=0).isoformat()   # after import: this run's items are < wm_new

    qa.run(cfg, scope=scope)                 # read-only audit (informational; never gates the watermark)
    state.set_watermark(cfg, wm_new)
    log.info("pipeline done; watermark -> %s", wm_new)
    return 0
