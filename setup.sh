#!/bin/bash
# Bootstrap (the only bash left): check deps, install beets + the `gbc` CLI via uv, then `gbc init`.
# Everything else is the Python app. Re-run after installing anything this flags as missing.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -t 1 ]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; D=$'\e[2m'; R=$'\e[0m'; else B=; G=; Y=; D=; R=; fi
have() { command -v "$1" >/dev/null 2>&1; }

echo "${B}gbc setup${R}"
echo "dependencies:"
have beet   && echo "  ${G}[ok]${R} beets   $(beet --version 2>/dev/null | head -1)" || echo "  ${Y}[--]${R} beets   MISSING"
have fpcalc && echo "  ${G}[ok]${R} fpcalc  ${D}chromaprint / AcoustID${R}"           || echo "  ${Y}[--]${R} fpcalc  MISSING ${D}(AcoustID matching)${R}"
have ffmpeg && echo "  ${G}[ok]${R} ffmpeg  ${D}replaygain + integrity${R}"           || echo "  ${Y}[--]${R} ffmpeg  MISSING ${D}(replaygain + qa integrity)${R}"
have flac   && echo "  ${G}[ok]${R} flac    ${D}qa integrity${R}"                     || echo "  ${Y}[--]${R} flac    MISSING ${D}(qa integrity)${R}"

if ! have uv; then
  echo "  ${Y}uv is required${R} (it installs beets + gbc): curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

# beets via uv tool (extras: chroma->pyacoustid, fetchart->requests/Pillow, lastgenre->pylast,
# discogs->python3-discogs-client). --with beetcamp = the keyless `bandcamp` source (`deezer` is built in).
if ! have beet; then
  read -rp "  install beets now via 'uv tool'? [y/N] " a
  if [ "$a" = y ] || [ "$a" = Y ]; then uv tool install 'beets[chroma,fetchart,lastgenre,discogs]' --with beetcamp; fi
  export PATH="$HOME/.local/bin:$PATH"
fi

need=(); have fpcalc || need+=(fpcalc); have ffmpeg || need+=(ffmpeg); have flac || need+=(flac)
if [ ${#need[@]} -gt 0 ]; then
  echo "  ${Y}install the missing system tools (${need[*]}):${R}"
  echo "    Debian/Ubuntu : sudo apt install -y libchromaprint-tools ffmpeg flac mp3val"
  echo "    Fedora        : sudo dnf install -y chromaprint-tools ffmpeg flac mp3val"
  echo "    macOS (brew)  : brew install chromaprint ffmpeg flac"
fi
if ! have beet || [ ${#need[@]} -gt 0 ]; then
  echo "  ${Y}>> install the items above, then re-run ./setup.sh${R}"; exit 1
fi
echo "  ${G}-> all dependencies present${R}"

# install the gbc CLI (editable: tracks this repo) and deploy config
uv tool install --editable "$HERE" --force >/dev/null
export PATH="$HOME/.local/bin:$PATH"
echo "  ${G}-> installed gbc${R} ${D}($(command -v gbc))${R}"
echo
read -rp "  schedule auto-import on drop (cron, every 15 min)? [y/N] " a
if [ "$a" = y ] || [ "$a" = Y ]; then gbc init --cron; else gbc init; fi
echo
echo "${B}${G}setup complete${R} -- drop album folders in your source dir, then: ${B}gbc run${R} (or wait for cron)."
