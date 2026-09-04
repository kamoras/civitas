"""Connecticut's confirmed-general-candidate strategy — the Secretary of
State's own election-night-reporting vendor ("TGS ENR", an AngularJS
single-page app at ctemspublic.tgstg.net backed by a set of static,
version-numbered JSON files, not a queried API), reached with no login
and no bot-detection friction at all (a plain unauthenticated GET
succeeds — verified live 2026-09-04). Not found on any other state
probed (guessed subdomains for a dozen other uncovered states all failed
DNS resolution), so this is written as a single-state module for now,
the same way KY/IN/AL/MS/AR/KS each started.

FOUR calls, nothing cycle-specific hardcoded:

1. GET .../ng-app/data/Elections.json lists every election back to ~2021
   with a name and an id, newest first — the year's Democratic and
   Republican statewide primaries are found by matching each entry's
   "MM/DD/YYYY -- ..." name against the target year, requiring the month
   be AUGUST (Connecticut's regular state primary is fixed by statute —
   Conn. Gen. Stat. Sec. 9-423 — as the second Tuesday of August every
   even year, the same class of legally-grounded assumption this system
   already relies on elsewhere, e.g. "no state legislature is called
   Congress"), and requiring "Democratic Primary"/"Republican Primary"
   appear in the name while "special" does not — real named SPECIAL
   primaries for a single district (a Bridgeport special primary, a
   legislative-vacancy primary) share the exact same "-- ... Primary"
   suffix shape and would otherwise collide. The exact wording around
   the party name isn't stable year to year (2026: "-- Democratic
   Primary"; 2024: "-- August 2024 Democratic Primary") — verified live
   against both — so the match is a substring check, not an exact
   pattern.
2. GET .../ng-app/data/election/{id}/Version.json gives the dataset's
   current version number — this vendor updates results IN PLACE at the
   same URL as the count changes (confirmed live: the version number for
   the SAME election advanced between two fetches minutes apart), so the
   version is read fresh every call rather than cached.
3. GET .../ng-app/data/election/{id}/{version}/Lookupdata.json carries
   office and candidate NAMES, keyed by opaque numeric ids. A federal
   House contest's office entry has OT (office type) "C" for Congress —
   a literal type CODE, not a text label to parse — with its district
   number already isolated in its own "D" field ("Connecticut 01" in DT,
   "1" in D). No Senate seat ever appears here because Connecticut's two
   Senate seats are on staggered 6-year terms and neither is up in an
   even year that isn't 2028/2030 — a real structural absence, not a
   parsing gap.
4. GET .../ng-app/data/election/{id}/{version}/stateVotes_Electiondata.json
   carries the actual vote totals, keyed by the SAME office/candidate ids
   Lookupdata.json uses.

An entire election (one Democratic, one Republican) is scoped to ONE
party — Connecticut's ballot never mixes them the way a state that
prints "D-Name"/"R-Name" prefixes does — so party comes from the
election's own Lookupdata.election.P field ("Democratic Party" /
"Republican Party"), read once per election rather than per candidate.

Connecticut nominates on a PLURALITY — Conn. Gen. Stat. Sec. 9-433 sets
no runoff or majority requirement for a party primary — so
`runoff_threshold_pct: null`.

The vendor publishes no certification flag anywhere (confirmed empty
across every real response captured, and the version-number churn
observed live during this same research session is itself evidence
results are still being updated after polls close) — the same shape as
Arkansas/Tennessee/Florida in this system, so a nominee is confirmed
only once `settle_days` has passed since the matched election's own
date (reusing `_settled` from state_candidates_tabular.py rather than
re-deriving the same freshness rule a fourth time).

The August requirement is empirically load-bearing today, not
decoration: checked against the real, unfiltered election list back to
2016, year + party-phrase + non-special alone is NOT enough to
disambiguate in every year -- 2021 also carries a real "Judge of Probate
8th Democratic Primary" and 2026 a real "September 1st Democratic
Primary", both of which share the exact "-- ... Democratic Primary"
substring shape and would otherwise collide with the actual statewide
primary. That said, Connecticut HAS moved this date by statute before
(from September to August, 2013, for MOVE Act compliance) and could
again -- a future such change would make this filter silently reject
the real primary and report the cycle as "not yet published" with no
error. A disclosed, real limitation, not a proven-impossible one.
"""

import logging
import re

import httpx

from app.pipeline.fetch.http_utils import BROWSER_JSON_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, office_from_columns, pick_nominee, surname
from app.pipeline.fetch.state_candidates_tabular import DEFAULT_SETTLE_DAYS, _settled
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE = "https://ctemspublic.tgstg.net/ng-app/data"

_ELECTION_NAME_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})\s+--\s+(.*)$")
_OFFICE_SPEC = {"type_column": "OT", "type_value": "C", "district_column": "D"}

_HEADERS = BROWSER_JSON_HEADERS
_rate_limiter = RateLimiter(rps=1.0)


async def _get_json(client: httpx.AsyncClient, url: str, label: str) -> dict | list | None:
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=30.0, log_label=label, headers=_HEADERS,
    )
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        logger.warning("%s did not return valid JSON", label)
        return None


def _find_primary(elections: list[dict], year: int, party_phrase: str) -> dict | None:
    """The one real statewide {party} primary this `year` -- August only,
    never a named special -- or None if it isn't listed (not yet
    scheduled, or a cycle with no such primary)."""
    for e in elections:
        m = _ELECTION_NAME_RE.match(e.get("Name") or "")
        if not m:
            continue
        month, _day, y, rest = m.groups()
        if int(y) != year or int(month) != 8:
            continue
        if "special" in rest.lower():
            continue
        if party_phrase.lower() not in rest.lower():
            continue
        return {"id": e.get("ID"), "date": f"{y}-{month}-{_day}"}
    return None


async def _party_nominees(
    client: httpx.AsyncClient, election_id: str, year: int,
) -> list[dict] | None:
    """Every confirmed federal House nominee ONE party's primary
    decides, or None on a real fetch failure."""
    version = await _get_json(client, f"{BASE}/election/{election_id}/Version.json", f"CT election version {year}")
    if not isinstance(version, dict) or not version.get("Version"):
        return None
    v = version["Version"]

    lookup = await _get_json(
        client, f"{BASE}/election/{election_id}/{v}/Lookupdata.json", f"CT lookup data {year}",
    )
    if not isinstance(lookup, dict):
        return None
    party = normalize_party((lookup.get("election") or {}).get("P") or "")
    if party is None:
        return None
    congress_races: dict[str, int | None] = {}
    for entry in lookup.get("officeList") or []:
        for oid, office in entry.items():
            office_district = office_from_columns(office, _OFFICE_SPEC)
            if office_district is not None:
                congress_races[oid] = office_district[1]
    if not congress_races:
        return []  # no federal House primary on this party's ballot this cycle
    candidates = lookup.get("candidateIds") or {}

    votes = await _get_json(
        client, f"{BASE}/election/{election_id}/{v}/stateVotes_Electiondata.json", f"CT vote totals {year}",
    )
    if not isinstance(votes, dict):
        return None

    records = []
    for office_id, district in congress_races.items():
        # Every choice's votes count toward the total (an unresolvable
        # name -- a write-in bucket, a candidateIds gap -- still counted
        # a real vote), but only a resolvable name can be confirmed the
        # winner below: dropping an unresolvable choice before ranking
        # would let a lower-vote resolvable candidate win by default if
        # the TRUE leader is the one that's unresolvable.
        choices = []
        for choice in votes.get(office_id) or []:
            for choice_id, vote in choice.items():
                try:
                    vote_count = int(vote.get("V"))
                except (TypeError, ValueError):
                    continue
                name = surname(candidates.get(choice_id, {}).get("NM") or "")
                choices.append((name, vote_count))
        won = pick_nominee(choices, runoff_threshold_pct=None)
        if won and won[0]:
            records.append({"office": "H", "district": district, "party": party, "last_name": won[0]})
    return records


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state unused, this strategy is CT-only by construction
) -> list[dict] | None:
    settle_days = source.get("settle_days", DEFAULT_SETTLE_DAYS)
    elections = await _get_json(client, f"{BASE}/Elections.json", f"CT election list {year}")
    if not isinstance(elections, list):
        return None

    dem = _find_primary(elections, year, "Democratic Primary")
    rep = _find_primary(elections, year, "Republican Primary")
    if dem is None and rep is None:
        return []  # not published yet this cycle — healthy unknown

    results: list[dict] = []
    for primary in (dem, rep):
        if primary is None or not _settled(primary["date"], settle_days):
            continue  # no primary this party, or too soon to trust the count
        party_results = await _party_nominees(client, primary["id"], year)
        if party_results is None:
            return None
        results.extend(party_results)
    return results
