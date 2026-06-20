import json
import unittest
from pathlib import Path
from unittest import mock

from gbc.passes import verify
from tests.base import Base


class TestVerify(Base):
    def _items(self, specs):
        """specs: [(stem, mbid)] -> create the files (so stat() works) + return the fake `beet ls` text."""
        lines = []
        for stem, mbid in specs:
            (self.tmp / f"{stem}.m4a").write_bytes(b"x")
            lines.append(f"{self.tmp / f'{stem}.m4a'}{verify.SEP}{mbid}")
        return "\n".join(lines)

    def test_quarantines_only_conclusive_imposters(self):
        text = self._items([("a", "mbA"), ("b", "mbB"), ("c", "mbC"), ("d", "mbD")])
        # a: genuine match -> kept ; b: no-match + official KNOWN -> IMPOSTER (quarantined) ;
        # c: throttled -> inconclusive (kept) ; d: no-match + official ALSO unknown -> rare/genuine (kept)
        fv = {"a": ("ok", True), "b": ("ok", False), "c": ("error", False), "d": ("ok", False)}
        of = {"mbB": True, "mbD": False}
        with mock.patch.object(verify, "_acoustid_available", lambda: True), \
             mock.patch.object(verify, "run_beet", lambda *a, **k: (0, text)), \
             mock.patch.object(verify, "_file_verdict", lambda p, m: fv[Path(p).stem]), \
             mock.patch.object(verify, "_official_known", lambda m: of.get(m)):
            n = verify.run(self.cfg)
        self.assertEqual(n, 1)                                    # only the real imposter (b)
        self.assertFalse((self.tmp / "b.m4a").exists())           # imposter moved out of "clean"
        for stem in ("a", "c", "d"):
            self.assertTrue((self.tmp / f"{stem}.m4a").exists())  # genuine / inconclusive kept

    def test_skips_cleanly_without_pyacoustid(self):
        with mock.patch.object(verify, "_acoustid_available", lambda: False):
            self.assertEqual(verify.run(self.cfg), 0)

    def test_imposter_cached_inconclusive_not_cached(self):
        text = self._items([("b", "mbB"), ("c", "mbC")])      # b -> imposter, c -> inconclusive (throttled)
        fv = {"b": ("ok", False), "c": ("error", False)}
        with mock.patch.object(verify, "_acoustid_available", lambda: True), \
             mock.patch.object(verify, "run_beet", lambda *a, **k: (0, text)), \
             mock.patch.object(verify, "_file_verdict", lambda p, m: fv[Path(p).stem]), \
             mock.patch.object(verify, "_official_known", lambda m: True):
            verify.run(self.cfg)
        cache = json.loads((self.cfg.beetsdir / "gbc-verify-cache.json").read_text())
        self.assertEqual(list(cache.values()), ["imposter"])  # imposter cached; inconclusive deliberately not


if __name__ == "__main__":
    unittest.main()
