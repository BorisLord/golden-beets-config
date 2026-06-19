import unittest
from unittest import mock

from musicrec import state
from musicrec.passes import import_, pipeline, qa
from tests.base import Base


class TestPipeline(Base):
    def test_qa_scope_follows_watermark(self):
        seen = {}

        def fake_qa(c, scope=""):
            seen["qa"] = scope
            return 0

        with mock.patch.object(import_, "run", lambda c, src=None: 0), \
             mock.patch.object(qa, "run", fake_qa):
            # first run: no watermark -> qa over whole library; watermark set afterwards
            self.assertEqual(pipeline.run(self.cfg), 0)
            self.assertEqual(seen["qa"], "")
            wm = state.get_watermark(self.cfg)
            self.assertTrue(wm)

            # second run: qa scoped to added since the watermark
            pipeline.run(self.cfg)
            self.assertTrue(seen["qa"].startswith("added:"))
            self.assertTrue(seen["qa"].endswith(".."))

            # full mode ignores the watermark
            pipeline.run(self.cfg, full=True)
            self.assertEqual(seen["qa"], "")

    def test_failed_import_aborts_before_qa_and_keeps_watermark(self):
        reached = {"qa": False}

        def fake_qa(c, scope=""):
            reached["qa"] = True
            return 0

        with mock.patch.object(import_, "run", lambda c, src=None: 2), \
             mock.patch.object(qa, "run", fake_qa):
            rc = pipeline.run(self.cfg)
        self.assertEqual(rc, 2)
        self.assertFalse(reached["qa"])                    # import failure aborts the pipeline
        self.assertIsNone(state.get_watermark(self.cfg))   # watermark NOT advanced


if __name__ == "__main__":
    unittest.main()
