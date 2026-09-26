"""Ingest each member's latest annual financial disclosure (asset holdings).

Runs inside the stock-trades pipeline as two more best-effort phases (see
stock_pipeline.run_stock_trades_pipeline): the sources are the same two
disclosure systems the trade ingest reads, and so is the filer matching.

For every sitting member it keeps exactly one report — the newest one —
and replaces the member's previous report when a newer one is found. A
report covers holdings at one date, so an older report is superseded, not
accumulated — and never brought back: a partial index or search that only
turned up a member's older report leaves the newer stored one alone.
Nothing is derived from the value brackets here; the read path
(holdings_service) reports them as disclosed.

Each phase is time-boxed (PHASE_BUDGET). A normal run fetches only the
handful of reports filed since the last one and finishes in a minute or
two; the first run, or a PARSER_VERSION bump, has every member's report to
read, and the budget spreads that over a few nightly runs instead of
holding the stock-trades run — and the hourly action-center refresh that
waits on it — past its 2h overrun alarm. Members with nothing stored go
first, so coverage fills before re-reads.
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy.orm import Session

from app.alerting import safe_ops_alert as _alert
from app.models import FinancialDisclosure, FinancialHolding
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.fd_common import AnnualReport
from app.pipeline.fetch.house_fd import PARSER_VERSION as HOUSE_PARSER_VERSION
from app.pipeline.fetch.house_fd import fetch_and_parse_annual as fetch_house_annual, fetch_annual_filing_index
from app.pipeline.fetch.senate_fd import PARSER_VERSION as SENATE_PARSER_VERSION
from app.pipeline.fetch.senate_fd import (
    SessionLapsed,
    fetch_and_parse_annual as fetch_senate_annual,
    is_annual_title,
    is_senator_filing,
    search_annual_filings,
)
from app.pipeline.fetch.senate_ptr import accept_terms as senate_accept_terms
from app.pipeline.fetch.senate_ptr import senate_filing_id
from app.pipeline.filer_matching import match_representative, match_senator
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# Annual reports cover the previous calendar year and are due in May, with
# extensions into August — so a member's newest report is for last year, or
# (before they file) the year before.
_YEARS_BACK = 2

# Wall-clock budget for each holdings phase. The deadline is set when the
# phase starts, so the index download and the Senate search count against
# it; it is checked before every report fetch, so only a fetch already in
# flight can run past it. Measured 2026-09: a first House run reads ~430
# reports (~9 min in a dev container, network-bound at the Clerk's 1 req/s;
# slower on the Pi's CPU), the Senate ~100 (~4 min, most of it the browser
# search). Past the budget the remaining members wait for the next run.
PHASE_BUDGET = timedelta(minutes=10)

# Members attempted with nothing fetched, and none fetched at all, before a
# phase counts its source as down (see _SourceHealth).
MIN_ATTEMPTS_FOR_OUTAGE = 5


@dataclass
class _Stored:
    filing_id: str
    parser_version: int | None
    report_year: int | None
    filed_date: str | None
    unreadable_reason: str | None


def _stored_reports(db: Session, column) -> dict[str, _Stored]:
    rows = db.query(
        column, FinancialDisclosure.filing_id, FinancialDisclosure.parser_version,
        FinancialDisclosure.report_year, FinancialDisclosure.filed_date,
        FinancialDisclosure.unreadable_reason,
    ).filter(column.isnot(None))
    return {owner_id: _Stored(*rest) for owner_id, *rest in rows.all()}


def _is_current(stored: _Stored | None, filing_id: str, parser_version: int) -> bool:
    """Already ingested: same filing, read by this parser. The same filing
    read by an older parser is re-read, so a parser fix reaches every member
    instead of only those who file something new."""
    return stored is not None and (stored.filing_id, stored.parser_version) == (filing_id, parser_version)


def _is_older(stored: _Stored | None, report_year: int | None, filed_date: str | None) -> bool:
    """The candidate describes an earlier date than what's stored."""
    if stored is None:
        return False
    return (report_year or 0, filed_date or "") < (stored.report_year or 0, stored.filed_date or "")


def _members_in_order(per_member: dict[str, list[dict]], stored: dict[str, _Stored]) -> list[str]:
    """Members with nothing stored first: under the time budget, filling a
    missing scorecard section matters more than refreshing a present one."""
    return sorted(per_member, key=lambda member_id: member_id in stored)


def _replace_disclosure(
    db: Session, *, owner_filter: dict, filing_id: str, report_year: int | None, report_label: str,
    filed_date: str | None, source_url: str, report: AnnualReport, parser_version: int,
) -> int:
    """Swap a member's stored report for this one. Returns holdings stored."""
    # Bulk deletes, not the ORM cascade, which would load every stored
    # holding into the session to delete it row by row — hundreds per member
    # on a first run or a PARSER_VERSION bump, inside the phase's budget.
    # SQLite here runs without FK enforcement, so the holdings go explicitly.
    old_ids = [row.id for row in db.query(FinancialDisclosure.id).filter_by(**owner_filter)]
    if old_ids:
        # "fetch": anything already loaded is dropped from the session too,
        # so a reused id can't collide with a stale object.
        db.query(FinancialHolding).filter(FinancialHolding.disclosure_id.in_(old_ids)).delete(
            synchronize_session="fetch",
        )
        db.query(FinancialDisclosure).filter(FinancialDisclosure.id.in_(old_ids)).delete(
            synchronize_session="fetch",
        )
    disclosure = FinancialDisclosure(
        **owner_filter,
        filing_id=filing_id,
        report_year=report_year,
        report_label=report_label,
        filed_date=filed_date,
        source_url=source_url,
        parsed=report.holdings is not None,
        unreadable_reason=report.unreadable_reason,
        parser_version=parser_version,
    )
    for row in report.holdings or []:
        disclosure.holdings.append(FinancialHolding(
            asset_name=row.asset_name,
            account=row.account,
            ticker=row.ticker,
            asset_type=row.asset_type,
            category=row.category,
            owner=row.owner,
            value_text=row.value_text,
            value_low=row.value_low,
            value_high=row.value_high,
        ))
    db.add(disclosure)
    return len(report.holdings or [])


def _house_report_label(filing: dict) -> str:
    year = filing.get("year")
    base = f"{year} annual report" if year else "annual report"
    return f"{base} (amended)" if filing.get("filing_type") == "A" else base


def _is_member_prefix(prefix: str | None) -> bool:
    """The index's "Hon." — sitting members' honorific; candidates file as
    "Mr."/"Dr."/"Ms." or with none. Tolerant of the period and case."""
    return (prefix or "").strip().rstrip(".").lower() == "hon"


class _SourceHealth:
    """Whether a phase's source looked down, judged once the phase is done.

    Deliberately not an early abort: members with nothing stored are
    processed first, so a few genuinely broken filings bunched at the front
    would otherwise stop the phase before it reached anyone else. A source
    that is down fails every fetch; a source with some bad filings doesn't.
    So the phase fails when at least MIN_ATTEMPTS_FOR_OUTAGE members were
    tried and not one report came back — including members who then kept a
    stored report, since a newer report that won't load is exactly what an
    outage looks like on a night when only new reports need fetching.
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.attempted = 0
        self.fetched = 0

    def record(self, attempted: bool, fetched: bool) -> None:
        self.attempted += attempted
        self.fetched += fetched

    def check(self) -> None:
        if self.attempted >= MIN_ATTEMPTS_FOR_OUTAGE and not self.fetched:
            raise RuntimeError(f"{self.source}: {self.attempted} members tried, no report fetched")


# A filing that has failed this many nightly runs in a row is "known bad" —
# a report PDF the Clerk lists but 404s, one pdfplumber crashes on, a page
# eFD won't serve. It is still tried each run (one request), but no longer
# counts toward _SourceHealth: a handful of permanently broken filings must
# not read as the source being down, night after night. A success forgets
# the misses.
KNOWN_BAD_AFTER = 3
_MISS_TIER = "holdings_fetch_miss"
_MISS_MAX_AGE_HOURS = 24 * 60


def _miss_count(db: Session, filing_id: str) -> int:
    cached = api_cache_get(db, _MISS_TIER, filing_id, max_age_hours=_MISS_MAX_AGE_HOURS)
    return int(cached.get("count", 0)) if cached else 0


def _is_known_bad(db: Session, filing_id: str) -> bool:
    return _miss_count(db, filing_id) >= KNOWN_BAD_AFTER


def _record_miss(db: Session, filing_id: str) -> None:
    api_cache_set(
        db, _MISS_TIER, filing_id, {"count": _miss_count(db, filing_id) + 1},
        normal_ttl_hours=_MISS_MAX_AGE_HOURS,
    )


def _clear_miss(db: Session, filing_id: str) -> None:
    if _miss_count(db, filing_id):
        api_cache_set(db, _MISS_TIER, filing_id, {"count": 0}, normal_ttl_hours=_MISS_MAX_AGE_HOURS)


async def ingest_house_holdings(db: Session, client: httpx.AsyncClient) -> int:
    """Store each representative's newest annual report. Returns holdings stored."""
    deadline = time.monotonic() + PHASE_BUDGET.total_seconds()
    current_year = utcnow().year
    # Candidate filings per representative, most preferred first: the
    # newest calendar year, then the latest filed within it (an amendment
    # supersedes the original it amends).
    per_rep: dict[str, list[dict]] = {}
    matched: dict[tuple[str, str, str], str | None] = {}  # one lookup per filer, not per filing
    indexed = 0
    for year in range(current_year - 1, current_year - 1 - _YEARS_BACK, -1):
        filings = await fetch_annual_filing_index(client, db, year)
        indexed += len(filings)
        filings = sorted(filings, key=lambda f: f.get("filing_date") or "", reverse=True)
        for filing in filings:
            filer = (filing["last"], filing["first"], filing["state_district"])
            if filer not in matched:
                rep = match_representative(db, *filer)
                matched[filer] = rep.id if rep is not None else None
            if matched[filer] is not None:
                per_rep.setdefault(matched[filer], []).append(filing)
    if indexed == 0:
        # Two calendar years with no annual report from anyone in the House
        # is a failed or changed index, not a quiet year — fail the phase so
        # the run records it, instead of leaving every stored report to age.
        raise RuntimeError("House annual-report index returned no filings for either year")

    stored = _stored_reports(db, FinancialDisclosure.representative_id)
    inserted = 0
    health = _SourceHealth("House Clerk")
    order = _members_in_order(per_rep, stored)
    for position, rep_id in enumerate(order):
        mine = stored.get(rep_id)
        attempted = fetched = False
        out_of_time = False
        for filing in per_rep[rep_id]:
            if _is_current(mine, filing["doc_id"], HOUSE_PARSER_VERSION):
                break  # already have the newest report, as this parser reads it
            if _is_older(mine, filing.get("year"), filing.get("filing_date")):
                # Only older reports than the stored one turned up (a year's
                # index failed to load): keep the newer stored report.
                break
            if time.monotonic() > deadline:
                out_of_time = True
                break
            known_bad = _is_known_bad(db, filing["doc_id"])
            attempted = attempted or not known_bad
            report = await fetch_house_annual(client, db, filing)
            if report is None or not report.final:
                # Nothing usable this run: not fetched, or a read that may be
                # transient (the parser crashed) — which is stored nowhere,
                # since "can't be read" would be a claim about the report
                # that this run can't back. Try the member's next filing (the
                # failed one may not even be theirs — a same-surname
                # candidate's amendment can rank first); _is_older keeps that
                # from ever displacing a newer stored report.
                _record_miss(db, filing["doc_id"])
                continue
            _clear_miss(db, filing["doc_id"])
            fetched = True
            status = (report.filer_status or "").lower()
            if status and status != "member":
                # A candidate for the seat who shares the member's surname
                # and district — not this member's report.
                continue
            if not status and filing.get("filing_type") != "O" and not _is_member_prefix(filing.get("prefix")):
                # A scanned filing has no readable Status line. An original
                # annual report ("O") is only ever a member's, but an
                # amendment can be a candidate's — and in the index those
                # carry no "Hon." (2025: 82 of 105 amendments), which every
                # sitting member's filing does. Both are the Clerk's own
                # structured index fields, not inferred from any name.
                continue
            inserted += _replace_disclosure(
                db,
                owner_filter={"representative_id": rep_id},
                filing_id=filing["doc_id"],
                report_year=filing.get("year"),
                report_label=_house_report_label(filing),
                filed_date=filing.get("filing_date"),
                source_url=filing["pdf_url"],
                report=report,
                parser_version=HOUSE_PARSER_VERSION,
            )
            break
        db.commit()  # per member, so a budget stop or a later failure keeps what's done
        health.record(attempted, fetched)
        if out_of_time:
            logger.info("House holdings: time budget spent — %d members wait for the next run", len(order) - position)
            break
    health.check()
    return inserted


_CY_RE = re.compile(r"\bCY\s*(\d{4})\b", re.I)
_DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")


def _senate_rank(filing: dict) -> tuple[int, str]:
    """Newest first: by the year the report states, then by filing date. A
    report whose year isn't stated (a paper filing) ranks below every one
    that states one, so it's used only when a senator has nothing dated."""
    return (_senate_report_year(filing) or 0, filing.get("filed_date") or "")


def _senate_is_older(stored: _Stored | None, filing: dict) -> bool:
    """_is_older, in _senate_rank's order."""
    if stored is None:
        return False
    return _senate_rank(filing) < (stored.report_year or 0, stored.filed_date or "")


def _senate_report_year(filing: dict) -> int | None:
    """The calendar year a Senate report's holdings describe — only when
    its title states it — else None.

    "Annual Report for CY 2025" states it; a "New Filer Report for
    03/24/2026" describes that date. A paper filing's link reads only
    "Annual Report", "Annual Report (Amendment)" or similar, and its year is
    not inferred: an original is usually filed the year after the one it
    covers, but late and early filings exist, and an amendment can amend any
    earlier report (Ricketts' paper amendment of February 2026, live
    2026-09, predates every CY2025 report). Every attempt to guess one
    produced a report shown under the wrong year; None is the honest value.
    """
    title = filing.get("title") or ""
    if m := _CY_RE.search(title):
        return int(m.group(1))
    if m := _DATE_RE.search(title):
        return int(m.group(3))
    return None


def _senate_report_label(filing: dict) -> str:
    """What the report is, in the words the page shows. A new-filer report
    is a snapshot at its date, not a year-end annual report; an undated
    (paper) filing is named by kind and filing date, never a guessed year."""
    title = (filing.get("title") or "").lower()
    amended = "amendment" in title
    if "new filer" in title and (m := _DATE_RE.search(filing.get("title") or "")):
        return f"new-filer report as of {m.group(3)}-{m.group(1)}-{m.group(2)}" + (" (amended)" if amended else "")
    year = _senate_report_year(filing)
    if year is not None:
        return f"{year} annual report" + (" (amended)" if amended else "")
    kind = "new-filer report" if "new filer" in title else "annual report"
    kind += " amendment" if amended else ""
    filed = filing.get("filed_date")
    return f"{kind} filed {filed}" if filed else kind


async def ingest_senate_holdings(db: Session, client: httpx.AsyncClient) -> int:
    """Store each senator's newest annual report. Returns holdings stored."""
    deadline = time.monotonic() + PHASE_BUDGET.total_seconds()
    if await senate_accept_terms(client) is None:
        raise RuntimeError("Could not establish a Senate eFD session")

    current_year = utcnow().year
    filings = await search_annual_filings(f"{current_year - _YEARS_BACK}-01-01")
    if not any(is_senator_filing(f) and is_annual_title(f.get("title") or "") for f in filings):
        # Every senator files one every year; none across two years means
        # the search broke (search_filings returns [] on any failure).
        raise RuntimeError("Senate eFD annual-report search returned no senators' reports")
    per_senator: dict[str, list[dict]] = {}
    matched: dict[tuple[str, str, str | None], str | None] = {}  # one lookup per filer, not per filing
    for filing in filings:
        if not is_senator_filing(filing) or not is_annual_title(filing.get("title") or ""):
            continue
        filer = (filing["last"], filing["first"], filing.get("office"))
        if filer not in matched:
            senator = match_senator(db, *filer)
            matched[filer] = senator.id if senator is not None else None
        if matched[filer] is not None:
            per_senator.setdefault(matched[filer], []).append(filing)

    stored = _stored_reports(db, FinancialDisclosure.senator_id)
    inserted = 0
    health = _SourceHealth("Senate eFD")
    order = _members_in_order(per_senator, stored)
    for position, senator_id in enumerate(order):
        mine = stored.get(senator_id)
        attempted = fetched = False
        out_of_time = False
        for filing in sorted(per_senator[senator_id], key=_senate_rank, reverse=True):
            filing_id = senate_filing_id(filing["report_url"])
            if _is_current(mine, filing_id, SENATE_PARSER_VERSION):
                break
            if _senate_is_older(mine, filing):
                # The search came back without the stored (newer) report — a
                # page of results failed to load — or the best candidate left
                # is a paper amendment of unknowable year. Keep what's stored.
                break
            if time.monotonic() > deadline:
                out_of_time = True
                break
            known_bad = _is_known_bad(db, filing_id)
            attempted = attempted or not known_bad
            try:
                report = await fetch_senate_annual(client, db, filing)
            except SessionLapsed:
                # The session lapsed partway through the phase: accept the
                # terms again and retry this report once. Only on an actual
                # lapse — a filing that simply won't load costs no extra
                # round trips.
                logger.info("Senate eFD session lapsed — re-accepting terms")
                if await senate_accept_terms(client) is None:
                    # Re-accepting failed too: every remaining fetch would
                    # lapse the same way. Stop, and fail the phase.
                    db.commit()
                    raise RuntimeError("Senate eFD session lapsed and the terms could not be re-accepted")
                try:
                    report = await fetch_senate_annual(client, db, filing)
                except SessionLapsed:
                    # Lapsed again straight after re-accepting: a session
                    # problem, not this filing's — no miss recorded.
                    continue
            if report is None or not report.final:
                # Nothing usable this run: the filing won't load, or its read
                # may be transient (a parser crash, or a page that is neither
                # a report nor the terms page — an error or block page looks
                # just like that). Stored nowhere: "can't be read" would be a
                # claim about the report this run can't back. Fall through to
                # the senator's next-best filing.
                _record_miss(db, filing_id)
                continue
            _clear_miss(db, filing_id)
            fetched = True
            inserted += _replace_disclosure(
                db,
                owner_filter={"senator_id": senator_id},
                filing_id=filing_id,
                report_year=_senate_report_year(filing),
                report_label=_senate_report_label(filing),
                filed_date=filing.get("filed_date"),
                source_url=filing["report_url"],
                report=report,
                parser_version=SENATE_PARSER_VERSION,
            )
            break
        db.commit()
        health.record(attempted, fetched)
        if out_of_time:
            logger.info("Senate holdings: time budget spent — %d members wait for the next run", len(order) - position)
            break
    health.check()
    return inserted


# Progress-tracker steps for the two phases, appended to the stock-trades
# run's own (stock_pipeline.STOCK_PIPELINE_STEPS).
HOLDINGS_STEPS = [
    ("house_holdings",  "fetch", "Ingest House annual disclosures (holdings)"),
    ("senate_holdings", "fetch", "Ingest Senate annual disclosures (holdings)"),
]


async def run_holdings_phases(
    db: Session, client: httpx.AsyncClient, progress,
) -> tuple[dict[str, int], list[str]]:
    """Run both phases, best-effort each. Returns (holdings stored per
    step, error summaries for the run row).

    Lives here rather than in stock_pipeline.py so that later changes to how
    the holdings phases run don't touch a file the analysis-code fingerprint
    covers. A failed phase sends its own ops alert: the holdings phases
    deliberately don't decide the stock-trades run's status (see
    stock_pipeline), so without it a holdings outage would go unseen.
    """
    counts = {step: 0 for step, _, _ in HOLDINGS_STEPS}
    errors: list[str] = []
    for (step, _, _), label, ingest in zip(
        HOLDINGS_STEPS,
        ("House holdings", "Senate holdings"),
        (ingest_house_holdings, ingest_senate_holdings),
    ):
        progress.begin(step)
        try:
            counts[step] = await ingest(db, client)
            progress.complete(step, detail=f"{counts[step]} holdings")
        except Exception:
            logger.exception("%s ingestion failed", label)
            db.rollback()
            errors.append(f"{label}: failed — see server logs")
            progress.fail(step)
            # Every failed phase alerts: the holdings phases don't decide
            # the run's status, so the run row alone would let one chamber's
            # holdings age silently for as long as it keeps failing.
            _alert(
                f"{label} ingest failed",
                f"The {label} phase of tonight's stock-trades run failed. Reports it stored "
                "before failing are kept (each member is committed as it's done); every "
                "other member's stored holdings stay as they were and will age until a "
                "run succeeds — see the server logs for the cause.",
                dedupe_key=f"{step}-failed-{utcnow():%Y-%m-%d}",
            )
    return counts, errors


