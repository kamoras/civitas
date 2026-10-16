"""Federal agencies: who they are (the Federal Register) and what they
spend (USAspending.gov), synced weekly outside the nightly chain.

- The Federal Register's /api/v1/agencies.json lists every agency its
  documents name (473 in 2026-10, abolished ones included), with its
  slug, parent and children. The slug is the key: a Register document's
  agencies[].slug names the same row, so documents reach their agency
  with no name matching at all (explore_documents.agency_slug).
- USAspending's /api/v2/references/toptier_agencies/ gives each toptier
  agency's current-fiscal-year budget authority, obligations and outlays
  (about 110 agencies, one request), and /api/v2/agency/{code}/
  budgetary_resources/ the same by fiscal year (one request each).

USAspending names no Federal Register agency, so the two are joined by
name (crosswalk): token-sorted names compared by SequenceMatcher ratio
(Ratcliff & Obershelp 1988), one USAspending agency to at most one
Register agency, at a threshold scripts/calibrate_agency_crosswalk.py
derives from the data and writes to app/data/agency_crosswalk.json
(AGENTS.md §3a). An agency whose best name falls short, or whose best
match another agency scored higher on, is stored unmatched and listed as
such, never paired with its runner-up.

Every fetcher returns None when its source could not be read, never an
empty list, and a source that could not be read leaves what is stored
untouched: an outage is never read as "no agencies" or "no budget".
"""

import asyncio
import json
import logging
import pathlib
import re
from datetime import timedelta
from difflib import SequenceMatcher

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.http_client import DEFAULT_FETCH_TIMEOUT_S, make_async_client
from app.models import Agency, AgencyBudget
from app.ops_alerts import resolve_ops_alert, send_ops_alert
from app.pipeline import lease
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

FR_AGENCIES_URL = "https://www.federalregister.gov/api/v1/agencies.json"
USASPENDING_BASE = "https://api.usaspending.gov/api/v2"
TOPTIER_URL = f"{USASPENDING_BASE}/references/toptier_agencies/"
FR_SOURCE = {"name": "Federal Register", "url": "https://www.federalregister.gov/agencies"}
USASPENDING_SOURCE = {"name": "USAspending.gov", "url": "https://www.usaspending.gov/agency"}

# A week between syncs: both lists change on the scale of reorganizations
# and quarterly budget reports. The job is tried hourly, so a source that
# failed is retried within the hour rather than a week later, and a new
# deploy fills the agency pages within the hour.
SYNC_EVERY = timedelta(days=7)
# Between USAspending's per-agency requests: about 110 a week.
REQUEST_PAUSE_S = 0.25

CROSSWALK_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "agency_crosswalk.json"
_crosswalk_cache: dict | None = None


def crosswalk_calibration() -> dict:
    """app/data/agency_crosswalk.json (scripts/calibrate_agency_crosswalk.py):
    the name-similarity threshold and the evidence it was chosen on."""
    global _crosswalk_cache
    if _crosswalk_cache is None:
        _crosswalk_cache = json.loads(CROSSWALK_PATH.read_text())
    return _crosswalk_cache


# ── Fetch ────────────────────────────────────────────────────────

async def _get_json(client: httpx.AsyncClient, url: str):
    """The decoded body of a 200, or None for any failure."""
    try:
        resp = await client.get(url, timeout=DEFAULT_FETCH_TIMEOUT_S)
        if resp.status_code != 200:
            logger.warning("%s returned %d", url, resp.status_code)
            return None
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("%s failed: %s", url, e)
        return None


async def fetch_fr_agencies(client: httpx.AsyncClient) -> list[dict] | None:
    """The Federal Register's agency list, or None when it could not be read
    (an empty list is not a plausible answer, so it counts as unread)."""
    data = await _get_json(client, FR_AGENCIES_URL)
    if not isinstance(data, list):
        return None
    rows = [a for a in data if isinstance(a, dict) and a.get("slug") and a.get("id") and a.get("name")]
    return rows or None


async def fetch_toptier_agencies(client: httpx.AsyncClient) -> list[dict] | None:
    """USAspending's toptier agencies with this fiscal year's figures, or None."""
    data = await _get_json(client, TOPTIER_URL)
    rows = data.get("results") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    rows = [r for r in rows if isinstance(r, dict) and r.get("toptier_code") and r.get("agency_name")]
    return rows or None


async def fetch_budgetary_resources(client: httpx.AsyncClient, toptier_code: str) -> list[dict] | None:
    """One agency's budgetary resources, obligations and outlays by fiscal
    year, newest first, years USAspending has no figures for left out; None
    when it could not be read."""
    data = await _get_json(client, f"{USASPENDING_BASE}/agency/{toptier_code}/budgetary_resources/")
    years = data.get("agency_data_by_year") if isinstance(data, dict) else None
    if not isinstance(years, list):
        return None
    out = []
    for y in years:
        if not isinstance(y, dict) or y.get("agency_budgetary_resources") is None:
            continue
        periods = [p.get("period") for p in y.get("agency_obligation_by_period") or [] if p.get("period")]
        out.append({
            "fiscalYear": y.get("fiscal_year"),
            "budgetaryResources": y.get("agency_budgetary_resources"),
            "obligated": y.get("agency_total_obligated"),
            "outlays": y.get("agency_total_outlayed"),
            # The last reporting period (fiscal month) the year's figures
            # run through: 12 is a whole year, fewer a year still running.
            "throughPeriod": max(periods) if periods else None,
        })
    return sorted(out, key=lambda r: r["fiscalYear"] or 0, reverse=True)


# ── Crosswalk ────────────────────────────────────────────────────

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalized_name(name: str) -> str:
    """Lower case, "&" read as "and", punctuation dropped, words sorted: the
    Register files departments as "Agriculture Department" where USAspending
    says "Department of Agriculture", and sorting puts the two side by side."""
    return " ".join(sorted(_NON_ALNUM.sub(" ", name.lower().replace("&", " and ")).split()))


def name_similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio of two normalized names, 0..1."""
    return SequenceMatcher(None, a, b).ratio()


def best_matches(fr_names: dict[str, str], us_names: dict[str, str]) -> dict[str, tuple[str, float]]:
    """{USAspending code: (its most similar Register slug, ratio)}."""
    fr_norm = {slug: normalized_name(n) for slug, n in fr_names.items()}
    out = {}
    for code, name in us_names.items():
        n = normalized_name(name)
        out[code] = max(((slug, name_similarity(n, fn)) for slug, fn in fr_norm.items()),
                        key=lambda pair: (pair[1], pair[0]))
    return out


def crosswalk(fr_names: dict[str, str], us_names: dict[str, str], threshold: float) -> dict[str, tuple[str, float]]:
    """{USAspending code: (Register slug, ratio)} for the agencies that
    match; the rest are unmatched. Each code takes only its best Register
    agency, at `threshold` or above, and a Register agency goes to the code
    that scored highest on it: a code whose best is taken stays unmatched
    rather than falling back to its runner-up."""
    best = best_matches(fr_names, us_names)
    taken: set[str] = set()
    out = {}
    for code, (slug, score) in sorted(best.items(), key=lambda kv: (-kv[1][1], kv[0])):
        if score >= threshold and slug not in taken:
            taken.add(slug)
            out[code] = (slug, score)
    return out


# ── Store ────────────────────────────────────────────────────────

def _store_fr(db: Session, agencies: list[dict], now) -> int:
    by_id = {a["id"]: a["slug"] for a in agencies}
    stored = {a.slug: a for a in db.query(Agency)}
    for a in agencies:
        row = stored.get(a["slug"]) or Agency(slug=a["slug"])
        row.fr_id = a["id"]
        row.name = a["name"].strip()
        row.short_name = (a.get("short_name") or "").strip() or None
        row.description = (a.get("description") or "").strip() or None
        row.fr_url = a.get("url") or None
        row.website = a.get("agency_url") or None
        row.parent_slug = by_id.get(a.get("parent_id"))
        row.synced_at = now
        db.add(row)
    return len(agencies)


def _store_toptier(db: Session, toptier: list[dict], matches: dict[str, tuple[str, float]] | None, now) -> None:
    """`matches` None (no Register agencies to match against): each row
    keeps the match it had."""
    stored = {b.toptier_code: b for b in db.query(AgencyBudget)}
    for t in toptier:
        code = t["toptier_code"]
        row = stored.get(code) or AgencyBudget(toptier_code=code)
        row.name = t["agency_name"].strip()
        row.abbreviation = t.get("abbreviation") or None
        row.usaspending_slug = t.get("agency_slug") or None
        if matches is not None:
            row.agency_slug, row.match_score = matches.get(code, (None, None))
        row.fiscal_year = int(t["active_fy"]) if t.get("active_fy") else None
        row.fiscal_quarter = int(t["active_fq"]) if t.get("active_fq") else None
        row.budget_authority = t.get("budget_authority_amount")
        row.obligated = t.get("obligated_amount")
        row.outlays = t.get("outlay_amount")
        row.synced_at = now
        row.retired_at = None
        db.add(row)
    # A read that succeeded and no longer lists an agency retires it, dated:
    # its last figures are kept, and never counted among this year's.
    listed = {t["toptier_code"] for t in toptier}
    for code, row in stored.items():
        if code not in listed and row.retired_at is None:
            row.retired_at = now


def _alert(source: str, ok: bool) -> None:
    condition = f"agency-sync-{source}"
    if ok:
        resolve_ops_alert(condition)
        return
    send_ops_alert(
        f"Agency sync: {source} could not be read",
        f"The weekly agency sync could not read {source}; what was stored stays as it was, "
        "and it is tried again within the hour.",
        dedupe_key=f"{condition}-{utcnow():%G-W%V}",
        condition=condition,
    )


async def sync_agencies(db: Session, client: httpx.AsyncClient, *, fr_due: bool = True,
                        usaspending_due: bool = True) -> dict:
    """Read the sources that are due and store what each gave. Returns
    counts; a source that was not due, or could not be read, is None in them
    and leaves its rows as they were."""
    now = utcnow()
    fr = await fetch_fr_agencies(client) if fr_due else None
    if fr is not None:
        _store_fr(db, fr, now)
        db.commit()
    toptier = await fetch_toptier_agencies(client) if usaspending_due else None
    stats: dict = {"frAgencies": len(fr) if fr is not None else None,
                   "toptier": len(toptier) if toptier is not None else None,
                   "matched": None, "byYearRead": 0, "byYearFailed": 0}
    if toptier is not None:
        fr_names = {a.slug: a.name for a in db.query(Agency.slug, Agency.name)}
        matches = crosswalk(fr_names, {t["toptier_code"]: t["agency_name"] for t in toptier},
                            crosswalk_calibration()["threshold"]) if fr_names else None
        stats["matched"] = len(matches) if matches is not None else None
        _store_toptier(db, toptier, matches, now)
        db.commit()
        for t in toptier:
            years = await fetch_budgetary_resources(client, t["toptier_code"])
            if years is None:
                stats["byYearFailed"] += 1
            else:
                row = db.get(AgencyBudget, t["toptier_code"])
                row.by_year, row.by_year_synced_at = json.dumps(years), utcnow()
                stats["byYearRead"] += 1
            await asyncio.sleep(REQUEST_PAUSE_S)
        db.commit()
    if fr_due:
        _alert("the Federal Register's agency list", fr is not None)
    if usaspending_due:
        # Most agencies' year-by-year figures unread is an outage too, not a
        # few agencies USAspending has no page for.
        _alert("USAspending", toptier is not None and stats["byYearFailed"] <= stats["byYearRead"])
    return stats


def due(db: Session) -> tuple[bool, bool]:
    """(the Register's list, USAspending's budgets): each due when it last
    stored more than SYNC_EVERY ago, or never. Each source is retried on
    its own, so one that is down costs one request an hour, not the other's
    hundred."""
    since = utcnow() - SYNC_EVERY
    fr_last = db.query(func.max(Agency.synced_at)).scalar()
    us_last = db.query(func.max(AgencyBudget.synced_at)).scalar()
    return fr_last is None or fr_last < since, us_last is None or us_last < since


async def _run(db: Session, fr_due: bool = True, usaspending_due: bool = True) -> dict:
    async with make_async_client() as client:
        return await sync_agencies(db, client, fr_due=fr_due, usaspending_due=usaspending_due)


def run_agency_sync() -> dict | None:
    """The scheduled job: under its lease, and only when a sync is due.
    None when it didn't run."""
    with lease.job(lease.AGENCY_SYNC, who="Agency sync") as held:
        if not held:
            return None
        db = SessionLocal()
        try:
            fr_due, usaspending_due = due(db)
            if not (fr_due or usaspending_due):
                return None
            stats = asyncio.run(_run(db, fr_due, usaspending_due))
            logger.info("Agency sync: %s", stats)
            return stats
        except Exception:
            logger.exception("Agency sync failed")
            db.rollback()
            return None
        finally:
            db.close()
