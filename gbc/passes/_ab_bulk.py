"""Bulk-apply AcousticBrainz fields to the beets library in ONE process (vs one `beet modify` per recording,
the acousticbrainz pass's real cost). Run by the BEETS venv's python (gbc's venv can't `import beets`):

    <beets-python> _ab_bulk.py <library.db> <fields.json>      fields.json = {mb_trackid: {field: value}, ...}

Mirrors `beet modify`: db store (incl. flex attrs) + native tags (bpm/initial_key) to the file. Prints the
count updated. Imports only beets + stdlib (never gbc -- different venv).
"""
import json
import sys
from pathlib import Path

from beets.library import Library


def main() -> int:
    db_path, fields_path = sys.argv[1], sys.argv[2]
    with Path(fields_path).open(encoding="utf-8") as fh:
        data = json.load(fh)

    lib = Library(db_path)
    by_mbid: dict = {}
    for item in lib.items():                       # index every item by its recording id (one DB read)
        mb = item.get("mb_trackid")
        if mb:
            by_mbid.setdefault(mb, []).append(item)

    updated = 0
    written = []
    with lib.transaction():
        for mbid, fields in data.items():
            for item in by_mbid.get(mbid, []):     # a recording can sit on several albums -> several items
                for key, value in fields.items():
                    item[key] = value
                item.store()
                written.append(item)
                updated += 1
    failed = 0
    for item in written:                           # try_write OUTSIDE the txn: don't hold the SQLite write lock
        try:
            item.try_write()                       # best-effort: the committed DB store is the source of truth
        except Exception:                          # one bad file must not abort the rest or hide the store success
            failed += 1
    print(updated)                                 # ALWAYS report the store count, even if some tag writes failed
    if failed:
        print(f"tag-write failed for {failed} item(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
