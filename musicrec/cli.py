"""musicrec CLI -- one tool, several doors. beets does art/genres/replaygain/scrub natively during import.

  musicrec run [--all]        full pipeline now (import -> qa), incremental
  musicrec inbox              cron door: import a drop if anything is new, then the pipeline
  musicrec import [SOURCE]    album-match import only (art + genres + replaygain run automatically)
  musicrec qa [QUERY]         read-only technical audit + anomaly scan
  musicrec anomaly [QUERY]    read-only name/anomaly scan only
  musicrec convert            normalise formats in the clean lib (WMA->AAC, WAV/AIFF->FLAC; originals->quarantine)
  musicrec init [--cron]      deploy config + beets configs (+ optional cron)
  musicrec uninstall [--purge] remove tooling (never your music)
"""
import argparse
import secrets

from . import admin
from . import config as configmod
from .lock import import_lock
from .logs import configure
from .passes import convert, import_, inbox, pipeline, qa


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="musicrec", description="Beets-driven music recovery pipeline.")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("run", help="full pipeline now (incremental)")
    pr.add_argument("--all", action="store_true", help="reprocess the whole library (ignore watermark)")
    sub.add_parser("inbox", help="cron door: import a drop if there's anything new, then the pipeline")
    pi = sub.add_parser("import", help="album-match import only (art/genres/replaygain run automatically)")
    pi.add_argument("source", nargs="?", help="source dir (default: MUSIC_SRC)")
    pq = sub.add_parser("qa", help="read-only technical audit")
    pq.add_argument("query", nargs="?", default="", help="scope query (default: whole library)")
    pa = sub.add_parser("anomaly", help="read-only name/anomaly scan only")
    pa.add_argument("query", nargs="?", default="", help="scope query (default: whole library)")
    sub.add_parser("convert", help="normalise formats (WMA->AAC, WAV/AIFF->FLAC; originals -> quarantine)")
    pini = sub.add_parser("init", help="deploy config + beets overlays (+ optional cron)")
    pini.add_argument("--cron", action="store_true", help="also schedule `musicrec inbox` every 15 min")
    pun = sub.add_parser("uninstall", help="remove tooling (never your music)")
    pun.add_argument("--purge", action="store_true", help="also remove the beets config dir + catalog")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = configmod.load()
    configure(cfg.log_dir, secrets.token_hex(2))

    if args.cmd == "run":
        with import_lock(cfg, blocking=True):
            return pipeline.run(cfg, full=args.all)
    if args.cmd == "inbox":
        return inbox.run(cfg)
    if args.cmd == "import":
        with import_lock(cfg, blocking=True):
            return import_.run(cfg, src=args.source)
    if args.cmd == "qa":
        return qa.run(cfg, scope=args.query)
    if args.cmd == "anomaly":
        return qa.run_anomaly(cfg, scope=args.query)
    if args.cmd == "convert":
        with import_lock(cfg, blocking=True):
            return convert.run(cfg)
    if args.cmd == "init":
        return admin.init(cfg, cron=args.cron)
    if args.cmd == "uninstall":
        return admin.uninstall(cfg, purge=args.purge)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main())
