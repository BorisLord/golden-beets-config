"""Pass -- per-track AcoustID fingerprint verification: detect & quarantine IMPOSTER tracks.

An imposter is a file with the right title/duration/tags but whose AUDIO is not the matched recording.
Album-mode import trusts the lot, and `chroma` gives NO penalty to a track it cannot identify at all -> such
a file slips into an otherwise "strong" album (a real blind spot). We re-fingerprint each accepted track and
act ONLY when BOTH hold: its own fingerprint matches don't include the tagged recording (AcoustID status=ok)
AND the official recording (mb_trackid) is itself known to AcoustID. Any rate-limit/timeout ->
"inconclusive" -> left alone (we never act). A conclusive imposter is MOVED to $MUSIC_DUMP (never deleted)
and dropped from the lib, so the clean library stays clean. Verdicts are cached per file (checked once).
"""
import importlib.util
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..beets import run_beet
from ..logs import get_logger
from ..sidecars import safe_move
from ..util import backup_db

APIKEY = os.environ.get("GBC_ACOUSTID_APIKEY", "1vOwZtEn")  # beets' shared key; set your own to avoid throttling
MATCH_SCORE = 0.5   # AcoustID result score above which the file is a genuine match
RETRIES = 4         # attempts on rate-limit / network error before giving up -> inconclusive
SEP = "@@@"


def _acoustid_available() -> bool:
    return importlib.util.find_spec("acoustid") is not None


def _file_verdict(path, mbid):
    """('ok', present) once AcoustID answers conclusively, else ('error', False). present=True when the file's
    OWN fingerprint matches list the tagged recording (mbid) -> genuine. False => the audio is something else
    (unknown to AcoustID, or a different known song -> both are imposters if the tagged recording is known)."""
    import acoustid
    for attempt in range(RETRIES):
        try:
            dur, fp = acoustid.fingerprint_file(path)
            resp = acoustid.lookup(APIKEY, fp, dur, meta="recordingids")
        except acoustid.FingerprintGenerationError:
            return "error", False                       # can't fingerprint -> inconclusive (never act)
        except acoustid.WebServiceError:
            time.sleep(2 ** attempt)
            continue
        if resp.get("status") != "ok":
            time.sleep(2 ** attempt)
            continue
        present = any(rec.get("id") == mbid
                      for r in (resp.get("results") or []) if (r.get("score") or 0) >= MATCH_SCORE
                      for rec in (r.get("recordings") or []))
        return "ok", present
    return "error", False


def _official_known(mbid):
    """True if the MusicBrainz recording is registered in AcoustID (>=1 fingerprint); None if inconclusive."""
    url = f"https://api.acoustid.org/v2/track/list_by_mbid?format=json&client={APIKEY}&mbid={mbid}"
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                data = json.loads(r.read())
        except (urllib.error.URLError, ValueError, TimeoutError, OSError):
            time.sleep(2 ** attempt)
            continue
        if data.get("status") != "ok":
            time.sleep(2 ** attempt)
            continue
        return len(data.get("tracks") or []) >= 1
    return None


def run(cfg, scope="") -> int:
    """Flag imposter tracks among items added in `scope` (whole library if empty). Returns the imposter count."""
    log = get_logger("verify")
    if not _acoustid_available():
        log.warning("pyacoustid not available -> fingerprint verification skipped")
        return 0
    sc = [scope] if scope else []
    _, text = run_beet(cfg, ["ls", "-f", f"$path{SEP}$mb_trackid", "mb_trackid::.", *sc],
                       passname="verify", echo_lines=False)
    rows = [ln.split(SEP, 1) for ln in text.splitlines() if SEP in ln]

    cpath = cfg.beetsdir / "gbc-verify-cache.json"
    try:
        cache = json.loads(cpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}

    moved, checked, incon, backed = [], 0, 0, False
    for path, mbid in rows:
        try:
            st = Path(path).stat()
        except OSError:
            continue
        key = f"{int(st.st_mtime)}:{st.st_size}:{mbid}"        # re-check only if the file changed
        verdict = cache.get(key)
        if verdict is None:
            status, present = _file_verdict(path, mbid)
            if status != "ok":
                incon += 1
                continue                                       # inconclusive -> not cached, retried next run
            if present:
                verdict = "ok"
            else:
                known = _official_known(mbid)
                if known is None:
                    incon += 1
                    continue
                verdict = "imposter" if known else "rare"      # rare = file & official both unknown -> genuine, kept
            cache[key] = verdict
            checked += 1
        if verdict == "imposter":                              # conclusive imposter -> quarantine (never deleted)
            if not backed:
                backup_db(cfg, "verify", log)
                backed = True
            qd = cfg.dump / Path(path).parent.name
            dest = qd / Path(path).name
            i = 1
            while dest.exists():
                i += 1
                dest = qd / f"{Path(path).stem} ({i}){Path(path).suffix}"
            qd.mkdir(parents=True, exist_ok=True)
            if safe_move(path, dest, log):                     # move out of clean, then drop the now-stale lib entry
                run_beet(cfg, ["remove", "-f", f"path:{path}"], passname="verify", echo_lines=False)
                moved.append(path)
                log.info("QUARANTINE imposter (audio != tagged recording): %s -> %s/", Path(path).name, qd)

    cfg.beetsdir.mkdir(parents=True, exist_ok=True)
    with cpath.open("w", encoding="utf-8") as fh:
        json.dump(cache, fh)
    log.info("=== fingerprint verify: %d new check(s), %d imposter(s) quarantined, %d inconclusive ===",
             checked, len(moved), incon)
    if moved:
        log.info("  [IMPOSTER] %d track(s) (audio != tagged recording) moved to %s -- recoverable, never deleted",
                 len(moved), cfg.dump)
    return len(moved)
