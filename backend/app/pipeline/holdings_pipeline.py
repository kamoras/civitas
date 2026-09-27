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

Each phase is time-boxed (app/holdings_schedule.py). A normal run fetches
only the handful of reports filed since the last one and finishes in a
minute or two; the first run, or a PARSER_VERSION bump, has every member's
report to read, and the budget spreads that over a few nightly runs instead
of holding the stock-trades run — and the hourly action-center refresh that
waits on it — for longer (ops_alerts.stock_trades_overrun_budget allows for
PHASE_CEILING). Members with nothing stored go first, so coverage fills
before re-reads.
"""

import asyncio
import dataclasses
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
from app.pipeline.fetch.ptr_common import normalize_date
from app.pipeline.fetch.senate_ptr import senate_filing_id
from app.pipeline.filer_matching import (
    FilerMatcher,
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


# (as-of date, precedence, seq or 0, filed date, 0 or seq, filing id) —
# newest first when sorted descending; see _rank.
Rank = tuple[str, int, int, str, int, str]


def _rank(
    as_of: str | None, amended: bool, filed_date: str | None, seq: int, filing_id: str, seq_before_date: bool,
) -> Rank:
    """Where a report ranks among a member's filings. The date its holdings
    describe decides first — a year end, or a new-filer report's stated
    date, so an annual report outranks a same-year new-filer snapshot
    however either was amended. For the same date, an amendment supersedes
    the original — always filed after it, so this holds even when a filing
    date didn't parse.

    Among amendments, the House document id (seq) decides before the
    filing date (seq_before_date): the House index
    often gives an amendment its original's filing date (2025: Chu, Johnson
    and five others list both on one day), while its document ids rise with
    filing order — every pair of 8-digit electronic ids with different
    filing dates in the 2025–26 indexes (31 of 31); the only exceptions
    mixed a 7-digit paper id with an 8-digit electronic one, where the id
    only keeps the choice stable. A Senate amendment's seq is the number in
    its title, and there the filing date — reliable in the eFD search —
    decides first: an unnumbered amendment has no number to compare, so the
    number only breaks a same-day tie. (Every Senate report stored in the
    2026-09 live runs had a filing date — 210 of 210 — so a row without one
    ranking below dated amendments of its year is the case that isn't seen.) The filing id last makes any
    remaining tie resolve the same way every run, whatever order a search
    returns.

    A report whose date isn't known (a Senate paper filing, or a title
    that states none) ranks below every dated one, and among those an
    original goes before an amendment: an original's place in the
    once-a-year sequence is at least known, while an undated amendment can
    amend any earlier report."""
    if as_of:
        precedence = 1 if amended else 0
    else:
        precedence = 0 if amended else 1
    first, second = (seq, 0) if seq_before_date else (0, seq)
    return (as_of or "", precedence, first, filed_date or "", second, filing_id)


@dataclass
class _Stored:
    filing_id: str
    parser_version: int | None
    filed_date: str | None
    parsed: bool
    holding_count: int
    amended: bool
    report_label: str
    as_of_date: str | None
    seq: int

    def rank_fields(self) -> dict:
        """What the stored row knows that decides its rank and its label."""
        return {
            "report_label": self.report_label, "filed_date": self.filed_date,
            "as_of_date": self.as_of_date, "amended": self.amended, "seq": self.seq,
        }


def _stored_reports(db: Session, column) -> dict[str, _Stored]:
    rows = (
        db.query(
            column, FinancialDisclosure.filing_id, FinancialDisclosure.parser_version,
            FinancialDisclosure.filed_date, FinancialDisclosure.parsed,
            func.count(FinancialHolding.id), FinancialDisclosure.amended, FinancialDisclosure.report_label,
            FinancialDisclosure.as_of_date, FinancialDisclosure.seq,
        )
        .outerjoin(FinancialHolding, FinancialHolding.disclosure_id == FinancialDisclosure.id)
        .filter(column.isnot(None))
        .group_by(FinancialDisclosure.id)
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
    every run — from the parse cache for its 30 days, then downloaded again
    (live, counted like any fetch, inside FETCH_BUDGET) — and each re-read
    counts as a parser miss in _SourceHealth for as long as the regression
    lasts. Stamping it as tried would silence the alarm after one night."""
    if prior is None or report.holdings is not None:
        return False
    logger.warning("Parser could not read a report an earlier parser read %d holdings from — keeping those", prior)
    return True


def _merge_known(stored: dict, tonight: dict, same_rules: bool) -> dict:
    """One filing's fields from two nights' rows. What the row's title or
    type says — as-of date, label, amended, seq (a Senate title's amendment
    number; a House document id, which never differs) — comes as a set from
    whichever row's title parsed (tonight's, if both did), so a label never
    disagrees with its date. (Tonight's row already carries the stored
    filing date when it had none of its own: _ingest_members recomputes it
    with that date.)

    Only while the stored row was derived by the same rules (same_rules:
    the same PARSER_VERSION — the title rules in _senate_fields are part of
    what it versions). After a bump, tonight's derivation stands, so a
    tightened rule reaches rows stored under the old one."""
    title_from = tonight if tonight["as_of_date"] or not stored["as_of_date"] or not same_rules else stored
    return {**tonight, **{key: title_from[key] for key in ("as_of_date", "report_label", "amended", "seq")}}


def _is_older(stored_rank: Rank | None, rank: Rank) -> bool:
    """The candidate ranks below what's stored. The stored report's rank
    comes from its own stored fields, so this holds even when a partial
    index or search no longer returns the stored filing — what it
    superseded can't come back."""
    return stored_rank is not None and rank < stored_rank


def _members_in_order(per_member: dict[str, list[dict]], stored: dict[str, _Stored]) -> list[str]:
    """Members with nothing stored first: under the time budget, filling a
    missing scorecard section matters more than refreshing a present one."""
    return sorted(per_member, key=lambda member_id: member_id in stored)


def _replace_disclosure(
    db: Session, *, owner_filter: dict, filing_id: str, report_label: str,
    filed_date: str | None, source_url: str, report: AnnualReport, parser_version: int, as_of_date: str | None,
    amended: bool, seq: int,
) -> int:
    """Swap a member's stored report for this one. Returns holdings stored."""
    # Bulk deletes, not the ORM cascade, which would load every stored
    # holding into the session to delete it row by row — hundreds per member
    # on a first run or a PARSER_VERSION bump, inside the phase's budget.
    # SQLite here runs without FK enforcement, so the holdings go explicitly.
    old = db.query(
        FinancialDisclosure.id, FinancialDisclosure.later_filing_label, FinancialDisclosure.later_filing_url,
        FinancialDisclosure.later_filing_filed,
    ).filter_by(**owner_filter).all()
    old_ids = [row.id for row in old]
    # A note naming a filing made on or after the old report's date stays
    # while that holds for the new one too (a re-read of the same report, say):
    # tonight's search may not have the row to rebuild it from.
    carried = next(
        (
            row for row in old
            # Notes are Senate-only: the same filing-id rule as _note_later_filing.
            if _filed_not_before(row.later_filing_filed, filed_date)
            and senate_filing_id(row.later_filing_url or "") != filing_id
        ),
        None,
    )
    if old_ids:
        # "fetch", not False: the session can hold these rows as objects
        # (_note_later_filing loads disclosures; so can a caller), and SQLite
        # reuses a deleted row's id — a stale object left in the identity map
        # would collide with the new row (SQLAlchemy warns "Identity map
        # already had an identity", seen when this was False).
        db.query(FinancialHolding).filter(FinancialHolding.disclosure_id.in_(old_ids)).delete(
            synchronize_session="fetch",
        )
        db.query(FinancialDisclosure).filter(FinancialDisclosure.id.in_(old_ids)).delete(
            synchronize_session="fetch",
        )
    disclosure = FinancialDisclosure(
        **owner_filter,
        filing_id=filing_id,
        report_label=report_label,
        filed_date=filed_date,
        as_of_date=as_of_date,
        amended=amended,
        seq=seq,
        later_filing_label=carried.later_filing_label if carried else None,
        later_filing_url=carried.later_filing_url if carried else None,
        later_filing_filed=carried.later_filing_filed if carried else None,
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
    fetch: Callable[[dict, float], Awaitable[AnnualReport | None]]  # (filing, deadline)
    owner: Callable[[dict, AnnualReport], str]
    # What gets stored for a filing: report_label, filed_date, source_url,
    # as_of_date, amended, seq.
    # Its rank is computed from these same values (_rank), so the rank and
    # the stored row can't disagree.
    fields: Callable[[dict], dict]
    date_key: str  # the filing row's filing-date key
    seq_before_date: bool  # see _rank
    still_loads: Callable[[str], Awaitable[bool]]

    def rank(self, fields: dict, filing_id: str) -> Rank:
        """A filing's rank from its stored or to-be-stored fields — the one
        place a rank is computed, with this chamber's seq rule."""
        return _rank(
            fields["as_of_date"], fields["amended"], fields["filed_date"], fields["seq"], filing_id,
            self.seq_before_date,
        )


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
        fields = {chamber.filing_id(f): chamber.fields(f) for f in per_member[member_id]}
        ranks = {fid: chamber.rank(v, fid) for fid, v in fields.items()}
        if mine is not None and mine.filing_id in fields:
            own = next(f for f in per_member[member_id] if chamber.filing_id(f) == mine.filing_id)
            if not own.get(chamber.date_key) and mine.filed_date:
                # Recomputed with the stored date, so a label that names the
                # date (a paper filing's) names it.
                fields[mine.filing_id] = chamber.fields({**own, chamber.date_key: mine.filed_date})
            # Tonight's row and the stored row describe the same filing;
            # either may lack what failed to parse on its night. Each value
            # comes from whichever has it, so the filing neither sorts below
            # the reports it superseded nor keeps a gap tonight's row fills.
            merged = _merge_known(
                mine.rank_fields(), fields[mine.filing_id], mine.parser_version == chamber.parser_version,
            )
            fields[mine.filing_id] = merged
            ranks[mine.filing_id] = chamber.rank(merged, mine.filing_id)
            repair = {k: merged[k] for k, v in mine.rank_fields().items() if merged[k] != v}
            if repair:
                # Saved now, whatever happens to this member below, and used
                # for every comparison: the stored report's rank is what it
                # is, not what it was stored with.
                db.query(FinancialDisclosure).filter_by(
                    **{chamber.owner_key: member_id}, filing_id=mine.filing_id,
                ).update(repair, synchronize_session=False)
                db.commit()
                mine = dataclasses.replace(mine, **repair)
        stored_rank = chamber.rank(mine.rank_fields(), mine.filing_id) if mine is not None else None
        for filing in sorted(per_member[member_id], key=lambda f: ranks[chamber.filing_id(f)], reverse=True):
            filing_id = chamber.filing_id(filing)
            if _is_current(mine, filing_id, chamber.parser_version):
                break  # already have the newest report, as this parser reads it
            if _is_older(stored_rank, ranks[filing_id]):
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
                **fields[filing_id],
                report=report,
                parser_version=chamber.parser_version,
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
    match = FilerMatcher(current_representatives(db), match_representative)
    indexed = 0
    for year in range(current_year - 1, current_year - 1 - _YEARS_BACK, -1):
        filings = await fetch_annual_filing_index(client, db, year)
        if filings is None:
            # Not an empty year, for either index. Without last year's, a
            # member's older report would pass for their newest; the year
            # before holds nearly every member's current report and its
            # amendments until last year's are filed in May. Either way
            # nothing fetched would look wrong, so the phase fails (and
            # alerts) instead.
            raise RuntimeError(f"House annual-report index for {year} could not be loaded")
        indexed += len(filings)
        for filing in filings:
            if (rep_id := match(filing["last"], filing["first"], filing["state_district"])) is not None:
                per_rep.setdefault(rep_id, []).append(filing)
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
        fetch=lambda f, deadline: fetch_house_annual(client, db, f, deadline=deadline),
        owner=_house_owner,
        date_key="filing_date",
        seq_before_date=True,
        fields=lambda f: {
            "report_label": _house_report_label(f),
            "filed_date": f.get("filing_date") or None,
            "source_url": f["pdf_url"],
            "as_of_date": f"{f['year']}-12-31" if f.get("year") else None,  # annual: holdings at year end
            "amended": f.get("filing_type") == "A",
            "seq": int(f["doc_id"]) if str(f["doc_id"]).isdigit() else 0,  # see _rank
        },
        still_loads=lambda url: house_report_still_loads(client, url),
    )
    return await _ingest_members(db, chamber, per_rep)


_AMENDMENT_NO_RE = re.compile(r"\bAmendment\s+(\d+)", re.I)
_CY_RE = re.compile(r"\bCY\s*(\d{4})\b", re.I)
_DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")


def _senate_as_of(filing: dict) -> str | None:
    """The date a Senate report's holdings describe: the year end of the
    calendar year an annual report's title states, or a new-filer report's
    stated date.

    A paper filing states neither — not in its link, and not on its page,
    which is page images (checked 2026-09-26) — so it has no date and ranks
    below every dated report, this run's or the one stored (_rank): a
    senator's newest dated report stays, and a paper one is stored only for
    a senator with none. A paper filing made on or after the report shown is
    named beside it instead (_note_later_filing): "filed later" is a fact;
    "newer" would be a guess."""
    title = filing.get("title") or ""
    if m := _CY_RE.search(title):
        return f"{m.group(1)}-12-31"
    # Only a new-filer report is described by the date in its title; a
    # date elsewhere (on an amendment, say) is not what its holdings describe.
    if is_new_filer_title(title) and (m := _DATE_RE.search(title)) and (iso := normalize_date(m.group(0))):
        return iso
    return None


def _write_notes(db: Session, per_senator: dict[str, list[dict]]) -> None:
    """_note_later_filing, which is secondary: a note that can't be written
    is logged, and neither fails a phase that stored its reports nor
    replaces the failure of one that didn't."""
    try:
        _note_later_filing(db, per_senator)
    except Exception:
        logger.exception("Senate holdings: later-filing notes not updated")
        db.rollback()
        _alert(
            "Senate holdings notes not updated",
            "Tonight's Senate holdings phase could not update the notes that name an undated filing "
            "made on or after a senator's shown report, so some may be stale or missing — see the server "
            "logs for the cause. (Any reports it stored before then are kept.)",
            dedupe_key=f"senate-holdings-notes-{utcnow():%Y-%m-%d}",
        )


def _filed_not_before(filed: str | None, reference: str | None) -> bool:
    """Whether a filing date is known to be on or after another — the one
    rule for which undated filings a note may name, keep and carry (a
    filing made the same day may be the later of the two). With either
    date missing, nothing can be said."""
    return bool(filed and reference and filed >= reference)


def _note_later_filing(db: Session, per_senator: dict[str, list[dict]]) -> None:
    """Name, beside each senator's stored report, the newest undated filing
    — no as-of date to rank by (see _senate_as_of): a paper filing, or a
    title that states no year — they made on or after its filing date. Only for senators this
    search returned filings for, and a note is only ever replaced by a
    later such filing or dropped once the stored report is itself filed
    after it — never cleared just because tonight's rows lack it, since a
    page of search results that failed to load looks exactly like that."""
    for disclosure in db.query(FinancialDisclosure).filter(FinancialDisclosure.senator_id.in_(list(per_senator))):
        later = [
            f for f in per_senator[disclosure.senator_id]
            if not _senate_as_of(f)
            and senate_filing_id(f["report_url"]) != disclosure.filing_id
            # On the same day counts: an undated amendment filed alongside
            # the dated report may be the later of the two.
            and _filed_not_before(f.get("filed_date"), disclosure.filed_date)
        ]
        # Filing date, then report URL: two filed the same day resolve the
        # same way every run, whatever order the search returned them in.
        newest = max(later, key=lambda f: (f["filed_date"], senate_filing_id(f["report_url"])), default=None)
        if newest is not None:
            noted = (disclosure.later_filing_filed or "", senate_filing_id(disclosure.later_filing_url or ""))
            if (newest["filed_date"], senate_filing_id(newest["report_url"])) >= noted:
                disclosure.later_filing_label = _senate_fields(newest)["report_label"]
                disclosure.later_filing_url = newest["report_url"]
                disclosure.later_filing_filed = newest["filed_date"]
        elif disclosure.later_filing_url and not _filed_not_before(disclosure.later_filing_filed, disclosure.filed_date):
            disclosure.later_filing_label = disclosure.later_filing_url = disclosure.later_filing_filed = None
    db.commit()


def _senate_fields(filing: dict) -> dict:
    """What gets stored for a Senate filing (_Chamber.fields), from one
    parse of its title. These title rules are part of what
    senate_fd.PARSER_VERSION versions: changing one is a bump, so stored
    rows are re-derived (_merge_known)."""
    title = filing.get("title") or ""
    as_of = _senate_as_of(filing)
    amended = is_amendment_title(title)
    return {
        "report_label": _senate_report_label(title, as_of, amended, filing.get("filed_date")),
        "filed_date": filing.get("filed_date") or None,
        "source_url": filing["report_url"],
        "as_of_date": as_of,
        "amended": amended,
        # "(Amendment 2)" after "(Amendment 1)" — a same-day tiebreak only
        # (see _rank); an unnumbered one has none.
        "seq": int(m.group(1)) if (m := _AMENDMENT_NO_RE.search(title)) else 0,
    }


def _senate_report_label(title: str, as_of: str | None, amended: bool, filed: str | None) -> str:
    """What the report is, in the words the page shows. A new-filer report
    is a snapshot at its date, not a year-end annual report; an undated
    (paper) filing is named by kind and filing date, never a guessed year."""
    new_filer = is_new_filer_title(title)
    if new_filer and as_of:
        return f"new-filer report as of {as_of}" + (" (amended)" if amended else "")
    if as_of:
        return f"{as_of[:4]} annual report" + (" (amended)" if amended else "")
    kind = "new-filer report" if new_filer else "annual report"
    kind += " amendment" if amended else ""
    return f"{kind} filed {filed}" if filed else kind


async def _senate_candidates(db: Session, client: httpx.AsyncClient) -> dict[str, list[dict]]:
    if await senate_accept_terms(client) is None:
        raise RuntimeError("Could not establish a Senate eFD session")
    # Filed since last Jan 1 — covering reports for the same two calendar
    # years the House phase reads (last year's, filed this year, and the
    # year before's, filed last year); earlier filings can't be anyone's
    # newest, and each extra page is a slow real-browser round trip.
    filings = await search_annual_filings(f"{utcnow().year - (_YEARS_BACK - 1)}-01-01")
    if not any(is_senator_filing(f) and is_annual_title(f.get("title") or "") for f in filings):
        # Every senator files one every year; none across two years means
        # the search broke (search_filings returns [] on any failure).
        raise RuntimeError("Senate eFD annual-report search returned no senators' reports")
    per_senator: dict[str, list[dict]] = {}
    match = FilerMatcher(current_senators(db), match_senator)
    for filing in filings:
        if not is_senator_filing(filing) or not is_annual_title(filing.get("title") or ""):
            continue
        if (senator_id := match(filing["last"], filing["first"], filing.get("office"))) is not None:
            per_senator.setdefault(senator_id, []).append(filing)
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
    """Store each senator's newest annual report (see _senate_as_of).
    Returns holdings stored."""
    per_senator = await _within(_senate_candidates(db, client), PREP_BUDGET, "Senate eFD terms and search")
    chamber = _Chamber(
        source="Senate eFD",
        owner_key="senator_id",
        parser_version=SENATE_PARSER_VERSION,
        filing_id=lambda f: senate_filing_id(f["report_url"]),
        fetch=lambda f, deadline: _fetch_senate(client, db, f, deadline),
        owner=lambda f, report: _MEMBERS,  # the search is filtered to senators' own filings
        date_key="filed_date",
        seq_before_date=False,
        fields=_senate_fields,
        still_loads=lambda url: _senate_probe(client, url),
    )
    try:
        inserted = await _ingest_members(db, chamber, per_senator)
    except Exception:
        # Reports replaced and committed before the failure still get their
        # note — after whatever the failure left half-done is rolled back,
        # so the notes' commit can't carry it.
        db.rollback()
        _write_notes(db, per_senator)
        raise
    except BaseException:
        db.rollback()  # cancellation or shutdown: unwind, committing nothing half-done
        raise
    _write_notes(db, per_senator)
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
    # Keyed by step, so the pairing can't drift with HOLDINGS_STEPS' order.
    phases = {
        "house_holdings": ("House holdings", ingest_house_holdings),
        "senate_holdings": ("Senate holdings", ingest_senate_holdings),
    }
    counts = {step: 0 for step, _, _ in HOLDINGS_STEPS}
    errors: list[str] = []
    for step, _, _ in HOLDINGS_STEPS:
        label, ingest = phases.get(step, (step, None))
        progress.begin(step)
        try:
            if ingest is None:
                raise RuntimeError(f"no ingest registered for holdings step {step!r}")
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


