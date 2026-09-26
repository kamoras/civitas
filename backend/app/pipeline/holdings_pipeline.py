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

Each phase is time-boxed (PHASE_CEILING). A normal run fetches only the
handful of reports filed since the last one and finishes in a minute or
two; the first run, or a PARSER_VERSION bump, has every member's report to
read, and the budget spreads that over a few nightly runs instead of
holding the stock-trades run — and the hourly action-center refresh that
waits on it — for longer (ops_alerts.stock_trades_overrun_budget allows
for PHASE_CEILING). Members with nothing stored go
first, so coverage fills before re-reads.
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import TypeVar

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.alerting import safe_ops_alert as _alert
from app.models import FinancialDisclosure, FinancialHolding
from app.pipeline.fetch.fd_common import UNREADABLE_SCANNED, AnnualReport
from app.pipeline.fetch.house_fd import PARSER_VERSION as HOUSE_PARSER_VERSION
from app.pipeline.fetch.house_fd import fetch_and_parse_annual as fetch_house_annual, fetch_annual_filing_index
from app.pipeline.fetch.house_fd import report_still_loads as house_report_still_loads
from app.pipeline.fetch.senate_fd import PARSER_VERSION as SENATE_PARSER_VERSION
from app.pipeline.fetch.senate_fd import (
    SessionLapsed,
    fetch_and_parse_annual as fetch_senate_annual,
    is_amendment_title,
    is_annual_title,
    is_new_filer_title,
    is_senator_filing,
    search_annual_filings,
)
from app.pipeline.fetch.senate_fd import report_still_loads as senate_report_still_loads
from app.pipeline.fetch.senate_ptr import accept_terms as senate_accept_terms
from app.pipeline.fetch.senate_ptr import senate_filing_id
from app.pipeline.filer_matching import (
    current_representatives,
    current_senators,
    match_representative,
    match_senator,
)
from app.holdings_schedule import FETCH_BUDGET, HOLDINGS_STEPS, PREP_BUDGET, PROBE_BUDGET
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Annual reports cover the previous calendar year and are due in May, with
# extensions into August — so a member's newest report is for last year, or
# (before they file) the year before.
_YEARS_BACK = 2

# Members tried with nothing coming back live before _SourceHealth looks
# further — asking the source about a stored report, or (when reports load
# but none can be read) failing the phase outright.
MIN_ATTEMPTS_FOR_OUTAGE = 5


Rank = tuple[int, str, bool]  # (year, filed date, amended): newest first when sorted descending


@dataclass
class _Stored:
    filing_id: str
    parser_version: int | None
    report_year: int | None
    filed_date: str | None
    parsed: bool
    holding_count: int
    amended: bool

    @property
    def rank(self) -> Rank:
        return (self.report_year or 0, self.filed_date or "", self.amended)


def _stored_reports(db: Session, column) -> dict[str, _Stored]:
    counts = (
        db.query(FinancialHolding.disclosure_id, func.count().label("n"))
        .group_by(FinancialHolding.disclosure_id)
        .subquery()
    )
    rows = (
        db.query(
            column, FinancialDisclosure.filing_id, FinancialDisclosure.parser_version,
            FinancialDisclosure.report_year, FinancialDisclosure.filed_date, FinancialDisclosure.parsed,
            func.coalesce(counts.c.n, 0), FinancialDisclosure.amended,
        )
        .outerjoin(counts, counts.c.disclosure_id == FinancialDisclosure.id)
        .filter(column.isnot(None))
    )
    return {owner_id: _Stored(*rest) for owner_id, *rest in rows.all()}


def _is_current(stored: _Stored | None, filing_id: str, parser_version: int) -> bool:
    """Already ingested: same filing, read by this parser. The same filing
    read by an older parser is re-read, so a parser fix reaches every member
    instead of only those who file something new."""
    return stored is not None and (stored.filing_id, stored.parser_version) == (filing_id, parser_version)


def _prior_count(mine: _Stored | None, filing_id: str) -> int | None:
    """Holdings an earlier parser read from this very filing, when it is the
    one stored — what a re-read after a PARSER_VERSION bump is compared to."""
    if mine is None or mine.filing_id != filing_id or not mine.parsed:
        return None
    return mine.holding_count


def _keeps_earlier_read(prior: int | None, report: AnnualReport) -> bool:
    """A re-read of the very report already stored — after a PARSER_VERSION
    bump — that couldn't read it at all (unrecognized, or "scanned" though
    an earlier parser read its text). A filed report never changes, so that
    is the new parser's failure, not the report's: the earlier read stays,
    and a parser regression never turns a scorecard's pie into "not
    machine-readable". A re-read that reads rows — fewer, more, or none —
    replaces it: a parser fix looks exactly like that.

    The stored row keeps its old parser_version, so the report is re-read
    (from the parse cache — no request) every run, and each re-read counts
    as a parser miss in _SourceHealth for as long as the regression lasts.
    Stamping it as tried would silence the alarm after one night."""
    if prior is None or report.holdings is not None:
        return False
    logger.warning("Parser could not read a report an earlier parser read %d holdings from — keeping those", prior)
    return True


def _is_older(stored: _Stored | None, rank: Rank) -> bool:
    """The candidate ranks below what's stored. The stored report's rank is
    kept with it (year, filing date, amended), so this holds even when a
    partial index or search no longer returns the stored filing — what it
    superseded can't come back."""
    return stored is not None and rank < stored.rank


def _members_in_order(per_member: dict[str, list[dict]], stored: dict[str, _Stored]) -> list[str]:
    """Members with nothing stored first: under the time budget, filling a
    missing scorecard section matters more than refreshing a present one."""
    return sorted(per_member, key=lambda member_id: member_id in stored)


def _replace_disclosure(
    db: Session, *, owner_filter: dict, filing_id: str, report_year: int | None, report_label: str,
    filed_date: str | None, source_url: str, report: AnnualReport, parser_version: int, rank: Rank,
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
        amended=rank[2],
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


class _Outcome:
    """What one member's fetches showed about the source this run.

    Only live requests count — a parse-cache hit or a paper filing that is
    never fetched proves nothing about the source (AnnualReport.live).
    `attempted`: a live request was made. `fetched`: one returned a report.
    """

    def __init__(self) -> None:
        self.attempted = self.fetched = False

    def fetch(self, report: AnnualReport | None) -> None:
        if report is None or report.live:
            self.attempted = True
        if report is not None and report.live and report.final:
            self.fetched = True

    def lapsed(self) -> None:
        """A request answered with eFD's terms page even after re-accepting."""
        self.attempted = True


class _SourceHealth:
    """Whether a phase's source or parser looked broken, judged once the
    phase is done.

    Deliberately not an early abort: members with nothing stored are
    processed first, so a few genuinely broken filings bunched at the front
    would otherwise stop the phase before it reached anyone else.

    - Parser, counted per read (`parsed`): only unambiguous failures are
      misses — a crash, an unrecognized report, or "scanned" where an
      earlier parser read the same filing's text. Rows read are successes.
      Empty reads, changed row counts and filer attribution are neither: a
      genuine report can list nothing, a parser fix changes counts too, and
      a former member running again reads as a candidate on a filing with
      a member's honorific; the parser tests' real-filing fixtures are what
      guard those. At least MIN_ATTEMPTS_FOR_OUTAGE misses outnumbering
      successes fails the phase, and nothing about the source can excuse
      it. A kept earlier read (_keeps_earlier_read) is re-read every run, so
      a regression keeps failing the phase for as long as it lasts.
    - Source, counted per member (`record`): no report fetched from at
      least MIN_ATTEMPTS_FOR_OUTAGE members tried — or from any tried when
      FETCH_BUDGET ran out: with nothing fetched, the budget went on
      requests that failed (a parse-cache hit or a paper filing takes no
      time), which is what a host that hangs rather than refuses looks like
      after two or three members. That is an outage, or a night on
      which the only filings left to fetch are ones that won't load (a PDF
      the index lists but the Clerk 404s). Counts alone can't tell those
      apart, and neither can a memory of past failures — anything that
      learns to stop counting a filing also learns to stop seeing an
      outage. So the phase asks the source directly: it re-requests reports
      it already stored, live and past any cache, within PROBE_BUDGET. If
      one loads, the failures are those filings'; if none does — or nothing
      is stored to ask about — the phase fails, every night the outage
      lasts.
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.attempted = self.fetched = 0
        self.out_of_time = False
        self.parsed_ok = self.parser_miss = 0

    def record(self, outcome: _Outcome) -> None:
        self.attempted += outcome.attempted
        self.fetched += outcome.fetched

    def parsed(self, report: AnnualReport, prior: int | None) -> None:
        if report.holdings:
            self.parsed_ok += 1
        elif report.holdings is None and (report.unreadable_reason != UNREADABLE_SCANNED or prior is not None):
            self.parser_miss += 1

    def _looks_down(self) -> bool:
        if self.fetched or not self.attempted:
            return False
        return self.attempted >= MIN_ATTEMPTS_FOR_OUTAGE or self.out_of_time

    async def check(
        self, stored_urls: Callable[[], list[str]], still_loads: Callable[[str], Awaitable[bool]],
    ) -> None:
        if self.parser_miss >= MIN_ATTEMPTS_FOR_OUTAGE and self.parser_miss > self.parsed_ok:
            raise RuntimeError(
                f"{self.source}: {self.parser_miss} reports could not be read, {self.parsed_ok} were — "
                "a parser regression"
            )
        if not self._looks_down():
            return
        deadline = time.monotonic() + PROBE_BUDGET.total_seconds()
        for url in stored_urls():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                loads = await asyncio.wait_for(still_loads(url), remaining)
            except TimeoutError:
                break
            if loads:
                logger.warning(
                    "%s: %d members' reports failed to load, but a stored report (%s) still does — "
                    "those filings, not the source", self.source, self.attempted, url,
                )
                return
        raise RuntimeError(f"{self.source}: {self.attempted} members tried, no report fetched")


# How many stored reports to ask about before calling the source down: one
# could itself have been withdrawn since it was stored.
_PROBES = 3


def _stored_urls(db: Session, owner_column) -> list[str]:
    """The most recently stored reports that were fetched and read — the
    ones most likely to still be where they were."""
    rows = (
        db.query(FinancialDisclosure.source_url)
        .filter(owner_column.isnot(None), FinancialDisclosure.parsed == True, FinancialDisclosure.source_url != "")  # noqa: E712
        .order_by(FinancialDisclosure.ingested_at.desc(), FinancialDisclosure.id.desc())
        .limit(_PROBES)
    )
    return [url for (url,) in rows]


async def _within(step: Awaitable[T], budget: timedelta, what: str) -> T:
    """A phase's preparation step, failed if it outlasts PREP_BUDGET."""
    try:
        return await asyncio.wait_for(step, budget.total_seconds())
    except TimeoutError:
        raise RuntimeError(f"{what} took longer than {budget}") from None


class _SkipFiling(Exception):
    """This filing can't be tried this run, for a reason that isn't the
    filing's (a session that lapsed again right after re-accepting)."""


# Whose report a chamber's reader says it just read.
_MEMBERS, _NOT_MEMBERS = "member", "not member"


@dataclass
class _Chamber:
    """What differs between the two phases; _ingest_members does the rest."""

    source: str
    owner_key: str  # FinancialDisclosure column naming the member
    parser_version: int
    filing_id: Callable[[dict], str]
    # One member's filings, keyed by filing id, to the Rank they sort
    # newest-first and compare to the stored report by.
    ranks: Callable[[list[dict]], dict[str, Rank]]
    fetch: Callable[[dict, float], Awaitable[AnnualReport | None]]  # (filing, deadline)
    owner: Callable[[dict, AnnualReport], str]
    fields: Callable[[dict], dict]  # report_year, report_label, filed_date, source_url
    still_loads: Callable[[str], Awaitable[bool]]


async def _ingest_members(db: Session, chamber: _Chamber, per_member: dict[str, list[dict]]) -> int:
    """Store each member's newest report from their candidate filings.
    Returns holdings stored."""
    owner_column = getattr(FinancialDisclosure, chamber.owner_key)
    stored = _stored_reports(db, owner_column)
    health = _SourceHealth(chamber.source)
    deadline = time.monotonic() + FETCH_BUDGET.total_seconds()
    inserted = 0
    order = _members_in_order(per_member, stored)
    for position, member_id in enumerate(order):
        mine = stored.get(member_id)
        outcome = _Outcome()
        out_of_time = False
        ranks = chamber.ranks(per_member[member_id])
        for filing in sorted(per_member[member_id], key=lambda f: ranks[chamber.filing_id(f)], reverse=True):
            filing_id = chamber.filing_id(filing)
            if _is_current(mine, filing_id, chamber.parser_version):
                break  # already have the newest report, as this parser reads it
            if _is_older(mine, ranks[filing_id]):
                # Only older reports than the stored one turned up — a year's
                # index or a page of search results failed to load, or the
                # best left is a paper amendment of unknowable year. Keep
                # the newer stored report.
                break
            if time.monotonic() > deadline:
                out_of_time = True
                break
            try:
                report = await chamber.fetch(filing, deadline)
            except _SkipFiling:
                # The session, not the filing: every other filing of this
                # member's would lapse the same way. They wait for the next
                # run. (A filing that fails on its own does fall through, below.)
                outcome.lapsed()
                break
            outcome.fetch(report)
            if report is None:
                # Not fetched this run. Try the member's next filing: the
                # failed one may not even be theirs (a same-surname
                # candidate's amendment can rank first), and an older report
                # of theirs is labelled with its own year, so it never passes
                # for the newer one. The newer one outranks it and is tried
                # again every run; _is_older keeps a fall-through from ever
                # displacing a newer stored report.
                continue
            prior = _prior_count(mine, filing_id)
            if not report.final:
                # The parser crashed, which may be transient: stored nowhere,
                # since "can't be read" would be a claim about the report this
                # run can't back. A crash is the parser's, whoever filed it.
                health.parsed(report, prior)
                continue
            if chamber.owner(filing, report) != _MEMBERS:
                continue
            health.parsed(report, prior)
            if _keeps_earlier_read(prior, report):
                break
            inserted += _replace_disclosure(
                db,
                owner_filter={chamber.owner_key: member_id},
                filing_id=filing_id,
                **chamber.fields(filing),
                report=report,
                parser_version=chamber.parser_version,
                rank=ranks[filing_id],
            )
            # Per member, so a budget stop or a later failure keeps what's done.
            db.commit()
            break
        health.record(outcome)
        if out_of_time:
            logger.info("%s: time budget spent — %d members wait for the next run", chamber.source, len(order) - position)
            break
    # Also true when the last fetch was cut off at the deadline and every
    # member after it was already current.
    health.out_of_time = time.monotonic() > deadline
    await health.check(lambda: _stored_urls(db, owner_column), chamber.still_loads)
    return inserted


def _house_owner(filing: dict, report: AnnualReport) -> str:
    """Whose report a House filing is. The Status line on its cover page
    says, when the report has text: a candidate for the seat can share the
    member's surname and district. A scanned report has no readable Status
    line; then the Clerk's own structured index fields decide, never a
    name. An original annual report ("O") is a sitting member's — in the
    2025 index all 430 were (426 marked "Hon.", the other 4 sitting members
    too), and every one carries a district. An amendment is a member's only
    with the "Hon." every sitting member's filing carries (a candidate's
    has none: 2025, 82 of 105 amendments)."""
    status = (report.filer_status or "").lower()
    if status:
        return _MEMBERS if status == "member" else _NOT_MEMBERS
    if filing.get("filing_type") == "O" or _is_member_prefix(filing.get("prefix")):
        return _MEMBERS
    return _NOT_MEMBERS


async def _house_candidates(db: Session, client: httpx.AsyncClient) -> dict[str, list[dict]]:
    current_year = utcnow().year
    per_rep: dict[str, list[dict]] = {}
    matched: dict[tuple[str, str, str], str | None] = {}  # one lookup per filer, not per filing
    roster = current_representatives(db)
    indexed = 0
    for year in range(current_year - 1, current_year - 1 - _YEARS_BACK, -1):
        filings = await fetch_annual_filing_index(client, db, year)
        indexed += len(filings)
        for filing in filings:
            filer = (filing["last"], filing["first"], filing["state_district"])
            if filer not in matched:
                rep = match_representative(roster, *filer)
                matched[filer] = rep.id if rep is not None else None
            if matched[filer] is not None:
                per_rep.setdefault(matched[filer], []).append(filing)
    if indexed == 0:
        # Two calendar years with no annual report from anyone in the House
        # is a failed or changed index, not a quiet year — fail the phase so
        # the run records it, instead of leaving every stored report to age.
        raise RuntimeError("House annual-report index returned no filings for either year")
    return per_rep


async def ingest_house_holdings(db: Session, client: httpx.AsyncClient) -> int:
    """Store each representative's newest annual report — the newest
    calendar year, then the latest filed within it (an amendment supersedes
    the original it amends). Returns holdings stored."""
    per_rep = await _within(_house_candidates(db, client), PREP_BUDGET, "House annual-report index download")
    chamber = _Chamber(
        source="House Clerk",
        owner_key="representative_id",
        parser_version=HOUSE_PARSER_VERSION,
        filing_id=lambda f: f["doc_id"],
        ranks=lambda filings: {
            f["doc_id"]: (f.get("year") or 0, f.get("filing_date") or "", f.get("filing_type") == "A") for f in filings
        },
        fetch=lambda f, deadline: fetch_house_annual(client, db, f, deadline=deadline),
        owner=_house_owner,
        fields=lambda f: {
            "report_year": f.get("year"),
            "report_label": _house_report_label(f),
            "filed_date": f.get("filing_date"),
            "source_url": f["pdf_url"],
        },
        still_loads=lambda url: house_report_still_loads(client, url),
    )
    return await _ingest_members(db, chamber, per_rep)


_CY_RE = re.compile(r"\bCY\s*(\d{4})\b", re.I)
_DATE_RE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")


def _senate_ranks(filings: list[dict]) -> dict[str, Rank]:
    """One senator's filings, keyed by report URL, to the Rank they sort
    and compare by: the year the title states, the filing date, then an
    amendment over an original.

    A paper filing states no year — not in its link, and not on its page,
    which is page images (checked 2026-09-26) — so it ranks below every
    dated report and is used only when a senator has nothing dated. A
    paper filing made after the report shown is named beside it instead
    (_note_later_paper): "filed later" is a fact; "newer" would be a guess.
    """
    return {
        f["report_url"]: (_senate_report_year(f) or 0, f.get("filed_date") or "", is_amendment_title(f.get("title") or ""))
        for f in filings
    }


def _note_later_paper(db: Session, per_senator: dict[str, list[dict]]) -> None:
    """Name, beside each senator's stored report, the newest paper filing
    they made after it (see _senate_ranks), or clear the note when there is
    none. Only for senators this search returned filings for: a senator it
    missed keeps whatever note they had."""
    for disclosure in db.query(FinancialDisclosure).filter(FinancialDisclosure.senator_id.in_(list(per_senator))):
        later = [
            f for f in per_senator[disclosure.senator_id]
            if f.get("is_paper")
            and senate_filing_id(f["report_url"]) != disclosure.filing_id
            and (f.get("filed_date") or "") > (disclosure.filed_date or "")
        ]
        newest = max(later, key=lambda f: f.get("filed_date") or "", default=None)
        disclosure.later_paper_label = _senate_report_label(newest) if newest else None
        disclosure.later_paper_url = newest["report_url"] if newest else None
    db.commit()


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
    title = filing.get("title") or ""
    amended = is_amendment_title(title)
    new_filer = is_new_filer_title(title)
    if new_filer and (m := _DATE_RE.search(title)):
        return f"new-filer report as of {m.group(3)}-{m.group(1)}-{m.group(2)}" + (" (amended)" if amended else "")
    year = _senate_report_year(filing)
    if year is not None:
        return f"{year} annual report" + (" (amended)" if amended else "")
    kind = "new-filer report" if new_filer else "annual report"
    kind += " amendment" if amended else ""
    filed = filing.get("filed_date")
    return f"{kind} filed {filed}" if filed else kind


async def _senate_candidates(db: Session, client: httpx.AsyncClient) -> dict[str, list[dict]]:
    if await senate_accept_terms(client) is None:
        raise RuntimeError("Could not establish a Senate eFD session")
    filings = await search_annual_filings(f"{utcnow().year - _YEARS_BACK}-01-01")
    if not any(is_senator_filing(f) and is_annual_title(f.get("title") or "") for f in filings):
        # Every senator files one every year; none across two years means
        # the search broke (search_filings returns [] on any failure).
        raise RuntimeError("Senate eFD annual-report search returned no senators' reports")
    per_senator: dict[str, list[dict]] = {}
    matched: dict[tuple[str, str, str | None], str | None] = {}  # one lookup per filer, not per filing
    roster = current_senators(db)
    for filing in filings:
        if not is_senator_filing(filing) or not is_annual_title(filing.get("title") or ""):
            continue
        filer = (filing["last"], filing["first"], filing.get("office"))
        if filer not in matched:
            senator = match_senator(roster, *filer)
            matched[filer] = senator.id if senator is not None else None
        if matched[filer] is not None:
            per_senator.setdefault(matched[filer], []).append(filing)
    return per_senator


class _TermsRefused(Exception):
    """eFD's terms could not be re-accepted after a lapse."""


async def _retry_after_lapse(client: httpx.AsyncClient, call: Callable[[], Awaitable[T]]) -> T:
    """call(), and if eFD's session has lapsed, accept the terms again and
    call() once more — only on an actual lapse, so a filing that simply
    won't load costs no extra round trips. A second lapse raises
    SessionLapsed; terms that can't be re-accepted raise _TermsRefused."""
    try:
        return await call()
    except SessionLapsed:
        logger.info("Senate eFD session lapsed — re-accepting terms")
    if await senate_accept_terms(client) is None:
        raise _TermsRefused
    return await call()


async def _fetch_senate(client: httpx.AsyncClient, db: Session, filing: dict, deadline: float) -> AnnualReport | None:
    """One report, through the lapse handling, cut off at the deadline
    (the parse is synchronous, so cancelling only ever interrupts a
    request)."""
    try:
        return await asyncio.wait_for(
            _retry_after_lapse(client, lambda: fetch_senate_annual(client, db, filing)),
            max(deadline - time.monotonic(), 0.001),
        )
    except TimeoutError:
        return None
    except _TermsRefused:
        # Every remaining fetch would lapse the same way. Stop, and fail
        # the phase.
        raise RuntimeError("Senate eFD session lapsed and the terms could not be re-accepted") from None
    except SessionLapsed:
        raise _SkipFiling from None


async def _senate_probe(client: httpx.AsyncClient, url: str) -> bool:
    """senate_report_still_loads through the same lapse handling as the
    fetches — a lapse says nothing about whether eFD is up."""
    try:
        return await _retry_after_lapse(client, lambda: senate_report_still_loads(client, url))
    except (SessionLapsed, _TermsRefused):
        return False


async def ingest_senate_holdings(db: Session, client: httpx.AsyncClient) -> int:
    """Store each senator's newest annual report (see _senate_ranks).
    Returns holdings stored."""
    per_senator = await _within(_senate_candidates(db, client), PREP_BUDGET, "Senate eFD terms and search")
    chamber = _Chamber(
        source="Senate eFD",
        owner_key="senator_id",
        parser_version=SENATE_PARSER_VERSION,
        filing_id=lambda f: senate_filing_id(f["report_url"]),
        ranks=lambda filings: {
            senate_filing_id(url): rank for url, rank in _senate_ranks(filings).items()
        },
        fetch=lambda f, deadline: _fetch_senate(client, db, f, deadline),
        owner=lambda f, report: _MEMBERS,  # the search is filtered to senators' own filings
        fields=lambda f: {
            "report_year": _senate_report_year(f),
            "report_label": _senate_report_label(f),
            "filed_date": f.get("filed_date"),
            "source_url": f["report_url"],
        },
        still_loads=lambda url: _senate_probe(client, url),
    )
    inserted = await _ingest_members(db, chamber, per_senator)
    _note_later_paper(db, per_senator)
    return inserted


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


