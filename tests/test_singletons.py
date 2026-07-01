import contextlib
import unittest
from pathlib import Path
from unittest import mock

from gbc.passes import singletons
from tests.base import Base


class TestSingletons(Base):
    def _patches(self, calls, counts=(0, 0), rc=0, bi=None):
        bi = bi or singletons.beetscfg.BeetsImport(move=True)        # default: consumed source -> re-tag in place
        return [
            mock.patch.object(singletons.artfix, "run", lambda *a, **k: 0),
            mock.patch.object(singletons, "backup_db", lambda *a, **k: None),
            mock.patch.object(singletons, "count_items", lambda *a, **k: next(counts)),
            mock.patch.object(singletons, "run_beet", lambda c, a, **k: calls.append(a) or (rc, "")),
            mock.patch.object(singletons, "_promote_complete", lambda *a, **k: 0),   # tested separately
            mock.patch.object(singletons.nova, "reroute", lambda *a, **k: 0),        # tested in test_nova
            mock.patch.object(singletons.beetscfg, "read_import", lambda c: bi),     # move-vs-copy is beets' call
        ]

    def test_missing_source_returns_1(self):
        self.assertEqual(singletons.run(self.cfg, src=self.tmp / "nope"), 1)

    def test_imports_singletons_incremental_by_default(self):
        self.cfg.src.mkdir(parents=True, exist_ok=True)
        calls = []
        with self._stack(self._patches(calls, counts=iter([5, 8]))):
            rc = singletons.run(self.cfg, apply=True)
        self.assertEqual(rc, 0)
        imp = next(c for c in calls if c and c[0] == "import")
        self.assertIn("-s", imp)                         # singleton mode
        self.assertIn("-i", imp)                         # incremental by default
        self.assertEqual(imp[-1], str(self.cfg.src))     # imports the source dir

    def test_reimport_uses_noincremental(self):
        self.cfg.src.mkdir(parents=True, exist_ok=True)
        calls = []
        with self._stack(self._patches(calls, counts=iter([0, 0]))):
            singletons.run(self.cfg, reimport=True, apply=True)
        imp = next(c for c in calls if c and c[0] == "import")
        self.assertIn("-I", imp)                         # --reimport -> noincremental
        self.assertNotIn("-i", imp)

    def test_beet_import_failure_propagates(self):
        self.cfg.src.mkdir(parents=True, exist_ok=True)
        calls = []
        with self._stack(self._patches(calls, counts=iter([0, 0]), rc=2)):
            self.assertEqual(singletons.run(self.cfg, apply=True), 2)

    def test_also_imports_imposters_when_present(self):
        self.cfg.src.mkdir(parents=True, exist_ok=True)
        (self.cfg.dump / "imposters").mkdir(parents=True, exist_ok=True)
        calls = []
        with self._stack(self._patches(calls, counts=iter([0, 0]))):
            singletons.run(self.cfg, apply=True)
        imports = [c for c in calls if c and c[0] == "import"]
        self.assertEqual(len(imports), 2)                                 # source + quarantine/imposters
        self.assertEqual(imports[0][-1], str(self.cfg.src))
        self.assertEqual(imports[1][-1], str(self.cfg.dump / "imposters"))

    def test_imposters_skipped_when_absent(self):
        self.cfg.src.mkdir(parents=True, exist_ok=True)                   # no dump/imposters dir
        calls = []
        with self._stack(self._patches(calls, counts=iter([0, 0]))):
            singletons.run(self.cfg, apply=True)
        self.assertEqual(len([c for c in calls if c and c[0] == "import"]), 1)   # source only

    # --- move-vs-copy: NEVER mutate a preserved source; back up tags before any in-place re-tag ---

    def _acoustid(self, results):
        from gbc.passes import verify as verifymod
        return [mock.patch.object(verifymod, "_acoustid_available", lambda: True),
                mock.patch.object(verifymod, "_lookup", lambda p: results)]

    def test_preserve_copy_stages_and_imports_staging_not_source(self):
        self.cfg.src.mkdir(parents=True, exist_ok=True)
        (self.cfg.src / "loose.flac").write_bytes(b"audio")
        saved, calls = [], []

        class FakeMF:
            def __init__(self, path):
                self.path = path
                self.title = self.artist = self.mb_trackid = None

            def save(self):
                saved.append(self.path)

        bi = singletons.beetscfg.BeetsImport(copy=True)
        patches = [*self._patches(calls, counts=iter([0, 1]), bi=bi), *self._acoustid(self._results()),
                   mock.patch("mediafile.MediaFile", FakeMF)]
        with self._stack(patches):
            singletons.run(self.cfg, apply=True)
        imp = next(c for c in calls if c and c[0] == "import")
        self.assertEqual(imp[-1], str(self.cfg.beetsdir / singletons.STAGING))    # imports the staging copies
        self.assertEqual((self.cfg.src / "loose.flac").read_bytes(), b"audio")    # source NEVER mutated
        self.assertEqual(len(saved), 1)
        self.assertIn(singletons.STAGING, saved[0])                               # re-tagged the COPY, not the source

    def test_preserve_symlink_skips_source_retag(self):
        self.cfg.src.mkdir(parents=True, exist_ok=True)
        (self.cfg.src / "loose.flac").write_bytes(b"audio")
        calls = []
        bi = singletons.beetscfg.BeetsImport(link=True)                           # symlink: lib references originals
        with self._stack(self._patches(calls, counts=iter([0, 0]), bi=bi)):
            singletons.run(self.cfg, apply=True)
        self.assertEqual([c for c in calls if c and c[0] == "import"], [])        # source skipped -> no import
        self.assertEqual((self.cfg.src / "loose.flac").read_bytes(), b"audio")    # untouched

    def test_staging_retags_copy_and_leaves_source_untouched(self):
        self.cfg.src.mkdir(parents=True, exist_ok=True)
        (self.cfg.src / "loose.flac").write_bytes(b"audio")
        staging = self.cfg.beetsdir / "stg"
        staging.mkdir(parents=True, exist_ok=True)
        saved = []

        class FakeMF:
            def __init__(self, path):
                self.path = path
                self.title = self.artist = self.mb_trackid = None

            def save(self):
                saved.append(self.path)

        with self._stack([*self._acoustid(self._results()), mock.patch("mediafile.MediaFile", FakeMF)]):
            fixed, left = singletons._fingerprint_retag(self.cfg, self.cfg.src, {}, set(), mock.MagicMock(),
                                                        apply=True, staging=staging)
        self.assertEqual((fixed, left), (1, 0))
        self.assertEqual((self.cfg.src / "loose.flac").read_bytes(), b"audio")    # source byte-identical
        self.assertEqual(saved, [str(staging / "loose.flac")])                    # re-tagged the staged COPY
        self.assertFalse((self.cfg.beetsdir / singletons.RETAG_BACKUP).exists())  # staging needs no backup

    def test_inplace_retag_backs_up_original_tags(self):
        import json
        d = self.cfg.dump / "imposters"
        d.mkdir(parents=True, exist_ok=True)
        (d / "x.flac").write_bytes(b"x")

        class FakeMF:
            def __init__(self, path):
                self.title, self.artist, self.mb_trackid = "OldT", "OldA", "oldid"

            def save(self):
                pass

        with self._stack([*self._acoustid(self._results()), mock.patch("mediafile.MediaFile", FakeMF)]):
            singletons._fingerprint_retag(self.cfg, d, {}, set(), mock.MagicMock(), apply=True)
        bp = self.cfg.beetsdir / singletons.RETAG_BACKUP
        self.assertTrue(bp.exists())
        rec = json.loads(bp.read_text().splitlines()[0])
        self.assertEqual((rec["title"], rec["artist"], rec["mb_trackid"]), ("OldT", "OldA", "oldid"))

    # --- _fingerprint_retag: AcoustID-first identity + dedup of loose tracks/imposters before re-import ---

    @staticmethod
    def _results(rid="mbTrue", title="TrueTitle", artist="TrueArtist", extra_ids=()):
        recs = [{"id": i, "title": title, "artists": [{"name": artist}]} for i in (rid, *extra_ids)]
        return [{"score": 0.95, "recordings": recs}]

    def test_fingerprint_retag_writes_true_recording_on_apply(self):
        from gbc.passes import verify as verifymod
        d = self.cfg.dump / "imposters"
        (d / "A" / "Alb (2001)").mkdir(parents=True, exist_ok=True)
        (d / "A" / "Alb (2001)" / "01 - wrong.flac").write_bytes(b"x")
        saved = {}

        class FakeMF:
            def __init__(self, path):
                self.title = self.artist = self.mb_trackid = None

            def save(self):
                saved.update(title=self.title, artist=self.artist, mb_trackid=self.mb_trackid)

        with mock.patch.object(verifymod, "_acoustid_available", lambda: True), \
             mock.patch.object(verifymod, "_lookup", lambda p: self._results()), \
             mock.patch("mediafile.MediaFile", FakeMF):
            fixed, left = singletons._fingerprint_retag(self.cfg, d, {}, set(), mock.MagicMock(), apply=True)
        self.assertEqual((fixed, left), (1, 0))
        self.assertEqual(saved, {"title": "TrueTitle", "artist": "TrueArtist", "mb_trackid": "mbTrue"})

    def test_fingerprint_retag_dedups_against_clean(self):
        # audio maps to mbTrue (dominant) + mbAlbum (same audio, the album's recording id). mbAlbum is in clean
        # -> NOT a new single: re-tag to mbAlbum so the import dup-skips it (the bug the user spotted).
        from gbc.passes import verify as verifymod
        d = self.cfg.dump / "imposters"
        d.mkdir(parents=True, exist_ok=True)
        (d / "dupe.flac").write_bytes(b"x")
        saved = {}

        class FakeMF:
            def __init__(self, path):
                self.title = self.artist = self.mb_trackid = None

            def save(self):
                saved["mb_trackid"] = self.mb_trackid

        with mock.patch.object(verifymod, "_acoustid_available", lambda: True), \
             mock.patch.object(verifymod, "_lookup", lambda p: self._results(extra_ids=("mbAlbum",))), \
             mock.patch("mediafile.MediaFile", FakeMF):
            fixed, left = singletons._fingerprint_retag(self.cfg, d, {}, {"mbAlbum"}, mock.MagicMock(), apply=True)
        self.assertEqual((fixed, left), (0, 0))               # not counted as a NEW single
        self.assertEqual(saved["mb_trackid"], "mbAlbum")      # tagged the IN-CLEAN id -> import dup-skips it

    def test_fingerprint_retag_dry_run_does_not_write(self):
        from gbc.passes import verify as verifymod
        d = self.cfg.dump / "imposters"
        d.mkdir(parents=True, exist_ok=True)
        (d / "x.flac").write_bytes(b"x")
        opened = []
        with mock.patch.object(verifymod, "_acoustid_available", lambda: True), \
             mock.patch.object(verifymod, "_lookup", lambda p: self._results()), \
             mock.patch("mediafile.MediaFile", lambda p: opened.append(p)):
            fixed, left = singletons._fingerprint_retag(self.cfg, d, {}, set(), mock.MagicMock(), apply=False)
        self.assertEqual((fixed, left), (1, 0))
        self.assertEqual(opened, [])              # dry-run: no file opened for writing

    def test_fingerprint_retag_leaves_unidentified_in_place(self):
        from gbc.passes import verify as verifymod
        d = self.cfg.dump / "imposters"
        d.mkdir(parents=True, exist_ok=True)
        (d / "x.flac").write_bytes(b"x")
        with mock.patch.object(verifymod, "_acoustid_available", lambda: True), \
             mock.patch.object(verifymod, "_lookup", lambda p: None):   # AcoustID can't identify
            fixed, left = singletons._fingerprint_retag(self.cfg, d, {}, set(), mock.MagicMock(), apply=True)
        self.assertEqual((fixed, left), (0, 1))

    def test_fingerprint_retag_cache_skips_repeat_lookup(self):
        from gbc.passes import verify as verifymod
        d = self.cfg.dump / "imposters"
        d.mkdir(parents=True, exist_ok=True)
        (d / "x.flac").write_bytes(b"x")
        calls = []
        with mock.patch.object(verifymod, "_acoustid_available", lambda: True), \
             mock.patch.object(verifymod, "_lookup", lambda p: calls.append(p) or None):
            singletons._fingerprint_retag(self.cfg, d, {}, set(), mock.MagicMock(), apply=False)
            resumed = verifymod.load_idcache(self.cfg)   # 2nd run RESUMES from the appended JSONL on disk
            singletons._fingerprint_retag(self.cfg, d, resumed, set(), mock.MagicMock(), apply=False)
        self.assertEqual(len(calls), 1)           # 2nd run hits the persisted cache -> AcoustID called only once

    # --- _promote_complete: robust completeness via the live MB tracklist ---

    @staticmethod
    def _ls(rows):
        # rows = [(albumid, id, mb_trackid, path)] -> the `beet ls -f` output (tracktotal column removed by the fix)
        return "\n".join("\t".join(map(str, r)) for r in rows) + "\n"

    @staticmethod
    def _no_disk_cache():
        # keep _promote_complete offline: no persisted MB tracklist read from / written to disk
        return (mock.patch.object(singletons, "load_release_cache", lambda cfg, refresh=False: {}),
                mock.patch.object(singletons, "save_release_cache", lambda cfg, cache: None))

    def test_promote_assembles_complete_album(self):
        rows = [("albX", 1, "r1", "/c/_Singles/a/1.flac"),
                ("albX", 2, "r2", "/c/_Singles/a/2.flac"),
                ("albX", 3, "r3", "/c/_Singles/a/3.flac")]
        seen = []
        lc, sc = self._no_disk_cache()
        with mock.patch.object(singletons, "run_beet", lambda c, a, **k: (0, self._ls(rows))), \
             mock.patch.object(singletons, "release_recordings", lambda aid: frozenset({"r1", "r2", "r3"})), \
             mock.patch.object(singletons, "_assemble_album", lambda *a, **k: seen.append(a[1]) or True), \
             lc, sc:
            n = singletons._promote_complete(self.cfg, mock.MagicMock(), apply=True)
        self.assertEqual(n, 1)
        self.assertEqual(seen, ["albX"])                 # the complete album was handed to the assembler

    def test_promote_incomplete_album_fetches_mb_and_rejects(self):
        # 2 loose tracks (r1,r2) but the live MB release lists 3 (r1,r2,r3). The tracktotal pre-filter is GONE,
        # so release_recordings is ALWAYS fetched; the robust check then rejects the incomplete set.
        rows = [("albX", 1, "r1", "/c/a/1.flac"),
                ("albX", 2, "r2", "/c/a/2.flac")]
        rr = mock.Mock(return_value=frozenset({"r1", "r2", "r3"}))
        seen = []
        lc, sc = self._no_disk_cache()
        with mock.patch.object(singletons, "run_beet", lambda c, a, **k: (0, self._ls(rows))), \
             mock.patch.object(singletons, "release_recordings", rr), \
             mock.patch.object(singletons, "_assemble_album", lambda *a, **k: seen.append(a[1]) or True), \
             lc, sc:
            n = singletons._promote_complete(self.cfg, mock.MagicMock(), apply=True)
        rr.assert_called()                               # MB tracklist ALWAYS fetched now (no pre-filter short-circuit)
        self.assertEqual(n, 0)                           # incomplete vs the live tracklist -> rejected
        self.assertEqual(seen, [])                       # the assembler was never invoked

    def test_promote_robust_mb_overrides_stale_tracktotal(self):
        # Only 2 recordings (r1,r2) are present but MB says the release has 3 -> incomplete, never promoted.
        rows = [("albX", 1, "r1", "/c/a/1.flac"),
                ("albX", 2, "r2", "/c/a/2.flac")]
        lc, sc = self._no_disk_cache()
        with mock.patch.object(singletons, "run_beet", lambda c, a, **k: (0, self._ls(rows))), \
             mock.patch.object(singletons, "release_recordings", lambda aid: frozenset({"r1", "r2", "r3"})), \
             mock.patch.object(singletons, "_assemble_album", lambda *a, **k: True), \
             lc, sc:
            n = singletons._promote_complete(self.cfg, mock.MagicMock(), apply=True)
        self.assertEqual(n, 0)                            # the live MB tracklist is the sole arbiter

    def test_promote_inflated_tracktotal_still_promotes_when_mb_complete(self):
        # REGRESSION for the removed count-vs-tracktotal pre-filter: a bad rip could inflate tracktotal and, under
        # the old code, block a genuinely-complete set. Completeness is now `official <= have` against the MB
        # tracklist ONLY -> every MB recording present promotes, even with extra loose tracks (bonus/alt) beyond it.
        rows = [("albX", 1, "r1", "/c/_Singles/a/1.flac"),
                ("albX", 2, "r2", "/c/_Singles/a/2.flac"),
                ("albX", 3, "r3", "/c/_Singles/a/3.flac"),
                ("albX", 4, "r4", "/c/_Singles/a/4.flac")]   # r4 is not on the MB release; must not block promotion
        seen = []
        lc, sc = self._no_disk_cache()
        with mock.patch.object(singletons, "run_beet", lambda c, a, **k: (0, self._ls(rows))), \
             mock.patch.object(singletons, "release_recordings", lambda aid: frozenset({"r1", "r2", "r3"})), \
             mock.patch.object(singletons, "_assemble_album", lambda *a, **k: seen.append(a[1]) or True), \
             lc, sc:
            n = singletons._promote_complete(self.cfg, mock.MagicMock(), apply=True)
        self.assertEqual(n, 1)                            # all MB recordings present -> promoted despite count mismatch
        self.assertEqual(seen, ["albX"])                  # the assembler received the album id

    def test_promote_unverifiable_release_left_alone(self):
        rows = [("albX", 1, "r1", "/c/a/1.flac")]
        lc, sc = self._no_disk_cache()
        with mock.patch.object(singletons, "run_beet", lambda c, a, **k: (0, self._ls(rows))), \
             mock.patch.object(singletons, "release_recordings", lambda aid: frozenset()), \
             mock.patch.object(singletons, "_assemble_album", lambda *a, **k: True), \
             lc, sc:
            n = singletons._promote_complete(self.cfg, mock.MagicMock(), apply=True)
        self.assertEqual(n, 0)                            # MB fetch failed (empty) -> never a blind promotion

    # --- _assemble_album: a failed promotion NEVER loses files, NEVER claims PROMOTED ---

    def _loose(self, *names):
        """Real source files + the (sid, tid, path) rows _assemble_album consumes."""
        srcdir = self.cfg.src / "loose"
        srcdir.mkdir(parents=True, exist_ok=True)
        items = []
        for i, name in enumerate(names, start=1):
            p = srcdir / name
            p.write_bytes(b"audio")
            items.append((i, f"r{i}", str(p)))
        return items

    def test_assemble_failed_imports_keep_files_and_return_false(self):
        # BOTH the album import and the singleton-restore import fail (rc=2). Files must survive in the
        # .gbc-assemble/<albumid> staging dir and the fn must NOT claim PROMOTED.
        items = self._loose("01.flac", "02.flac")
        log = mock.MagicMock()
        calls = []
        with mock.patch.object(singletons, "run_beet", lambda c, a, **k: calls.append(a) or (2, "")), \
             mock.patch.object(singletons, "prune_empty_dirs", lambda *a, **k: None):
            ok = singletons._assemble_album(self.cfg, "albX", items, log, apply=True)
        self.assertFalse(ok)                                                    # failed promotion -> False
        self.assertTrue(any(a and a[0] == "import" for a in calls))             # the import WAS attempted
        staging = self.cfg.beetsdir / ".gbc-assemble" / "albX"
        left = sorted(p.name for p in staging.iterdir() if p.is_file())
        self.assertEqual(left, ["01.flac", "02.flac"])                          # files preserved, not lost
        self.assertFalse(any("PROMOTED" in str(c.args) for c in log.info.call_args_list))  # never claimed done

    def test_assemble_happy_path_clears_staging_and_returns_true(self):
        # Album import succeeds and consumes the staged files -> staging cleared, PROMOTED, True.
        items = self._loose("01.flac", "02.flac")

        def fake_beet(c, a, **k):
            if a and a[0] == "import":                       # beets moves the staged files into the library
                for p in Path(a[-1]).iterdir():
                    if p.is_file():
                        p.unlink()
            return (0, "")

        log = mock.MagicMock()
        with mock.patch.object(singletons, "run_beet", fake_beet), \
             mock.patch.object(singletons, "prune_empty_dirs", lambda *a, **k: None):
            ok = singletons._assemble_album(self.cfg, "albX", items, log, apply=True)
        self.assertTrue(ok)                                                     # promotion succeeded
        self.assertFalse((self.cfg.beetsdir / ".gbc-assemble" / "albX").exists())  # staging cleared
        self.assertTrue(any("PROMOTED" in str(c.args) for c in log.info.call_args_list))

    @staticmethod
    def _stack(patches):
        es = contextlib.ExitStack()
        for p in patches:
            es.enter_context(p)
        return es


if __name__ == "__main__":
    unittest.main()
