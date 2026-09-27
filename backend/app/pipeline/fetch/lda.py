"""Senate Lobbying Disclosure Act (LDA) filings — registered lobbying spend,
and the specific bills each filing says the client lobbied on.

The donor-vote overlap feature historically carried a lobbying_spend field
that was 0 for every match (the 2026-07 adversarial audit flagged the
feature as mislabeled). This module fills it with actual registered
federal lobbying activity: for an organization, the sum of
registrant-reported income (outside firms hired by the org) plus
self-reported expenses (in-house lobbying) across a filing year. The same
filings list, per issue area, the legislation lobbied on; those bill
numbers are matched against the member's own votes
(analyze/lobbying_records.py), which turns a topical overlap into a
record: this donor's filing names this bill, and the member voted on it.

The registry moved from lda.senate.gov to lda.gov in 2026. The old host
answers with a 301, which httpx does not follow by default, so every lookup
failed and was reported as "no registered lobbying" — the spend on all 21
live senate matches read $0 (checked 2026-09-27, Goldman Sachs and BlackRock
among them). Hence three things below: the new base, redirects followed,
and a failed lookup that says it failed (None, `lobbyingChecked: False`)
instead of posing as a real zero, with an ops alert when a whole run's
lookups fail.

Notes on interpretation:
- Amounts are order-of-magnitude signals, not audited totals — quarterly
  amendments can double-count and the client-name search is fuzzy on the
  LDA side. Good enough to distinguish "this org lobbies Washington with
  $2M/yr" from "no registered lobbying at all".
- The client searched for is a PAC's connected organization when the FEC
  records one ("JPMORGAN CHASE & CO." for its federal PAC): the registry
  lists the company, and the PAC's own name matches no client at all.
- The API is public, no key required; the anonymous rate limit is low, so
  results are cached hard in api_cache and only matched donor orgs are
  ever queried.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.http_client import make_async_client
from app.pipeline.analyze.lobbying_records import (
    TitlePool,
    bill_mentions,
    congress_of_year,
    names_bill,
)
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S
from app.pipeline.rate_limiter import RateLimiter
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

LDA_API_BASE = "https://lda.gov/api/v1"

# Anonymous LDA limit is ~15 requests/minute — stay safely under it.
_rate_limiter = RateLimiter(0.2)

# A finished year's filings change only by amendment; the current year's
# grow every quarter, so they use the normal pipeline cache TTL.
_FINISHED_YEAR_CACHE_HOURS = 24 * 30
# Bounds on what one org-year keeps: a heavy lobbyist files dozens of
# reports, and each names the same bills quarter after quarter.
_MAX_PAGES = 10
_MAX_MENTIONS = 1500

# Bills listed per match, newest filing first.
MAX_LOBBIED_BILLS = 10


@dataclass
class LobbyingActivity:
    """One organization's registered lobbying in one filing year."""

    total: float
    # {"billId", "before", "after", "filingUrl", "filingYear", "registrant"}
    mentions: list[dict] = field(default_factory=list)
    # False when the page cap was hit: the total is a lower bound.
    complete: bool = True


def _sum_filing_amounts(results: list[dict]) -> float:
    """Sum lobbying income+expenses across filings, skipping registrations.

    Registration filings (RR) carry no amounts; termination filings can.
    Income = what outside firms report earning from this client;
    expenses = what the org reports spending on in-house lobbying.
    """
    total = 0.0
    for filing in results or []:
        ftype = (filing.get("filing_type") or "").upper()
        if ftype.startswith("RR"):
            continue
        for field_name in ("income", "expenses"):
            val = filing.get(field_name)
            if val:
                try:
                    total += float(val)
                except (TypeError, ValueError):
                    pass
    return total


def _filing_mentions(results: list[dict]) -> list[dict]:
    """Every bill number named in the filings' activity descriptions."""
    out: list[dict] = []
    for filing in results or []:
        registrant = (filing.get("registrant") or {}).get("name") or ""
        for activity in filing.get("lobbying_activities") or []:
            for bill_id, before, after in bill_mentions(activity.get("description") or ""):
                out.append({
                    "billId": bill_id,
                    "before": before,
                    "after": after,
                    "filingUrl": filing.get("filing_document_url") or "",
                    "filingYear": filing.get("filing_year"),
                    "registrant": registrant,
                })
    return out


def _cache_key(org_key: str, year: int) -> str:
    # Include a stable hash of the full org key so two different orgs that
    # share an 80-char prefix (e.g. federal vs. state PAC variants of one
    # sponsor) can't collide onto one cached figure.
    key_hash = hashlib.sha256(org_key.encode()).hexdigest()[:12]
    return f"lda-activity-v2-{year}-{org_key[:60]}-{key_hash}"


async def fetch_lobbying_activity(
    client: httpx.AsyncClient, db: Session, org_name: str, year: int,
) -> LobbyingActivity | None:
    """Registered lobbying by an organization in a filing year: total spend
    and the bills its filings name.

    An organization with no registered lobbying gets a real zero (which is
    itself meaningful). A failed lookup returns None and is not cached, so
    it can't be mistaken for, or remembered as, "no lobbying".
    """
    org_key = (org_name or "").strip().upper()
    if len(org_key) < 3:
        return LobbyingActivity(total=0.0)

    finished = year < utcnow().year
    cache_key = _cache_key(org_key, year)
    cached = api_cache_get(
        db, "lda", cache_key,
        max_age_hours=_FINISHED_YEAR_CACHE_HOURS if finished else None,
    )
    if cached is not None:
        return LobbyingActivity(
            total=float(cached.get("total", 0.0)),
            mentions=cached.get("mentions") or [],
            complete=bool(cached.get("complete", True)),
        )

    # Follow pagination: a heavy-lobbying client can file dozens to
    # low-hundreds of filings a year (multiple outside firms × quarterly
    # reports + in-house). Summing only the first page systematically
    # UNDERcounted exactly the biggest spenders — and the figure is shown
    # verbatim in user-facing text. Bounded to keep one pathological org
    # from stalling the enrichment loop; the cap is logged if hit so a
    # silent truncation can't masquerade as a complete total.
    total = 0.0
    mentions: list[dict] = []
    url: str | None = f"{LDA_API_BASE}/filings/"
    params: dict | None = {"client_name": org_key, "filing_year": year, "page_size": 25}
    pages = 0
    try:
        while url and pages < _MAX_PAGES:
            await _rate_limiter.acquire()
            resp = await client.get(
                url, params=params, timeout=DEFAULT_FETCH_TIMEOUT_S, follow_redirects=True,
            )
            if resp.status_code == 429:
                logger.warning("LDA rate limited for %s — skipping (uncached)", org_key)
                return None
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            total += _sum_filing_amounts(results)
            mentions.extend(_filing_mentions(results))
            url = data.get("next")  # absolute URL from the API, or None
            params = None  # `next` already encodes the query
            pages += 1
    except Exception as exc:
        logger.warning("LDA fetch failed for %s: %s", org_key, exc)
        return None

    complete = not (url and pages >= _MAX_PAGES)
    if not complete:
        logger.warning(
            "LDA activity for %s (%d) hit the %d-page cap — total may be a lower bound",
            org_key, year, _MAX_PAGES,
        )
    # Quarterly reports repeat the same description word for word; keep one
    # of each (first seen, i.e. the API's order) before bounding the list.
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict] = []
    for m in mentions:
        key = (m["billId"], " ".join(m["before"].split()), " ".join(m["after"].split()))
        if key not in seen:
            seen.add(key)
            unique.append(m)
    mentions = unique[:_MAX_MENTIONS]
    api_cache_set(
        db, "lda", cache_key,
        {"total": round(total, 2), "mentions": mentions, "complete": complete},
        normal_ttl_hours=_FINISHED_YEAR_CACHE_HOURS if finished else None,
    )
    return LobbyingActivity(total=total, mentions=mentions, complete=complete)


async def fetch_lobbying_spend(
    client: httpx.AsyncClient, db: Session, org_name: str, year: int,
) -> float | None:
    """Total registered lobbying for an organization in a year, or None
    when the lookup failed."""
    activity = await fetch_lobbying_activity(client, db, org_name, year)
    return None if activity is None else activity.total


def _vote_bill_key(bill_id: str | None) -> str | None:
    """A vote's bill id ("H.R. 1492", "S. 4668", "H.Con.Res. 89") in the
    form bill_mentions produces ("HR.1492"); None for a nomination or an
    amendment roll call, which no filing can name by bill number."""
    from app.pipeline.fetch.floor_logs import bill_id_from_number

    return bill_id_from_number(bill_id)


def _voted_bills(votes: list[dict] | None) -> dict[str, dict]:
    """The member's Yea/Nay votes by bill, latest first per bill."""
    out: dict[str, dict] = {}
    for v in sorted(votes or [], key=lambda v: v.get("date") or "", reverse=True):
        if v.get("vote") not in ("Yea", "Nay"):
            continue
        key = _vote_bill_key(v.get("billId"))
        if key and key not in out:
            out[key] = v
    return out


async def _bill_titles(client: httpx.AsyncClient, db: Session, congress: int, bill_key: str) -> list[str]:
    """Every title Congress.gov records for a bill (cached by
    fetch_bill_titles; the significant-bill fetch has usually already paid
    for it)."""
    from app.pipeline.fetch.congress import fetch_bill_titles

    prefix, _, number = bill_key.partition(".")
    try:
        rows = await fetch_bill_titles(client, db, congress, prefix.lower(), int(number))
    except Exception:
        logger.exception("Bill titles unavailable for %s", bill_key)
        return []
    return [r.get("title") for r in rows or [] if r.get("title")]


# One pool per congress per day: the index over ~16,000 titles is built
# once and shared by every member of the night's run.
_pools: dict[tuple[int, str], TitlePool | None] = {}


async def _title_pool(client: httpx.AsyncClient, db: Session, congress: int) -> TitlePool | None:
    """Every current-congress bill title, for the rival check in
    lobbying_records.names_bill. None when the listing is unavailable, in
    which case nothing is claimed (see lobbied_bills_for)."""
    from app.pipeline.fetch.congress import fetch_congress_bill_titles

    key = (congress, f"{utcnow():%Y-%m-%d}")
    if key not in _pools:
        _pools.clear()
        try:
            titles = await fetch_congress_bill_titles(client, db, congress)
        except Exception:
            logger.exception("Congress %d bill titles unavailable", congress)
            titles = {}
        _pools[key] = TitlePool({k: [v] for k, v in titles.items()}) if titles else None
    return _pools[key]


def _congress_years(congress: int) -> list[int]:
    first = 1789 + (congress - 1) * 2
    return [y for y in (first, first + 1) if y <= utcnow().year]


async def lobbied_bills_for(
    client: httpx.AsyncClient,
    db: Session,
    activities: list[LobbyingActivity],
    voted: dict[str, dict],
    congress: int,
) -> list[dict]:
    """The bills the member voted on that these filings name, with the
    filing that names each. A named number counts only when the filer's
    words around it match one of that bill's current-congress titles and no
    other bill of the congress matches them better (see lobbying_records):
    filings still cite earlier congresses' bills by number, and sibling
    bills share nearly whole titles."""
    candidates: dict[str, list[dict]] = {}
    for activity in activities:
        for m in activity.mentions:
            year = m.get("filingYear")
            if not isinstance(year, int) or congress_of_year(year) != congress:
                continue
            if m.get("billId") in voted:
                candidates.setdefault(m["billId"], []).append(m)

    found: list[dict] = []
    if not candidates:
        return found
    pool = await _title_pool(client, db, congress)
    if pool is None:
        # Without the rest of the congress to compare against, a sibling
        # bill (the Education appropriations act for a Defense one) can't be
        # ruled out, so no filing is claimed to name anything.
        return found
    for bill_key, mentions in candidates.items():
        vote = voted[bill_key]
        titles = await _bill_titles(client, db, congress, bill_key)
        if vote.get("billName"):
            titles.append(vote["billName"])
        previous = await _bill_titles(client, db, congress - 1, bill_key)
        matching = [
            m for m in mentions
            if names_bill(m.get("before", ""), m.get("after", ""), titles, bill_key, pool, previous)
        ]
        if not matching:
            continue
        newest = max(matching, key=lambda m: (m.get("filingYear") or 0))
        found.append({
            "billId": vote.get("billId"),
            "billName": (vote.get("billName") or "")[:160],
            "vote": vote.get("vote"),
            "filingYear": newest.get("filingYear"),
            "filingUrl": newest.get("filingUrl"),
            "registrant": newest.get("registrant"),
            "filingCount": len({m.get("filingUrl") for m in matching}),
        })
    found.sort(key=lambda b: (-(b.get("filingYear") or 0), b.get("billId") or ""))
    return found[:MAX_LOBBIED_BILLS]


async def enrich_lobbying_matches_with_lda(
    matches: list[dict],
    db: Session,
    lda_year: int,
    votes: list[dict] | None = None,
    congress: int | None = None,
) -> dict:
    """Mutate donor-vote lobbying matches in place: registered lobbying
    spend for `lda_year`, and the bills the member voted on that the org's
    current-congress filings name.

    Uses its own short-lived httpx client rather than a caller-supplied one:
    an earlier version reused the FETCH-phase client here, which was already
    closed by the time the analysis loop ran, and silently failed every LDA
    lookup with "client has been closed" (2026-07 finding: 184 failures in a
    single run — lobbyingSpend had effectively always been 0 in production).
    Best-effort per match: one org's lookup failing doesn't block the others.
    Shared by senate_pipeline.py and house_pipeline.py.

    Returns {"lookups", "failed"} so a caller can tell an outage from a
    quiet night.
    """
    stats = {"lookups": 0, "failed": 0}
    if not matches:
        return stats

    congress = congress or settings.CURRENT_CONGRESS
    years = sorted({lda_year, *_congress_years(congress)})
    voted = _voted_bills(votes)

    async with make_async_client() as lda_client:
        for m in matches:
            # A match with no donor behind it carries an industry label
            # ("Finance industry") as its headline; searching the registry's
            # fuzzy client names for that would find somebody, wrongly.
            if "lobbyingClient" in m and not m["lobbyingClient"]:
                continue
            org = m.get("lobbyingClient") or m.get("lobbyistOrg", "")
            stats["lookups"] += 1
            try:
                activities: dict[int, LobbyingActivity | None] = {}
                for year in years:
                    activities[year] = await fetch_lobbying_activity(lda_client, db, org, year)
                spend_year = activities.get(lda_year)
                if spend_year is None:
                    stats["failed"] += 1
                    m["lobbyingChecked"] = False
                    continue
                m["lobbyingChecked"] = True
                m["lobbyingSpend"] = round(spend_year.total)
                if spend_year.total > 0:
                    m["description"] = (
                        m.get("description", "")
                        + f" Registered federal lobbying (LDA {lda_year}): ${spend_year.total:,.0f}."
                    )
                lobbied = await lobbied_bills_for(
                    lda_client, db, [a for a in activities.values() if a is not None], voted, congress,
                )
                m["lobbiedBills"] = lobbied
                if lobbied:
                    bills = list(m.get("billsInfluenced") or [])
                    for b in lobbied:
                        if b["billId"] not in bills:
                            bills.append(b["billId"])
                    m["billsInfluenced"] = bills
            except Exception:
                stats["failed"] += 1
                m["lobbyingChecked"] = False
                logger.exception(
                    "LDA enrichment failed for %s (non-fatal)", m.get("lobbyistOrg", "?"),
                )
    return stats


def alert_if_lda_down(stats: dict, chamber: str) -> None:
    """One ops alert when every lookup in a run failed — the signature of
    the 2026 host move, which read as "no lobbying" for months."""
    if stats.get("lookups", 0) and stats.get("failed", 0) >= stats["lookups"]:
        from app.ops_alerts import send_ops_alert

        send_ops_alert(
            "LDA lobbying lookups all failed",
            f"Every Lobbying Disclosure Act lookup in tonight's {chamber} run failed "
            f"({stats['failed']} of {stats['lookups']}). Donor-vote matches are marked "
            "lobbying-unchecked rather than $0; see the server logs for the cause "
            f"(a moved host, a changed API, or a block). Base URL: {LDA_API_BASE}.",
            dedupe_key=f"lda-down-{chamber}-{utcnow():%Y-%m-%d}",
        )
