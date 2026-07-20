"""Fetch per-justice voting data from the Oyez API.

The Oyez case detail endpoint (api.oyez.org/cases/{term}/{docket}) includes
a `decisions` array with per-justice vote records: majority/minority,
opinion type (author, dissent, concurrence), and who they joined.

We fetch case details for recent terms and extract structured vote records
for each sitting justice.
"""

import asyncio
import logging
from datetime import UTC, datetime

import httpx

from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S
from app.pipeline.fetch.oyez_common import OYEZ_BASE, unix_to_date as _unix_to_date

logger = logging.getLogger(__name__)

_PRESIDENT_PARTY: dict[str, str] = {
    "George Washington": "",
    "John Adams": "F",
    "Thomas Jefferson": "DR",
    "James Madison": "DR",
    "James Monroe": "DR",
    "John Quincy Adams": "DR",
    "Andrew Jackson": "D",
    "Martin Van Buren": "D",
    "John Tyler": "W",
    "James K. Polk": "D",
    "Millard Fillmore": "W",
    "Franklin Pierce": "D",
    "James Buchanan": "D",
    "Abraham Lincoln": "R",
    "Ulysses S. Grant": "R",
    "Rutherford B. Hayes": "R",
    "James A. Garfield": "R",
    "Chester A. Arthur": "R",
    "Grover Cleveland": "D",
    "Benjamin Harrison": "R",
    "William McKinley": "R",
    "Theodore Roosevelt": "R",
    "William Howard Taft": "R",
    "Woodrow Wilson": "D",
    "Warren G. Harding": "R",
    "Calvin Coolidge": "R",
    "Herbert Hoover": "R",
    "Franklin D. Roosevelt": "D",
    "Harry S. Truman": "D",
    "Dwight D. Eisenhower": "R",
    "John F. Kennedy": "D",
    "Lyndon B. Johnson": "D",
    "Richard Nixon": "R",
    "Gerald Ford": "R",
    "Jimmy Carter": "D",
    "Ronald Reagan": "R",
    "George H. W. Bush": "R",
    "Bill Clinton": "D",
    "George W. Bush": "R",
    "Barack Obama": "D",
    "Donald J. Trump": "R",
    "Donald Trump": "R",
    "Joe Biden": "D",
    "Joseph R. Biden": "D",
}

_OYEZ_DATA_GAPS: dict[str, tuple[str, str]] = {
    "ketanji_brown_jackson": ("Joe Biden", "D"),
}


def _appointing_party(president_name: str | None) -> str:
    if not president_name:
        return ""
    return _PRESIDENT_PARTY.get(president_name, "")


async def fetch_current_justices(client: httpx.AsyncClient) -> list[dict]:
    """Fetch the list of current (active) Supreme Court justices from Oyez."""
    resp = await client.get(f"{OYEZ_BASE}/justices", timeout=DEFAULT_FETCH_TIMEOUT_S)
    if resp.status_code != 200:
        logger.warning("Oyez justices endpoint returned %d", resp.status_code)
        return []

    all_justices = resp.json()
    if not isinstance(all_justices, list):
        return []

    current = []
    for j in all_justices:
        roles = j.get("roles") or []
        is_active = any(r.get("date_end") == 0 for r in roles)
        if not is_active:
            continue

        active_role = next((r for r in roles if r.get("date_end") == 0), roles[-1] if roles else {})
        appointing = active_role.get("appointing_president") or ""
        thumb = (j.get("thumbnail") or {}).get("href", "")

        jid = j.get("identifier", "")
        party = _appointing_party(appointing)

        if (not appointing or not party) and jid in _OYEZ_DATA_GAPS:
            appointing, party = _OYEZ_DATA_GAPS[jid]

        current.append({
            "id": jid,
            "name": j.get("name", ""),
            "last_name": j.get("last_name", ""),
            "role_title": active_role.get("role_title", "Associate Justice"),
            "appointing_president": appointing,
            "appointing_party": party,
            "date_start": _unix_to_date(active_role.get("date_start")),
            "date_end": None,
            "is_active": True,
            "thumbnail_url": thumb,
        })

    logger.info("Found %d active justices from Oyez", len(current))
    return current


async def fetch_case_votes(
    client: httpx.AsyncClient,
    terms: list[str] | None = None,
    per_page: int = 100,
) -> list[dict]:
    """Fetch per-justice vote data for cases in the given SCOTUS terms.

    Returns a list of vote records:
    {
        "case_id": str,
        "case_name": str,
        "case_term": str,
        "decided_date": str,
        "justice_id": str,
        "vote": "majority" | "minority",
        "opinion_type": "majority" | "dissent" | "concurrence" | "none",
        "is_unanimous": bool,
        "is_close": bool,
        "majority_votes": int,
        "minority_votes": int,
    }
    """
    if terms is None:
        current_year = datetime.now(tz=UTC).year
        terms = [str(y) for y in range(current_year, current_year - 4, -1)]

    case_refs: list[dict] = []
    for term in terms:
        try:
            resp = await client.get(
                f"{OYEZ_BASE}/cases",
                params={"per_page": per_page, "filter": f"term:{term}"},
                timeout=DEFAULT_FETCH_TIMEOUT_S,
            )
            if resp.status_code != 200:
                continue
            cases = resp.json()
            if isinstance(cases, list):
                for c in cases:
                    docket = (c.get("docket_number") or "").strip()
                    if docket:
                        case_refs.append({"term": term, "docket": docket, "href": c.get("href", "")})
        except Exception as e:
            logger.warning("Oyez case list fetch failed for term %s: %s", term, e)
        await asyncio.sleep(0.3)

    logger.info("Found %d case refs across terms %s", len(case_refs), ", ".join(terms))

    all_votes: list[dict] = []
    for i, ref in enumerate(case_refs):
        try:
            detail_url = ref["href"] or f"{OYEZ_BASE}/cases/{ref['term']}/{ref['docket']}"
            resp = await client.get(detail_url, timeout=DEFAULT_FETCH_TIMEOUT_S)
            if resp.status_code != 200:
                continue

            case_data = resp.json()
            decisions = case_data.get("decisions") or []
            if not decisions:
                continue

            case_name = case_data.get("name", "")
            decided_date = ""
            for ev in (case_data.get("timeline") or []):
                if ev.get("event") == "Decided":
                    dates = ev.get("dates", [])
                    if dates:
                        decided_date = _unix_to_date(dates[-1])
                    break

            if not decided_date:
                continue

            case_id = f"scotus-{ref['term']}-{ref['docket']}"

            for decision in decisions:
                votes = decision.get("votes") or []
                maj_count = decision.get("majority_vote") or 0
                min_count = decision.get("minority_vote") or 0
                is_unanimous = min_count == 0 and maj_count > 0
                is_close = (maj_count - min_count) <= 1 and min_count > 0

                for v in votes:
                    member = v.get("member") or {}
                    justice_id = member.get("identifier", "")
                    if not justice_id:
                        continue

                    vote_side = v.get("vote", "")
                    opinion = v.get("opinion_type", "none") or "none"

                    all_votes.append({
                        "case_id": case_id,
                        "case_name": case_name,
                        "case_term": ref["term"],
                        "decided_date": decided_date,
                        "justice_id": justice_id,
                        "vote": vote_side,
                        "opinion_type": opinion,
                        "is_unanimous": is_unanimous,
                        "is_close": is_close,
                        "majority_votes": maj_count,
                        "minority_votes": min_count,
                    })

        except httpx.TimeoutException:
            logger.warning("Oyez case detail timed out for %s", ref["docket"])
        except Exception as e:
            logger.warning("Oyez case detail failed for %s: %s", ref["docket"], e)

        if (i + 1) % 20 == 0:
            logger.info("  Fetched case details: %d/%d", i + 1, len(case_refs))
            await asyncio.sleep(0.5)
        else:
            await asyncio.sleep(0.2)

    logger.info("Extracted %d vote records from %d cases", len(all_votes), len(case_refs))
    return all_votes
