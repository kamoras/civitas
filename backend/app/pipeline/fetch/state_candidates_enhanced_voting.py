"""Enhanced Voting's public election-night-reporting JSON API —
confirmed-candidate strategy for every state that publishes results
through it, not one module per state (see state_candidates.py for the
shared contract).

Vendor identified from the app's own bundle rather than guessed: its
footer template renders the literal string "enhanced voting" next to a
`poweredBy` client-text key. Note this is the same VENDOR, but a
different product surface, from the Enhanced Voting *tabular exports*
GA/WA/VA/UT/ID already read through state_candidates_tabular.py — those
states publish spreadsheets, this is the vendor's own results API. A
state on either surface stays a JSON config entry, never new code.

TWO calls, both plain unauthenticated GETs:

1. GET {base_url}/api/jurisdictions/{jurisdiction} — the jurisdiction
   envelope, whose `elections` array is also the election INDEX: each
   entry carries `publicElectionId`, `electionDate` and a display
   `name`. The id is never hardcoded (Rhode Island's is the rather
   hopeful "RI2026StatewidePrimary", which will not survive the cycle);
   this cycle's regular primary is matched on the date's YEAR plus the
   name, excluding the separate presidential primary, the same rule
   state_candidates_clarity.py uses against its own free-text index.
   The array is NOT date-ordered (verified live: Rhode Island's runs
   2024, 2024, 2026, 2025, ... ), so the newest MATCH is taken by date
   rather than by position.

2. GET {base_url}/api/elections/{jurisdiction}/{id}/data — `ballotItems`,
   one per contest, each with `contestType`, a display `name`, a
   `reportingStatus`, and `summaryResults.ballotOptions[]` carrying per
   candidate `name`, `voteCount`, `party.abbreviation`, `isWriteIn` and
   `isQualifiedWriteIn`.

FEDERAL-ONLY IS THE WHOLE RISK HERE, and Rhode Island is the worst case
this system has met for it: its STATE legislature is named the "General
Assembly", so its own state-house and state-senate seats are labelled
"Representative in General Assembly District 13" and "Senator in General
Assembly District 5" — a few characters away from the real federal
"Representative in Congress District 1" / "Senator in Congress" on the
very same ballot (140 of the 192 contests in its real 2026 primary are
these state seats, plus party "Senatorial District Committee" races).
Nothing here pattern-matches offices itself: parse_office is the single
shared gate, and it already refuses every one of those (verified against
all 192 real contest labels) because it demands an explicitly federal
phrase. `Senator in Congress` needed a new arm there to be recognised at
all — added alongside the `Representative in/to Congress` arm that
already existed, and safe for the same reason: no state chamber is
called "Congress".

WINNER DERIVATION. `isWinner` is null on every ballot option, including
in a fully certified contest (verified live), so it is not trusted; the
nominee is the top vote-getter via the shared pick_nominee, which is
only correct where a plurality wins the primary outright. A state whose
sub-majority leader goes to a RUNOFF carries `runoff_threshold_pct` in
its source entry and yields nothing rather than a guess. Note the
vendor's own ballot annotation is NOT a winner signal either: Rhode
Island appends "*" to the party-ENDORSED candidate, and its real 2026
RI-2 Republican primary was won by unendorsed Victor Mellor (8,837)
over endorsed "Stephen T. Skoly*" (6,380) — the asterisk is stripped
as an annotation by the shared surname(), never read as a result.

FRESHNESS follows the rule state_candidates_tabular._withheld already
applies to this same vendor's `isOfficialResults` flag, rather than
inventing a second policy for it: certification passes IMMEDIATELY, and
`settle_days` is the FAILSAFE underneath for a flag that never gets
flipped — not an extra condition on top. That ordering is the one the
observed failure mode calls for: Utah's 2026 primary was canvassed,
certified and had its signed canvass report published on this vendor's
own portal while `isOfficialResults` still read false a month later, so
a gate that waited for BOTH would be a gate that never opens. Rhode
Island's went the other way and flipped honestly — certified six days
after its 2026-09-09 primary — which is precisely the case that must
not be made to sit out an arbitrary extra fortnight.

STATEWIDE EXECUTIVE CONTESTS ride the same ballot and the same two
calls. parse_statewide_office is a second gate beside parse_office, as
conservative in its own direction: where parse_office must not read a
state legislative seat as federal, that one must not read a county or
municipal office as statewide. Rhode Island's real primary splits
192 contests into 6 federal, 9 statewide executive, 133 General
Assembly seats, 21 party committees and 23 local offices; the last
group is what the gate is for ("DEM Providence: Mayor"). A statewide
nominee is reduced with clean_display_name rather than surname -- there
is no FEC row to match them against, so the state's own printed name is
all there will ever be -- and stored as a StatewideNominee, never a
Candidate. A state only PUBLISHES them once its entry opts in with
statewide_offices (see state_candidate_sources.json's _contract for why
that flag is a truth condition, not a toggle).

Verified live 2026-09-17 against Rhode Island's real, certified 2026
statewide primary (40/40 localities reporting, isOfficialResults true),
all six federal contests: John F. Reed (Senate D, real incumbent, 98,473
of 128,194 over Connor F. Burbridge and Luis Daniel Muñoz), Raymond T.
McKay (Senate R, unopposed), Gabriel Amo (CD1 D, real incumbent,
unopposed), Kellie Keenan (CD1 R, unopposed), Seth Magaziner (CD2 D,
real incumbent, unopposed), Victor Mellor (CD2 R, real winner over the
party-endorsed candidate). Re-verified 2026-09-21 for the statewide
half, same certified data: all 9 executive contests resolve (Helena
Buonanno Foulkes and Aaron C. Guckian for Governor, Sabina Matos and
John J. Loughlin II for Lieutenant Governor, Kimberly Ahern and Alan
Leonard Gordon for Attorney General, Gregg M. Amore for Secretary of
State, James A. Diossa and Micholas A. Credle for General Treasurer),
with all 177 non-federal, non-statewide contests still refused.
"""

import logging
import re

import httpx

from app.pipeline.fetch.http_utils import fetch_json_with_retry
from app.pipeline.fetch.state_candidates_common import (
    runoff_threshold,
    clean_display_name,
    is_not_a_person,
    normalize_party,
    STATEWIDE_OFFICE_LABELS,
    parse_office,
    parse_state_leg_office,
    parse_statewide_office,
    resolve_confirmed_nominees,
    surname,
)
from app.pipeline.fetch.state_candidates_tabular import DEFAULT_SETTLE_DAYS, _settled
from app.pipeline.fetch.election_results import (
    ContestCount,
    StateCount,
    UntrustedCount,
    is_special_contest,
    parse_utc,
    pick_general,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_rate_limiter = RateLimiter(rps=1.0)

_PRIMARY_RE = re.compile(r"primary", re.IGNORECASE)
# A cycle's own regular primary is the only one that names federal
# nominees. The other two kinds in this index have to be excluded by
# name, and BOTH exclusions are load-bearing rather than defensive:
# Rhode Island's real index carries "Special Election Central Falls City
# Council Ward 4 & Referendum and Special Election Senate District 4
# Primary", a purely local contest whose name really does contain
# "Primary". Since the newest match wins below, a special primary held
# LATER in the same year than the statewide one would otherwise be
# chosen, and its ballot carries no federal contest at all -- so the
# state would silently stop confirming anyone, which looks exactly like
# a state that simply has no nominees yet.
_EXCLUDED_RE = re.compile(r"presidential|special", re.IGNORECASE)


def _text(entries: list | None) -> str:
    """This vendor renders every human-facing string as a list of
    per-language objects (`[{"languageId": "en", "text": ...}]`), even
    where only one language is configured. The English entry is taken
    when present rather than the first one, so a jurisdiction that adds
    a second language can't silently flip the labels this module parses
    offices out of into another language."""
    if not isinstance(entries, list):
        return ""
    for entry in entries:
        if isinstance(entry, dict) and entry.get("languageId") == "en":
            return str(entry.get("text") or "")
    for entry in entries:
        if isinstance(entry, dict) and entry.get("text"):
            return str(entry["text"])
    return ""


def _is_primary(election: dict, year: int) -> bool:
    """This cycle's regular primary. Scoped by the `electionDate` YEAR
    rather than trusting the name to carry it, and excluding both the
    separate presidential primary and the local special elections this
    index is mostly made of -- see _EXCLUDED_RE for why the special
    exclusion is not merely defensive."""
    if not str(election.get("electionDate") or "").startswith(f"{year}-"):
        return False
    name = _text(election.get("name"))
    return bool(_PRIMARY_RE.search(name)) and not _EXCLUDED_RE.search(name)


def _candidates(ballot_item: dict) -> list[tuple[str, str, int]]:
    """(display name, party code, votes) for every real candidate in one
    contest. Write-ins are dropped on the vendor's own two flags rather
    than by matching the word, and a choice whose party doesn't normalize
    is dropped the same way every other vendor module in this system
    drops one — not silently bucketed into a major party."""
    options = (ballot_item.get("summaryResults") or {}).get("ballotOptions") or []
    out = []
    for option in options:
        if not isinstance(option, dict):
            continue
        if option.get("isWriteIn") or option.get("isQualifiedWriteIn"):
            continue
        name = _text(option.get("name"))
        party = normalize_party(str((option.get("party") or {}).get("abbreviation") or ""))
        votes = option.get("voteCount")
        if not name or party is None or not isinstance(votes, int):
            continue
        out.append((name, party, votes))
    return out


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    base_url = str(source.get("base_url") or "").rstrip("/")
    jurisdiction = source.get("jurisdiction")
    if not base_url or not jurisdiction:
        logger.error("%s enhanced_voting config is missing base_url/jurisdiction", state)
        return None

    envelope = await fetch_json_with_retry(
        client, _rate_limiter,
        f"{base_url}/api/jurisdictions/{jurisdiction}",
        f"{state} Enhanced Voting elections index",
    )
    if envelope is None:
        return None
    elections = envelope.get("elections") if isinstance(envelope, dict) else None
    if not isinstance(elections, list):
        logger.warning("%s Enhanced Voting index carried no elections array", state)
        return None

    matches = [e for e in elections if isinstance(e, dict) and _is_primary(e, year)]
    if not matches:
        logger.info("No %d primary indexed yet for %s -- nothing to confirm", year, state)
        return []
    # Newest match wins: this index is not date-ordered, and a state that
    # ever lists two matching elections for one cycle (an amended re-post,
    # a separate special primary) must not be resolved by position.
    election_entry = max(matches, key=lambda e: str(e.get("electionDate")))
    election_id = election_entry.get("publicElectionId")
    held_on = str(election_entry.get("electionDate") or "")
    if not election_id:
        logger.warning("%s's matched primary carries no publicElectionId", state)
        return None

    payload = await fetch_json_with_retry(
        client, _rate_limiter,
        f"{base_url}/api/elections/{jurisdiction}/{election_id}/data",
        f"{state} Enhanced Voting results",
    )
    if payload is None:
        return None
    if not isinstance(payload, dict):
        logger.warning("%s Enhanced Voting results were not an object", state)
        return None

    # Same rule state_candidates_tabular._withheld already applies to this
    # vendor's flag: certification passes immediately, and settle_days is
    # the FAILSAFE for a flag that never gets flipped -- not an extra AND.
    if not (payload.get("election") or {}).get("isOfficialResults"):
        if not _settled(held_on, source.get("settle_days", DEFAULT_SETTLE_DAYS)):
            return []

    by_seat: dict[tuple[str, int | None, str], list[tuple[str, int]]] = {}
    statewide_by_seat: dict[tuple[str, int | None, str], list[tuple[str, int]]] = {}
    state_leg_by_seat: dict[tuple[str, int | None, str], list[tuple[str, int]]] = {}
    for ballot_item in payload.get("ballotItems") or []:
        if not isinstance(ballot_item, dict) or ballot_item.get("contestType") != "Candidate":
            continue
        contest_name = _text(ballot_item.get("name"))
        seat = None
        office_district = parse_office(contest_name)
        if office_district is not None:
            office, district = office_district
        else:
            # Not federal. The same ballot also elects this state's
            # executive officers, and their contests were being parsed
            # and thrown away -- parse_statewide_office is the second,
            # equally conservative gate for them, and returns None for
            # the state-legislative, municipal and party-committee
            # contests that make up the rest of the file. A statewide
            # office has no district, and its code can never collide with
            # parse_office's "S"/"H".
            statewide = parse_statewide_office(contest_name)
            office, district = statewide if statewide else (None, None)
            if office is None:
                # Third and last gate: a seat in the state's own
                # legislature. It has to refuse the party-committee and
                # municipal contests that make up the rest of the file,
                # and on this ballot those collide word-for-word with
                # what it is looking for ("Senatorial District Committee
                # District 13") -- see parse_state_leg_office.
                parsed_seat = parse_state_leg_office(contest_name)
                if parsed_seat is None:
                    continue  # local/committee contest -- see module docstring
                office, district, seat = parsed_seat
        if office_district is not None:
            bucket = by_seat
        elif office in STATEWIDE_OFFICE_LABELS:
            bucket = statewide_by_seat
        else:
            bucket = state_leg_by_seat
        for name, party, votes in _candidates(ballot_item):
            bucket.setdefault((office, district, party, seat), []).append((name, votes))

    threshold = runoff_threshold(source)
    # The two kinds of contest are resolved by the same shared tie-safe
    # machinery but reduced to DIFFERENT name forms, because they are
    # asked different questions afterwards. A federal nominee is matched
    # against an FEC row, which files surnames, so `surname` is the right
    # reduction. A statewide nominee has no FEC row to match or to render
    # from -- the state's own printed name is all there will ever be --
    # so it is kept whole, with only the ballot annotations stripped.
    return resolve_confirmed_nominees(
        by_seat, threshold, name_transform=surname,
    ) + resolve_confirmed_nominees(
        statewide_by_seat, threshold, name_transform=clean_display_name,
    ) + resolve_confirmed_nominees(
        state_leg_by_seat, threshold, name_transform=clean_display_name,
    )


# --- Live general-election counts (fetch/election_results.py) -------------

def _endpoints(source: dict) -> tuple[str, str, str] | None:
    """(index url, data url template with {id}, public page template with
    {id}) for either way a state's entry names this vendor's portal: Rhode
    Island's base_url + jurisdiction, or the jurisdiction_url/election_url
    pair GA/WA/VA/UT/ID's tabular entries carry for the same API."""
    base_url = str(source.get("base_url") or "").rstrip("/")
    jurisdiction = source.get("jurisdiction")
    if base_url and jurisdiction:
        return (
            f"{base_url}/api/jurisdictions/{jurisdiction}",
            f"{base_url}/api/elections/{jurisdiction}/{{id}}/data",
            f"{base_url}/{jurisdiction}/elections/{{id}}",
        )
    discovery = source.get("discovery") or {}
    index_url, election_url = discovery.get("jurisdiction_url"), discovery.get("election_url")
    if not index_url or not election_url or "/api/jurisdictions/" not in index_url:
        return None
    public_base, jurisdiction = index_url.split("/api/jurisdictions/", 1)
    return (
        index_url,
        election_url.replace("{election_id}", "{id}") + "/data",
        f"{public_base}/{jurisdiction}/elections/{{id}}",
    )


def _house_patterns(source: dict) -> list[re.Pattern]:
    """The state's own election-night House label formats, from its entry's
    `house_label_regex` (one pattern or a list, each with a `district`
    group). parse_office is the shared gate and stays conservative; these
    are the labels it cannot read, and each is one state's printed format,
    verified against that state's own general-election payload:

      UT  "U.S. House 1 "   parse_office reads it as House but finds no
          district, so every seat would collapse onto one at-large race
      VA  "Member, House of Representatives (2nd District)"   no "U.S.";
          refused outright, exactly as the primary's house_from_columns
          entry documents
      GA  "US House Dist 3"   2022's short form, printed beside the long
          "US House of Representatives - District 1" on the same ballot

    Configured per state rather than added to parse_office, because the
    bare forms are only unambiguous inside that one state's federal
    ballot: "House 1" or "House of Representatives (2nd District)" is a
    state legislative seat's label somewhere else."""
    raw = source.get("house_label_regex")
    if not raw:
        return []
    patterns = []
    for text in [raw] if isinstance(raw, str) else raw:
        try:
            pattern = re.compile(text, re.IGNORECASE)
        except re.error:
            logger.error("house_label_regex %r does not compile", text)
            continue
        if "district" not in pattern.groupindex:
            logger.error("house_label_regex %r has no (?P<district>...) group", text)
            continue
        patterns.append(pattern)
    return patterns


def _office(label: str, house_patterns: list[re.Pattern]) -> tuple[str, int | None] | None:
    """A contest's federal seat: the state's own House formats first (a
    label they match is that district, whatever parse_office would make
    of it), then the shared gate."""
    for pattern in house_patterns:
        m = pattern.fullmatch(label.strip())
        if m:
            return "H", int(m.group("district"))
    return parse_office(label)


def _general_options(item: dict) -> list[tuple[str, str | None, int]]:
    """Every candidate's (name, party, votes) in one contest of a live count.

    Write-ins are told apart on the vendor's own two flags. A QUALIFIED
    write-in is a real, named candidate the state certified to receive
    write-in votes (Utah's 2024 general lists them with isWriteIn AND
    isQualifiedWriteIn true -- "STEVE M. JOHNSON", verified 2026-09-28),
    so their votes are theirs and they stay a candidate. Only an
    UNqualified write-in line is dropped -- Virginia's single aggregate
    "Write-In" row per contest (isWriteIn true, isQualifiedWriteIn false)
    -- and its votes still reach the contest's voteTotal. Washington also
    prints a "Write-In" row with BOTH flags false, which is why the
    aggregate-label check stays as well."""
    candidates = []
    for option in (item.get("summaryResults") or {}).get("ballotOptions") or []:
        if not isinstance(option, dict):
            continue
        if option.get("isWriteIn") and not option.get("isQualifiedWriteIn"):
            continue
        label, votes = _text(option.get("name")), option.get("voteCount")
        if not label or is_not_a_person(label) or not isinstance(votes, int):
            continue
        party = normalize_party(str((option.get("party") or {}).get("abbreviation") or ""))
        candidates.append((clean_display_name(label), party, votes))
    return candidates


def general_contests(payload: dict, house_patterns: list[re.Pattern] | None = None) -> list[ContestCount]:
    out = []
    for item in payload.get("ballotItems") or []:
        if not isinstance(item, dict) or item.get("contestType") != "Candidate":
            continue
        name = _text(item.get("name"))
        parsed = _office(name, house_patterns or [])
        if parsed is None:
            continue
        status = item.get("reportingStatus") or {}
        reporting, total = status.get("reportingUnits"), status.get("totalUnits")
        vote_total = item.get("voteTotal")
        out.append(ContestCount(
            office=parsed[0], district=parsed[1], candidates=_general_options(item),
            total_votes=vote_total if isinstance(vote_total, int) else None,
            reporting_units=reporting if isinstance(reporting, int) else None,
            total_units=total if isinstance(total, int) and total > 0 else None,
            is_special=is_special_contest(name),
        ))
    return out


def _unit_label(payload: dict, source: dict | None = None) -> str:
    """What this election's reporting units are. Every Enhanced Voting
    state verified so far reports by LOCALITY -- a county, or Virginia's
    counties and independent cities, or Rhode Island's cities and towns --
    not by precinct: a statewide contest's totalUnits is exactly the number
    of localityElections (UT 29/29, VA 133/133, GA 159/159, ID 44/44,
    WA 39/39, RI 40/40; verified 2026-09-28). Said as the state's own
    localities where that holds (its `locality_label`, default counties:
    Rhode Island has five counties, not forty), since "29 of 29 precincts"
    would claim a precinct-level completeness the source never stated;
    anything else stays precincts.

    A statewide item isn't always on the ballot (Utah elects no senator in
    2026), and a House contest counts only the localities it covers (UT's
    districts: 8, 13, 11 and 4 of 29). So a ballot whose every contest
    counts no more units than the state has localities is locality-counted
    too; any contest counting more is by precinct."""
    localities = len([loc for loc in payload.get("localityElections") or [] if isinstance(loc, dict)])
    if not localities:
        return "precincts"
    totals = [
        t for item in payload.get("ballotItems") or [] if isinstance(item, dict)
        for t in [(item.get("reportingStatus") or {}).get("totalUnits")]
        if isinstance(t, int) and not isinstance(t, bool) and t > 0
    ]
    if localities not in totals and not (totals and max(totals) <= localities):
        return "precincts"
    return (source or {}).get("locality_label") or "counties"


# This vendor's own marker for a practice copy of an election, carried in
# the id rather than the display name: Utah's index lists
# "primary09052023_Demo" as a plain "2023 Primary Election" (verified
# 2026-09-28). A demo is never the count, whatever it is dated.
_DEMO_ID_RE = re.compile(r"(?:^|[_\-\s])(?:demo|test|preview)(?:$|[_\-\s])", re.IGNORECASE)


async def fetch_general_results(
    client: httpx.AsyncClient, election_day, state: str, source: dict,
) -> StateCount | None:
    endpoints = _endpoints(source)
    if endpoints is None:
        return None
    index_url, data_url, page_url = endpoints
    envelope = await fetch_json_with_retry(client, _rate_limiter, index_url, f"{state} Enhanced Voting index")
    elections = envelope.get("elections") if isinstance(envelope, dict) else None
    if not isinstance(elections, list):
        return None
    day = election_day.isoformat()
    held = [
        e for e in elections
        if isinstance(e, dict) and str(e.get("electionDate") or "").startswith(day) and e.get("publicElectionId")
    ]
    demos = [e for e in held if _DEMO_ID_RE.search(str(e["publicElectionId"]))]
    if demos:
        logger.info("%s Enhanced Voting: ignoring demo election(s) %s", state, [e["publicElectionId"] for e in demos])
    election = pick_general(
        [(_text(e.get("name")), e) for e in held if not _DEMO_ID_RE.search(str(e["publicElectionId"]))], state,
    )
    if election is None:
        return None
    eid = election["publicElectionId"]
    payload = await fetch_json_with_retry(
        client, _rate_limiter, data_url.format(id=eid), f"{state} Enhanced Voting general results",
    )
    if not isinstance(payload, dict):
        return None
    meta = payload.get("election") or {}
    # The vendor's own page stamps "TEST" across any election whose
    # isProduction is false (its public bundle, verified 2026-09-28), and a
    # county's locality election carries its own flag: a count with any
    # non-production part in it is not the count.
    if meta.get("isProduction") is not True or any(
        isinstance(loc, dict) and loc.get("isProduction") is False for loc in payload.get("localityElections") or []
    ):
        raise UntrustedCount(f"{state} Enhanced Voting election {eid} is not production data")
    if not str(meta.get("electionDate") or "").startswith(day):
        raise UntrustedCount(f"{state} Enhanced Voting answered for {meta.get('electionDate')!r}, not {day}")
    return StateCount(
        source_name=source.get("source_name") or f"{state} election results",
        page_url=page_url.format(id=eid),
        official=bool(meta.get("isOfficialResults")),
        unit_label=_unit_label(payload, source),
        contests=general_contests(payload, _house_patterns(source)),
        source_updated=parse_utc(meta.get("lastUpdated")) or parse_utc(meta.get("asOf")),
    )
