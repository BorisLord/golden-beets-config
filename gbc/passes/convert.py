"""Pass -- normalise non-standard formats in the CLEAN library (pipeline + standalone `gbc convert`):
WMA -> Opus (only lossy re-encode; WMA is proprietary/broken), WAV/AIFF/ALAC -> FLAC (bit-perfect). Each
original is MOVED to quarantine (keep_new) -- NEVER deleted. Only files already in the clean lib are touched.
"""
from pathlib import Path

from ..beets import run_beet
from ..config import Config
from ..logs import get_logger
from ..util import backup_db, count_items

# (label, target desc, beet -f format, query, quarantine subdir). WMA stored as "Windows Media"
# (-> format::Windows); WAV/AIFF matched by path (avoids format-name surprises); ALAC by format (distinct
# from AAC, which shares .m4a). Quarantine sub "converted" = the reason; beets lays originals out by album.
JOBS = [
    ("WMA", "Opus (open, adaptive bitrate)", "opus", ["format::Windows"], "converted"),
    ("WAV/AIFF", "FLAC (lossless)", "flac", [r"path::(?i)\.(wav|aiff?)$"], "converted"),
    ("ALAC", "FLAC (lossless, universal)", "flac", ["format:ALAC"], "converted"),
]


def _reap_stale(cfg: Config, query: list, log) -> tuple[int, int]:
    """An item still matching the SOURCE-format query whose file VANISHED = failed encode: keep_new moved the
    original to quarantine, then the encode errored (beet convert still exits 0), leaving a row at a gone path.
    Drop those rows; original is safe in quarantine. Returns (reaped, still_present)."""
    _, text = run_beet(cfg, ["ls", "-f", "$id\t$path", *query], passname="convert", echo_lines=False)
    reaped = present = 0
    for line in text.splitlines():
        if "\t" not in line:
            continue
        itemid, path = line.split("\t", 1)
        path = path.strip().encode("utf-8", "surrogateescape").decode("utf-8", "surrogateescape")
        if path and not Path(path).exists():
            rc, _ = run_beet(cfg, ["remove", "-f", f"id:{itemid}"], passname="convert", echo_lines=False)
            if rc:
                log.warning("convert: `beet remove` rc=%d for stale id:%s", rc, itemid)
            reaped += 1
        else:
            present += 1
    return reaped, present


def run(cfg: Config) -> int:
    log = get_logger("convert")
    pending = [(lbl, tgt, fmt, q, sub, n)
               for (lbl, tgt, fmt, q, sub) in JOBS
               if (n := count_items(cfg, ["ls", *q], "convert"))]
    if not pending:
        log.info("no WMA/WAV/AIFF/ALAC in the library -> nothing to convert")
        return 0
    backup_db(cfg, "convert", log)
    for lbl, tgt, fmt, q, sub, n in pending:
        dest = cfg.dump / sub
        dest.mkdir(parents=True, exist_ok=True)
        log.info("converting %d %s -> %s; originals -> %s", n, lbl, tgt, dest)
        rc, _ = run_beet(cfg, ["convert", "-y", "-k", "-f", fmt, "-d", str(dest), *q],
                         overlay="convert.yaml", passname="convert")
        if rc:
            log.error("beet convert (%s) failed (rc=%d) -- originals untouched", lbl, rc)
            return rc
        reaped, present = _reap_stale(cfg, q, log)
        log.info("done: %d %s converted, %d failed (stale row reaped, original safe in quarantine), %d still "
                 "present; originals in %s", n - reaped - present, lbl, reaped, present, dest)
    return 0
