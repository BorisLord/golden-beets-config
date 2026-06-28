"""Pre-import dedup: within each SOURCE album folder, quarantine duplicate audio (same title + near-equal
duration), keeping the best bitrate -- NEVER deleted. Runs before `beet import` so a duplicate can't inflate
the unmatched-tracks penalty and block a good album. Conservative: only titled files, same-title files
beyond TOL apart are kept (distinct versions/reprises).
"""
import json
import os
import subprocess
from collections import defaultdict
from pathlib import Path

from .logs import get_logger
from .quality import eff, rank
from .sidecars import AUDIO, quarantine_dir, safe_move, unique_dest

# Same track via the SAME ffprobe -> tight tolerance (unlike sidecars' ±6s ffprobe-vs-beets comparison).
TOL = 3   # seconds


def _log(log):
    return log if log is not None else get_logger("dedup")


def _probe(path):
    """(title_key, duration_seconds, bitrate_kbps) for an audio file; title_key='' if unreadable/untitled.
    kbps (not raw bps) so `eff()` is on the same scale as upgrade/albumdedup."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-of", "json", "-i", str(path)],
                             capture_output=True, text=True).stdout
        fmt = json.loads(out).get("format", {})
    except (ValueError, OSError):
        return "", 0, 0
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    title = (tags.get("title") or "").strip().casefold()
    return title, round(float(fmt.get("duration") or 0)), int(fmt.get("bit_rate") or 0) // 1000


def _album_tags(path):
    """(albumartist|artist, album, year) from ffprobe tags -> names the per-album quarantine sub-folder."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-of", "json", "-i", str(path)],
                             capture_output=True, text=True).stdout
        tags = {k.lower(): v for k, v in (json.loads(out).get("format", {}).get("tags") or {}).items()}
    except (ValueError, OSError):
        return "", "", ""
    artist = (tags.get("album_artist") or tags.get("albumartist") or tags.get("artist") or "").strip()
    return artist, (tags.get("album") or "").strip(), (tags.get("date") or tags.get("year") or "").strip()


def dedup(src, dump, do_apply, log=None):
    """Move duplicate audio (best bitrate kept) to quarantine. Returns the count of files moved."""
    log = _log(log)
    by_folder = defaultdict(list)
    for dp, _, files in os.walk(src):
        for fn in files:
            if Path(fn).suffix.lower() in AUDIO:
                by_folder[dp].append(str(Path(dp) / fn))

    moved = 0
    for folder, paths in by_folder.items():
        groups = defaultdict(list)
        for p in paths:
            title, dur, br = _probe(p)
            if title:                        # only dedup titled files (safe key)
                groups[title].append((p, dur, br, Path(p).stat().st_size))
        for items in groups.values():
            if len(items) < 2:
                continue
            durs = [d for _, d, _, _ in items if d > 0]
            if len(durs) != len(items) or max(durs) - min(durs) > TOL:
                continue        # probe failed OR genuinely different lengths -> keep all (safe)
            # quality FIRST (lossless tier, so a FLAC whose ffprobe bitrate reads 0 never loses to a 320k MP3),
            # then codec-normalised bitrate (256k Opus > 320k MP3), then file size
            items.sort(key=lambda x: (rank(Path(x[0]).suffix), eff(Path(x[0]).suffix, x[2]), x[3]), reverse=True)
            keep = Path(items[0][0]).name
            for p, _, _, _ in items[1:]:
                qd = quarantine_dir(dump, "duplicates", *_album_tags(p), fallback=Path(folder).name)
                dest = unique_dest(qd, Path(p).name)
                if do_apply:
                    qd.mkdir(parents=True, exist_ok=True)
                if not do_apply or safe_move(p, dest, log):
                    moved += 1
                    log.info("%s dup %s -> %s/ (kept %s)",
                             "DEDUP" if do_apply else "DRY ", Path(p).name, qd, keep)
    log.info("%d duplicate audio file(s) -> quarantine", moved)
    return moved
