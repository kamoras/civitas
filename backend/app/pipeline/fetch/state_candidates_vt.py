"""Vermont's own official election-night results feed
(electionresults.vermont.gov) — a single-state deployment (see
state_candidates.py for why that earns a new module, not a config entry).

THREE real hops, all plain unauthenticated GETs, found by reading the
site's own Angular bundle for its literal API config object — NOT a
WebSocket/SignalR feed, despite an earlier research pass concluding
otherwise. The only non-REST traffic visible in a browser's network log
was Azure Application Insights telemetry (a "signalR" string match on
its SDK's own browser-link diagnostics, not real data) — the same class
of overly-pessimistic first read Oregon's SharePoint discovery hit with
its CSOM assumption, corrected the same way: by reading the page's own
bundled JS for the literal endpoint it actually calls, not just watching
the network tab.

1. `<baseUrl>/elections/elections.json` — every election ever held on
   this portal, flat, evergreen. Matched by `isStateWideElection: true`,
   `electionTypeCode: "P"` (Primary — verified against the real 2026
   "AUGUST PRIMARY" entry) and `electionYear == year`; requires exactly
   ONE match, refuses otherwise (a future year adding a second statewide
   primary, or this field's meaning drifting, should refuse rather than
   guess which one is current).
2. `<baseUrl>/elections/{electionGuid}.json` — that election's own report
   manifest. Its `federal.path` field names the CURRENT federal-results
   file, carrying a timestamp suffix that changes on every republish
   (`elections\\{guid}-f-20260908222640.json`, backslash-separated) — read
   fresh every run, never cached or reconstructed from a pattern, the
   same "a blob name changes on republish, never hardcode it" rule this
   system's Enhanced Voting adapters already follow for their own CDN
   blobs. `federal.isEnable` gates whether this election publishes
   federal results at all.
3. `<baseUrl>/{federal.path}` — the real per-town results.

The report's own shape (verified against the real 2026 file, 525KB, 3
federal party blocks x 247 towns): a party block per REAL Vermont ballot
party (`pn`/`pc` — DEMOCRATIC/REPUBLICAN/PROGRESSIVE all appear, since a
Vermont candidate can file for more than one party's primary at once),
each carrying one office block per real federal contest (`on` — Vermont's
own real label is the bare "REPRESENTATIVE TO CONGRESS", no "U.S."/
"United States" prefix and "to" not "in" — parse_office needed a new
alternative for this, added to the shared _CHAMBER_HOUSE pattern rather
than special-cased here, since a bare "Representative to Congress" is a
real, non-Vermont-specific chamber label a future state could publish
too). Vermont's single at-large House seat means every real office here
resolves to `("H", None)` — no Senate seat was up in 2026 (both VT
Senate terms run past this cycle), so nothing here needs to distinguish
Senate from House.

Within an office, `cs` is one entry per Vermont TOWN (not county — VT
tabulates and reports at town granularity), each carrying `rc` (the
town's real declared-ballot-line result, one entry, `isWriteIn: false`)
and `wc` (every write-in tally recorded in that town, `isWriteIn: true`
— genuine write-in noise: dozens of stray names each getting 1-2 votes
statewide, real public figures included, since VT ballots let a voter
write in anyone). A real candidate's TOTAL vote count is the sum of
their own `cid` across every town's `rc` AND `wc` combined — a
genuinely-elected write-in nominee is a real, documented possibility
under Vermont law (17 V.S.A., a write-in must clear its own separate
statutory minimum to qualify), which this module does not need to model
specially: pick_nominee's own plurality logic already picks whoever has
the real most votes, write-in or not, the same way it already does for
New Mexico's real Republican Senate write-in nominee.

Aggregation is keyed by each candidate's own numeric `cid`, NEVER by
`cn` (display name) — the same "never aggregate by the final output's
identifying field when a richer one exists" rule this system's
Tennessee/Mississippi modules already learned the hard way, doubly
necessary here since write-in noise routinely produces near-duplicate
spellings of the same real name across different towns ("SUZANNE
SEYMOUR" / "SUZ SEYMOUR" / "SUE SEYMOUR" all appear live). `cid` is only
scoped to one (party, office) tally, not a single global identity — the
SAME real candidate carries a DIFFERENT `cid` in each party/office
block they appear under (verified live: Becca Balint has three distinct
`cid`s across her real D ballot line and her R/PR write-in tallies), so
aggregation must group by (office, district, party) FIRST and only key
by `cid` within that group — confirmed live that no two same-named
candidates ever share one group with different ids, or vice versa.

Four real non-candidate placeholder `cn` values are excluded outright
(verified against the full live 2026 report, not just this module's own
trimmed test fixture): `cid: 0`'s "OTHER WRITE-IN" (a rolled-up count of
every write-in not individually itemized), a SEPARATE "OTHER WRITE-INS"
(plural) that instead carries its own real nonzero `cid` — a second,
differently-scoped rollup bucket, not a typo of the first — and "BLANK"/
"FLOWERY" (a blank or spoiled write-in line), each also with its own
real nonzero `cid`. None of the four are real people.

Vermont's Progressive Party primary carried NO real declared candidate
in 2026 — every one of its own real vote totals was scattered write-in
noise, none clearing any plausible threshold — so this module's own
`normalize_party` correctly refuses "PROGRESSIVE" (matching its existing,
deliberate refusal of "Unaffiliated"/"Independent" everywhere else) and
that party's contest is silently skipped rather than confirming a
write-in fusion nomination this module was never built to verify.

Vermont nominates by PLURALITY — most votes wins outright, no runoff
mechanism exists for a federal primary (Title 17 V.S.A.; confirmed via
Vermont's own official statutes site and a secondary civic-reform
summary, though the exact section number is not cited here since it
wasn't directly confirmed) — so `runoff_threshold_pct` is null.

No live "unofficial/preliminary" state needs guarding against with its
own gate here: the fetched election detail already carries a real
`isOfficial` flag (true for the live 2026 primary, "100% / 247 of 247
towns reporting / OFFICIAL" on the page itself) — but per this system's
own established "UT trap" precedent (a state's own official flag is not
reliably flipped), `settle_days` stays the actual gate here too, not the
flag. `isOfficial` is read only as a cross-check in tests, never trusted
directly to decide when to confirm.

Verified live 2026-09-08 against the real 2026 August Primary: Becca
Balint (D, real incumbent, 159,358 votes, unopposed on the real ballot
line) and Gerald Malloy (R, real plurality winner of a real 3-way field,
31,324 over runner-up Mark Coester's 9,046) for Vermont's single
at-large House seat.
"""

import logging

import httpx

from app.pipeline.fetch.http_utils import fetch_json_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, parse_office, pick_nominee, surname
from app.pipeline.fetch.state_candidates_tabular import DEFAULT_SETTLE_DAYS, _settled
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_BASE_URL = "https://static.electionresults.vermont.gov"
_ELECTIONS_URL = f"{_BASE_URL}/elections/elections.json"
_NON_CANDIDATE_NAMES = {"BLANK", "FLOWERY", "OTHER WRITE-IN", "OTHER WRITE-INS"}


class _DiscoveryFailed(Exception):
    """Raised on a genuine fetch/parse failure (network error, malformed
    list/detail response, or a matched election missing its own federal
    report path) — never for a healthy "nothing published for this cycle
    yet" or "no matching election this year", both of which return None
    instead. Collapsing the two would silently report a broken feed as a
    healthy empty cycle."""


async def _current_primary_guid(client: httpx.AsyncClient, state: str, year: int) -> str | None:
    """The one statewide primary election's own guid for `year`, or None
    if this year has no such election yet (healthy — not every year runs
    one on this portal's own history). Raises _DiscoveryFailed if the
    list itself couldn't be read, or if more than one election matches
    (this module's own single-match safety floor — see module docstring)."""
    elections = await fetch_json_with_retry(client, _rate_limiter, _ELECTIONS_URL, f"{state} elections list")
    if not isinstance(elections, list):
        raise _DiscoveryFailed(f"{state} elections list fetch failed")
    matches = [
        e for e in elections
        if e.get("isStateWideElection") and e.get("electionTypeCode") == "P" and e.get("electionYear") == year
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise _DiscoveryFailed(f"{state} elections list has {len(matches)} statewide primaries for {year}")
    return matches[0].get("electionGuid")


async def _federal_report_url(client: httpx.AsyncClient, state: str, guid: str) -> tuple[str, str] | None:
    """(report_url, election date) for the current federal report, or
    None if this election doesn't publish federal results (healthy).
    Raises _DiscoveryFailed on a genuine fetch/parse failure."""
    detail = await fetch_json_with_retry(client, _rate_limiter, f"{_BASE_URL}/elections/{guid}.json", f"{state} election detail")
    if not isinstance(detail, dict):
        raise _DiscoveryFailed(f"{state} election detail fetch failed for {guid}")
    federal = detail.get("federal") or {}
    if not federal.get("isEnable"):
        return None
    path = federal.get("path")
    if not path:
        raise _DiscoveryFailed(f"{state} election {guid} has federal.isEnable but no path")
    held = str((detail.get("electionDetails") or {}).get("electionDate") or "")[:10]
    return f"{_BASE_URL}/{path.replace(chr(92), '/')}", held


def _federal_contests(report: dict) -> list[tuple[str, int | None, str, str, int]]:
    """(office, district, party, cn, votes) for every real per-town
    tally in the report -- callers group by (office, district, party,
    cid) to aggregate statewide totals; cid isn't returned here since the
    shared result contract only ever needs a surname, but grouping
    upstream still happens by cid (see _fetch_federal_choices)."""
    results = []
    for party_block in report.get("d") or []:
        party = normalize_party(party_block.get("pn") or "")
        if party is None:
            continue
        for office in party_block.get("o") or []:
            office_district = parse_office(office.get("on") or "")
            if office_district is None:
                continue
            off, district = office_district
            for town in office.get("cs") or []:
                for c in [*(town.get("rc") or []), *(town.get("wc") or [])]:
                    cid = c.get("cid")
                    cn = (c.get("cn") or "").strip()
                    votes = c.get("vc")
                    if cid in (None, 0) or not cn or cn.upper() in _NON_CANDIDATE_NAMES:
                        continue
                    if not isinstance(votes, int):
                        continue
                    results.append((off, district, party, cid, cn, votes))
    return results


def _fetch_federal_choices(report: dict) -> dict[tuple[str, int | None, str], list[tuple[str, int]]]:
    by_group: dict[tuple[str, int | None, str], dict[int, tuple[str, int]]] = {}
    for off, district, party, cid, cn, votes in _federal_contests(report):
        group = by_group.setdefault((off, district, party), {})
        name, total = group.get(cid, (cn, 0))
        group[cid] = (name, total + votes)
    return {key: list(candidates.values()) for key, candidates in by_group.items()}


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    try:
        guid = await _current_primary_guid(client, state, year)
        if guid is None:
            return []
        discovered = await _federal_report_url(client, state, guid)
    except _DiscoveryFailed as exc:
        logger.warning("VT results: discovery failed: %s", exc)
        return None
    if discovered is None:
        return []
    report_url, held_on = discovered

    settle_days = source.get("settle_days", DEFAULT_SETTLE_DAYS)
    if not _settled(held_on, settle_days):
        return []

    report = await fetch_json_with_retry(client, _rate_limiter, report_url, f"{state} results {year}")
    if not isinstance(report, dict):
        return None

    runoff_threshold_pct = source.get("runoff_threshold_pct")
    results = []
    for (office, district, party), choices in _fetch_federal_choices(report).items():
        won = pick_nominee(choices, runoff_threshold_pct=runoff_threshold_pct)
        if won:
            name = surname(won[0])
            if name:
                results.append({"office": office, "district": district, "party": party, "last_name": name})
    return results
