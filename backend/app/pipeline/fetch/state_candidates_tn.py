"""Tennessee's own precinct-level primary results workbook — a genuinely
different SHAPE from every other state on file, so a bespoke module (like
state_candidates_pa.py) even though the FILE itself is an ordinary xlsx
readable with state_candidates_tabular's own `_xlsx_rows`.

Every other tabular state publishes one row per candidate. Tennessee
publishes one row per PRECINCT per office per party, with up to 5
candidates packed sideways into repeated column groups (RNAME1/PARTY1/
PVTALLY1 .. RNAME5/PARTY5/PVTALLY5) — a genuine county-election-system
export shape, not a results-vendor API. Getting a statewide total means
summing PVTALLY{n} for the same candidate across all ~40,000 precinct
rows, which state_candidates_tabular's row-per-candidate format has no
way to express; unpivoting-and-summing is the whole reason this is its
own module rather than a tabular config entry.

sos.tn.gov itself sits behind Cloudflare and answers a bot challenge to
a bare request — but the file host (sos-prod.tnsosgovfiles.com) does
not, so the results page is read with BROWSER_HEADERS (the same "answer
the challenge honestly" approach that already unblocked several other
states) and the file itself needs no special handling at all. Discovery
reuses state_candidates_tabular's landing_page mode as-is: the results
page lists every past election's files, dated in the filename itself
("20260806AllbyPrecinct.xlsx"), so the current cycle's file is found by
date the same way Florida's is.

Tennessee requires a MAJORITY (not a plurality) for a federal primary
to be decided outright — Tenn. Code Ann. 2-8-113: any of governor, US
Senator or US Representative that fails to clear 50% goes to a runoff
the last Thursday in August. runoff_threshold_pct is 50 for exactly
this reason; pick_nominee already withholds a sub-threshold leader
rather than mislabel a runoff-bound candidate as the nominee — verified
live against the real 2026 primary, where 2 of 9 House Republican
fields and 4 of 9 House Democratic fields (real 3+-way splits) came in
under 50% and are correctly left unconfirmed by this alone. Merging the
runoff itself is the documented upgrade (ponytail: this file only ever
carries the August primary; a second, later file decides those seats).

Tennessee publishes no certification flag anywhere in this file, same
as Florida — settle_days is the only gate.
"""

import logging
import re

import httpx

from app.pipeline.fetch.http_utils import BROWSER_HEADERS, fetch_with_retry
from app.pipeline.fetch.state_candidates_common import normalize_party, pick_nominee, surname
from app.pipeline.fetch.state_candidates_tabular import (
    MAX_DOWNLOAD_BYTES, _discover_urls, _withheld, _xlsx_rows,
)
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

DISCOVERY = {
    "mode": "landing_page",
    "page_url": "https://sos.tn.gov/elections/results",
    # The bare filename alone isn't enough: without capturing the full
    # absolute URL, urljoin resolves a matched fragment relative to
    # page_url's own path instead of the real file host
    # (sos-prod.tnsosgovfiles.com), which 404s.
    "link_regex": r"https://[^\"']*/\d{8}AllbyPrecinct\.xlsx",
    # No certification flag exists anywhere in this file (same as
    # Florida), so require_official is set purely to switch _withheld
    # on and let settle_days do the actual gating from the file's own
    # dated name.
    "require_official": True,
    "settle_days": 21,
}
RUNOFF_THRESHOLD_PCT = 50.0

_rate_limiter = RateLimiter(rps=1.0)

_DISTRICT_RE = re.compile(r"District (\d+)")
_MAX_CANDIDATE_SLOTS = 5


def _office_and_district(office_name: str) -> tuple[str, int | None] | None:
    if office_name.startswith("United States Senate") or office_name.startswith("United States Senator"):
        return "S", None
    if office_name.startswith("United States House of Representatives"):
        m = _DISTRICT_RE.search(office_name)
        if m:
            return "H", int(m.group(1))
        logger.info("TN: House race %r matched no district number", office_name)
        return None
    return None


def _sum_precinct_votes(rows: list[dict]) -> dict[tuple[str, int | None, str], list[tuple[str, int]]]:
    """Every federal race's (surname, statewide votes) choices, summed
    across every precinct row — one entry per (office, district, party).

    Aggregates by the candidate's FULL name, not surname alone, and only
    reduces to a surname for the winner pick_nominee returns: two real
    same-surname candidates in one crowded field (plausible in a 5+-slot
    TN primary, even though the real 2026 field has none) would
    otherwise silently merge into one fabricated vote total under
    surname-only keying."""
    totals: dict[tuple[str, int | None, str, str], int] = {}
    for row in rows:
        office_district = _office_and_district(row.get("OFFICENAME") or "")
        if office_district is None:
            continue
        office, district = office_district
        party = normalize_party(row.get("ELECTTYPE") or "")
        if party is None:
            continue
        for i in range(1, _MAX_CANDIDATE_SLOTS + 1):
            rname = row.get(f"RNAME{i}")
            if not rname or rname.startswith("Write-In"):
                continue
            # A candidate's own party column must actually AGREE with
            # the row's own primary (ELECTTYPE) — not just be SOME
            # recognized party — so a county data-entry slip can never
            # fold one party's candidate into the other party's total.
            if normalize_party(row.get(f"PARTY{i}") or "") != party:
                continue
            votes_raw = row.get(f"PVTALLY{i}")
            try:
                votes = int(votes_raw)
            except (TypeError, ValueError):
                continue
            key = (office, district, party, rname)
            totals[key] = totals.get(key, 0) + votes

    choices: dict[tuple[str, int | None, str], list[tuple[str, int]]] = {}
    for (office, district, party, full_name), votes in totals.items():
        last_name = surname(full_name)
        if not last_name:
            continue
        choices.setdefault((office, district, party), []).append((last_name, votes))
    return choices


async def fetch_confirmed_candidates(
    client: httpx.AsyncClient, year: int, state: str, source: dict,  # noqa: ARG001 — state/source unused, this strategy is TN-only by construction
) -> list[dict] | None:
    stages = await _discover_urls(client, "TN", year, DISCOVERY)
    if not stages:
        return None
    usable = [s for s in stages if s.get("url") and not _withheld(s, DISCOVERY)]
    if not usable:
        logger.info("TN has a %d primary file published but not yet settled — confirming nobody", year)
        return []

    resp = await fetch_with_retry(
        client, _rate_limiter, "GET", usable[0]["url"], timeout=120.0,
        log_label=f"TN precinct results {year}", headers=BROWSER_HEADERS,
    )
    if resp is None:
        return None
    if len(resp.content) > MAX_DOWNLOAD_BYTES:
        logger.warning("TN precinct results download for %d exceeds the size ceiling", year)
        return None

    rows = _xlsx_rows(resp.content)
    if rows is None:
        logger.warning("TN precinct workbook for %d failed to parse", year)
        return None

    race_choices = _sum_precinct_votes(rows)
    if not race_choices:
        logger.warning("TN precinct workbook for %d had no federal rows", year)
        return None

    results = []
    for (office, district, party), choices in race_choices.items():
        won = pick_nominee(choices, runoff_threshold_pct=RUNOFF_THRESHOLD_PCT)
        if won:
            results.append({
                "office": office, "district": district,
                "party": party, "last_name": won[0],
            })
    return results
