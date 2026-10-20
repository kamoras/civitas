"""Official links for each voting task, and the check that keeps them honest.

The state ballot page opens with "Your vote, step by step": check your
registration, register, see your sample ballot, find your polling place,
vote by mail (`vote_steps`, one link per task, in that order). The digest
deliberately shows only the statewide portion of a ballot (a ballot is
defined per ballot style, not per state), so these links out to the
voter's own election office are not decoration: they are what makes the
omission honest, and each names the task it does (WCAG 2.4.4), so a reader
looking for the ballot never lands on registration instead.

Which is exactly why the per-state links are GATED. A 404 on a step, in
election week, from a URL Civitas vouched for, strands the user at the
moment the page has just told them to go elsewhere. So:

  - `state_ballot_lookup.json` names a fallback for every task, stable and
    maintained by someone else: NASS's Can I Vote (the Secretaries of
    State's own directory), USAGov's office directory for a sample ballot,
    and vote.gov for registering (the voter calendar's registerFallback).
  - Per-state task links are served ONLY once `verified_at` or
    `blocked_at` is set, which only `refresh_link_verification()` below
    does, after the URL resolved or a bot wall refused the check. An
    unverified entry is invisible to users.

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
from app.voter_calendar import register_pages

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


# The voting tasks, in the order a voter does them. Each is one step on the
# state ballot page (vote_steps); `register` comes from the voter calendar.
TASKS = ("check_registration", "register", "sample_ballot", "polling_place", "mail_ballot")
_LISTED_TASKS = tuple(t for t in TASKS if t != "register")


def _host(url: str) -> str:
    host = httpx.URL(url).host
    return host[4:] if host.startswith("www.") else host


def _shown(entry: dict | None) -> bool:
    return bool(entry and entry.get("url") and (entry.get("verified_at") or entry.get("blocked_at")))


def _national(data: dict) -> dict:
    fallback = data.get("national_fallback") or {}
    return {
        "url": fallback.get("url", "https://www.usa.gov/election-office"),
        "label": fallback.get("label", "Find your state or local election office"),
        "sourceName": fallback.get("source_name", "USAGov"),
    }


def task_link(state: str, task: str) -> dict[str, Any]:
    """The link for one listed task (not `register`): the state's own page
    once the nightly check verified it (`isStateSpecific`), or one whose
    site's bot wall refused the check (`unconfirmed`, with the task's
    fallback beside it); otherwise the task's fallback alone (NASS's Can I
    Vote, or USAGov's office directory for a sample ballot)."""
    data = _load()
    fallback = (data.get("task_fallbacks") or {}).get(task)
    fallback = (
        {"url": fallback["url"], "sourceName": fallback.get("source_name", "")} if fallback
        else {"url": _national(data)["url"], "sourceName": _national(data)["sourceName"]}
    )
    state_entry = (data.get("states") or {}).get(state.upper()) or {}
    entry = (state_entry.get("tasks") or {}).get(task)
    if _shown(entry):
        unconfirmed = not entry.get("verified_at")
        return {"url": entry["url"], "sourceName": state_entry.get("source_name") or "", "isStateSpecific": True,
                "verifiedAt": entry.get("verified_at"), "unconfirmed": unconfirmed,
                "fallback": fallback if unconfirmed else None}
    return {**fallback, "isStateSpecific": False, "verifiedAt": None, "unconfirmed": False, "fallback": None}


def lookup_for_state(state: str) -> dict[str, Any]:
    """The state's official voter lookup, in the shape the issue ballot
    links and the state page's notes read: its registration lookup (the
    personal voter portal), worded by `isStateSpecific`, else USAGov's
    national directory of election offices. Always returns something usable.
    """
    link = task_link(state, "check_registration")
    national = _national(_load())
    if link["isStateSpecific"]:
        return {**link, "label": "Official voter lookup", "fallback": national if link["unconfirmed"] else None}
    return {**national, "isStateSpecific": False, "verifiedAt": None, "unconfirmed": False, "fallback": None}


def _register_link(state: str, calendar: dict | None) -> dict[str, Any] | None:
    """The register step's link: the calendar's (its build script checked
    it), with tonight's check of the same URL deciding when it has run; a
    page that check found dead gives way to vote.gov. None where the state
    has no registration."""
    pages, fallback = register_pages()
    page = pages.get(state.upper())
    if page and page.get("none"):
        return None
    vote_gov = {"url": (fallback or {}).get("url", "https://vote.gov/"),
                "sourceName": (fallback or {}).get("publisher", "Vote.gov")}
    if not page or not page.get("url"):
        return {**vote_gov, "isStateSpecific": False, "unconfirmed": False, "fallback": None}
    checked = ((((_load().get("states") or {}).get(state.upper()) or {}).get("tasks") or {}).get("register"))
    if checked and checked.get("url") == page["url"]:
        if not _shown(checked):
            return {**vote_gov, "isStateSpecific": False, "unconfirmed": False, "fallback": None}
        unconfirmed = not checked.get("verified_at")
    else:
        unconfirmed = page.get("status") != "verified"
    return {"url": page["url"], "sourceName": page.get("publisher") or "", "isStateSpecific": True,
            "unconfirmed": unconfirmed, "fallback": vote_gov if unconfirmed else None}


def _mail_deadline(calendar: dict) -> dict | None:
    """The mail-ballot request deadline still to come, else the last one
    that passed (a passed deadline is shown as passed, never dropped)."""
    requests = [i for i in calendar.get("items") or [] if i.get("kind") == "absentee_request"]
    item = next((i for i in requests if i["status"] != "closed"), requests[-1] if requests else None)
    if not item:
        return None
    return {"sentence": item["sentence"], "status": item["status"], "countdown": item.get("countdown"),
            "closesOn": item.get("closesOn")}


def _register_deadline(calendar: dict) -> dict | None:
    line = calendar.get("registration")
    if not line:
        return None
    return {"sentence": line["sentence"], "status": line["status"], "countdown": line.get("countdown"),
            "closesOn": line.get("closesOn")}


def vote_steps(state: str, calendar: dict | None) -> list[dict[str, Any]]:
    """The state ballot page's "Your vote, step by step": one entry per
    task in TASKS, each its link (`url` None only for registering in a
    state with no registration), the destination's host and publisher,
    and the deadline that applies from the voter calendar (`calendar` is
    calendar_for_state's answer, or None). Read path only: two JSON files."""
    calendar = calendar or {}
    steps = []
    for task in TASKS:
        link = _register_link(state, calendar) if task == "register" else task_link(state, task)
        deadline = (_register_deadline(calendar) if task == "register"
                    else _mail_deadline(calendar) if task == "mail_ballot" else None)
        if link is None:
            steps.append({"task": task, "url": None, "host": None, "sourceName": None, "isStateSpecific": True,
                          "unconfirmed": False, "fallback": None, "deadline": deadline})
            continue
        fallback = link.get("fallback")
        steps.append({
            "task": task,
            "url": link["url"],
            "host": _host(link["url"]),
            "sourceName": link["sourceName"],
            "isStateSpecific": link["isStateSpecific"],
            "unconfirmed": link["unconfirmed"],
            "fallback": {**fallback, "host": _host(fallback["url"])} if fallback else None,
            "deadline": deadline,
        })
    return steps


VERIFIED, BLOCKED, DEAD = "verified", "blocked", "dead"

# A refusal, not an answer about the page: a wall that will not talk to an
# automated client (Akamai's "Access Denied" 403 carries no challenge
# script, so is_bot_challenge alone misses it).
_REFUSALS = (401, 403, 429)


def _check(requested: str, response: httpx.Response) -> str:
    """Whether `response` is the page `requested` names (VERIFIED), a bot
    wall refusing the check (BLOCKED), or anything else (DEAD).

    Two stand-ins answer below 400 and must not verify: a bot wall's
    challenge (Imperva serves its script with a 200; AGENTS.md section 7 —
    a state whose wall refuses the check is unverifiable, never worked
    around), and a retired lookup redirected to its site's homepage, which
    "resolves" while no longer being the lookup. A 404 from a host that
    answered is DEAD: the state moved or retired the page.
    """
    if is_bot_challenge(response) or response.status_code in _REFUSALS:
        return BLOCKED
    if response.status_code >= 400:
        return DEAD
    asked, landed = httpx.URL(requested), response.url
    homepage = landed.host == asked.host and asked.path.strip("/") and not landed.path.strip("/")
    return DEAD if homepage else VERIFIED


async def _check_url(client: httpx.AsyncClient, url: str) -> str:
    try:
        # BROWSER_HEADERS: honest identity, complete request, the shape
        # several state WAFs require before they answer anyone (see
        # http_utils). A wall that still refuses is left refusing.
        # `client` is make_async_client's, which completes a chain missing
        # its intermediate the way a browser does (AIA) and fails every
        # other certificate fault (app/http_client.py): Alaska's
        # polling-place page read as dead from 2026-10 for want of that alone.
        response = await client.get(url, timeout=_LINK_CHECK_TIMEOUT_S, follow_redirects=True,
                                    headers=BROWSER_HEADERS)
        return _check(url, response)
    except Exception:
        return DEAD


async def refresh_link_verification(client: httpx.AsyncClient) -> dict[str, int]:
    """Re-check every per-state task URL and rewrite the volume copy.

    The candidates are the bundled file's tasks, never the volume copy's:
    the volume copy is this function's own output, so reading candidates
    from it would keep checking last release's URLs forever once it
    exists, and a corrected URL in app/data would never ship. The register
    task's candidate is the voter calendar's register page (one copy of
    each URL), stored here under `register` with the URL it checked.

    Verification is one-directional in effect: a URL that resolves gets a
    fresh `verified_at` and is shown; one that does not has its
    `verified_at` CLEARED, so a link that rots between runs stops being
    shown rather than continuing to be served on the strength of a check
    that passed weeks ago. A URL whose site refuses the check (a bot wall)
    gets `blocked_at` instead: shown as the state's own page, worded as
    unconfirmed, with the task's fallback beside it. A URL that fails any
    other way (a 404, a redirect to the homepage, no answer) clears both
    and falls back.

    The fallbacks (NASS's Can I Vote, USAGov, vote.gov) are never checked
    or gated: they are what a step falls back TO, and gating them would
    leave a step with no destination at all.
    """
    data = read_json(_BUNDLED_PATH) or {}
    states = data.get("states") or {}
    was_shown = {
        (code, task)
        for code, entry in (_load().get("states") or {}).items()
        for task, link in (entry.get("tasks") or {}).items() if link.get("verified_at")
    }
    register, _ = register_pages()
    for code, page in register.items():
        if page.get("url") and not page.get("none") and code in states:
            states[code].setdefault("tasks", {})["register"] = {"url": page["url"]}
    counts = {"checked": 0, "verified": 0, "blocked": 0, "failed": 0}
    now = utcnow().isoformat()
    for code, entry in states.items():
        for task, link in (entry.get("tasks") or {}).items():
            if not link.get("url"):
                continue
            result = await _check_url(client, link["url"])
            counts["checked"] += 1
            link["blocked_at"] = now if result == BLOCKED else None
            link["verified_at"] = now if result == VERIFIED else None
            if result == VERIFIED:
                counts["verified"] += 1
            elif result == BLOCKED:
                counts["blocked"] += 1
            else:
                # Cleared rather than left stale: see docstring.
                if (code, task) in was_shown:
                    logger.warning("Voting-step link %s for %s no longer verifies; hiding it", task, code)
                counts["failed"] += 1

    try:
        os.makedirs(os.path.dirname(_VOLUME_PATH), exist_ok=True)
        write_text_atomic(_VOLUME_PATH, json.dumps(data, indent=2))
        invalidate_cache()
    except Exception:
        logger.exception("Could not persist verified ballot lookup links")

    return counts
