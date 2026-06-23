"""Drive beets via subprocess -- the canonical interface.

beets writes much of its output (incl. the `--pretend` plan) to STDERR, so by default we merge stdout+stderr,
log every line (tagged by pass), and return the full text for callers to parse. BEETSDIR is always set in the
child env.
"""
import os
import subprocess

from .config import Config
from .logs import get_logger


def run_beet(cfg: Config, args, *, overlay: str | None = None, passname: str,
             echo_lines: bool = True, merge_stderr: bool = True) -> tuple[int, str]:
    """Run `beet [-c overlay] <args...>` -> (returncode, merged_output_text).
    echo_lines=False captures silently (for `ls`/counts/queries we parse rather than dump to the log)."""
    log = get_logger(passname)
    cmd = [cfg.beet]
    if overlay:
        cmd += ["-c", str(cfg.overlay(overlay))]
    cmd += [str(a) for a in args]
    env = dict(os.environ, BEETSDIR=str(cfg.beetsdir))
    log.info("$ %s", " ".join(cmd))
    lines: list[str] = []
    # merge_stderr=False keeps stdout CLEAN for callers parsing structured output (e.g. `beet config` YAML,
    # which beet's stderr warnings corrupt).
    err = subprocess.STDOUT if merge_stderr else subprocess.DEVNULL
    try:
        # surrogateescape: non-UTF-8 file names round-trip identically to reclaim's sqlite BLOB decode (so
        # verdict keys match), and a stray byte never crashes the capture.
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err,
                              text=True, errors="surrogateescape", bufsize=1, env=env) as proc:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip("\n")
                lines.append(line)
                if echo_lines and line.strip():
                    log.info("%s", line)
    except FileNotFoundError as e:
        raise RuntimeError(f"beet not found ({cfg.beet!r}) -- install beets or run ./setup.sh") from e
    return proc.returncode, "\n".join(lines)
