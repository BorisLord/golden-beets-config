# AGENTS.md — golden-beets-config (operational brief)

Check the official docs. Test every feature. These rules exist so past incidents aren't repeated. Full docs: **README.md**.

## What

A single Python app (**`gbc`**) drives **beets** (via subprocess) to recover a chaotic library into a clean
**album** library (any Subsonic/DLNA player). Album-mode + AcoustID: only complete, strong albums are kept;
loose singletons stay in the source for manual curation.

## Architecture

`gbc run` (manual) and `gbc inbox` (cron) call the same pipeline (`gbc/passes/pipeline.py`):
**import → albumdedup → convert → verify → acousticbrainz → qa → reclaim**.

beets does art/genres/replaygain/scrub/ftintitle natively during `beet import` (`auto: yes`). gbc adds:
**artfix** (pre-import: strip mime=None WMA art so scrub can't crash), **albumdedup** (cross-source duplicate
albums), **dedup**/**sidecars** (move mode only), **convert** (WMA→Opus, WAV/AIFF→FLAC, ALAC→FLAC, BEFORE
verify), **verify** (AcoustID imposter → quarantine), **acousticbrainz** (network BPM/key/mood), **qa**
(audit; culls corrupt files), **reclaim**. beets is driven via `beets.run_beet` (captures stdout **and**
stderr — `--pretend` plan goes to stderr). config in `config.py`, logger in `logs.py`, import lock (filelock)
in `lock.py`, watermark in `state.py`. `setup.sh` is the only bash.

- **Move-vs-copy is beets' decision** (`beetscfg.py` reads `beet config`). **Source CONSUMED** (`move`, or
  `copy`+`delete`): dedup → sidecars → prune run. **Source PRESERVED** (`copy`/`reflink`/`hardlink`/symlink/
  in-place): source is READ-ONLY, those skipped. **reclaim** runs only in preserve+`clean_independent`
  (copy/reflink/hardlink, never symlink/in-place): per album, when every track is `verify`-verdict `ok`, the
  whole source folder moves to `$MUSIC_DUMP` (never deleted); partial/unverified → kept. clean↔source by
  duration-multiset (ambiguous/multi-disc → kept, never guessed).
- **Quarantine layout** (`sidecars.quarantine_dir`): `<reason>/<Albumartist>/<Album (Year)>/…`. Reasons:
  `imposters` (verify), `duplicates` (dedup/albumdedup), `redundant-art` (sidecars), `shells` (prune,
  audio-less → source folder name), `converted` (convert originals), `corrupt` (qa cull), `reclaimed`
  (verified-good source originals — purge when confident). Good (reclaimed) never mixes with bad.
- **Logs:** one append-only `$LOG_DIR/gbc.log`, every line tagged `[pass]` + run id. beets' own decisions go
  to `import-decisions.log`.
- **Incremental:** qa scopes to items added since the last successful run (watermark); `--all` reprocesses.
  import is incremental (beets `incremental: yes`).
- **Tooling:** `mise run test|lint|fix|audit`; stdlib **unittest** (no pytest), no network.

## Config

Paths come from `config.env` (copy of `config.env.example`, gitignored) — use the vars, never hardcode:
`BEET`, `BEETSDIR`, `MUSIC_SRC`, `MUSIC_CLEAN`, `MUSIC_DUMP` (never `rm`), `LOG_DIR`. `config.py` resolves
`$GBC_CONFIG` / `~/.config/gbc/` / repo root.

## CRITICAL RULES (learned the hard way)

1. **Never delete → move to `$MUSIC_DUMP`** (an `rm` once destroyed thousands of tracks). Snapshot
   (ZFS/btrfs/LVM) before mass ops; always `cp library.db` first. Only empty dir shells may be `rmdir`'d.
2. **Never bulk `modify`/`move`/`remove` without a query** (empty = whole library). Back up `library.db` first.
3. **Confirm before any irreversible op**, in the same turn.
4. **Test on ~10 items** before the whole library.
5. **Scrub is mandatory on every move.** The `mime=None` crash (WMA/ASF) → strip the art FIRST via `helpers/`
   (`scan-scrub-crash.py` → `mutagen-strip.py`/`strip-broken-art.py`, run `uv run --with mediafile --with
   mutagen python helpers/<x>.py`). Never disable scrub.
6. **Never `--from-logfile`** (parent paths + `;` in names → recursion). Import the directory directly.
7. **`musicbrainz` must be in `plugins:`** — without it `chroma` yields no MB candidates and fingerprinting
   silently finds nothing.
8. **Never dedup on the db alone (`mb_trackid`)** — the same recording on a studio album AND a compilation is
   legitimate. Disk-correlate (duration + bitrate), keep best bitrate, never break an album folder.
9. **Compilations grouped by album TITLE, not albumartist**; exclude generic titles + dominant-artist albums.
   "comp-heavy" (a hit on many comps) is NOT a comp signal.

## Verified facts (do not re-debate)

- **Rate-limiting is NOT the cause of skips** (0 real 429/503 over 10k+ evals); skips = weak match + quiet mode
  refusing to guess. No personal AcoustID key needed (non-commercial is free).
- **WMA** is stored as "Windows Media" → query `format::Windows`, not `format:WMA`.
- **AcousticBrainz is frozen, not dead:** the read API (`/api/v1/{low,high}-level?recording_ids=`, keyed by
  `mb_trackid`) still serves every recording it analysed (high coverage on MB-matched tracks). Cheap network
  pass — NOT `beets-xtractor`/Essentia (local DSP, CPU-heavy). Do NOT use beets' built-in `acousticbrainz`
  plugin: deprecated, and its `initial_key` "F# major" is mangled to "F" by beets' `MusicalKey` regex
  `[\W\s]+major` — our pass emits canonical "F#"/"F#m".
- **acousticbrainz keeps a CURATED 14-field subset:** `bpm`+`initial_key` → file tags (Subsonic-visible); 7
  `mood_*` + `danceable` + `tonal` + `key_strength` (floats, typed via `types` so `mood_relaxed:0.9..` works)
  + `moods_mirex` + `voice_instrumental` (strings) → db flex attrs. Deliberately DROP: genre taxonomies,
  gender, timbre, ballroom rhythm, chord stats, average_loudness (redundant with ReplayGain), low-level DSP.

## Secrets

API keys in `beets/config.yaml` are redacted (`REPLACE_ME`); supply your own locally.

## Style

Concise, English. No emoji unless asked. `file:line` pointers, not pasted code. Confirm in one word. Never
`git commit` without explicit approval.
