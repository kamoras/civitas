"""Clarity (SOE/Scytl "Election Night Reporting") confirmed-candidate
strategy — one adapter serving EVERY state that publishes a state-level
Clarity feed, not one module per state (see state_candidates.py for the
shared contract).

This is the point of the STRATEGIES dispatch: adding another Clarity state
is a JSON entry in state_candidate_sources.json, never new code. Only a
state on a genuinely different vendor needs a new module (Texas runs Civix,
hence state_candidates_tx.py).

Verified live against Colorado's real 2026 primary on 2026-08-12, by
walking the same three public endpoints this module uses — no API key, no
auth, no scraping of the rendered Angular SPA:

1. GET /{ST}/elections.json — every election the state has indexed, each
   with `EID`, `ElectionName` and a real `Date`. Unlike Texas's Civix feed
   there is NO election-type code here, only free text, so `_is_primary`
   matches on the name and scopes by the `Date` YEAR — never a hardcoded
   EID, which changes every cycle. A state whose OWN copy of this
   endpoint is empty (West Virginia's is, even though its real results
   are live) sets `discovery: {"mode": "landing_page", "page_url", "link_
   regex"}` in its source entry instead — the EID is read off the link
   the state's own elections page keeps current, same "the listing
   endpoint is empty but a static page still points at the real data"
   shape Minnesota's and Arizona's adapters already handle for their own
   vendors. See `_discover_election_id`.

2. GET /{ST}/{EID}/current_ver.txt — the current results version (plain
   text, e.g. "377440"). Also changes constantly as results are amended;
   fetched every run, never cached to a constant.

3. GET /{ST}/{EID}/{VER}/json/sum.json — `Contests`, each carrying `C`
   (free-text contest name), `CH` (choice/candidate names), `V` (votes)
   and `PCT` (percentages), positionally aligned.

Ground truth check (the discipline state_candidates_tx.py used with
Paxton/Cornyn): Colorado's CO-01 Democratic primary in this feed shows
Melat Kiros 83,855 (53.2%) over 15-term incumbent Diana DeGette 62,715,
with Wanda James a distant third — which is exactly what actually happened
on 2026-06-30 and was reported statewide. The feed is the state's own
canonical result, not a projection.

WINNER DERIVATION. `W` (the per-choice winner flag) was all zeros in
Colorado's feed even for long-decided contests, so it cannot be trusted as
the confirmed-nominee signal; the nominee is derived as the top vote-getter
instead. That is only correct where a plurality wins the primary outright.
States that send a sub-majority leader to a RUNOFF (TX, GA, MS, AL, AR, OK,
SC, ...) would have their runoff-bound leader mislabeled as the nominee, so
those states carry `runoff_threshold_pct` in their source entry and a
contest whose leader is under it yields NOTHING rather than a guess. Same
under-include-rather-than-fabricate rule the Texas adapter follows for
declaration-only independents.
"""

import logging
import re

import httpx

from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import (
    normalize_party as _parse_party,
    parse_office as _parse_office,
    pick_nominee,
    surname as _surname,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

CLARITY_BASE = "https://results.enr.clarityelections.com"

# Clarity 403s a request with no browser-like User-Agent (same behaviour
# Civix showed) — an honest, identifying UA works, matching the convention
# state_candidates_tx.py and sec_tickers.py already use.
_HEADERS = BROWSER_HEADERS

# Three small requests per state per run — a polite pace is plenty.
_rate_limiter = RateLimiter(rps=1.0)

_PRESIDENTIAL_RE = re.compile(r"presidential", re.IGNORECASE)
_PRIMARY_RE = re.compile(r"primary", re.IGNORECASE)


def _is_primary(election: dict, year: int) -> bool:
    """This cycle's regular (non-presidential) primary. Scoped by the
    `Date` YEAR rather than trusting the name to carry it, and excluding
    the separate presidential primary some states run in the same year."""
    date = str(election.get("Date") or "")
    name = str(election.get("ElectionName") or "")
    if str(year) not in date:
        return False
    return bool(_PRIMARY_RE.search(name)) and not _PRESIDENTIAL_RE.search(name)


def _nominee(contest: dict, runoff_threshold_pct: float | None) -> tuple[str, float] | None:
    """Unwrap Clarity's positionally-aligned `CH`/`V` arrays and hand them
    to the shared winner rule. A length mismatch between the two means the
    envelope isn't what this adapter understands, so nothing is derived
    from it rather than pairing a name with the wrong candidate's votes."""
    names = contest.get("CH") or []
    votes = contest.get("V") or []
    if not names or len(votes) != len(names):
        return None
    return pick_nominee(list(zip(names, votes)), runoff_threshold_pct)


async def _get(client: httpx.AsyncClient, url: str, label: str) -> httpx.Response | None:
    return await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=30.0,
        log_label=label, headers=_HEADERS,
    )


async def _discover_election_id(
    client: httpx.AsyncClient, state: str, year: int, discovery: dict,
) -> str | None:
    """This cycle's Clarity EID, by whichever means this state needs.

    Most Clarity states index every election at /{ST}/elections.json,
    scoped to this cycle by `_is_primary`. West Virginia's own copy of
    that endpoint is empty — its real results are reachable, but only
    through the link the state's OWN elections page keeps current
    (sos.wv.gov), the same "the listing endpoint is empty/blocked but a
    static page still points at the real data" shape Minnesota's and
    Arizona's adapters already handle for their own vendors. A state
    whose `discovery.mode` is "landing_page" is read that way instead of
    via elections.json; every other state's behavior is unchanged."""
    if discovery.get("mode") == "landing_page":
        resp = await _get(client, discovery["page_url"], f"{state} Clarity landing page")
        if resp is None:
            return None
        m = re.search(discovery["link_regex"], resp.text)
        if not m:
            logger.warning("No Clarity results link found on %s's own elections page", state)
            return None
        return m.group(1)

    resp = await _get(client, f"{CLARITY_BASE}/{state}/elections.json", f"{state} Clarity elections")
    if resp is None:
        return None
    try:
        elections = resp.json() or []
    except ValueError:
        logger.warning("Clarity elections list for %s was not JSON", state)
        return None

    matches = [e for e in elections if isinstance(e, dict) and _is_primary(e, year)]
    if not matches:
        logger.warning("No %d primary indexed yet for %s — skipping", year, state)
        return None
    # Newest first: a state that indexes more than one matching election
    # for the cycle (e.g. an amended re-post) should use the latest.
    return matches[0].get("EID")


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    """Every confirmed federal nominee `state` has produced for `year`, or
    None on a fetch failure or when the cycle's primary isn't indexed yet —
    the tri-state None-vs-[] discipline used throughout this codebase (a
    real empty result is not the same as "couldn't check").

    Each item: {"office": "S"|"H", "district": int|None, "party": str,
    "last_name": str}, matched against Civitas's FEC-derived Candidate rows
    by the caller (state_candidates.py), not here.
    """
    st = state.upper()
    threshold = source.get("runoff_threshold_pct")

    election_id = await _discover_election_id(client, st, year, source.get("discovery") or {})
    if not election_id:
        return None

    resp = await _get(
        client, f"{CLARITY_BASE}/{st}/{election_id}/current_ver.txt", f"{st} Clarity version",
    )
    if resp is None:
        return None
    version = resp.text.strip()
    if not version.isdigit():
        logger.warning("Clarity version for %s was not a version id: %r", st, version[:40])
        return None

    resp = await _get(
        client,
        f"{CLARITY_BASE}/{st}/{election_id}/{version}/json/sum.json",
        f"{st} Clarity summary",
    )
    if resp is None:
        return None
    try:
        contests = (resp.json() or {}).get("Contests") or []
    except ValueError:
        logger.warning("Clarity summary for %s was not JSON", st)
        return None

    results = []
    for contest in contests:
        if not isinstance(contest, dict):
            continue
        name = contest.get("C") or ""
        parsed = _parse_office(name)
        if parsed is None:
            continue
        party = _parse_party(name)
        if party is None:
            continue
        won = _nominee(contest, threshold)
        if won is None:
            continue
        last_name = _surname(won[0])
        if not last_name:
            continue
        office, district = parsed
        results.append({
            "office": office, "district": district,
            "party": party, "last_name": last_name,
        })
    return results
