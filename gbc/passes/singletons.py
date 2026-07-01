"""Singleton recovery (OPT-IN) -- import LOOSE source tracks (and quarantined imposters) as singletons, filed
under _Singles/ (config `singleton:` path). Tracks already in clean DUP-SKIP by mb_trackid.

FINGERPRINT-FIRST: before importing, every loose file is identified by its AUDIO (AcoustID) -- the source of
truth -- and re-tagged to that recording, so the import matches it instead of skipping on bad tags. Metadata
(MB/Discogs/Deezer/Bandcamp) corroborates at import time. What AcoustID can't identify is LEFT IN PLACE (the
default-skip import keeps it in the source = the curation backlog); nothing is force-tagged.

MOVE-vs-COPY (beets' call): a CONSUMED source (move/delete) is re-tagged IN PLACE, originals first saved to
`gbc-singletons-retag-backup.jsonl`; a PRESERVED source is NEVER mutated -- copy/reflink/hardlink re-tag
throwaway COPIES in a staging dir, symlink/in-place skip source recovery (the library references the original
file, so it can't be staged). Quarantined imposters are gbc-owned -> always re-tagged in place.

Then two reassembly steps run (dry unless --apply):
  1. nova.reroute() -- OPT-IN/detachable: re-tag dispersed Nova-compilation tracks to their compil (Nova first).
  2. _promote_complete() -- any album whose ENTIRE MusicBrainz tracklist is now present as singletons is
     re-imported as a real album (the inverse of verify's demote of incomplete albums). Incomplete sets stay.

NOT part of `gbc run` (which stays album-only by design); run it deliberately with `gbc singletons`.
"""
import json
import re
import shutil
from collections import defaultdict
from contextlib import suppress
from pathlib import Path

from .. import artfix, beetscfg
from ..beets import run_beet
from ..config import Config
from ..logs import get_logger
from ..mb import load_release_cache, release_recordings, save_release_cache
from ..sidecars import safe_move, unique_dest
from ..util import backup_db, count_items, prune_empty_dirs, skip_on_error
from . import verify  # AcoustID identity + the shared id-cache helpers live in verify

try:                                  # Nova is OPT-IN + detachable: deleting nova.py just disables the re-tag
    from . import nova
except Exception:                     # pragma: no cover
    nova = None                       # type: ignore[assignment]


def run(cfg: Config, src=None, reimport: bool = False, apply: bool = False) -> int:
    log = get_logger("singletons")
    src = Path(src) if src else cfg.src
    if not src.is_dir():
        log.error("source missing: %s", src)
        return 1
    backup_db(cfg, "singletons", log)
    bi = beetscfg.read_import(cfg)             # move-vs-copy is BEETS' call -> it dictates how we may re-tag the source
    cache = verify.load_idcache(cfg)           # prior identities (incl. verify's imposters) + resume state
    clean_ids = _clean_recording_ids(cfg)      # so a loose copy of a track already in clean is NOT re-added
    inc = "-I" if reimport else "-i"           # -I re-evaluates album-rejected folders as singletons

    # Each entry = (walk_dir, import_dir, inc_flag, staging|None). The SOURCE re-tag MUST respect beets' decision:
    # a CONSUMED source (move/delete) may be re-tagged in place; a PRESERVED source must never be mutated.
    plan: list = []
    if bi.source_consumed:
        plan.append((src, src, inc, None))                         # files leave the source anyway -> re-tag in place
    elif bi.copy or bi.reflink or bi.hardlink:                     # preserved, but beets makes an INDEPENDENT copy
        staging = cfg.beetsdir / STAGING                           # -> re-tag throwaway COPIES, source left untouched
        if apply:
            with suppress(OSError):
                shutil.rmtree(staging)                             # clean slate: a killed run can't poison this one
            staging.mkdir(parents=True, exist_ok=True)
        plan.append((src, staging, "-I", staging))                 # staging is fresh each run -> always re-evaluate
        log.info("singletons: source preserved (%s) -> re-tag copies in staging, source untouched", bi.label)
    else:                                                          # symlink / in-place: the library REFERENCES the
        log.warning("singletons: source preserved (%s) references originals -> source re-tag SKIPPED "  # source file
                    "(use move/copy/reflink/hardlink mode to recover loose source tracks)", bi.label)

    imposters = cfg.dump / "imposters"
    if imposters.is_dir():
        plan.append((imposters, imposters, inc, None))             # gbc-owned quarantine -> always re-tag in place
    else:
        log.info("singletons: no %s -> imposters step skipped", imposters)

    # FINGERPRINT-FIRST: identify every loose file by its audio + re-tag (in place, or onto a staging copy) BEFORE
    # import, so a bad tag no longer makes it skip. Cached -> re-runs only fingerprint new files.
    for walk, _imp, _inc, staging in plan:
        _fingerprint_retag(cfg, walk, cache, clean_ids, log, apply, staging=staging)

    # DRY-RUN = identification only. The import must NOT run dry: it would mark the folders "seen" (incremental),
    # so a later --apply would skip the now-re-tagged files. Gate import + re-tag-dependent steps on --apply.
    if apply:
        before = count_items(cfg, ["ls"], "singletons")
        for _walk, imp_dir, inc_flag, staging in plan:
            artfix.run(cfg, src=imp_dir, log=log)      # strip mime=None WMA art so scrub can't crash beet import
            rc, _ = run_beet(cfg, ["import", "-q", "-s", inc_flag, str(imp_dir)], passname="singletons")
            if rc:
                log.error("beet import -s %s failed (rc=%d) -- nothing deleted", imp_dir, rc)
                return rc
            if staging is not None:
                with suppress(OSError):
                    shutil.rmtree(staging)             # throwaway copies; the clean copies beets made are independent
        added = count_items(cfg, ["ls"], "singletons") - before    # already-present tracks dup-skip; delta = new
        log.info("singletons: +%d loose track(s) recovered -> _Singles/", added)
    else:
        log.info("singletons: dry-run -- identification only; re-run with --apply to re-tag + import")
    if nova is not None:
        nova.reroute(cfg, log, apply)              # NOVA FIRST: dispersed Nova tracks regroup under their compil
    _promote_complete(cfg, log, apply, reimport)   # then promote ANY now-complete album out of _Singles/
    return 0


_AUDIO_EXT = {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".wav", ".aiff", ".aif"}
STAGING = ".gbc-singletons-staging"                 # preserve-mode: throwaway re-tagged copies, imported from here
RETAG_BACKUP = "gbc-singletons-retag-backup.jsonl"  # original tags saved before an in-place AcoustID re-tag


def _clean_recording_ids(cfg: Config) -> set:
    """Every mb_trackid currently in the clean library (snapshot, ONE query). A loose track whose AUDIO maps to
    ANY of these is already in clean -> we re-tag it to that in-clean id so beets DUP-skips it at import instead
    of adding a second copy as a singleton (the album's recording id often differs from AcoustID's dominant
    pick, so the bare mb_trackid match would miss it)."""
    _, text = run_beet(cfg, ["ls", "-f", "$mb_trackid", "mb_trackid::."], passname="singletons", echo_lines=False)
    return {ln.strip() for ln in text.splitlines() if ln.strip()}


def _backup_tags(cfg: Config, path: Path, mf) -> None:
    """Append the ORIGINAL title/artist/mb_trackid to a backup log BEFORE an in-place AcoustID re-tag overwrites
    them -- the safety net when the source is CONSUMED (a rare confident-but-wrong AcoustID match stays
    reversible). Preserve-mode staging needs none: it re-tags a copy, so the original file is never overwritten."""
    rec = {"path": str(path), "title": mf.title, "artist": mf.artist, "mb_trackid": mf.mb_trackid}
    bp = cfg.beetsdir / RETAG_BACKUP
    bp.parent.mkdir(parents=True, exist_ok=True)
    with bp.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()


def _fingerprint_retag(cfg: Config, directory: Path, cache: dict, clean_ids: set, log, apply: bool,
                       staging: Path | None = None) -> tuple[int, int]:
    """Fingerprint-FIRST identity for every loose audio file under `directory`: ask AcoustID what the audio
    really is and overwrite its artist/title/mb_trackid with that recording, so the singleton import matches it
    instead of skipping on bad tags (audio = source of truth; metadata only corroborates at import). If the
    audio is ALREADY in clean (any of its recording ids in `clean_ids`), re-tag it to the in-clean id so the
    import DUP-skips it rather than adding a duplicate single. Ambiguous/unidentifiable files are LEFT UNTOUCHED.
    `cache` is verify's SHARED id-cache; values are [rid, artist, title, [all_ids]] (or the 3-field form verify
    pre-writes for imposters), or null. Writes only with --apply: in place (originals backed up first), or -- when
    `staging` is set, for a PRESERVED source -- onto a throwaway COPY in `staging`. Returns (identified, left)."""
    if not verify._acoustid_available():
        log.info("%s: pyacoustid absent -> AcoustID identify skipped", directory.name)
        return 0, 0
    try:
        import mediafile
    except ImportError:
        log.info("%s: mediafile absent -> AcoustID identify skipped", directory.name)
        return 0, 0
    fixed = dup = left = scanned = 0
    for p in sorted(directory.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in _AUDIO_EXT:
            continue
        scanned += 1
        if scanned % 500 == 0:                 # liveness on a huge source (the fingerprint walk is silent + slow)
            log.info("  ...%s: %d scanned (%d new, %d dup, %d left)", directory.name, scanned, fixed, dup, left)
        with skip_on_error(log, "singletons", p.name):
            key = verify.idcache_key(p)
            if key is None:                        # unreadable file (stat failed) -> skip
                continue
            if key in cache:
                entry = cache[key]                 # cached (incl. imposter identities verify pre-wrote) -> no lookup
            else:
                results = verify._lookup(str(p))
                tup = verify._dominant_from_results(results) if results is not None else None
                entry = [*tup, sorted(verify._all_recording_ids(results))] if tup else None
                verify.append_idcache(cfg, key, entry)   # persist now (resume) without holding the cache in RAM
            if not entry:
                left += 1
                continue
            rid, artist, title = entry[0], entry[1], entry[2]
            all_ids = set(entry[3]) if len(entry) > 3 else {rid}   # 3-field (verify) -> dominant only
            in_clean = all_ids & clean_ids
            tag_id = sorted(in_clean)[0] if in_clean else rid      # in-clean id -> the import DUP-skips it
            if in_clean:
                dup += 1
            else:
                fixed += 1
            log.info("  re-id%s: %s -> %s - %s [%s]%s", "" if apply else " (dry)", p.name, artist, title, tag_id,
                     "  (already in clean -> dup-skip)" if in_clean else "")
            if apply:
                if staging is not None:                # preserved source -> re-tag a throwaway COPY, never the original
                    dest = unique_dest(staging, p.name)
                    shutil.copy2(str(p), str(dest))
                    mf = mediafile.MediaFile(str(dest))
                else:                                  # consumed source / gbc-owned quarantine -> re-tag in place
                    mf = mediafile.MediaFile(str(p))
                    _backup_tags(cfg, p, mf)           # save original tags first (reversible if AcoustID was wrong)
                mf.title = title
                if artist:
                    mf.artist = artist
                mf.mb_trackid = tag_id
                mf.save()
    log.info("%s: %d new identified, %d already-in-clean (dup-skip), %d unidentified%s",
             directory.name, fixed, dup, left, "" if apply else " (dry-run, not written)")
    return fixed, left


def _promote_complete(cfg: Config, log, apply: bool, refresh: bool = False) -> int:
    """Group loose singletons by their matched release; an album whose ENTIRE MusicBrainz tracklist is now
    present as singletons is re-imported as a real album (beets routes it to <artist>/_Various Artists/
    _Soundtracks per the paths rules). ROBUST: completeness is decided SOLELY against the live MB release
    tracklist (fetched once per release, then cached) -- NEVER the stored `tracktotal`, which a bad rip can
    inflate and thereby skip a genuinely-complete set. `refresh` (a --reimport run) re-pulls the persisted
    MB tracklist cache instead of reusing it."""
    _, text = run_beet(cfg, ["ls", "-f", "$mb_albumid\t$id\t$mb_trackid\t$path",
                             "singleton:1", "mb_albumid::."], passname="singletons", echo_lines=False)
    albums: dict = defaultdict(list)
    for line in text.splitlines():
        albumid, _, rest = line.partition("\t")
        sid, _, rest = rest.partition("\t")
        tid, _, path = rest.partition("\t")
        if albumid.strip() and path:
            albums[albumid.strip()].append((sid.strip(), tid.strip(), path))
    cache = load_release_cache(cfg, refresh)          # persisted MB tracklists, shared with verify's demote
    promoted = 0
    for albumid, items in albums.items():
        if albumid not in cache:
            cache[albumid] = release_recordings(albumid)   # one fetch/release, cached across runs (cheap after)
        official = cache[albumid]
        have = {tid for _, tid, _ in items if tid}
        if not official or not official <= have:      # completeness decided ONLY by the live MB tracklist
            continue
        if _assemble_album(cfg, albumid, items, log, apply):
            promoted += 1
    save_release_cache(cfg, cache)                     # persist tracklists fetched this pass for the next run/pass
    log.info("singletons: %d complete album(s) %s out of _Singles/",
             promoted, "promoted" if apply else "would be promoted")
    return promoted


def _assemble_album(cfg: Config, albumid: str, items, log, apply: bool) -> bool:
    """Stage the album's files, drop their singleton rows, re-import the staging dir AS ONE ALBUM from its
    EXISTING tags (`-A --flat -m`). No MB re-match: `_promote_complete` already verified the set against the live
    tracklist, so `-A` files it deterministically (offline, never quiet-mode 'Skipping'); beets routes by tags
    into <artist>/ | _Various Artists/ | _Soundtracks/. Leftover files are put back as singletons -- never lost."""
    label = f"{albumid} ({len(items)} trk)"
    if not apply:
        log.info("  COMPLETE -> would promote album %s", label)
        return True
    staging = cfg.beetsdir / ".gbc-assemble" / re.sub(r"[^\w.-]", "_", albumid)
    staging.mkdir(parents=True, exist_ok=True)
    moved = []
    for sid, _tid, path in items:                     # de-collide same-basename tracks (VA comps) -> never overwrite
        if Path(path).exists() and safe_move(path, unique_dest(staging, Path(path).name), log):
            moved.append(sid)
    if not moved:
        log.warning("  promote %s: no files moved -> skipped", label)
        return False
    rm = ["remove", "-f"]                              # drop the now-staged singleton rows (else import dup-skips)
    for i, sid in enumerate(moved):
        rm += ([","] if i else []) + [f"id:{sid}"]
    run_beet(cfg, rm, passname="singletons", echo_lines=False)
    rc, _ = run_beet(cfg, ["import", "-q", "-I", "-A", "--flat", "-m", str(staging)], passname="singletons")
    if any(p.is_file() for p in staging.iterdir()):   # leftover -> restore as singletons (no loss)
        log.warning("  promote %s: album import left files -> restoring as singletons", label)
        run_beet(cfg, ["import", "-q", "-I", "-s", "-A", "-m", str(staging)], passname="singletons")
    leftover = [p.name for p in staging.iterdir() if p.is_file()]   # rows already dropped -> anything left is orphaned
    if leftover:
        log.error("  promote %s: %d file(s) still in %s after restore -- lib rows dropped, files kept for manual "
                  "recovery (NOT lost)", label, len(leftover), staging)
        return False                                  # don't rmdir, don't claim PROMOTED -- files are orphaned
    with suppress(OSError):
        staging.rmdir()
        staging.parent.rmdir()
    prune_empty_dirs(cfg.clean / "_Singles")
    if rc:                                            # album import failed but the singleton restore recovered them
        log.warning("  promote %s: album import rc=%d -- files restored as singletons, NOT promoted", label, rc)
        return False
    log.info("  PROMOTED album -> %s", label)
    return True
