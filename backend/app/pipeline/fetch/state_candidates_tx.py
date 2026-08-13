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
   confirmed. Verified against known-correct 2026 ground truth: Ken
   Paxton beat John Cornyn in the May 26 Republican runoff — Paxton's row
   carries cdFilingStatus "CG", Cornyn has no row at all under the GE
   election id (correctly absent, not fabricated as some other status).
   Other real statuses seen live: "LP" (Lost Primary), "LR" (Lost
   Runoff), "W" (Withdrawn), and independents can lack cdFilingStatus
   entirely and instead carry cdDeclarationStatus ("A"=Accepted,
   "R"=Rejected) — a real case verified live (a rejected independent
   Senate candidate). This module only trusts the well-verified "CG"
   signal and skips anything else, including declaration-only records —
   under-including a genuinely accepted independent is a smaller, safer
   gap than guessing at an unverified second rule.

No API key, no auth — public GET/POST JSON.
"""

import logging
import re

import httpx

from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

CIVIX_BASE = "https://goelect.txelections.civixapps.com/api-ivis-cbp/api/cbp"
CONFIRMED_FILING_STATUS = "CG"

# Civix's API 403s a request with no browser-like User-Agent at all
# (verified live) — an honest, identifying UA (same convention
# sec_tickers.py uses for the SEC's own documented UA request) works
# fine; no need to spoof a real browser's string.
_HEADERS = {"User-Agent": "Civitas civic-transparency-platform contact@civitas-research.org"}

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
    `year`. `state`/`source` are part of the shared STRATEGIES signature
    (see state_candidates.py) and unused here — Civix's portal is a single
    Texas deployment, so unlike the Clarity adapter this one serves exactly
    one state. Returns None on a fetch failure or if no "GE" election is indexed
    yet for that year (e.g. queried too early in the cycle) — the tri-
    state None-vs-[] discipline this codebase uses throughout (a real
    empty result is not the same as "couldn't check").

    Each item: {"office": "S"|"H", "district": int|None, "party": str,
    "last_name": str} — last_name is TX's own ballot-printed surname
    (txLastNameBallot), matched against Civitas's FEC-derived Candidate
    rows by the caller (state_candidates.py), not here.
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

    results = []
    for row in resp.json() or []:
        if row.get("cdOfficeType") != "FD":
            continue
        if row.get("cdFilingStatus") != CONFIRMED_FILING_STATUS:
            continue
        parsed = _parse_office(row.get("txOfficeName") or "")
        if parsed is None:
            continue
        last_name = (row.get("txLastNameBallot") or "").strip()
        party = row.get("cdParty")
        if not last_name or not party:
            continue
        office, district = parsed
        results.append({
            "office": office, "district": district,
            "party": party, "last_name": last_name,
        })
    return results
