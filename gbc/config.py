"""Config: the config.env shell vars, parsed in Python.

config.env stays shell-syntax (`VAR="${VAR:-default}"`); we source it in a subshell so its `${VAR:-default}`
and any inline env override behave exactly as in shell. No config.env -> built-in defaults.
Resolution: $GBC_CONFIG, ~/.config/gbc/config.env, <repo>/config.env (repo root for the editable install).
"""
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_VARS = ("BEET", "BEETSDIR", "MUSIC_SRC", "MUSIC_CLEAN", "MUSIC_DUMP", "LOG_DIR")
REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Config:
    beet: str
    beetsdir: Path
    src: Path
    clean: Path
    dump: Path
    log_dir: Path

    @property
    def library(self) -> Path:
        return self.beetsdir / "library.db"

    def overlay(self, name: str) -> Path:
        return self.beetsdir / name


def _defaults() -> dict:
    home = Path.home()
    base = home / "Music" / "beetsPipeline"
    clean = base / "clean"
    return {
        "BEET": "beet",
        "BEETSDIR": str(home / ".config" / "beets-rebuild"),
        "MUSIC_SRC": str(base / "source"),
        "MUSIC_CLEAN": str(clean),
        "MUSIC_DUMP": str(base / "quarantine"),
        "LOG_DIR": str(clean.parent / "logs"),
    }


def config_path() -> Path | None:
    env = os.environ.get("GBC_CONFIG")
    candidates = [Path(env)] if env else []
    candidates += [Path.home() / ".config" / "gbc" / "config.env", REPO_ROOT / "config.env"]
    return next((p for p in candidates if p.is_file()), None)


def _source_env(path: Path) -> dict:
    """Source config.env in bash, read back effective values (honours ${VAR:-default} + env). RAISES on a
    sourcing failure -- a config.env typo must fail loudly, never silently fall back to the built-in defaults
    (this tool MOVES files; operating on the wrong dirs is dangerous)."""
    bash = shutil.which("bash") or shutil.which("sh")
    if not bash:
        raise RuntimeError(f"no bash/sh available to source {path}")
    # path passed as $1 (not interpolated) -> no shell injection via a weird path.
    # `. "$1" || exit 3`: a source/syntax error aborts (without it the later printf still returns rc=0).
    script = 'set -a; . "$1" || exit 3; ' + "".join(f'printf "%s\\0" "${v}"; ' for v in _VARS)
    out = subprocess.run([bash, "-c", script, "_", str(path)], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"failed to source {path} (rc={out.returncode}): {out.stderr.strip()}")
    parts = out.stdout.split("\0")
    return {v: parts[i].strip() for i, v in enumerate(_VARS) if i < len(parts) and parts[i].strip()}


def load() -> Config:
    values = _defaults()
    path = config_path()
    if path:
        values.update(_source_env(path))
    return Config(
        beet=values["BEET"],
        beetsdir=Path(values["BEETSDIR"]).expanduser(),
        src=Path(values["MUSIC_SRC"]).expanduser(),
        clean=Path(values["MUSIC_CLEAN"]).expanduser(),
        dump=Path(values["MUSIC_DUMP"]).expanduser(),
        log_dir=Path(values["LOG_DIR"]).expanduser(),
    )
