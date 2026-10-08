"""Official "see your real ballot" links, and the check that keeps them honest.

The state ballot digest deliberately shows only the statewide portion of a
ballot: a ballot is defined per ballot style, not per state, so a U.S.
House district, county offices, judicial retentions and local questions
cannot appear on a state page without misstating somebody's ballot. The
link out to the voter's own election office is therefore not decoration —
it is the part of the design that makes the omission honest. (The
user-facing version of this is the About page's "State Ballots & Ballot
Measures" section.)

Which is exactly why the per-state deep links are GATED. A 404 on that
link, in election week, from a URL Civitas vouched for, strands the user
at the moment the page has just told them to go elsewhere. So:

  - `state_ballot_lookup.json` ships with a national directory (USAGov's
    state-election-office finder) that is stable and maintained by someone
    else, and it is always available as a target.
  - Per-state entries are served ONLY once `verified_at` is set, which
    only `refresh_link_verification()` below does, and only after the URL
    actually resolved. An unverified entry is invisible to users.

Same read path as the PVI files (score_calculator._read_pvi_json): prefer
the writable volume's refreshed copy, fall back to the image-baked one.
app/data/ is COPY'd into the Docker image and is NOT writable at runtime,
so the verifier writes /data/ and this module reads both.
"""

import json
import logging
import os
from typing import Any

import httpx

from app.atomic_write import write_text_atomic
from app.file_cache import Stamp, read_json, read_json_preferring, reload_if_moved, new_reload_lock
from app.http_client import is_bot_challenge
from app.pipeline.fetch.http_utils import BROWSER_HEADERS
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

_reload_lock = new_reload_lock()

_BUNDLED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "state_ballot_lookup.json")
_VOLUME_PATH = "/data/state_ballot_lookup.json"

# Populated on first read, cleared by refresh_link_verification() after it
# writes — an accessor cached for the process lifetime without that
# invalidation would serve the pre-verification copy until the container
# restarted (the bug district_pvi.py's explicit cache reset exists to
# avoid). That clear only reaches the writer's own process; the API
# processes, which serve lookup_for_state, reload when the volume copy's
# mtime changes (file_cache.files_stamp).
_cache: dict[str, Any] | None = None
_cache_stamp: Stamp = None

_LINK_CHECK_TIMEOUT_S = 10.0


def _read() -> dict[str, Any]:
    """The volume copy, else the bundled one (file_cache.read_json_preferring:
    a volume copy that exists but can't be read right now falls back without
    being kept)."""
    return read_json_preferring(_VOLUME_PATH, _BUNDLED_PATH, default={})


def _load() -> dict[str, Any]:
    global _cache, _cache_stamp
    with _reload_lock:
        _cache, _cache_stamp = reload_if_moved([_VOLUME_PATH], _cache, _cache_stamp, _read)
        return _cache


def invalidate_cache() -> None:
    global _cache
    _cache = None


def lookup_for_state(state: str) -> dict[str, Any]:
    """The best available official-ballot link for `state`.

    Always returns something usable: a verified per-state entry when one
    exists, otherwise the national directory. `isStateSpecific` tells the
    frontend which it got, so the page can word the link accurately
    instead of promising a state lookup it doesn't have.
    """
    data = _load()
    fallback = data.get("national_fallback") or {}
    entry = (data.get("states") or {}).get(state.upper())

    if entry and entry.get("verified_at") and entry.get("url"):
        return {
            "url": entry["url"],
            "label": entry.get("label") or "Official state ballot lookup",
            "sourceName": entry.get("source_name") or "",
            "isStateSpecific": True,
            "verifiedAt": entry["verified_at"],
        }

    return {
        "url": fallback.get("url", "https://www.usa.gov/election-office"),
        "label": fallback.get("label", "Find your state or local election office"),
        "sourceName": fallback.get("source_name", "USAGov"),
        "isStateSpecific": False,
        "verifiedAt": None,
    }


def _is_the_page(requested: str, response: httpx.Response) -> bool:
    """Whether `response` is the page `requested` names, not a stand-in.

    Two stand-ins answer below 400 and would otherwise pass: a bot
    wall's challenge (Imperva serves its script with a 200; AGENTS.md
    section 7 — a state whose wall refuses the check is unverifiable, not
    worked around), and a retired lookup redirected to its site's
    homepage, which "resolves" while no longer being the lookup.
    """
    if response.status_code >= 400 or is_bot_challenge(response):
        return False
    asked, landed = httpx.URL(requested), response.url
    return not (
        landed.host == asked.host
        and asked.path.strip("/")
        and not landed.path.strip("/")
    )


async def refresh_link_verification(client: httpx.AsyncClient) -> dict[str, int]:
    """Re-check every candidate per-state URL and rewrite the volume copy.

    The candidates are the bundled file's entries, never the volume
    copy's: the volume copy is this function's own output, so reading
    candidates from it would keep checking last release's URLs forever
    once it exists, and a corrected URL in app/data would never ship.

    Verification is one-directional in effect: a URL that resolves gets a
    fresh `verified_at` and becomes renderable; one that does not has its
    `verified_at` CLEARED, so a link that rots between runs stops being
    shown rather than continuing to be served on the strength of a check
    that passed weeks ago.

    The national fallback is never checked or gated — it is the thing we
    fall back TO, and gating it would leave a state page with no escape
    hatch at all.
    """
    data = read_json(_BUNDLED_PATH) or {}
    states = data.get("states") or {}
    was_shown = {
        code for code, entry in (_load().get("states") or {}).items() if entry.get("verified_at")
    }
    if not states:
        return {"checked": 0, "verified": 0, "failed": 0}

    verified = failed = 0
    now = utcnow().isoformat()
    for code, entry in states.items():
        url = entry.get("url")
        if not url:
            continue
        try:
            # BROWSER_HEADERS: honest identity, complete request — the shape
            # several state WAFs require before they answer anyone (see
            # http_utils). A wall that still refuses is left refusing.
            response = await client.get(
                url, timeout=_LINK_CHECK_TIMEOUT_S, follow_redirects=True,
                headers=BROWSER_HEADERS,
            )
            ok = _is_the_page(url, response)
        except Exception:
            ok = False
        if ok:
            entry["verified_at"] = now
            verified += 1
        else:
            # Clear rather than leave stale — see docstring.
            if code in was_shown:
                logger.warning(
                    "Ballot lookup link for %s no longer verifies; hiding it", code,
                )
            entry["verified_at"] = None
            failed += 1

    try:
        os.makedirs(os.path.dirname(_VOLUME_PATH), exist_ok=True)
        write_text_atomic(_VOLUME_PATH, json.dumps(data, indent=2))
        invalidate_cache()
    except Exception:
        logger.exception("Could not persist verified ballot lookup links")

    return {"checked": verified + failed, "verified": verified, "failed": failed}
