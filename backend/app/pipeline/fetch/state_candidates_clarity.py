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
   EID, which changes every cycle.

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

from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

CLARITY_BASE = "https://results.enr.clarityelections.com"

# Clarity 403s a request with no browser-like User-Agent (same behaviour
# Civix showed) — an honest, identifying UA works, matching the convention
# state_candidates_tx.py and sec_tickers.py already use.
_HEADERS = {"User-Agent": "Civitas civic-transparency-platform contact@civitas-research.org"}

# Three small requests per state per run — a polite pace is plenty.
_rate_limiter = RateLimiter(rps=1.0)

# "Representative to the 120th United States Congress - District 1 -
# Democratic Party". The ordinal ("120th") advances every Congress and the
# label wording varies between Clarity states, so match on the stable
# parts only: the chamber word and the district number.
_HOUSE_RE = re.compile(
    r"(?:United States Congress|U\.?\s*S\.?\s*(?:House|Representative))"
    r".*?District\s+(\d+)",
    re.IGNORECASE | re.DOTALL,
)
_SENATE_RE = re.compile(r"(?:United States|U\.?\s*S\.?)\s*Senator", re.IGNORECASE)

# A federal contest label that names a chamber but NO district is an
# at-large House seat (AK, DE, MT, ND, SD, VT, WY) — FEC models those as
# district 0, which is what _race_id_for already falls back to.
_HOUSE_AT_LARGE_RE = re.compile(
    r"(?:United States Congress|U\.?\s*S\.?\s*(?:House|Representative))", re.IGNORECASE,
)

# Clarity spells the party out in the contest name; the shared matcher in
# state_candidates.py speaks the states' own single-letter codes.
_PARTY_WORDS = {
    "democratic": "D", "democrat": "D", "republican": "R", "libertarian": "L",
    "green": "G", "constitution": "C", "independent": "I",
}

# Name suffixes that must not be mistaken for the surname when splitting
# Clarity's display name ("Dwayne L. Romero" -> "Romero").
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

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


def _parse_office(contest_name: str) -> tuple[str, int | None] | None:
    """("S", None) / ("H", 3) / ("H", None) for an at-large seat, or None
    for any contest this doesn't positively recognise as federal — a state
    legislative or judicial race must never be guessed into a federal one."""
    name = contest_name or ""
    if _SENATE_RE.search(name):
        return "S", None
    m = _HOUSE_RE.search(name)
    if m:
        return "H", int(m.group(1))
    if _HOUSE_AT_LARGE_RE.search(name):
        return "H", None
    return None


def _parse_party(contest_name: str) -> str | None:
    """Clarity scopes a primary contest to one party in the contest name
    itself ("... - Democratic Party"). No recognised party word means this
    isn't a partisan primary contest we can attribute — skipped, never
    defaulted to a major party."""
    lowered = (contest_name or "").lower()
    for word, code in _PARTY_WORDS.items():
        if re.search(rf"\b{word}\b", lowered):
            return code
    return None


def _surname(display_name: str) -> str | None:
    """Clarity prints "First [Middle] Last"; the shared matcher compares
    against the surname FEC stores before the comma. Trailing generational
    suffixes are dropped so "Robert Cruz Jr." yields "Cruz"."""
    tokens = [t for t in re.split(r"\s+", (display_name or "").strip()) if t]
    while tokens and tokens[-1].strip(".,").lower() in _NAME_SUFFIXES:
        tokens.pop()
    if not tokens:
        return None
    return tokens[-1].strip(".,")


def _nominee(contest: dict, runoff_threshold_pct: float | None) -> tuple[str, float] | None:
    """Top vote-getter as (name, pct), or None when this contest can't
    safely name one: no choices, no votes cast yet, an exact tie, or a
    leader who failed to clear a runoff state's threshold."""
    names = contest.get("CH") or []
    votes = contest.get("V") or []
    pcts = contest.get("PCT") or []
    if not names or len(votes) != len(names):
        return None

    ranked = sorted(zip(names, votes), key=lambda t: t[1], reverse=True)
    if ranked[0][1] <= 0:
        return None
    # A tie has no winner to report — the state will resolve it (recount,
    # runoff, draw), and guessing either way would be fabrication.
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None

    top_name = ranked[0][0]
    idx = names.index(top_name)
    pct = float(pcts[idx]) if idx < len(pcts) else 0.0

    if runoff_threshold_pct is not None and pct < runoff_threshold_pct:
        # ponytail: withholds the whole contest in runoff states until the
        # leader clears the bar; the real upgrade is fetching that state's
        # separate runoff election feed and merging it in.
        return None
    return top_name, pct


async def _get(client: httpx.AsyncClient, url: str, label: str) -> httpx.Response | None:
    return await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=30.0,
        log_label=label, headers=_HEADERS,
    )


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

    resp = await _get(client, f"{CLARITY_BASE}/{st}/elections.json", f"{st} Clarity elections")
    if resp is None:
        return None
    try:
        elections = resp.json() or []
    except ValueError:
        logger.warning("Clarity elections list for %s was not JSON", st)
        return None

    matches = [e for e in elections if isinstance(e, dict) and _is_primary(e, year)]
    if not matches:
        logger.warning("No %d primary indexed yet for %s — skipping", year, st)
        return None
    # Newest first: a state that indexes more than one matching election
    # for the cycle (e.g. an amended re-post) should use the latest.
    election_id = matches[0].get("EID")
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
