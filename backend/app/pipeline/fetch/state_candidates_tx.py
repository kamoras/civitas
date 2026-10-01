"""Texas's confirmed-general-election-candidate strategy — the Secretary
of State's official "Civix" Candidate Bio Portal (one of potentially many
per-state strategies in state_candidates.py; see that module for the
shared contract).

Verified live via network inspection of the real API (not scraping the
rendered Angular SPA) on 2026-08-09:

1. GET .../getElectionsByYear/{year} returns every election indexed for
   that year, e.g. primaries, runoffs, specials, and the general —
   distinguished by `cdElectionType`, not by string-matching the name
   ("GE" is General Election; "P"=Primary, "RU"=Runoff, "S"/"SR"=Special/
   Special Runoff). Filtering on `cdElectionType == "GE"` is evergreen
   against wording changes; the id itself (e.g. 53815 for 2026) changes
   every cycle and must never be hardcoded.

2. POST .../findQualifiedCandidates with that election's id returns every
   candidate qualified for ANY office that election cycle — not just
   federal. `cdOfficeType == "FD"` isolates Senate/House. `txOfficeName`
   is a free-text office label ("U. S. SENATOR ", "U. S. REPRESENTATIVE
   DISTRICT 12") that OFFICE_RE/SENATE_RE parse into (office, district).

3. `cdFilingStatus` is the authoritative status field: "CG" = "Candidate
   in the General Election" is the only status this module treats as
   confirmed. Verified against known-correct 2026 ground truth: the
   winner of a 2026 Senate primary runoff carries cdFilingStatus "CG", and
   the loser has no row at all under the GE election id (correctly absent,
   not fabricated as some other status).
   Other real statuses seen live: "LP" (Lost Primary), "LR" (Lost
   Runoff), "W" (Withdrawn), and independents can lack cdFilingStatus
   entirely and instead carry cdDeclarationStatus ("A"=Accepted,
   "R"=Rejected) — a real case verified live (a rejected independent
   Senate candidate). This module only trusts the well-verified "CG"
   signal and skips anything else, including declaration-only records —
   under-including a genuinely accepted independent is a smaller, safer
   gap than guessing at an unverified second rule.

No API key, no auth — public GET/POST JSON.

4. With `statewide_offices` set, the same response's STATE offices are
   read too, under the same CG / not-write-in gate — so a runoff is
   already settled here exactly as it is for Congress: Civix carries CG
   only for whoever the party's primary (or its May 26 runoff) actually
   nominated. Civix's own office type scopes them first: "SW" (statewide)
   and "SR" (state, by district) — never the county types "CW"/"CR" —
   and each label then goes through the shared statewide and legislative
   gates. SW also holds the Supreme Court and Court of Criminal Appeals,
   and SR the courts of appeals, district judges and district attorneys;
   the gates refuse every one of those (judicial is a separate opt-in
   this adapter does not claim). Verified against the real 2026 GE list:
   7 executive offices + 9 State Board of Education seats, all 150 House
   districts and the 16 Senate districts up this year.
"""

import logging
import re

import httpx

from app.contact import CONTACT_EMAIL
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.fetch.state_candidates_common import (
    PARTY_CODE_MAP,
    clean_display_name,
    parse_state_leg_office,
    parse_statewide_office,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

CIVIX_BASE = "https://goelect.txelections.civixapps.com/api-ivis-cbp/api/cbp"
CONFIRMED_FILING_STATUS = "CG"
WRITE_IN_CAND_TYPE = "WRTIN"
FEDERAL_OFFICE_TYPE = "FD"
# Civix's own office types for STATE offices: statewide, and state by
# district. County ("CW"/"CR") offices are never read.
STATE_OFFICE_TYPES = frozenset({"SW", "SR"})

# Civix's API 403s a request with no browser-like User-Agent at all
# (verified live) — an honest, identifying UA (same convention
# sec_tickers.py uses for the SEC's own documented UA request) works
# fine; no need to spoof a real browser's string.
_HEADERS = {"User-Agent": f"Civitas civic-transparency-platform {CONTACT_EMAIL}"}

# Two real requests per sync run (elections list, then candidates) — a
# light, polite pace is enough; this isn't FEC's thousands-of-calls scale.
_rate_limiter = RateLimiter(rps=1.0)

_SENATE_RE = re.compile(r"U\.?\s*S\.?\s*SENATOR", re.IGNORECASE)
_HOUSE_RE = re.compile(r"U\.?\s*S\.?\s*REPRESENTATIVE\s+DISTRICT\s+(\d+)", re.IGNORECASE)


def _parse_office(office_name: str) -> tuple[str, int | None] | None:
    """"U. S. SENATOR" -> ("S", None); "U. S. REPRESENTATIVE DISTRICT 12"
    -> ("H", 12); anything else (a state/county/judicial office, or a
    label this doesn't recognize) -> None, never guessed."""
    name = (office_name or "").strip()
    if _SENATE_RE.search(name):
        return "S", None
    m = _HOUSE_RE.search(name)
    if m:
        return "H", int(m.group(1))
    return None


def _state_office_record(row: dict) -> dict | None:
    """A statewide-executive or state-legislative record for one
    already-confirmed Civix row, or None for anything the gates refuse
    (a court, a district attorney, an unrecognised label)."""
    label = row.get("txOfficeName") or ""
    statewide = parse_statewide_office(label)
    seat = None
    if statewide is not None:
        office, district = statewide
    else:
        legislative = parse_state_leg_office(label)
        if legislative is None:
            return None
        office, district, seat = legislative
    # Civix's cdParty is already a one-letter code (R, D, L, G, I; W is a
    # write-in and never reaches here). Anything else is left out rather
    # than stored under a code the page cannot name.
    party = (row.get("cdParty") or "").strip()
    name = clean_display_name(
        (row.get("txFullNameBallot") or "").strip()
        or " ".join(
            p for p in (
                (row.get("txFirstNameBallot") or "").strip(),
                (row.get("txLastNameBallot") or "").strip(),
            ) if p
        )
    )
    if party not in PARTY_CODE_MAP or not name:
        return None
    record = {"office": office, "district": district, "party": party, "last_name": name}
    if seat is not None:
        record["seat"] = seat
    return record


async def _find_general_election_id(client: httpx.AsyncClient, year: int) -> int | None:
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", f"{CIVIX_BASE}/getElectionsByYear/{year}",
        timeout=30.0, log_label="TX Civix elections list", headers=_HEADERS,
    )
    if resp is None:
        return None
    for election in resp.json() or []:
        if election.get("cdElectionType") == "GE":
            return election.get("idElection")
    return None


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str = "TX", source: dict | None = None,
) -> list[dict] | None:
    """Every confirmed general-election federal candidate in Texas for
    `year`. `state` is part of the shared STRATEGIES signature (see
    state_candidates.py) and unused here — Civix's portal is a single
    Texas deployment, so unlike the Clarity adapter this one serves exactly
    one state. `source` is read only for its `statewide_offices` opt-in.
    Returns None on a fetch failure or if no "GE" election is indexed
    yet for that year (e.g. queried too early in the cycle) — the tri-
    state None-vs-[] discipline this codebase uses throughout (a real
    empty result is not the same as "couldn't check").

    Each item: {"office": "S"|"H", "district": int|None, "party": str,
    "last_name": str} — last_name is TX's own ballot-printed surname
    (txLastNameBallot), matched against Civitas's FEC-derived Candidate
    rows by the caller (state_candidates.py), not here. With the opt-in,
    state-office records follow the statewide shape instead: office is a
    STATEWIDE_OFFICE_LABELS or chamber key and last_name the whole
    printed name (see _state_office_record).
    """
    election_id = await _find_general_election_id(client, year)
    if election_id is None:
        logger.warning("No 'GE' election indexed yet for TX %d — skipping", year)
        return None

    resp = await fetch_with_retry(
        client, _rate_limiter, "POST", f"{CIVIX_BASE}/findQualifiedCandidates",
        timeout=60.0, log_label="TX Civix qualified candidates", headers=_HEADERS,
        json={
            "electionYear": year, "electionId": election_id,
            "party": None, "officeId": None, "officeType": None,
            "status": None, "countyId": None,
        },
    )
    if resp is None:
        return None

    want_state = bool((source or {}).get("statewide_offices"))
    results = []
    for row in resp.json() or []:
        office_type = row.get("cdOfficeType")
        if office_type != FEDERAL_OFFICE_TYPE and not (
            want_state and office_type in STATE_OFFICE_TYPES
        ):
            continue
        if row.get("cdFilingStatus") != CONFIRMED_FILING_STATUS:
            continue
        # A declared write-in also carries "CG" but is not printed on the
        # ballot. Every other certified-list adapter drops these; Civix
        # marks them in its own candidate-type field (party "W" too).
        if row.get("cdCandType") == WRITE_IN_CAND_TYPE:
            continue
        if office_type != FEDERAL_OFFICE_TYPE:
            state_record = _state_office_record(row)
            if state_record is not None:
                results.append(state_record)
            continue
        parsed = _parse_office(row.get("txOfficeName") or "")
        if parsed is None:
            continue
        last_name = (row.get("txLastNameBallot") or "").strip()
        party = row.get("cdParty")
        if not last_name or not party:
            continue
        office, district = parsed
        full = (row.get("txFullNameBallot") or "").strip() or " ".join(
            p for p in ((row.get("txFirstNameBallot") or "").strip(), last_name) if p
        )
        results.append({
            "office": office, "district": district,
            "party": party, "last_name": last_name,
            "display_name": full,
        })
    return results
