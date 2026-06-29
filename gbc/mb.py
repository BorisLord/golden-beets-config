"""Tiny MusicBrainz read client (no key needed). Shared by passes; not coupled to any one feature (deleting
nova leaves it intact)."""
import json
import time
import urllib.error
import urllib.request

_UA = "gbc/0.8 (golden-beets-config)"
_BASE = "https://musicbrainz.org/ws/2/"


def get(path: str, retries: int = 4):
    """GET a MusicBrainz endpoint as JSON, with backoff retry on transient errors (MB 503s under its rate
    limiter are routine). A 4xx (bad id) is not retried. Raises the last error if all attempts fail."""
    req = urllib.request.Request(_BASE + path, headers={"User-Agent": _UA})
    last: Exception = RuntimeError("no attempt made")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                raise                       # client error (e.g. bad/absent id) -> retrying won't help
            last = e
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            last = e
        time.sleep(2 ** attempt)
    raise last


def release_recordings(albumid: str) -> frozenset:
    """Every recording MBID on a release (all discs). Empty frozenset on fetch error -> caller treats it as
    'cannot verify' and leaves the album alone (never a wrong promotion)."""
    try:
        data = get(f"release/{albumid}?inc=recordings&fmt=json")
    except (urllib.error.URLError, OSError, ValueError):
        return frozenset()
    time.sleep(1.1)                                  # MB rate limit (~1 req/s)
    return frozenset(t["recording"]["id"]
                     for m in data.get("media", []) for t in m.get("tracks", [])
                     if t.get("recording", {}).get("id"))


def missing_recordings(albumid: str, present_trackids, cache: dict | None = None):
    """The release's recordings NOT present in `present_trackids`, or None if the tracklist can't be fetched
    (caller leaves the album alone). Empty frozenset = the album is COMPLETE. `cache` (albumid -> recordings)
    avoids re-fetching the same release across albums. Shared by verify (demote incomplete) + singletons
    (promote complete) so both judge completeness against the live MB tracklist the same way."""
    if cache is not None and albumid in cache:
        official = cache[albumid]
    else:
        official = release_recordings(albumid)
        if cache is not None:
            cache[albumid] = official
    if not official:
        return None
    return official - frozenset(present_trackids)
