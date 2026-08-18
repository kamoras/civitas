"""Pennsylvania's own election-returns API — a single-state deployment, so
this is a vendor module in the same sense state_candidates_tx.py is, not a
per-state fetcher (see state_candidates.py for why that distinction is the
whole design).

Pennsylvania looked unreachable for months: electionreturns.pa.gov's front
page is behind Imperva and answers a bot challenge, which is what earlier
attempts hit. The site's API is NOT — it answers a plain server-side
request with an honest User-Agent. The endpoints below were read off the
page's own network activity in a browser on 2026-08-18, which is the only
reliable way to find them, and then confirmed to work with no browser at
all.

Three hops, nothing cycle-specific written down:

1. GetAllElections lists every election with an `ElectionType` code — "P"
   for a general primary, "G" for the general, "S" for a special. Matching
   the CODE rather than the name is what keeps this evergreen; the id
   (117 for 2026) changes every cycle.
2. GetOfficeNames maps offices to ids for that election, again by CODE:
   "USC" is Representative in Congress, "USS" United States Senator. A
   state legislature's own chambers carry different codes and are never
   picked up.
3. GetOfficeData returns the results nested district -> party ->
   candidates, with votes.

Verified live on the real 2026-05-19 primary (election 117): Bob Harvie
taking the PA-01 Democratic primary with 65.14%.
"""

import json
import logging

import httpx

from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.fetch.state_candidates_common import (
    normalize_party, parse_office, pick_nominees, surname,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

API_BASE = "https://www.electionreturns.pa.gov/api/ElectionReturn"
_HEADERS = {"User-Agent": "Civitas civic-transparency-platform contact@civitas-research.org"}
_rate_limiter = RateLimiter(rps=0.5)

# The election TYPE and OFFICE codes this reads, rather than any wording.
PRIMARY_TYPE = "P"
FEDERAL_OFFICE_CODES = {"USC", "USS"}


async def _get(client: httpx.AsyncClient, url: str, label: str):
    """Every response is JSON that has been serialised TWICE — the API
    returns a JSON string whose contents are themselves JSON — so this
    unwraps until it stops being a string."""
    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", url, timeout=60.0,
        retry_on_4xx=False, log_label=label, headers=_HEADERS,
    )
    if resp is None:
        return None
    try:
        payload = resp.json()
    except ValueError:
        logger.warning("%s was not JSON", label)
        return None
    for _ in range(3):
        if not isinstance(payload, str):
            return payload
        try:
            payload = json.loads(payload)
        except ValueError:
            return None
    return payload


async def _primary_election_id(client: httpx.AsyncClient, year: int) -> str | None:
    rows = await _get(
        client, f"{API_BASE}/GetAllElections?methodName=GetAllElections", "PA elections",
    )
    if not isinstance(rows, list):
        return None
    for row in rows:
        elections = row.get("ElectionData") if isinstance(row, dict) else None
        if isinstance(elections, str):
            try:
                elections = json.loads(elections)
            except ValueError:
                continue
        for election in elections or []:
            if (str(election.get("ElectionYear")) == str(year)
                    and election.get("ElectionType") == PRIMARY_TYPE):
                return str(election.get("Electionid"))
    return None


async def _federal_office_ids(
    client: httpx.AsyncClient, election_id: str,
) -> list[tuple[int, str]]:
    payload = await _get(
        client,
        f"{API_BASE}/GetOfficeNames?countyName=&methodName=GetOfficeNames"
        f"&electionid={election_id}&electiontype={PRIMARY_TYPE}&isactive=0",
        "PA offices",
    )
    table = (payload or {}).get("Table") if isinstance(payload, dict) else None
    return [
        (row.get("OfficeID"), row.get("OfficeName") or "")
        for row in table or []
        if row.get("OfficeCode") in FEDERAL_OFFICE_CODES and row.get("OfficeID") is not None
    ]


def _contests(payload: dict, office_name: str) -> list[tuple[str, dict]]:
    """(contest label, {party: [candidate rows]}) for one office.

    The label is the office plus the district as Pennsylvania writes it
    ("Representative in Congress 1st Congressional District"), which
    parse_office reads without help — "Congress" is decisive and the
    ordinal gives the district.
    """
    out = []
    for office_block in ((payload or {}).get("Election") or {}).get(office_name, []) or []:
        for districts in (office_block or {}).values():
            for district in districts or []:
                label = f"{office_name} {district.get('District') or ''}".strip()
                by_party: dict[str, list] = {}
                for group in district.get("Candidates") or []:
                    for party, rows in (group or {}).items():
                        by_party.setdefault(party, []).extend(rows or [])
                out.append((label, by_party))
    return out


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,
) -> list[dict] | None:
    """Pennsylvania's confirmed federal nominees for `year`, or None on a
    fetch/parse failure — same contract every other strategy honours.

    Pennsylvania nominates on a plurality, so `runoff_threshold_pct` is
    expected to be null; it is still read from config rather than assumed,
    exactly as the other adapters do.
    """
    threshold = source.get("runoff_threshold_pct")
    election_id = await _primary_election_id(client, year)
    if not election_id:
        logger.warning("No %d primary indexed yet for PA — skipping", year)
        return None

    records: list[dict] = []
    for office_id, office_name in await _federal_office_ids(client, election_id):
        payload = await _get(
            client,
            f"{API_BASE}/GetOfficeData?officeId={office_id}&methodName=GetOfficeDetails"
            f"&electionid={election_id}&electiontype={PRIMARY_TYPE}&isactive=0",
            f"PA office {office_id}",
        )
        if payload is None:
            return None
        for label, by_party in _contests(payload, office_name):
            parsed = parse_office(label)
            if parsed is None:
                continue
            office, district = parsed
            for party_name, rows in by_party.items():
                party = normalize_party(party_name)
                if party is None:
                    continue
                choices = [
                    (r.get("CandidateName") or "", int(str(r.get("Votes") or "0").replace(",", "") or 0))
                    for r in rows
                    if str(r.get("Votes") or "").replace(",", "").isdigit()
                ]
                won = pick_nominees(choices, threshold)
                for name, _pct in won:
                    last_name = surname(name)
                    if last_name:
                        records.append({
                            "office": office, "district": district,
                            "party": party, "last_name": last_name,
                        })
    return records
