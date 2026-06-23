"""QA anomaly / name scanner for the clean album library. READ-ONLY; writes one TSV per family, logs counts.

Input TSV (one '@@@'-delimited row per track), produced by beets (11 @@@-fields):
  $id@@@$albumartist@@@$artist@@@$album@@@$title@@@$length@@@$bitrate@@@$singleton@@@$comp@@@$albumtype@@@$mb_trackid

Families (each needs human + MB verification before fixing):
  artist_variant   - same artist, different albumartist spelling (case/accent/hyphen/punct/quote)
  feat_albumartist - albumartist carries 'feat. X' -> track torn out of its album/base folder
  album_variant    - same album under several names (year/disc/punct/case) -> split folders
  loose_dup        - singleton whose own album = a matched complete album (dup of an album track)
  orphan           - singleton with no album tag
  junk_album       - album is URL/spam, equals the artist name, or 'unknown/inconnu/timestamp'
  intra_album_dup  - same (albumartist, album) has the same title twice (~same duration)
  disc_in_name     - disc number baked into the album name ('- cd2', '(1 of 2)', 'Disc 3')
"""
# Deliberately matches ambiguous unicode punctuation (en-dashes, curly quotes) -> RUF001 off.
# ruff: noqa: RUF001
import csv
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from .logs import get_logger

FEAT = re.compile(r"\b(feat|featuring|ft|avec|with|vs)\b\.?", re.I)
URL = re.compile(r"www\.|https?:|\.(net|com|org|ru|br|info|biz)\b|torrent|blogspot|@", re.I)
JUNKNAME = re.compile(
    r"^(unknown|inconnu|untitled|various|track \d+|\d+)$|album inconnu|\(\d{2}[/.]\d{2}[/.]\d{4}", re.I)
DISCNAME = re.compile(
    r"\b(cd|disc|disk|disque)\s*\.?\s*\d|\(\d+\s*(of|sur|/)\s*\d+\)|[-–]\s*(cd|disc|disk)\b", re.I)
YEARDISC = re.compile(
    r"\s*[\(\[]\s*(19|20)?\d{2}\s*[\)\]]\s*$"
    r"|\s*[-–]\s*(cd|disc|disk|disque)\s*\.?\s*\d.*$"
    r"|\s*[\(\[][^)\]]*\b(cd|disc|disk|bonus|single|ep|remaster|deluxe)\b[^)\]]*[\)\]]\s*$", re.I)


def strip_accents(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c))


def norm(s):
    s = strip_accents(s).lower()
    s = re.sub(r"[‐‑‒–—―]", "-", s)
    s = re.sub(r"[’‘`´]", "'", s)
    s = re.sub(r"\s+(and|et|&|\+)\s+", " ", s)   # unify separators &/and/et/+
    s = re.sub(r"[^a-z0-9'-]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normbase(s):
    """Album name without year/disc/edition parentheticals, for variant grouping."""
    s = s or ""
    prev = None
    while prev != s:
        prev = s
        s = YEARDISC.sub("", s).strip()
    return norm(s)


def secs(t):
    t = (t or "").strip()
    if not t:
        return -999
    try:
        p = [int(x) for x in t.split(":")]
    except ValueError:
        return -999
    return p[0] * 60 + p[1] if len(p) == 2 else (p[0] * 3600 + p[1] * 60 + p[2] if len(p) == 3 else -999)


def scan(dump, workdir, log=None):
    log = log if log is not None else get_logger("anomaly")
    Path(workdir).mkdir(parents=True, exist_ok=True)
    rows = []
    for line in Path(dump).read_text(encoding="utf-8").splitlines():
        f = line.split("@@@")
        if len(f) < 11:
            continue
        rows.append({"id": f[0], "albumartist": f[1], "artist": f[2], "album": f[3], "title": f[4],
                     "length": f[5], "bitrate": f[6], "singleton": f[7] == "True", "comp": f[8] == "True",
                     "albumtype": f[9], "mb_trackid": f[10]})

    matched_albums = {norm(r["album"]) for r in rows if not r["singleton"] and r["album"].strip()}
    matched_tracks = defaultdict(list)   # (norm album, norm title) -> [secs]
    for r in rows:
        if not r["singleton"] and r["album"].strip():
            matched_tracks[(norm(r["album"]), norm(r["title"]))].append(secs(r["length"]))

    cats = defaultdict(list)

    def spellings(v):
        return " || ".join(f"{a}={n}" for a, n in sorted(v.items(), key=lambda x: -x[1]))

    # artist_variant
    aa_groups = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r["albumartist"].strip() and not FEAT.search(r["albumartist"]):
            aa_groups[norm(r["albumartist"])][r["albumartist"]] += 1
    for k, v in aa_groups.items():
        if len(v) > 1 and k:
            cats["artist_variant"].append([k, spellings(v)])

    # album_variant: group by albumartist + normbase album
    alb_groups = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r["album"].strip():
            alb_groups[(norm(r["albumartist"]), normbase(r["album"]))][r["album"]] += 1
    for (aa, base), v in alb_groups.items():
        if len(v) > 1 and base:
            cats["album_variant"].append([aa, base, spellings(v)])

    # intra_album_dup: same raw album, same title twice, ~same length
    seen = defaultdict(list)
    for r in rows:
        if r["album"].strip():
            seen[(r["albumartist"], r["album"], norm(r["title"]))].append((r["id"], secs(r["length"]), r["bitrate"]))
    for (aa, alb, t), items in seen.items():
        if len(items) > 1:
            ss = [s for _, s, _ in items if s >= 0]
            if len(ss) < 2 or (max(ss) - min(ss) <= 5):
                cats["intra_album_dup"].append([aa, alb, t, "; ".join(f"{i}({b})" for i, _, b in items)])

    # per-item families: feat_albumartist, junk_album, disc_in_name, orphan, loose_dup
    for r in rows:
        alb, aa = r["album"].strip(), r["albumartist"].strip()
        if FEAT.search(aa):
            cats["feat_albumartist"].append([r["id"], aa, re.split(FEAT, aa)[0].strip(" -,&"), alb, r["title"]])
        if alb and (URL.search(alb) or JUNKNAME.search(alb) or norm(alb) == norm(r["artist"]) or norm(alb) == norm(aa)):
            cats["junk_album"].append([r["id"], aa, alb, r["title"], r["mb_trackid"]])
        if alb and DISCNAME.search(alb):
            cats["disc_in_name"].append([r["id"], aa, alb, r["title"]])
        if r["singleton"] and not alb:
            cats["orphan"].append([r["id"], r["artist"], r["title"], r["bitrate"], r["mb_trackid"]])
        if r["singleton"] and alb and norm(alb) in matched_albums:
            ls = secs(r["length"])
            hit = matched_tracks.get((norm(alb), norm(r["title"])), [])
            if any(ms >= 0 and ls >= 0 and abs(ms - ls) <= 5 for ms in hit):
                cats["loose_dup"].append([r["id"], aa, alb, r["title"], r["bitrate"]])

    order = ["artist_variant", "feat_albumartist", "album_variant", "loose_dup",
             "orphan", "junk_album", "intra_album_dup", "disc_in_name"]
    counts = {}
    for c in order:
        rows_c = cats.get(c, [])
        counts[c] = len(rows_c)
        with (Path(workdir) / f"{c}.tsv").open("w", newline="", encoding="utf-8") as fo:
            csv.writer(fo, delimiter="\t", lineterminator="\n").writerows(rows_c)
        log.info("anomaly %-18s %d", c, len(rows_c))
    log.info("one TSV per category in %s/", workdir)
    return counts
