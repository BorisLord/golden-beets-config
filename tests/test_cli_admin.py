import os
import unittest
from unittest import mock

from musicrec import admin, cli
from musicrec import config as configmod
from musicrec.passes import convert, import_, pipeline, qa
from tests.base import Base


class TestCliDispatch(Base):
    """The CLI routes each subcommand to the right code path (passes/lock mocked -- no beets, no network)."""

    def test_run_routes_to_pipeline_with_full_flag(self):
        seen = {}
        with mock.patch.object(cli, "configure", lambda *a, **k: None), \
             mock.patch.object(configmod, "load", lambda: self.cfg), \
             mock.patch.object(pipeline, "run", lambda c, *, full=False, src=None: seen.update(full=full) or 0):
            self.assertEqual(cli.main(["run", "--all"]), 0)
        self.assertTrue(seen.get("full"))            # --all -> full=True

    def test_qa_routes_with_scope(self):
        seen = {}
        with mock.patch.object(cli, "configure", lambda *a, **k: None), \
             mock.patch.object(configmod, "load", lambda: self.cfg), \
             mock.patch.object(qa, "run", lambda c, scope="": seen.update(scope=scope) or 0):
            self.assertEqual(cli.main(["qa", "added:x.."]), 0)
        self.assertEqual(seen.get("scope"), "added:x..")

    def test_import_takes_lock_and_routes(self):
        with mock.patch.object(cli, "configure", lambda *a, **k: None), \
             mock.patch.object(configmod, "load", lambda: self.cfg), \
             mock.patch.object(import_, "run", lambda c, src=None: 0):
            self.assertEqual(cli.main(["import"]), 0)

    def test_convert_takes_lock_and_routes(self):
        with mock.patch.object(cli, "configure", lambda *a, **k: None), \
             mock.patch.object(configmod, "load", lambda: self.cfg), \
             mock.patch.object(convert, "run", lambda c: 0):
            self.assertEqual(cli.main(["convert"]), 0)


class TestAdminInit(Base):
    def test_init_deploys_patched_config(self):
        cenv = self.tmp / "config.env"          # point resolution at a tmp file so init won't write the repo's
        cenv.write_text('MUSIC_CLEAN="${MUSIC_CLEAN:-/x}"\n')
        with mock.patch.dict(os.environ, {"MUSICREC_CONFIG": str(cenv)}):
            self.assertEqual(admin.init(self.cfg, cron=False), 0)
        deployed = self.cfg.beetsdir / "config.yaml"
        self.assertTrue(deployed.exists())
        text = deployed.read_text()
        self.assertIn(f"directory: {self.cfg.clean}", text)                       # patched
        self.assertIn(f"log: {self.cfg.log_dir}/import-decisions.log", text)      # patched
        for d in (self.cfg.src, self.cfg.clean, self.cfg.dump, self.cfg.log_dir):
            self.assertTrue(d.is_dir())                                           # dirs created
        self.assertTrue((self.cfg.beetsdir / "qa.yaml").exists())                 # qa overlay deployed


if __name__ == "__main__":
    unittest.main()
