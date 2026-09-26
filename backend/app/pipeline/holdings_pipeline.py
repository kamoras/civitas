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

from app.models import FinancialDisclosure, FinancialHolding
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

# Wall-clock budget for each holdings phase, counted from the phase's start
# and checked before every report fetch. Measured 2026-09: a first House run
# reads ~430 reports (~9 min in a dev container, network-bound at the
# Clerk's 1 req/s; slower on the Pi's CPU), the Senate ~100 (~4 min, most of
# it the browser search). Past the budget the remaining members wait for the
# next run. It bounds the report fetching, which is what grows with the
# number of members; the index download and the Senate search before it
# are single steps bounded by their own request timeouts (a normal search
# is a few pages, filtered to senators).
PHASE_BUDGET = timedelta(minutes=10)

# Consecutive members with no report fetchable before the phase gives up
# and fails (see _FailureRun).
MAX_CONSECUTIVE_FETCH_FAILURES = 5


@dataclass
class _Stored:
    filing_id: str
    parser_version: int | None
    report_year: int | None
    filed_date: str | None


def _stored_reports(db: Session, column) -> dict[str, _Stored]:
    rows = db.query(
        column, FinancialDisclosure.filing_id, FinancialDisclosure.parser_version,
        FinancialDisclosure.report_year, FinancialDisclosure.filed_date,
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
    for old in db.query(FinancialDisclosure).filter_by(**owner_filter).all():
        db.delete(old)
    disclosure = FinancialDisclosure(
        **owner_filter,
        filing_id=filing_id,
        report_year=report_year,
        report_label=report_label,
        filed_date=filed_date,
        source_url=source_url,
        parsed=report.holdings is not None,
        unreadable_reason=report.unreadable_reason,
        # A non-final read (the parser crashed) records no version, so the
        # next run doesn't count the report as read and tries it again.
        parser_version=parser_version if report.final else None,
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


class _FailureRun:
    """Counts consecutive members whose every fetch attempt failed. One
    bad filing is that filing's problem; a run of members with nothing
    fetchable is the source (or its session) being down, and must fail the
    phase rather than leave it quietly short."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.count = 0

    def record(self, attempted: bool, fetched: bool, settled: bool) -> None:
        """`settled`: the member turned out to be up to date (their stored
        report is current or newer) — whatever failed on the way there, the
        source served what was needed."""
        if not attempted or settled:
            return
        self.count = 0 if fetched else self.count + 1
        if self.count >= MAX_CONSECUTIVE_FETCH_FAILURES:
            raise RuntimeError(f"{self.source}: {self.count} members in a row with no report fetchable")


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
    failure_run = _FailureRun("House Clerk")
    order = _members_in_order(per_rep, stored)
    for position, rep_id in enumerate(order):
        mine = stored.get(rep_id)
        attempted = fetched = settled = False
        out_of_time = False
        for filing in per_rep[rep_id]:
            if _is_current(mine, filing["doc_id"], HOUSE_PARSER_VERSION):
                settled = True
                break  # already have the newest report, as this parser reads it
            if _is_older(mine, filing.get("year"), filing.get("filing_date")):
                # Only older reports than the stored one turned up (a year's
                # index failed to load): keep the newer stored report.
                settled = True
                break
            if time.monotonic() > deadline:
                out_of_time = True
                break
            attempted = True
            report = await fetch_house_annual(client, db, filing)
            if report is None:
                # Couldn't fetch this filing (it may not even be the
                # member's — a same-surname candidate's amendment can rank
                # first): try the member's next one. _is_older above keeps
                # that from ever displacing a newer stored report.
                continue
            fetched = True
            if not report.final and mine is not None:
                # Transiently unreadable: keep the stored report rather than
                # replacing a readable one with a link.
                break
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
        if out_of_time:
            logger.info("House holdings: time budget spent — %d members wait for the next run", len(order) - position)
            break
        failure_run.record(attempted, fetched, settled)
    return inserted


_CY_RE = re.compile(r"\bCY\s*(\d{4})\b", re.I)
_DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")


def _senate_rank(filing: dict) -> tuple[int, bool, str]:
    """Newest first: by the year the report describes (a paper amendment,
    whose year can't be known, ranks below every report with one), then by
    whether that year was stated rather than inferred, then by filing
    date."""
    title = filing.get("title") or ""
    stated = bool(_CY_RE.search(title) or _DATE_RE.search(title))
    return (_senate_report_year(filing) or 0, stated, filing.get("filed_date") or "")


def _is_paper_amendment(filing: dict) -> bool:
    title = (filing.get("title") or "").lower()
    return bool(filing.get("is_paper")) and "amendment" in title


def _senate_report_year(filing: dict) -> int | None:
    """The calendar year a Senate report's holdings describe, or None when
    it can't be known.

    "Annual Report for CY 2025" states it; a "New Filer Report for
    03/24/2026" describes that date. A paper filing's link reads only
    "Annual Report" or "Annual Report (Amendment)". An original annual
    report is filed the spring or summer after the year it covers, so the
    year before its filing is its year. An amendment can amend any earlier
    report — Ricketts' paper amendment of February 2026 (live, 2026-09)
    amends something filed before any CY2025 report existed — so its year is
    None, not a guess.
    """
    title = filing.get("title") or ""
    if m := _CY_RE.search(title):
        return int(m.group(1))
    if m := _DATE_RE.search(title):
        return int(m.group(3))
    if _is_paper_amendment(filing):
        return None
    filed = filing.get("filed_date") or ""
    return int(filed[:4]) - 1 if filed[:4].isdigit() else None


def _senate_report_label(filing: dict) -> str:
    """What the report is, in the words the page shows: a new-filer report
    is a snapshot at its date, not a year-end annual report, and a paper
    amendment's year isn't claimed."""
    title = filing.get("title") or ""
    amended = " (amended)" if "amendment" in title.lower() else ""
    if "new filer" in title.lower() and (m := _DATE_RE.search(title)):
        return f"new-filer report as of {m.group(3)}-{m.group(1)}-{m.group(2)}{amended}"
    year = _senate_report_year(filing)
    if year is None and filing.get("filed_date"):
        return f"annual report amendment filed {filing['filed_date']}"
    return f"{year} annual report{amended}" if year else f"annual report{amended}"


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
    for filing in filings:
        if not is_senator_filing(filing) or not is_annual_title(filing.get("title") or ""):
            continue
        senator = match_senator(db, filing["last"], filing["first"])
        if senator is None:
            continue
        per_senator.setdefault(senator.id, []).append(filing)

    stored = _stored_reports(db, FinancialDisclosure.senator_id)
    inserted = 0
    failure_run = _FailureRun("Senate eFD")
    order = _members_in_order(per_senator, stored)
    for position, senator_id in enumerate(order):
        mine = stored.get(senator_id)
        attempted = fetched = settled = False
        out_of_time = False
        for filing in sorted(per_senator[senator_id], key=_senate_rank, reverse=True):
            filing_id = senate_filing_id(filing["report_url"])
            if _is_current(mine, filing_id, SENATE_PARSER_VERSION):
                settled = True
                break
            if _is_older(mine, _senate_report_year(filing), filing.get("filed_date")):
                settled = True
                # The search came back without the stored (newer) report — a
                # page of results failed to load — or the best candidate left
                # is a paper amendment of unknowable year. Keep what's stored.
                break
            year, stated, _ = _senate_rank(filing)
            if mine is not None and not stated and mine.report_year == year:
                # A paper report's year is inferred from its filing date; it
                # never displaces a stored report for that same year, which
                # may state it (a readable electronic report beats a scan).
                settled = True
                break
            if time.monotonic() > deadline:
                out_of_time = True
                break
            attempted = True
            try:
                report = await fetch_senate_annual(client, db, filing)
            except SessionLapsed:
                # The session lapsed partway through the phase: accept the
                # terms again and retry this report once. Only on an actual
                # lapse — a filing that simply won't load costs no extra
                # round trips.
                logger.info("Senate eFD session lapsed — re-accepting terms")
                report = None
                if await senate_accept_terms(client) is not None:
                    try:
                        report = await fetch_senate_annual(client, db, filing)
                    except SessionLapsed:
                        report = None
            if report is None:
                # This filing won't load (withdrawn, or not a report page):
                # fall through to the senator's next-best one.
                continue
            fetched = True
            if not report.final and mine is not None:
                # The parser crashed on it this run: keep what's stored and
                # try again next run.
                break
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
        if out_of_time:
            logger.info("Senate holdings: time budget spent — %d members wait for the next run", len(order) - position)
            break
        failure_run.record(attempted, fetched, settled)
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

    Lives here rather than in stock_pipeline.py so that changing how the
    holdings phases run never touches a file the analysis-code fingerprint
    covers. When both phases fail, an ops alert says so directly: the
    holdings phases deliberately don't decide the stock-trades run's status
    (see stock_pipeline), so without it a holdings outage would go unseen.
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
    if len(errors) == len(HOLDINGS_STEPS):
        _alert(
            "Annual-report holdings ingest failed",
            "Both the House and the Senate holdings phases of tonight's stock-trades run "
            f"failed ({'; '.join(errors)}). Stored holdings are unchanged and will age "
            "until a run succeeds — see the server logs for the cause.",
            dedupe_key=f"holdings-ingest-failed-{utcnow():%Y-%m-%d}",
        )
    return counts, errors


def _alert(subject: str, body: str, *, dedupe_key: str) -> None:
    """Best-effort ops alert — never allowed to raise (the same pattern
    president_ptr.py uses)."""
    try:
        from app.ops_alerts import send_ops_alert
        send_ops_alert(subject, body, dedupe_key=dedupe_key)
    except Exception:
        logger.exception("Failed to send ops alert: %s", subject)
