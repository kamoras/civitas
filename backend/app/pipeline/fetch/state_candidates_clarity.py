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

Ground truth check (the same discipline as state_candidates_tx.py):
Colorado's 2026 primary results in this feed, including a long-serving
incumbent's defeat, matched what the state certified and what was
reported statewide. The feed is the state's own
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

STATE OFFICES ride the same summary. A state whose entry opts in reads
them through the same two conservative gates every other adapter uses,
and their names are kept whole rather than cut to a surname, because
there is no FEC row to match a state office against.

All three states on this vendor are live, and each needed one wording
the shared parsers had never seen:

  CO   Regent of the University of Colorado, elected statewide but
       seated by CONGRESSIONAL district -- a seat number with nothing to
       do with any legislative map, and a label close enough to a
       federal one that parse_office refusing it is worth a test. 86
       legislative seats and six statewide office types, nothing
       unmatched.
  IA   "Secretary of Agriculture" and "Auditor of State", Iowa's own
       names for offices the parsers knew under other wordings. 125
       legislative seats, which is exactly its 25 Senate seats up plus
       all 100 House, and nothing unmatched.
  WV   Writes the district number FIRST -- "HOUSE OF DELEGATES, 1st
       District", "STATE SENATOR, 3rd Senatorial District" -- so the
       plain "District N" pattern read nothing and the whole legislature
       was invisible. 117 seats, exactly its 17 Senate seats up plus all
       100 Delegates. No statewide offices, which is checked: its
       summary leaves only three local park-district supervisors
       unmatched, because West Virginia elects its statewide officers in
       presidential years.
"""

import logging
import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

import httpx

from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import (
    is_not_a_person,
    runoff_threshold,
    normalize_party as _parse_party,
    clean_display_name as _clean_display_name,
    parse_office as _parse_office,
    parse_state_leg_office as _parse_state_leg_office,
    parse_statewide_office as _parse_statewide_office,
    pick_nominee,
    federal_record,
)
from app.pipeline.fetch.election_results import ContestCount, StateCount, UntrustedCount, is_special_contest, pick_general
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


async def _election_meta(
    client: httpx.AsyncClient, state: str, eid: str, base: str = CLARITY_BASE,
) -> dict | None:
    """An election's own name and date, in elections.json's shape, read
    from its settings file — for a state whose listing gives only ids."""
    resp = await _get(client, f"{base}/{state}/{eid}/current_ver.txt", f"{state} Clarity version {eid}")
    version = resp.text.strip() if resp is not None else ""
    if not version.isdigit():
        return None
    resp = await _get(
        client, f"{base}/{state}/{eid}/{version}/json/en/electionsettings.json",
        f"{state} Clarity settings {eid}",
    )
    try:
        details = (resp.json().get("settings") or {}).get("electiondetails") or {} if resp is not None else {}
    except ValueError:
        return None
    if not details.get("electiondate"):
        return None
    return {"Date": details["electiondate"], "ElectionName": details.get("internalname") or ""}


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
        page_url = discovery.get("page_url")
        link_regex = discovery.get("link_regex")
        if not page_url or not link_regex:
            logger.warning("%s's landing_page discovery is missing page_url/link_regex", state)
            return None
        resp = await _get(client, page_url, f"{state} Clarity landing page")
        if resp is None:
            return None
        ids = {m.group(1) for m in re.finditer(link_regex, resp.text)}
        if not ids:
            # A page that answers 200 with none of its own links on it is
            # usually a bot-manager challenge rather than a genuinely
            # empty page — Minnesota's file host shows the identical
            # symptom, and the retry succeeds once the challenge has set
            # its cookie. One retry only: a truly empty page stays empty.
            logger.info("%s's elections page had no results link — retrying once", state)
            resp = await _get(client, page_url, f"{state} Clarity landing page (retry)") or resp
            ids = {m.group(1) for m in re.finditer(link_regex, resp.text)}
        if not ids:
            logger.warning("No Clarity results link found on %s's own elections page", state)
            return None
        # The page carries no dates, so each linked election is scoped by its
        # OWN settings — the same name/date test elections.json gets. West
        # Virginia's current-elections page dropped its primary link once
        # the general approached, and the page that still links it is the
        # results archive, which lists every election back to 2016; a lone
        # stale link would otherwise be trusted too.
        current = []
        for eid in sorted(ids):
            meta = await _election_meta(client, state, eid)
            if meta is not None and _is_primary(meta, year):
                current.append(eid)
        if len(current) != 1:
            logger.warning(
                "%s's elections page links %d Clarity elections, %d of them this cycle's primary — "
                "refusing to guess", state, len(ids), len(current),
            )
            return None
        return current[0]

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
    threshold = runoff_threshold(source)
    state_offices = bool(source.get("statewide_offices"))

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
        seat = None
        if parsed is not None:
            office, district = parsed
            federal = True
        elif not state_offices:
            continue
        else:
            # This vendor's summary carries the state's own executive
            # offices and legislative seats beside the federal ones,
            # read through the same two conservative gates the other
            # adapters use, and only for a state that opts in.
            federal = False
            statewide = _parse_statewide_office(name)
            if statewide is not None:
                office, district = statewide
            else:
                parsed_seat = _parse_state_leg_office(name)
                if parsed_seat is None:
                    continue
                office, district, seat = parsed_seat
        party = _parse_party(name)
        if party is None:
            continue
        won = _nominee(contest, threshold)
        if won is None:
            continue
        # A federal nominee is matched against an FEC row, which files
        # surnames; a state-office nominee has no FEC row to match or
        # render from, so the printed name is kept whole.
        if federal:
            record = federal_record(office, district, party, won[0])
            if record is None:
                continue
        else:
            last_name = _clean_display_name(won[0])
            if not last_name:
                continue
            record = {
                "office": office, "district": district,
                "party": party, "last_name": last_name,
            }
        if seat is not None:
            record["seat"] = seat
        results.append(record)
    return results


# --- Live general-election counts (fetch/election_results.py) -------------

def _clarity_date(raw: str) -> str | None:
    """"11/8/2022 12:00:00 AM" (elections.json) or "11/8/2022"
    (electionsettings) as an ISO date."""
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", raw or "")
    if not m:
        return None
    month, day, year = (int(g) for g in m.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


# (host, state, election day) -> EID, recorded only once a read of that
# EID has passed every trust check in fetch_general_results (never on
# discovery alone: a landing page can link a test copy first, and a cached
# test id would be re-read every pass without ever re-discovering).
_general_eids: dict[tuple[str, str, str], str] = {}
# (host, state, election day) -> monotonic time a landing-page walk last
# found nothing. A results archive links every election back a decade
# (South Carolina's page, ~60 links, two requests each at 1 rps): walking
# it on every five-minute pass until the state links the new election
# would spend the whole pass on it. So a miss is remembered for
# _MISS_TTL_S and the walk is retried after that -- never remembered for
# good, since the link appearing later that night is the expected case.
# A walk that ended in a refusal (pick_general's UntrustedCount: several
# same-day elections, none singly the general) is remembered the same way,
# with its reason, and re-raised from memory until the TTL is up: it once
# re-walked ~16 EIDs (~32 requests at 1 rps) on every five-minute pass.
_general_eid_misses: dict[tuple[str, str, str], tuple[float, str | None]] = {}
_MISS_TTL_S = 20 * 60
# The most EIDs one walk probes. Newest first (Clarity's EIDs only grow),
# after the ones whose link text names the year, so the general is
# reached long before the cap on any real archive page.
_MAX_WALK = 16
# How much text after a link's href is read as its link text when looking
# for the year -- bounded, so a page whose markup never closes the anchor
# can't make "the rest of the document" count as one link's text.
_LINK_TEXT_WINDOW = 300


def _link_text(page: str, after: int) -> str:
    """The visible text of the link whose href ends at `after`: up to its
    closing </a>, never past _LINK_TEXT_WINDOW characters, and nothing at
    all when no </a> follows (str.find's -1 would otherwise slice to the
    page's last character)."""
    close = page.find("</a>", after, after + _LINK_TEXT_WINDOW)
    if close == -1:
        return ""
    return re.sub(r"<[^>]+>", "", page[after:close])


def _eid_order(eid: str) -> tuple[int, str]:
    return (-int(eid), eid) if eid.isdigit() else (0, eid)


async def _general_election_id(
    client: httpx.AsyncClient, state: str, election_day: str, discovery: dict, base: str = CLARITY_BASE,
) -> str | None:
    """The EID of the election held on `election_day`, found the same two
    ways _discover_election_id finds a primary — by date, not by name."""
    key = (base, state, election_day)
    if key in _general_eids:
        return _general_eids[key]
    if discovery.get("mode") == "landing_page":
        missed = _general_eid_misses.get(key)
        if missed is not None and time.monotonic() - missed[0] < _MISS_TTL_S:
            if missed[1] is not None:
                raise UntrustedCount(missed[1])  # the same refusal, not a fresh walk
            return None
        page_url, link_regex = discovery.get("page_url"), discovery.get("link_regex")
        if not page_url or not link_regex:
            return None
        resp = await _get(client, page_url, f"{state} Clarity landing page")
        if resp is None:
            return None
        page = resp.text
        year = election_day[:4]
        ids, named = set(), set()
        for m in re.finditer(link_regex, page):
            ids.add(m.group(1))
            if year in _link_text(page, m.end()):
                named.add(m.group(1))
        # Try first the links whose text names the year, then the rest,
        # newest first; stop at the first batch that holds that day.
        order = sorted(named, key=_eid_order) + sorted(ids - named, key=_eid_order)
        held = []
        for i, eid in enumerate(order[:_MAX_WALK]):
            if held and i == len(named):
                break
            meta = await _election_meta(client, state, eid, base)
            if meta is not None and _clarity_date(meta["Date"]) == election_day:
                held.append((meta.get("ElectionName") or "", eid))
        if len(order) > _MAX_WALK and not held:
            logger.warning(
                "%s Clarity landing page links %d elections; probed the newest %d, none held %s",
                state, len(order), _MAX_WALK, election_day,
            )
        try:
            found = pick_general(held, state)
        except UntrustedCount as refused:
            _general_eid_misses[key] = (time.monotonic(), str(refused))
            raise
        if not found:
            _general_eid_misses[key] = (time.monotonic(), None)
        return found
    resp = await _get(client, f"{base}/{state}/elections.json", f"{state} Clarity elections")
    if resp is None:
        return None
    try:
        elections = resp.json() or []
    except ValueError:
        return None
    held = [
        (e.get("ElectionName") or "", e.get("EID"))
        for e in elections
        if isinstance(e, dict) and _clarity_date(str(e.get("Date") or "")) == election_day and e.get("EID")
    ]
    return pick_general(held, state)


def general_contests(summary: dict) -> list[ContestCount]:
    """Every federal contest in a Clarity sum.json, counts kept. Parties
    come per choice (`P`), since a general-election contest's name carries
    none; `PR`/`TP` are units reporting / total."""
    out = []
    for contest in summary.get("Contests") or []:
        if not isinstance(contest, dict):
            continue
        name = contest.get("C") or ""
        parsed = _parse_office(name)
        if parsed is None:
            continue
        names, votes, parties = contest.get("CH") or [], contest.get("V") or [], contest.get("P") or []
        if not names or len(votes) != len(names):
            continue  # an envelope this doesn't understand: pair nothing wrongly
        candidates = []
        for i, raw in enumerate(names):
            if not isinstance(votes[i], int):
                continue
            code = str(parties[i]).strip() if i < len(parties) and parties[i] else ""
            # Not `name`: that is the CONTEST's label, read again below for
            # is_special. Reusing it here once left is_special reading the
            # last candidate's name, so a state's special Senate election
            # was merged into its regular one.
            choice = str(raw).strip()
            # South Carolina prints the party code before the name
            # ("REP Jane Doe"); it is the choice's own `P`, not a name.
            if code and choice.upper().startswith(code.upper() + " "):
                choice = choice[len(code) + 1:]
            if is_not_a_person(choice):
                continue
            candidates.append((_clean_display_name(choice), _parse_party(code) if code else None, votes[i]))
        tp, pr, total = contest.get("TP"), contest.get("PR"), contest.get("T")
        # The contest's own total (`T`) counts the write-in and other rows
        # dropped above; without it every share came out a little high.
        # Only a total at least the candidates' sum is believed.
        counted = sum(v for _, _, v in candidates)
        out.append(ContestCount(
            office=parsed[0], district=parsed[1], candidates=candidates,
            total_votes=total if isinstance(total, int) and total >= counted else None,
            reporting_units=pr if isinstance(pr, int) else None,
            total_units=tp if isinstance(tp, int) and tp > 0 else None,
            is_special=is_special_contest(name),
        ))
    return out


def _flag_set(value) -> bool:
    """A Clarity settings flag. The file mixes JSON booleans with strings:
    `istestmode` is false, but `showtestdatawatermark` is the STRING "0" on
    every real 2026 election (CO, IA, SC, WV primaries, read 2026-10-08), and
    a plain truthiness test read that "0" as a test watermark and refused
    every one of those states' counts. Off only when the value says off;
    anything else (a value this does not know) counts as set, so an
    unfamiliar flag errs toward refusing."""
    if value is None or value is False or value == 0:
        return False
    return str(value).strip().lower() not in ("", "0", "false", "no", "off")


async def fetch_general_results(
    client: httpx.AsyncClient, election_day, state: str, source: dict,
) -> StateCount | None:
    day = election_day.isoformat()
    # A state may host Clarity under its own domain (South Carolina's
    # enr-scvotes.org); the paths below it are the vendor's own.
    base = str(source.get("base_url") or CLARITY_BASE).rstrip("/")
    eid = await _general_election_id(client, state, day, source.get("discovery") or {}, base)
    if not eid:
        return None
    resp = await _get(client, f"{base}/{state}/{eid}/current_ver.txt", f"{state} Clarity version")
    version = resp.text.strip() if resp is not None else ""
    if not version.isdigit():
        return None
    resp = await _get(client, f"{base}/{state}/{eid}/{version}/json/sum.json", f"{state} Clarity summary")
    if resp is None:
        return None
    try:
        summary = resp.json() or {}
    except ValueError:
        return None
    # The election's own settings say whether this is a test run and which
    # day it is for. Clarity publishes dry runs on the real endpoints; a
    # settings file that can't be read is not taken as "not a test".
    settings = await _get(
        client, f"{base}/{state}/{eid}/{version}/json/en/electionsettings.json",
        f"{state} Clarity settings",
    )
    try:
        details = ((settings.json().get("settings") or {}).get("electiondetails") or {}) if settings else None
    except ValueError:
        details = None
    if details is None:
        return None
    key = (base, state, day)
    if _flag_set(details.get("istestmode")) or _flag_set(details.get("showtestdatawatermark")):
        _general_eids.pop(key, None)
        raise UntrustedCount(f"{state} Clarity election {eid} is in test mode")
    if _clarity_date(str(details.get("electiondate") or "")) != day:
        _general_eids.pop(key, None)
        raise UntrustedCount(f"{state} Clarity election {eid} is dated {details.get('electiondate')!r}, not {day}")
    # Trusted: this id is that day's production count, and is kept.
    _general_eids[key] = eid
    _general_eid_misses.pop(key, None)
    # A state-level Clarity summary aggregates its counties' own sub-
    # elections, and `TP` counts those counties, not precincts — Colorado's
    # Senate contest reads 64 of 64, its county count. Said as what it is.
    unit_label = "counties" if details.get("participatingcounties") else "precincts"
    modified = resp.headers.get("Last-Modified") if hasattr(resp, "headers") else None
    try:
        updated = parsedate_to_datetime(modified).astimezone(timezone.utc).replace(tzinfo=None) if modified else None
    except (TypeError, ValueError):
        updated = None
    return StateCount(
        source_name=source.get("source_name") or f"{state} Clarity results",
        page_url=f"{base}/{state}/{eid}/",
        # Clarity's payload carries no official/certified flag.
        official=False,
        unit_label=unit_label,
        contests=general_contests(summary),
        source_updated=updated,
        # current_ver.txt only ever increases as the state republishes —
        # within one EID, which is why it is stored scoped by it
        # (sync.freshness_problem compares only the same EID's).
        source_version=f"{eid}:{version}",
    )
