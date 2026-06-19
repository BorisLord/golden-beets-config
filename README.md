# music-recovery

Recover, organize and enrich a chaotic music library (tens of thousands of loose, badly-tagged
files) into a clean **album** library you can serve with any player (e.g. Navidrome, Jellyfin, Plex).

Built on **beets**, driven by a single Python app (`musicrec`). Files are re-matched by **AcoustID
audio fingerprint** + tags, so it is robust to duplicate track numbers, download-batch folders and
untitled/failed rips — the source folder structure is ignored.

**Strategy:** import in **album mode** with `move` — only **complete, strongly-matched albums** are
moved to the clean lib; everything not matched (singletons, weak/failed rips) stays in the source =
the leftover pile to curate later (e.g. Picard). Empty shells of moved albums are pruned after import.

**One core, several doors:** `musicrec run` (manual) and `musicrec inbox` (cron, on drop) call the
**same** pipeline — only the trigger and the scope differ, never the logic.

---

## Quick start

```bash
git clone <repo-url> && cd music-recovery
./setup.sh        # checks deps, installs beets + the `musicrec` CLI (via uv), deploys config (+ optional cron)
musicrec run      # full pipeline: import -> enrich -> replaygain -> qa
```

No config needed: by default everything lives under `~/Music/beetsPipeline/` (`source/`, `clean/`,
`quarantine/`, `logs/`). Put the music to import in `source/` — matched albums **move** to `clean/`, the
rest stay in `source/` to curate. Want other paths? Edit `config.env` (created by setup) and re-run
`./setup.sh`. For ongoing additions, drop albums in `source/` and let cron auto-import them (see below).

---

## Commands

```
musicrec run [--all]        full pipeline now (import -> qa); --all = qa over the whole library
musicrec inbox              cron door: import a drop if anything is new, then the pipeline
musicrec import [SOURCE]    album-match import (art + genres + replaygain run automatically via beets)
musicrec qa [QUERY]         read-only technical audit + anomaly scan
musicrec anomaly [QUERY]    read-only name/anomaly scan only
musicrec init [--cron]      deploy config + beets config (+ schedule cron)
musicrec uninstall [--purge] remove the tooling (never your music)
```

**Incremental by default.** `run`/`inbox` keep a watermark (last successful run); enrich/replaygain/qa
are scoped to items added since then — so a run *omits what the previous run already did*. The first run
(no watermark) processes the whole library; `--all` forces that again.

---

## Configuration

`setup.sh` does all of this — this section is reference for **customizing**. Setup creates `config.env`
(gitignored) from `config.env.example` and deploys `beets/*.yaml` into `$BEETSDIR`, filling `directory:`
and the import `log:`. To change paths, edit `config.env` and re-run `./setup.sh` (or `musicrec init`):

| Var | Meaning |
|---|---|
| `BEET` | beets binary (absolute path if not on `$PATH`) |
| `BEETSDIR` | beets config dir (holds `config.yaml` + overlays + `library.db`) |
| `MUSIC_SRC` | messy source library to import **from** |
| `MUSIC_CLEAN` | clean album destination (point your player here) |
| `MUSIC_DUMP` | quarantine dir — cull here, **never `rm`** |
| `LOG_DIR` | logs (default: next to `clean/`) |

The one thing setup can't fill in: your **API keys** — set `fanarttv_key` / `lastfm_key` in
`$BEETSDIR/config.yaml` for online artwork + genres (optional; left as `REPLACE_ME` otherwise). Never commit keys.

---

## Requirements

`./setup.sh` installs **beets** and the **musicrec** CLI for you via **uv** (first choice); for the
system tools it prints the exact install command for your OS (apt/dnf/brew):

- **uv** — installs beets + musicrec. (`helpers/` run via `uv run --with mediafile --with mutagen python …`.)
- **beets**. MusicBrainz is a separate plugin → `plugins:` MUST include `musicbrainz`, otherwise `chroma`
  yields no MusicBrainz candidates and matching finds nothing.
- **fpcalc / Chromaprint** — for the `chroma` (AcoustID) fingerprint matching.
- **ffmpeg** — for ReplayGain and the QA integrity decode.
- **flac + mp3val** — for the QA file-integrity check (`beet bad`); `mp3val` is best-effort.

---

## Pipeline

`musicrec run` = **import → qa**. `library.db` is backed up before the import.

| Step | Role |
|---|---|
| **import** | Album import with **AcoustID + tags**. Only complete, strong albums are kept (`quiet`, weak matches → skip). **beets runs the enrichment natively during import** (`auto: yes`): scrub, **fetchart** (folder cover first, then web), **embedart**, **lastgenre** (genres), **ftintitle** (move "feat. X" into the title), **replaygain** (EBU R128, ffmpeg). musicrec adds **dedup** (drop duplicate audio first) + carries official sidecars (booklet/back/scan/`.lrc`) into the clean album. |
| **qa** | READ-ONLY audit: format/bitrate/WMA, duplicates, integrity (`beet bad` for mp3/flac + ffmpeg decode for every other format), **junk metadata** (URLs/EAC/LAME in comments + encoder), then the anomaly scan; ends with a conditional **ACTIONS** summary. Overlay `beets/qa.yaml`. |

Path templates (`config.yaml`): `$albumartist/$album/…`, compilations under `Various Artists/…`,
VA soundtracks under `Soundtracks/…`. `fetchart` `sources` lists `filesystem` first, so the folder cover wins.

`helpers/` fix the scrub crash on embedded images with `mime=None` (WMA/ASF). Run via uv (it pulls the
libs): `uv run --with mediafile --with mutagen python helpers/<x>.py` — `scan-scrub-crash.py` to find
them, then `mutagen-strip.py` / `strip-broken-art.py` (`ROOT=`/`EXTS=`).

**Logs:** **one** file, `$LOG_DIR/musicrec.log` (default `~/Music/beetsPipeline/logs/`), always
**appended**, every line tagged with the pass and a run id — identical whether the door was `run` or
`inbox` (no per-pass files; separating identical logs is a smell). beets' own match/skip decisions stay
in `import-decisions.log` (its native format), and the anomaly TSVs in `logs/anomalies/`.

---

## How matching works

beets identifies albums by **content, not by file/folder names** — so it copes with mis-tagged and
mis-named files. Per source folder:

1. **Group** — audio files in a folder = one album candidate (hidden + video files are ignored).
2. **Fingerprint + tags** — each candidate gets an **AcoustID** fingerprint (`chroma`/`fpcalc`) and its
   tags are read, then matched against **MusicBrainz** releases.
3. **Score** — a distance is computed (`strong_rec_thresh: 0.10` = strong); missing/extra tracks are
   penalized (`max_rec`), so only **complete** albums pass strongly.
4. **Decide** — `quiet` + `quiet_fallback: skip`: strong matches auto-accept, everything else is
   **skipped** (it never guesses).
5. **Act** — accepted → files **move** to `MUSIC_CLEAN`, reorganized by the path templates, scrubbed,
   with the folder cover + official sidecars **moved** over (a dup already in clean → quarantine, never
   deleted). Unmatched files **stay** in source = your curation pile; empty shells pruned.

**Formats:** all common types work — MP3, FLAC, M4A/AAC/ALAC, Ogg/Opus, WMA, APE, WavPack, AIFF… WAV
carries almost no tags by design, and WMA/ASF with a broken embedded image needs the `helpers/`
pre-strip first (see Lessons).

Drop complete **album folders** (not loose files); `incremental: yes` records done folders, so a re-run
only processes new drops.

---

## Auto-import (drop & go)

Out of the box nothing watches the folder — you run `musicrec run` yourself. The optional auto-import
uses **cron** (`setup.sh` / `musicrec init --cron` adds it):

```
*/15 * * * * PATH=$HOME/.local/bin:$HOME/.local/share/mise/shims:/usr/local/bin:/usr/bin:/bin musicrec inbox >/dev/null 2>&1
```

Each tick, `musicrec inbox` takes the import lock (bows out if a run is in progress), skips if there's
nothing **new** to import (it reads beet's `--pretend` plan — which beet writes to *stderr*), waits until
the drop has finished copying (debounce on folder size), then runs the **same** pipeline as `musicrec
run`. Cron and a manual run are mutually exclusive via the shared lock, so you can use either, anytime.

---

## Development

Tooling backbone is `mise` (pinned tools) + the stdlib `unittest`:

```bash
mise install        # python, uv, ruff
mise run test       # unit tests (python -m unittest discover)
mise run lint       # ruff (F,E,W,B,I,UP,RUF,SIM,PIE,RET,C4,PTH,Q @120)
mise run fix        # ruff safe auto-fixes
mise run audit      # pip-audit on runtime deps
```

The Python app is `musicrec/` (CLI in `cli.py`; passes in `musicrec/passes/`; beets driven via subprocess
in `beets.py`). Tests in `tests/` run with **no network** (a fake `beet`, tmp dirs); the live MusicBrainz/
AcoustID match is exercised manually.

---

## Uninstall

`./uninstall.sh` (or `musicrec uninstall`) removes the **tooling only** — the cron entry, logs, and
`config.env`; `--purge` also removes the beets config dir + `library.db`. It then offers to uninstall the
`musicrec` and `beets` CLIs. It **never touches your music**: `MUSIC_SRC`, `MUSIC_CLEAN`, `MUSIC_DUMP`
are left exactly as they are.

---

## Lessons / gotchas (verified in production)

- **Scrub crashes on `mime=None` images** (WMA/ASF): strip the image first with `helpers/`. **Never
  disable scrub** — it runs on every move.
- **Quarantine, don't `rm`.** Cull by moving to `$MUSIC_DUMP`; an accidental delete once destroyed
  thousands of tracks. If your FS supports snapshots (ZFS/btrfs/LVM), snapshot before any mass op, and
  `cp library.db` first. Only empty dir shells may be `rmdir`'d.
- **Never dedup on the db alone.** The same recording can legitimately be on a studio album AND a
  compilation. Correlate the files (duration + bitrate), keep the best bitrate, never break an album.
- **Compilations group by album TITLE, not albumartist**; then exclude generic titles + dominant-artist albums.
- **Back up `library.db`** before any bulk write (the passes do it automatically).
- **Never use `--from-logfile`** (parent paths + names with `;` → recursion). Import the directory directly.
- **WMA format is stored as "Windows Media"** → query `format::Windows`, not `format:WMA`.
- **`incremental` remembers *skipped* folders too.** A weak-match folder left in source won't be re-tried by
  the cron after you re-tag it — re-import it explicitly, drop it under a different path, or clear it from
  beets' history (`$BEETSDIR/state.pickle`).
- **Rate-limiting is NOT the cause of skips** (0 real 429/503 over 10k+ evaluations); skips = weak match
  + quiet mode refusing to guess. No personal AcoustID key needed (non-commercial use is free).
