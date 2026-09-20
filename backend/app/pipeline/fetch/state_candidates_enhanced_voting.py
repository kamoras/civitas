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

Verified live 2026-09-17 against Rhode Island's real, certified 2026
statewide primary (40/40 localities reporting, isOfficialResults true),
all six federal contests: John F. Reed (Senate D, real incumbent, 98,473
of 128,194 over Connor F. Burbridge and Luis Daniel Muñoz), Raymond T.
McKay (Senate R, unopposed), Gabriel Amo (CD1 D, real incumbent,
unopposed), Kellie Keenan (CD1 R, unopposed), Seth Magaziner (CD2 D,
real incumbent, unopposed), Victor Mellor (CD2 R, real winner over the
party-endorsed candidate).
"""

import logging
import re

import httpx

from app.pipeline.fetch.http_utils import fetch_json_with_retry
from app.pipeline.fetch.state_candidates_common import (
    normalize_party,
    parse_office,
    resolve_confirmed_nominees,
    surname,
)
from app.pipeline.fetch.state_candidates_tabular import DEFAULT_SETTLE_DAYS, _settled
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
    for ballot_item in payload.get("ballotItems") or []:
        if not isinstance(ballot_item, dict) or ballot_item.get("contestType") != "Candidate":
            continue
        office_district = parse_office(_text(ballot_item.get("name")))
        if office_district is None:
            continue  # state/local contest on the same ballot -- see module docstring
        office, district = office_district
        for name, party, votes in _candidates(ballot_item):
            by_seat.setdefault((office, district, party), []).append((name, votes))

    return resolve_confirmed_nominees(
        by_seat, source.get("runoff_threshold_pct"), name_transform=surname,
    )
