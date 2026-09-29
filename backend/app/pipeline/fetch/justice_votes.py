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

        current.append({
            "id": jid,
            "name": j.get("name", ""),
            "last_name": j.get("last_name", ""),
            "role_title": active_role.get("role_title", "Associate Justice"),
            # Oyez leaves this empty for some justices (Ketanji Brown
            # Jackson); justice_pipeline.resolve_appointment fills it, and
            # the party, from the presidents table.
            "appointing_president": appointing,
            "date_start": _unix_to_date(active_role.get("date_start")),
            "date_end": None,
            "is_active": True,
            "thumbnail_url": thumb,
        })

    logger.info("Found %d active justices from Oyez", len(current))
    return current


def _one_vote_per_justice(votes: list[dict], case_id: str) -> list[tuple[str, str, str]]:
    """(justice_id, vote, opinion_type) per justice in one decision.

    Oyez sometimes lists a justice twice. For two 2025-term cases (Enbridge
    Energy v. Nessel, 24-783; Hencely v. Fluor, 24-924) Ketanji Brown
    Jackson appears twice and another justice not at all, and the second
    row broke JusticeVote's (justice_id, case_id) key: every Sunday's
    justice refresh rolled back and left all nine scorecards stale. Rows
    that agree are one vote; rows that disagree cannot say which is right,
    so that justice's vote in that case is left out. The missing justice's
    vote is never reconstructed.
    """
    by_justice: dict[str, set[tuple[str, str]]] = {}
    for v in votes:
        justice_id = (v.get("member") or {}).get("identifier", "")
        if justice_id:
            by_justice.setdefault(justice_id, set()).add(
                (v.get("vote", ""), v.get("opinion_type", "none") or "none")
            )
    kept = []
    for justice_id, sides in by_justice.items():
        if len(sides) > 1:
            logger.warning("Oyez lists conflicting votes for %s in %s — leaving that vote out", justice_id, case_id)
            continue
        (vote, opinion), = sides
        kept.append((justice_id, vote, opinion))
    duplicated = sum(1 for v in votes if (v.get("member") or {}).get("identifier")) - len(by_justice)
    if duplicated:
        logger.warning("Oyez lists %d justice(s) more than once in %s", duplicated, case_id)
    return kept


async def fetch_case_votes(
    client: httpx.AsyncClient,
    terms: list[str] | None = None,
    per_page: int = 100,
) -> list[dict]:
    """Fetch per-justice vote data for cases in the given SCOTUS terms.

    A case's `decisions` array can legitimately hold more than one entry —
    e.g. Moyle v. United States (2023-23-726) carries both a "dismissal -
    improvidently granted" decision (5-4) and a separate "per curiam"
    decision (6-3) for the same docket, with different vote splits and, for
    some justices, different vote sides. Flattening every decision's votes
    (2026-08 audit) wrote 2 rows per justice for cases like this, one of
    them not uncommonly the opposite of the other. JusticeVote has no way
    to represent two decisions for one case, so only the LAST decision is
    kept — same "final/most recent event wins" convention this function
    already uses for decided_date below (`dates[-1]`).

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

            # Only the last decision — see this function's docstring on why
            # a case can carry more than one and why "last" is the tie-break.
            decision = decisions[-1]
            votes = decision.get("votes") or []
            maj_count = decision.get("majority_vote") or 0
            min_count = decision.get("minority_vote") or 0
            is_unanimous = min_count == 0 and maj_count > 0
            is_close = (maj_count - min_count) <= 1 and min_count > 0

            for justice_id, vote_side, opinion in _one_vote_per_justice(votes, case_id):
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
