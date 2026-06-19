import importlib
import py_compile
import unittest
from pathlib import Path

MODULES = [
    "musicrec", "musicrec.cli", "musicrec.config", "musicrec.logs", "musicrec.beets",
    "musicrec.lock", "musicrec.state", "musicrec.util", "musicrec.admin", "musicrec.sidecars",
    "musicrec.anomaly", "musicrec.dedup", "musicrec.passes.import_",
    "musicrec.passes.qa", "musicrec.passes.pipeline", "musicrec.passes.inbox",
    "musicrec.passes.convert",
]


class TestSmoke(unittest.TestCase):
    def test_every_module_imports(self):
        for m in MODULES:
            importlib.import_module(m)

    def test_cli_parser_builds(self):
        from musicrec.cli import _build_parser
        parser = _build_parser()
        for cmd in ("run", "inbox", "import", "qa", "anomaly", "convert", "init", "uninstall"):
            self.assertTrue(parser.parse_args([cmd]))

    def test_helpers_have_valid_syntax(self):
        helpers = sorted((Path(__file__).resolve().parents[1] / "helpers").glob("*.py"))
        self.assertTrue(helpers, "no helper scripts found")
        for h in helpers:
            py_compile.compile(str(h), doraise=True)


if __name__ == "__main__":
    unittest.main()
