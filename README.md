# golden-beets-config

Turn a chaotic music library — tens of thousands of loose, mis-tagged files — into a clean **album**
library you can serve with any player (Navidrome, Jellyfin, Plex, any Subsonic/DLNA).

At its core is a documented [beets](https://beets.io) config for album-mode recovery; a small Python CLI
(`gbc`) wraps it with the orchestration a config can't express (dedup, sidecars, QA, format conversion). Albums are matched by **AcoustID audio fingerprint** + tags — robust to duplicate track
numbers, download-batch folders and untitled rips, because the source folder structure is ignored.

## Golden beets config

[`golden-beets-config.yaml`](golden-beets-config.yaml) is that config: one beets config for the recovery
described here, every setting commented:

- **Album mode + AcoustID** — identify by audio content, not by file/folder names.
- **`quiet` + `quiet_fallback: skip`** — auto-accept only *strong* matches; never guess.
- **Complete albums only** — missing/extra tracks can't be strong (`max_rec`), so partial rips are left
  for manual curation rather than mis-filed.
- **Native enrichment on import** (`auto: yes`) — cover art, MusicBrainz genres, ReplayGain (R128),
  ftintitle, scrub.
- **`zero` junk cleaning** — always blanks `encoder`; blanks junk `comments`/`grouping` (URLs, "ripped by",
  ripper names) only on a pattern match, so a real note ("Live at…") is kept.
- **Sane paths** — `Various Artists/` for compilations, `Soundtracks/` for VA OSTs, continuous numbering
  for multi-disc (no track-number collisions).

Copy it to `~/.config/beets/config.yaml`, set `directory:`, and it works **standalone** for ~80% of the
value. The `gbc` CLI adds the rest (below).

## Quick start

```bash
git clone <repo-url> && cd golden-beets-config
./setup.sh     # checks deps, installs beets + the gbc CLI (via uv), deploys the config (+ optional cron)
gbc run        # run the pipeline: import → albumdedup → convert → verify → acousticbrainz → qa → reclaim
```

No config needed: by default everything lives under `~/Music/beetsPipeline/` (`source/`, `clean/`,
`quarantine/`, `logs/`). Drop **album folders** in `source/` — matched albums go to `clean/` (**moved** or
**copied** per beets `import.move`/`copy`), the rest stay in `source/` to curate. For other paths, edit
`config.env` (created by setup) and re-run `./setup.sh`.

## Commands

```
gbc run [--all] [--reimport]   pipeline now (import → albumdedup → convert → verify → acousticbrainz → qa → reclaim); --all re-checks all, --reimport re-tries seen folders
gbc inbox               cron door: import a fresh drop if anything is new, then the pipeline
gbc import [SOURCE] [--reimport]   album-match import only (--reimport re-tries already-seen folders)
gbc qa [QUERY]          read-only technical audit + anomaly scan
gbc anomaly [QUERY]     read-only name/anomaly scan only
gbc verify [QUERY]      quarantine imposter tracks (audio ≠ tagged recording) via AcoustID
gbc reclaim             copy-mode: move fully-verified source albums to quarantine (per album, all tracks ok)
gbc acousticbrainz [QUERY]   fetch BPM/key/mood metadata from AcousticBrainz (network-only; bpm+key → file tags)
gbc convert             normalise formats in the clean lib: WMA→AAC, WAV/AIFF→FLAC (originals → quarantine)
gbc init [--cron]       (re)deploy config + beets overlays (+ schedule cron)
gbc uninstall [--purge] remove the tooling (never your music)
```

**Incremental by default:** `run`/`inbox` keep a watermark of the last successful run, so verify +
acousticbrainz + qa only touch what's new (import is incremental via beets). `--all` re-checks everything;
the first run has no watermark and covers the whole library.

## How it works

`gbc run` runs the **same pipeline**, in this order:

    import  →  albumdedup  →  convert  →  verify  →  acousticbrainz  →  qa  →  reclaim

| pass | what it does |
|---|---|
| **import** | AcoustID + MusicBrainz match, scrub, art/genres/ReplayGain; only complete, strong albums are kept |
| **albumdedup** | same album imported twice (MB vs Discogs) → quarantine the lesser copy. **Runs first**: needs only metadata, so the later passes never process a dropped duplicate |
| **convert** | WMA → Opus, WAV/AIFF → FLAC, so every later pass operates on the final files |
| **verify** | re-fingerprint each track → quarantine imposters (audio ≠ tagged recording) + log tag mismatches |
| **acousticbrainz** | add BPM / key / mood / danceable metadata (file tags + db flex attrs) |
| **qa** | audit + cull corrupt / undecodable files → quarantine |
| **reclaim** | copy-mode only: drain a source album to quarantine once every track is verified `ok` |

`library.db` is backed up before any file-moving pass. `run` (manual) and `inbox` (cron) call this same
pipeline — only the trigger and scope differ.

**gbc follows the beets import op.** Whether the source is consumed or kept is beets' `import.move`/`copy`
decision, read from `beet config` — gbc adapts (`gbc/beetscfg.py`):

- **move** (or `copy`+`delete`) — source is the leftover pile: dedup, sidecars and shell-pruning run (below).
- **copy / reflink / hardlink** (or symlink/in-place) — source stays **read-only**: dedup/sidecars/pruning
  are all skipped. The **reclaim** pass then drains the source safely: per album, once **every** track has a
  clean copy positively verified by AcoustID (`verify` = `ok`), the whole source folder is moved to
  quarantine (`$MUSIC_DUMP`, never deleted). Partially-matched or any-track-unverified albums stay intact in
  source. (Symlink/in-place never reclaim — clean would dangle.)

**Import** — per source folder:

1. **Group** the audio files into one album candidate (hidden + video files ignored).
2. **Fingerprint** each with AcoustID (`chroma`/`fpcalc`) and read its tags, then match against MusicBrainz.
3. **Score** the match as a distance (`strong_rec_thresh: 0.20`); missing/extra tracks are penalised
   (`max_rec`) so only **complete** albums score strongly.
4. **Decide** — `quiet` + `quiet_fallback: skip`: strong matches auto-accept, everything else is **skipped**
   (never guessed; left in `source/` to curate).
5. **Act** — accepted albums land in the clean library (moved or copied per `import.move`/`copy`),
   reorganised by the path templates and scrubbed, with native enrichment (art, genres, ReplayGain,
   ftintitle) applied during the import. **In move mode only**, `gbc` adds **dedup** before (drop duplicate
   audio, keep best bitrate) and carries official sidecars (booklet/back/scan/`.lrc`) into the album after;
   empty source shells are pruned. **In copy mode the source is untouched** (sidecars stay with it) — see
   reclaim below. A dup already in clean goes to quarantine, never deleted.

**Verify** — re-fingerprints each accepted track and treats it as an **imposter** (a file with the right
title/duration/tags but whose audio isn't the matched recording) only when AcoustID is conclusive that the
file has no match *and* the official recording is itself known to AcoustID. A conclusive imposter is **moved
to quarantine** (`$MUSIC_DUMP`, recoverable, never deleted) and dropped from the lib, so clean stays clean.
Any rate-limit/timeout → inconclusive, left alone (never acted on); verdicts are cached. (Closes the
album-mode blind spot: `chroma` gives no penalty to a track it can't identify.)

**QA** (read-only) — format/bitrate, WMA, duplicates, integrity (`beet bad` + ffmpeg decode + RIFF-in-`.mp3`
detection), junk metadata, and a name/anomaly scan; ends with a conditional **ACTIONS** summary. Overlay:
`beets/qa.yaml`.

**Reclaim** (copy/reflink/hardlink mode only) — drains the source once each track is proven safe. Per source
album, when **every** track has a clean copy that `verify` confirmed `ok`, the whole folder is moved to
`$MUSIC_DUMP` (never deleted). clean↔source correlation reuses the `sidecars` duration-multiset match; a
partially-matched, any-track-unverified, multi-disc or otherwise ambiguous album is left intact in source.
No-op in move/delete mode (beets already consumed the source) and in symlink/in-place mode (clean would dangle).

**Logs** — **one** file, `$LOG_DIR/gbc.log`, append-only, every line tagged with the pass + a run id
(identical whether triggered by `run` or `inbox`). beets' own match/skip decisions stay in
`import-decisions.log`; anomaly TSVs in `logs/anomalies/`.

**Formats** — MP3, FLAC, M4A/AAC/ALAC, Ogg/Opus, WMA, APE, WavPack, AIFF… all work. WAV carries almost no
tags (so an untagged WAV rarely matches); a WMA/ASF file with a broken embedded image needs the `helpers/`
pre-strip first (see Gotchas).

## Configuration

`setup.sh` does all of this — this is reference for **customising**. It creates `config.env` (gitignored)
from `config.env.example` and deploys `beets/*.yaml` into `$BEETSDIR`. Edit `config.env` and re-run
`./setup.sh` (or `gbc init`) to change paths:

| Var | Meaning |
|---|---|
| `BEET` | beets binary (absolute path if not on `$PATH`) |
| `BEETSDIR` | beets config dir (`config.yaml` + overlays + `library.db`) |
| `MUSIC_SRC` | messy source library to import **from** |
| `MUSIC_CLEAN` | clean album destination (point your player here) |
| `MUSIC_DUMP` | quarantine — cull here, **never `rm`** |
| `LOG_DIR` | logs (default: next to `clean/`) |

The one thing setup can't fill in: **API keys** — set `fanarttv_key` / `lastfm_key` in
`$BEETSDIR/config.yaml` for online artwork + genres (optional; left as `REPLACE_ME` otherwise). Never commit keys.

## Requirements

`./setup.sh` installs **beets** + the **gbc** CLI via **uv**, and prints the OS install command
(apt/dnf/brew) for the system tools:

- **uv** — installs beets + gbc.
- **fpcalc / Chromaprint** — AcoustID fingerprint matching.
- **ffmpeg** — ReplayGain, the QA integrity decode, and `gbc convert`.
- **flac + mp3val** — the QA file-integrity check (`beet bad`; `mp3val` is best-effort).

`musicbrainz` MUST stay in the config's `plugins:` — it's a separate metadata-source plugin; without it
`chroma` yields no MusicBrainz candidates and matching finds nothing.

## Auto-import (drop & go)

Nothing watches the folder by default. The optional cron (`setup.sh` / `gbc init --cron`) adds:

```
*/15 * * * * PATH=$HOME/.local/bin:$HOME/.local/share/mise/shims:/usr/local/bin:/usr/bin:/bin gbc inbox >/dev/null 2>&1
```

Each tick, `gbc inbox` takes the import lock (bows out if a run is in progress), skips unless there's
something **new** (it reads beet's `--pretend` plan — which beet writes to *stderr*), waits for the drop to
finish copying (debounce on folder size), then runs the same pipeline. Cron and manual runs are mutually
exclusive via the shared lock.

## Development

```bash
mise install     # python, uv, ruff
mise run test    # unit tests (stdlib unittest, no network — fake beet + tmp dirs)
mise run lint    # ruff (F,E,W,B,I,UP,RUF,SIM,PIE,RET,C4,PTH,Q @120)
mise run fix     # ruff safe auto-fixes
mise run audit   # pip-audit on runtime deps
```

The app is `gbc/` (CLI in `cli.py`, passes in `gbc/passes/`, beets driven via subprocess in `beets.py`).
The live MusicBrainz/AcoustID match is exercised manually.

## Uninstall

`./uninstall.sh` (or `gbc uninstall`) removes the **tooling only** — cron entry, logs, `config.env`;
`--purge` also removes the beets config dir + `library.db`, then offers to uninstall the `gbc` and `beets`
CLIs. It **never touches your music** (`MUSIC_SRC`, `MUSIC_CLEAN`, `MUSIC_DUMP`).

## Gotchas (verified in production)

- **Quarantine, never `rm`.** Cull by moving to `$MUSIC_DUMP` — an accidental delete once destroyed
  thousands of tracks. Snapshot first if your FS supports it (ZFS/btrfs/LVM); only empty shells may be
  `rmdir`'d. (The passes back up `library.db` before any bulk write automatically.)
- **Scrub crashes on `mime=None` images** (WMA/ASF): strip the image first with `helpers/`
  (`scan-scrub-crash.py` to find them, then `mutagen-strip.py` / `strip-broken-art.py`, run via
  `uv run --with mediafile --with mutagen python helpers/<x>.py`). Never disable scrub.
- **Never dedup on the db alone.** The same recording can legitimately be on a studio album AND a
  compilation — correlate the files (duration + bitrate), keep the best bitrate, never break an album.
- **Compilations group by album TITLE, not albumartist** (then exclude generic titles + dominant-artist albums).
- **Never use `--from-logfile`** (parent paths + names with `;` cause recursion) — import the directory directly.
- **WMA is stored as "Windows Media"** → query `format::Windows`, not `format:WMA`.
- **`incremental` remembers *skipped* folders too.** A weak-match folder you re-tag won't be retried by
  cron — re-run with `gbc run --reimport` (beets `-I`), drop it under a new path, or clear beets' history
  (`$BEETSDIR/state.pickle`).
- **Rate-limiting is NOT the cause of skips** (0 real 429/503 over 10k+ evaluations) — skips = weak match
  + quiet mode refusing to guess. No personal AcoustID key needed (non-commercial use is free).
