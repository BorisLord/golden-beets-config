"""Pass -- per-track AcoustID fingerprint verification: detect & quarantine IMPOSTER tracks.

An imposter has the right tags but its AUDIO is a different recording; album-mode import trusts it and
`chroma` doesn't penalise a track it can't ID, so it slips into a "strong" album. We act ONLY on POSITIVE
evidence: the fingerprint CONFIDENTLY matches a DIFFERENT artist's recording. Can't confirm, or a SAME-artist
match (alt mix/edition/typo) -> KEEP. Imposter -> MOVED to $MUSIC_DUMP (never deleted) + dropped. Cached per file.
"""
import importlib.util
import json
import os
import re
import time
from pathlib import Path

from ..beets import run_beet
from ..config import Config
from ..logs import get_logger
from ..sidecars import quarantine_dir, safe_move, unique_dest
from ..util import backup_db, prune_empty_dirs, skip_on_error, write_json

APIKEY = os.environ.get("GBC_ACOUSTID_APIKEY", "1vOwZtEn")  # beets' shared key; set your own to avoid throttling
MATCH_SCORE = 0.5   # AcoustID result score above which the file CONFIRMS the tagged recording
MISMATCH_SCORE = 0.9  # higher bar to REFUTE: audio matches a DIFFERENT recording this strongly -> tag likely wrong
RETRIES = 4         # attempts on rate-limit / network error before giving up -> inconclusive
SEP = "\x1f"        # US control char: can't appear in tags/paths and survives str.splitlines() (unlike \x1e)


def _acoustid_available() -> bool:
    return importlib.util.find_spec("acoustid") is not None


# generic tokens two UNRELATED artists routinely share -- never enough alone to call it "same artist":
# connectives + multilingual articles ("De La Soul" vs "La Roux", "DJ X" vs "DJ Y").
_GENERIC = {"the", "and", "feat", "ft", "featuring", "with", "dj", "mc", "of", "vs", "for", "an",
            "la", "le", "les", "el", "los", "las", "de", "del", "da", "du", "des", "et", "und"}


def _credit_tokens(name: str) -> set:
    """Distinctive tokens of an artist credit. A SINGLE-token credit keeps its token (so 1-char -M-/K matches
    itself); a MULTI-token credit drops stray 1-char tokens ('A Tribe...' must not read as 'A Perfect...')."""
    toks = [t for t in re.split(r"\W+", name.lower()) if t and t not in _GENERIC]
    return set(toks) if len(toks) == 1 else {t for t in toks if len(t) >= 2}


def _same_artist(m_artist: str, artist: str) -> bool:
    """Audio matched a different recording but the credits share a DISTINCTIVE token -> a version/edition/typo
    variant WITHIN the artist, KEEP it. Only a COMPLETELY different artist is the evidence we quarantine on:
    AcoustID's title is too noisy ('feat.' moves around, alt mixes), so artist identity is the airtight signal."""
    return bool(_credit_tokens(m_artist) & _credit_tokens(artist))


def _file_verdict(path, mbid):
    """('ok', present, mismatch) once AcoustID answers conclusively, else ('error', False, None). present=True
    when the file's own fingerprint lists the tagged recording -> genuine; False => audio is something else.
    mismatch=(artist, title, score) when the audio matches a DIFFERENT recording >= MISMATCH_SCORE -- the
    positive evidence that flags an imposter."""
    import acoustid
    for attempt in range(RETRIES):
        try:
            dur, fp = acoustid.fingerprint_file(path)
            resp = acoustid.lookup(APIKEY, fp, dur, meta="recordings")
        except acoustid.FingerprintGenerationError:
            return "error", False, None                 # can't fingerprint -> inconclusive
        except acoustid.WebServiceError:
            time.sleep(2 ** attempt)
            continue
        if resp.get("status") != "ok":
            time.sleep(2 ** attempt)
            continue
        results = resp.get("results") or []
        present = any(rec.get("id") == mbid
                      for r in results if (r.get("score") or 0) >= MATCH_SCORE
                      for rec in (r.get("recordings") or []))
        mismatch = None
        if not present:                                 # audio != tag: is it confidently some other known recording?
            for r in results:                           # results are best-score first
                if (r.get("score") or 0) < MISMATCH_SCORE:
                    break                               # sorted desc -> nothing below the bar matters
                for rec in (r.get("recordings") or []):
                    if rec.get("id") == mbid:
                        continue
                    artist = ", ".join(a.get("name", "") for a in (rec.get("artists") or []))
                    title = rec.get("title") or ""
                    if artist or title:
                        mismatch = (artist, title, round(r.get("score") or 0, 2))
                        break
                if mismatch:
                    break
        return "ok", present, mismatch
    return "error", False, None


def run(cfg: Config, scope="") -> int:
    """Flag imposter tracks among items in `scope` (whole library if empty). Returns the imposter count."""
    log = get_logger("verify")
    if not _acoustid_available():
        log.warning("pyacoustid not available -> fingerprint verification skipped")
        return 0
    sc = [scope] if scope else []
    fmt = f"$id{SEP}$path{SEP}$mb_trackid{SEP}$artist{SEP}$title{SEP}$length{SEP}$bitrate"
    _, text = run_beet(cfg, ["ls", "-f", fmt, "mb_trackid::.", *sc], passname="verify", echo_lines=False)
    rows = [ln.split(SEP, 6) for ln in text.splitlines() if ln.count(SEP) >= 6]

    cpath = cfg.beetsdir / "gbc-verify-cache.json"
    try:
        cache = json.loads(cpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}

    moved, checked, incon, backed = [], 0, 0, False
    mismatches = 0
    for itemid, path, mbid, artist, title, length, bitrate in rows:
        if not Path(path).exists():
            continue
        # Key on the file PLUS its audio identity (mbid + duration + bitrate), NOT mtime/size: tag writes
        # (acousticbrainz, comp normalisation) change mtime but not the audio -> an mtime key would invalidate
        # the whole cache every run; mbid/length/bitrate flip only on a re-tag to another id or a re-encode.
        key = f"{path}:{mbid}:{length}:{bitrate}"
        verdict = cache.get(key)
        if verdict is None:
            status, present, mismatch = _file_verdict(path, mbid)
            if status != "ok":
                incon += 1
                continue                                       # inconclusive -> not cached, retried next run
            # matched artist has NO distinctive token (empty/generic "DJ"/"The") -> can't prove a different artist
            sibling = bool(mismatch) and (not _credit_tokens(mismatch[0]) or _same_artist(mismatch[0], artist))
            if present or sibling:                 # tagged recording present, or a match by the SAME artist (kept)
                verdict = "ok"
            elif mismatch:                         # audio matches a DIFFERENT artist's recording -> proven imposter
                mismatches += 1
                log.warning("IMPOSTER: %s - %s | audio = %s - %s (%.2f)",
                            artist, title, mismatch[0], mismatch[1], mismatch[2])
                verdict = "imposter"
            else:                                  # tagged id absent but NO confident alternative -> unprovable, KEEP
                verdict = "rare"
            cache[key] = verdict
            checked += 1
        if verdict == "imposter":                              # quarantine, never deleted
            with skip_on_error(log, "verify", path):           # one bad move never loses the run's verdicts
                if not backed:
                    backup_db(cfg, "verify", log)
                    backed = True
                # mirror the EXACT clean sub-path (any depth: _Various Artists/_Soundtracks/_Singles/Artist-Album)
                folder = Path(path).parent
                try:
                    qd = cfg.dump / "imposters" / folder.relative_to(cfg.clean)
                except ValueError:                          # not under clean (shouldn't happen) -> flat fallback
                    qd = quarantine_dir(cfg.dump, "imposters", fallback=folder.name)
                dest = unique_dest(qd, Path(path).name)
                qd.mkdir(parents=True, exist_ok=True)
                if safe_move(path, dest, log):                 # move out of clean, then drop the stale lib entry
                    rc, _ = run_beet(cfg, ["remove", "-f", f"id:{itemid}"], passname="verify", echo_lines=False)
                    if rc:
                        log.warning("verify: `beet remove` rc=%d for id:%s -- stale lib entry may remain", rc, itemid)
                    moved.append(path)
                    log.info("QUARANTINE imposter (audio != tagged recording): %s -> %s/", Path(path).name, qd)

    write_json(cpath, cache)                               # atomic (tmp + replace): a crash can't corrupt the cache
    log.info("=== fingerprint verify: %d check(s), %d imposter(s) quarantined, %d mismatch(es), %d inconclusive ===",
             checked, len(moved), mismatches, incon)
    if moved:
        prune_empty_dirs(cfg.clean)                            # remove album shells left fully empty by quarantine
        log.info("  [IMPOSTER] %d track(s) (audio != tagged recording) moved to %s -- recoverable, never deleted",
                 len(moved), cfg.dump)
    return len(moved)
