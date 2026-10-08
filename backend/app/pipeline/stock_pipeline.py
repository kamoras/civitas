"""Ingest STOCK Act periodic transaction reports for senators, reps, and
the sitting president.

Runs as a sibling phase after the member pipelines (see scheduler.py's
_nightly_pipeline) rather than inside senate_pipeline.py/house_pipeline.py —
those functions are already large single units and this ingestion is
independent of member scoring. See issue #45 for the source-selection
rationale and the plan this was implemented from.

FETCH -> match filer to a known senator/rep by name (the president's own
filings need no matching — OGE indexes them under the office) -> resolve
ticker or name to the issuer's SEC industry code (sec_tickers) -> compute
disclosure timeliness -> upsert.

No profit/gain is computed anywhere in this module, for any filer: every
one of these forms reports an amount *bracket* with no cost basis or share
count, so there is nothing to compute one from. See models.py
PresidentTrade's docstring.
"""

import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.http_client import make_async_client
from app.pipeline.cache import api_cache_get, api_cache_set
from app.models import (
    PipelineRun, HousePipelineRun, PipelineStatus, President, PresidentTrade,
    StockTrade, RepStockTrade, StockTradesPipelineRun,
)
from app.pipeline.fetch.house_ptr import fetch_and_parse_ptr as fetch_house_ptr, fetch_ptr_filing_index
from app.pipeline.fetch.president_fd import fetch_annual_transactions
from app.pipeline.fetch.president_ptr import (
    fetch_and_parse_ptr as fetch_president_ptr,
    fetch_ptr_filing_index as fetch_president_ptr_index,
)
from app.pipeline.fetch.ptr_common import PARSER_VERSION as PTR_PARSER_VERSION
from app.pipeline.fetch.ptr_common import TICKER_RE, TradeRow
from app.pipeline.fetch.fd_common import SENATE_ASSET_TYPE_CATEGORY, house_category, strip_house_code
from app.pipeline.fetch.sec_tickers import SecUnavailable, issuer_industries
from app.holdings_schedule import HOLDINGS_STEPS, PTR_REREAD_BUDGET
from app.pipeline.holdings_pipeline import run_holdings_phases
from app.pipeline.filer_matching import FilerMatcher, current_representatives, current_senators
from app.pipeline.filer_matching import match_representative as _match_representative
from app.pipeline.filer_matching import match_senator as _match_senator
from app.pipeline.fetch.senate_fd import is_senator_filing
from app.pipeline.fetch.senate_ptr import (
    accept_terms as senate_accept_terms,
    fetch_and_parse_ptr as fetch_senate_ptr,
    report_version,
    search_ptr_filings,
    senate_filing_id,
)
from app.pipeline.progress_tracker import ProgressTracker
from app.pipeline.run_tracker import PipelineRunTracker, STALE_PIPELINE_TIMEOUT, MEMBER_PIPELINE_RUNNING, acquire_tracked_run, run_in_progress, skip_reason_text
from app.services.president_service import current_president
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

STOCK_PIPELINE_STEPS = [
    ("house_ptr",     "fetch", "Ingest House PTR filings"),
    ("senate_ptr",    "fetch", "Ingest Senate PTR filings"),
    ("president_ptr", "fetch", "Ingest presidential 278-T filings"),
    *HOLDINGS_STEPS,
]
# Every step that isn't a holdings step — derived, so a trade phase added to
# the list above can't be left out of the run-status rule.
_HOLDINGS_STEP_KEYS = {step for step, _, _ in HOLDINGS_STEPS}
TRADE_STEPS = tuple(step for step, _, _ in STOCK_PIPELINE_STEPS if step not in _HOLDINGS_STEP_KEYS)

# How far back to search on a cold start (no existing Senate trades in the
# DB). The House walks whole yearly filing indexes instead (_ingest_house).
COLD_START_LOOKBACK_DAYS = 120

# The Senate search re-covers this many days before the newest stored
# disclosure, every run. Starting exactly at the newest stored date meant a
# filing skipped on an earlier run — its filer not yet matched to a
# senator (a newly seated member), a detail page that failed to load, an
# empty parse — was never searched again once any later filing had been
# stored. Filing-id dedup makes re-covering the window cheap (already-
# ingested filings are skipped before any fetch; detail pages are cached
# 30 days). 90 days is twice the STOCK Act's 45-day disclosure deadline.
SENATE_REVISIT_DAYS = 90

# In-memory tracker mirroring house_pipeline.py's pattern — lets the admin
# dashboard detect a "stuck" run (DB row still says "running" but this
# tracker says not-running after a restart) rather than only the DB row,
# which a crashed/killed process can never update to "failed" itself.
_tracker = PipelineRunTracker()


def is_stock_pipeline_running() -> bool:
    return _tracker.is_running


def stock_pipeline_age() -> "timedelta | None":
    """Wall-clock age of the in-process stock-trades run, or None when idle."""
    return _tracker.age


def _other_pipeline_running(db: Session) -> bool:
    """Best-effort guard against overlapping with a member pipeline run.

    Reuses the existing PipelineRun/HousePipelineRun "running" status
    rather than introducing a third lock table — see scheduler.py's
    _hourly_action_refresh for the same pattern.

    Staleness-aware since 2026-07-23: an orphaned "running" row (left by
    a killed process — a deploy restarting the container mid-run) used
    to block Stock forever, with no auto-clear anywhere in this check.
    Confirmed live: this is what left stock-trades data stale for 4+
    days after a since-fixed deploy-race incident. Liveness is
    run_tracker.live_run's — the same test every other reader and the
    run locks apply — so this check and the thing that eventually cleans
    these rows up agree on what "stuck" means.
    """
    return any(run_in_progress(db, model) for model in (PipelineRun, HousePipelineRun))


def _compute_days_to_disclose(transaction_date: str | None, disclosure_date: str) -> int:
    try:
        t = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        d = datetime.strptime(disclosure_date, "%Y-%m-%d").date()
        return (d - t).days
    except (TypeError, ValueError):  # no date, or not one: shown as unknown (StockTradeSchema)
        return 0


def _issuer_name(asset_name: str) -> str:
    """A disclosed asset's text with the House's "[XX]" asset-type code and
    a "(TICKER)" taken out and its line breaks collapsed — the part that can
    be an issuer's name."""
    name, _code = strip_house_code(asset_name)
    return " ".join(TICKER_RE.sub(" ", name).split())


def _declared_crypto(asset_name: str, asset_type: str | None) -> bool:
    """Whether the filer declared the asset a cryptocurrency: the House's
    "[CT]" code in the asset text, or the Senate's Asset Type column."""
    _name, code = strip_house_code(asset_name)
    if code:
        return house_category(code) == "CRYPTO"
    return SENATE_ASSET_TYPE_CATEGORY.get(" ".join((asset_type or "").split()).lower()) == "CRYPTO"


async def _industries(
    db: Session, client: httpx.AsyncClient, assets: list[tuple[str | None, str, str | None]],
) -> list[str | None]:
    """The industry of each (ticker, asset_name, asset_type), from
    structured metadata only: a cryptocurrency the filer declared as one is
    CRYPTO; anything else takes the industry of the SIC code the SEC
    assigned its issuer, found by ticker, else by the asset's name matching
    an SEC-registered company's exactly (sec_tickers.issuer_industries).
    Everything else — a bond, a fund, a private company, a rental property,
    an SEC code with no category of ours — is None (UNCLASSIFIED).

    2026-09: this used to run the SEC company name (or, with no ticker,
    the asset text itself) through the donor-industry embedding classifier.
    Measured against the SEC's own SIC codes on the 1,299 production trade
    names that have one, it agreed 30% of the time, and 65% at the most
    confident margins: Broadcom, ConocoPhillips and PPG were LOBBYISTS,
    GitLab and CoStar PRIVATE_PRISON, and all 18 Senate CRYPTO rows were
    municipal bonds or ETF options. The prototypes describe donors, not
    securities, and no threshold separated right from wrong. It also wrote
    every trade name into the donor learning store, where its guesses
    became kNN reference examples for donor classification.

    This field is UI-display-only, read by no scoring code.
    """
    names = [_issuer_name(asset_name) for _t, asset_name, _at in assets]
    by_ticker, by_name = await issuer_industries(
        client, db, sorted({t for t, _a, _at in assets if t}), sorted({n for n in names if n}),
    )
    return [
        "CRYPTO" if _declared_crypto(asset_name, asset_type)
        else by_ticker[ticker] if ticker in by_ticker
        else by_name.get(name)
        for (ticker, asset_name, asset_type), name in zip(assets, names)
    ]


async def _classify_rows_industry(db: Session, client: httpx.AsyncClient, rows: list[TradeRow]) -> None:
    """Set each parsed row's `industry` (_industries). With the SEC down
    the rows are stored UNCLASSIFIED and the nightly pass labels them
    (_reclassify_stored_trades): a trade is not left unstored for it."""
    try:
        industries = await _industries(db, client, [(r.ticker, r.asset_name, r.asset_type) for r in rows])
    except SecUnavailable as e:
        logger.warning("Trade industries not set at ingest, SEC unavailable: %s", e)
        return
    for row, industry in zip(rows, industries):
        row.industry = industry


async def _reclassify_stored_trades(db: Session, client: httpx.AsyncClient) -> int:
    """Bring every stored trade's industry up to date with _industries,
    returning how many changed; with the SEC unavailable, none change
    (SecUnavailable propagates). A row otherwise keeps the industry it was
    stored with until its filing is read again, and that re-read is
    rationed (PTR_REREAD_BUDGET) and skips filings that don't read; this
    costs one cached SEC lookup per issuer."""
    changed = 0
    for model in (StockTrade, RepStockTrade, PresidentTrade):
        rows = db.query(model).all()
        industries = await _industries(
            db, client, [(r.ticker, r.asset_name, getattr(r, "asset_type", None)) for r in rows],
        )
        for row, industry in zip(rows, industries):
            if row.industry != (industry or "UNCLASSIFIED"):
                row.industry = industry or "UNCLASSIFIED"
                changed += 1
        db.commit()
    return changed


def collapse_refiled_trades(db: Session, model, owner_key: str) -> int:
    """Keep each transaction once when several of a filer's filings list
    it: the same owner, asset, date, type and amount bracket in two reports
    is one trade disclosed twice, an amended or re-submitted report, not
    two trades. The rows of the earliest-filed report are kept (its date is
    when the trade was first disclosed); the repeats are deleted. Returns
    the rows deleted.

    Measured 2026-10-08: 31 Senate groups (two reports filed the same day
    with the same rows, and one report filed twice) and 2 House groups.
    Repeats within one report are left: those can be separate same-day lots.
    An amendment that corrects a trade's details doesn't match the original
    and both stay; the forms don't link an amendment to what it amends.
    """
    owner = getattr(model, owner_key)
    rows = db.query(
        model.id, owner, model.asset_name, model.transaction_date, model.transaction_type,
        model.amount_low, model.owner, model.filing_id, model.disclosure_date,
    ).filter(model.transaction_date.isnot(None)).all()
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault(tuple(r[1:7]), []).append(r)
    doomed: list[int] = []
    for group in groups.values():
        if len({r.filing_id for r in group}) < 2:
            continue
        first = min(group, key=lambda r: (r.disclosure_date or "9999", r.id)).filing_id
        doomed += [r.id for r in group if r.filing_id != first]
    if doomed:
        db.query(model).filter(model.id.in_(doomed)).delete(synchronize_session=False)
        db.commit()
    return len(doomed)


def _trade(model, *, row: TradeRow, **owner):
    """A stored trade row of `model` (StockTrade, RepStockTrade or
    PresidentTrade) for one parsed transaction; `owner` names the filer
    (senator_id=, representative_id= or president_id=). The one place a
    parsed row becomes a stored one, for every chamber and for re-reads."""
    return model(
        **owner,
        ticker=row.ticker,
        asset_name=row.asset_name,
        owner=row.owner,
        transaction_type=row.transaction_type,
        transaction_date=row.transaction_date,
        disclosure_date=row.disclosure_date,
        days_to_disclose=_compute_days_to_disclose(row.transaction_date, row.disclosure_date),
        amount_low=row.amount_low,
        amount_high=row.amount_high,
        industry=row.industry or "UNCLASSIFIED",
        source_url=row.source_url,
        filing_id=row.filing_id,
        parse_confidence=row.parse_confidence,
        parser_version=PTR_PARSER_VERSION,
        **({"report_kind": row.report_kind} if model is PresidentTrade else {}),
        **({"asset_type": row.asset_type} if model is StockTrade else {}),
    )


async def _ingest_house(db: Session, client: httpx.AsyncClient) -> int:
    existing_rep_filing_ids = {row[0] for row in db.query(RepStockTrade.filing_id).all()}

    current_year = utcnow().year
    inserted = 0
    match = FilerMatcher(current_representatives(db), _match_representative)
    for year in (current_year - 1, current_year):
        filings = await fetch_ptr_filing_index(client, db, year)
        for filing in filings:
            if filing["doc_id"] in existing_rep_filing_ids:
                continue
            rep_id = match(filing["last"], filing["first"], filing["state_district"])
            if rep_id is None:
                continue
            rows = await fetch_house_ptr(client, db, filing)
            if not rows:
                continue
            await _classify_rows_industry(db, client, rows)
            for row in rows:
                db.add(_trade(RepStockTrade, representative_id=rep_id, row=row))
                inserted += 1
            existing_rep_filing_ids.add(filing["doc_id"])
    db.commit()
    return inserted


def _senate_search_since(db: Session) -> str:
    """Start date (YYYY-MM-DD) for this run's Senate PTR search — see
    SENATE_REVISIT_DAYS."""
    latest = db.query(StockTrade.disclosure_date).order_by(StockTrade.disclosure_date.desc()).first()
    if latest and latest[0]:
        try:
            newest = datetime.strptime(latest[0][:10], "%Y-%m-%d").date()
            return (newest - timedelta(days=SENATE_REVISIT_DAYS)).strftime("%Y-%m-%d")
        except ValueError:
            logger.warning("Unparseable stored disclosure_date %r — using the cold-start window", latest[0])
    return (utcnow().date() - timedelta(days=COLD_START_LOOKBACK_DAYS)).strftime("%Y-%m-%d")


_SUPERSEDED_KEY = "ptr-superseded-{}"
_SETTLED_KEY = "ptr-settled-{}"
_FOREVER_H = 24 * 365 * 10


def _marked(db: Session, key: str) -> bool:
    return api_cache_get(db, "senate_ptr", key, max_age_hours=_FOREVER_H) is not None


def _family(filing: dict) -> tuple | None:
    """A filing's report: (filer, the report's date), shared by the
    original and every amendment of it; None when the title names none."""
    date, _n = report_version(filing.get("title", ""))
    return (filing["last"].lower(), filing["first"].lower(), date) if date else None


async def _with_missing_originals(db: Session, filings: list[dict]) -> list[dict]:
    """`filings` plus the original of any amended report whose original is
    older than the search: its rows say when each trade was first
    disclosed. One wider search, only when an amendment's original is
    missing."""
    have = {(_family(f), report_version(f.get("title", ""))[1]) for f in filings}
    wanted = {
        fam for f in filings
        if (fam := _family(f)) and (fam, 0) not in have
        and not _marked(db, _SETTLED_KEY.format(senate_filing_id(f["report_url"])))
    }
    if not wanted:
        return filings
    older = await search_ptr_filings(min(fam[2] for fam in wanted))
    return filings + [f for f in older if _family(f) in wanted and report_version(f.get("title", ""))[1] == 0]


def settle_amended_reports(db: Session, filings: list[dict]) -> int:
    """Keep one version of each amended report: the newest, whose rows the
    filer last swore to. Each of its trades takes the date it was first
    disclosed, the earliest filing of the report that lists the same
    owner, asset, date and type, so a correction (an amount, say) keeps the
    original's date and a trade first added in an amendment keeps the
    amendment's. The older versions' rows are deleted and their ids
    remembered, so they are not ingested again. Returns rows deleted.

    Measured 2026-10-08: nine of one senator's reports filed one day were
    amendments of reports filed from 2024 on, stored as if first disclosed
    that day (up to 867 days late)."""
    families: dict[tuple, list[tuple[int, str, str]]] = {}
    for f in filings:
        fam = _family(f)
        if fam:
            families.setdefault(fam, []).append(
                (report_version(f["title"])[1], senate_filing_id(f["report_url"]), f.get("filed_date") or fam[2]))
    deleted = 0
    for versions in families.values():
        stored = [v for v in sorted(versions) if db.query(StockTrade.id).filter_by(filing_id=v[1]).first()]
        if len(stored) < 2:
            continue
        current = stored[-1][1]
        first_seen: dict[tuple, str] = {}
        for _n, fid, filed in stored:
            for t in db.query(StockTrade).filter_by(filing_id=fid):
                key = (t.owner, t.asset_name, t.transaction_date, t.transaction_type)
                first_seen[key] = min(first_seen.get(key, filed), filed)
        for t in db.query(StockTrade).filter_by(filing_id=current):
            first = first_seen[(t.owner, t.asset_name, t.transaction_date, t.transaction_type)]
            t.disclosure_date = first
            t.days_to_disclose = _compute_days_to_disclose(t.transaction_date, first)
        for _n, fid, _filed in stored[:-1]:
            deleted += db.query(StockTrade).filter_by(filing_id=fid).delete(synchronize_session=False)
            api_cache_set(db, "senate_ptr", _SUPERSEDED_KEY.format(fid), {"by": current},
                          normal_ttl_hours=_FOREVER_H)
        for _n, fid, _filed in versions:
            api_cache_set(db, "senate_ptr", _SETTLED_KEY.format(fid), {"current": current},
                          normal_ttl_hours=_FOREVER_H)
    db.commit()
    return deleted


async def _ingest_senate(db: Session, client: httpx.AsyncClient) -> int:
    existing_filing_ids = {row[0] for row in db.query(StockTrade.filing_id).all()}

    since_date = _senate_search_since(db)

    # Only fetch_senate_ptr (per-filing detail pages) needs this httpx
    # session — search_ptr_filings runs its own real browser session
    # (see its module docstring: the search endpoint itself is behind
    # Akamai bot-management no plain HTTP client gets past).
    csrf_token = await senate_accept_terms(client)
    if csrf_token is None:
        logger.error("Could not establish a Senate eFD session — skipping Senate PTR ingestion this run")
        return 0

    filings = await _with_missing_originals(db, await search_ptr_filings(since_date))
    inserted = 0
    match = FilerMatcher(current_senators(db), _match_senator)
    for filing in filings:
        filing_id = senate_filing_id(filing["report_url"])
        if filing_id in existing_filing_ids or _marked(db, _SUPERSEDED_KEY.format(filing_id)):
            continue
        if filing.get("office") and not is_senator_filing(filing):
            # A former senator's (or anyone else's) filing: never attributed
            # to a sitting senator who happens to share the surname.
            continue
        senator_id = match(filing["last"], filing["first"], filing.get("office"))
        if senator_id is None:
            continue
        rows = await fetch_senate_ptr(client, db, filing)
        if not rows:
            continue
        await _classify_rows_industry(db, client, rows)
        for row in rows:
            db.add(_trade(StockTrade, senator_id=senator_id, row=row))
            inserted += 1
        existing_filing_ids.add(filing_id)
    db.commit()
    settle_amended_reports(db, filings)
    return inserted


@dataclass(frozen=True)
class _StoredSource:
    """One trade table whose stored filings can be read again: `fetch`
    takes a filing's stored id, source URL and the filed date its stored
    rows carry, if any."""
    label: str
    model: type
    owner_key: str
    fetch: Callable[[str, str, str | None], Awaitable[list[TradeRow] | None]]


# A filing that didn't read is not tried again for this long, so a few dead
# links can't spend every night's budget ahead of the filings that do read;
# one that never reads again costs a request a month.
_REREAD_RETRY_HOURS = 24 * 30
_REREAD_TIER = "ptr_reread"
# This many failures in a row with nothing read looks like the source being
# down: its re-read stops for the night, and they sit out the next one.
_REREAD_OUTAGE_AFTER = 5
# Past the next night's run and short of the one after, whatever time each
# starts: the nightly chain's stages run for hours, so the re-read's start
# drifts by that much from night to night.
_SIT_OUT_A_NIGHT = timedelta(hours=36)


async def _reread_trades(db: Session, client: httpx.AsyncClient) -> int:
    """Read stored filings again when an older ptr_common.PARSER_VERSION
    read them, replacing their rows — the Senate's first (its owners were
    misread), newest first within each, until PTR_REREAD_BUDGET is spent.
    The budget is shared: each source gets an equal part of what is left
    when it starts, and whatever it doesn't use passes on — otherwise one
    source's backlog (a scan's OCR can take minutes) keeps the next source's
    filings waiting for weeks.
    Every stored row names its filing's URL, so this needs no search or
    index, whose windows reach back only weeks. A filing that doesn't read
    keeps its rows and waits _REREAD_RETRY_HOURS — or sits out a night, when
    its source read nothing that night, which may be the source being down,
    not the filing. Returns filings re-read."""
    sources = [
        _StoredSource("Senate", StockTrade, "senator_id", lambda _fid, url, filed: fetch_senate_ptr(
            client, db, {"report_url": url, "is_paper": "/view/paper/" in url, "stored_filed_date": filed},
        )),
        _StoredSource("House", RepStockTrade, "representative_id", lambda fid, url, _filed: _reread_house_filing(
            client, db, fid, url,
        )),
        _StoredSource("President", PresidentTrade, "president_id", lambda fid, url, _filed: _read_president_filing(
            db, {"doc_id": fid, "pdf_url": url},
        )),
    ]
    deadline = time.monotonic() + PTR_REREAD_BUDGET.total_seconds()
    reread = 0
    for index, source in enumerate(sources):
        model = source.model
        share = (deadline - time.monotonic()) / (len(sources) - index)
        source_deadline = time.monotonic() + share
        query = db.query(
            model.filing_id,
            func.min(model.source_url),
            # A filed date the rows really carry: an electronic row stored
            # before the filed-date fix has its transaction date there. A
            # same-day filing's rows are left out too, which loses nothing:
            # the parser's fallback for its disclosure date is that same day.
            func.max(case((model.disclosure_date != model.transaction_date, model.disclosure_date))),
        ).filter(model.parser_version < PTR_PARSER_VERSION)
        stale = query.group_by(model.filing_id).order_by(func.max(model.disclosure_date).desc()).all()
        if stale and source.label == "Senate" and await senate_accept_terms(client) is None:
            logger.warning("Senate PTR re-read skipped: no eFD session")
            continue
        read, failed = 0, []
        for position, (filing_id, url, filed) in enumerate(stale):
            if time.monotonic() >= source_deadline:
                logger.info("PTR re-read: time budget spent — %d %s filings wait", len(stale) - position, source.label)
                break
            failed_key = f"failed-{source.label}-{filing_id}"
            marker = api_cache_get(db, _REREAD_TIER, failed_key, max_age_hours=_REREAD_RETRY_HOURS)
            # A marker that names no time is one written before markers did,
            # which waits out its month (the max_age above).
            if marker is not None and marker.get("retry_after", "9999") > utcnow().isoformat():
                continue
            try:
                rows = await source.fetch(filing_id, url, filed)
                if rows:
                    await _classify_rows_industry(db, client, rows)
            except Exception:
                logger.exception("PTR re-read of %s filing %s failed", source.label, filing_id)
                db.rollback()
                rows = None
            stored = db.query(model).filter(model.filing_id == filing_id)
            # A scan the current parser reads nothing from, fetched fine:
            # what an older parser read from it goes. Before version 3 a
            # scan's line reader took any two numbers for the amount and
            # the first date for the transaction's — a bond's maturity
            # (one stored trade was dated 2033) — and its whole line for
            # the asset. A text filing that reads nothing is kept: that is
            # the parser failing, not the filing.
            if rows == [] and stored.filter(model.parse_confidence != "ocr").first() is None:
                logger.info("PTR re-read: %s scan %s reads no rows now — dropping its older reading",
                            source.label, filing_id)
                stored.delete(synchronize_session="fetch")
                db.commit()
                read += 1
                continue
            if not rows:
                failed.append((failed_key, url))
                if not read and len(failed) >= _REREAD_OUTAGE_AFTER:
                    break
                continue
            filer = getattr(stored.first(), source.owner_key)
            stored.delete(synchronize_session="fetch")
            for row in rows:
                db.add(_trade(model, row=row, **{source.owner_key: filer}))
            db.commit()
            read += 1
        reread += read
        # A failure beside filings that read is the filing's: it waits a
        # month. On a night nothing read it may be the source's, down: the
        # filings sit out the next night — whenever the chain reaches the
        # re-read, dead links at the head of the order let it reach the
        # filings behind them — and are tried the night after.
        wait = timedelta(hours=_REREAD_RETRY_HOURS) if read else _SIT_OUT_A_NIGHT
        if failed and not read:
            logger.warning("PTR re-read: %s read nothing (%d failed) — they sit out a night, not a month",
                           source.label, len(failed))
        for failed_key, url in failed:
            api_cache_set(
                db, _REREAD_TIER, failed_key, {"url": url, "retry_after": (utcnow() + wait).isoformat()},
                normal_ttl_hours=_REREAD_RETRY_HOURS,
            )
    return reread


async def _reread_house_filing(
    client: httpx.AsyncClient, db: Session, doc_id: str, url: str,
) -> list[TradeRow] | None:
    """A stored House filing read again, with its filing date from the
    Clerk's yearly index (cached; the year is in the PDF's path). The stored
    rows can't supply it: a scan read before PARSER_VERSION 5 stored each
    row's transaction date as its disclosure date. Without it a scan's dates
    have no upper bound, and a row whose date isn't legible has no date at
    all to show."""
    match = re.search(r"/ptr-pdfs/(\d{4})/", url)
    filed = None
    if match:
        index = await fetch_ptr_filing_index(client, db, int(match.group(1)))
        filed = next((f["filing_date"] for f in index if f["doc_id"] == doc_id), None)
    return await fetch_house_ptr(client, db, {"doc_id": doc_id, "pdf_url": url, "filing_date": filed})


def _annual_covered_through(db: Session, president_id: str) -> str | None:
    """The last day of the latest year the president's stored annual report
    covers (YYYY-12-31), or None when none is stored."""
    latest = (
        db.query(func.max(PresidentTrade.transaction_date))
        .filter(PresidentTrade.president_id == president_id, PresidentTrade.report_kind == "annual")
        .scalar()
    )
    return f"{latest[:4]}-12-31" if latest else None


async def _read_president_filing(db: Session, filing: dict) -> list[TradeRow] | None:
    """A 278-T's rows (None when it couldn't be fetched), read inside its window (the term's start to the
    filing's date, from OGE's index when the caller holds only its URL, as
    a re-read does) and without the transactions of a year an annual report
    covers: that report is their record (president_fd)."""
    president = current_president(db)
    if president is None:
        return []
    if not filing.get("filing_date"):
        dates = {f["doc_id"]: f["filing_date"] for f in await fetch_president_ptr_index(db, president.name)}
        filing = {**filing, "filing_date": dates.get(filing["doc_id"])}
    rows = await fetch_president_ptr(db, {**filing, "not_before": president.term_start})
    if rows is None:
        return None
    covered = _annual_covered_through(db, president.id)
    # An undated row is the annual report's when its whole filing is: filed
    # by the year's end the report covers.
    return [r for r in rows if not covered or (r.transaction_date or r.disclosure_date) > covered]


async def _ingest_president_annual(db: Session, client: httpx.AsyncClient, president: President, filings: list[dict]) -> int:
    """Store the newest annual report's transactions when not stored yet,
    replacing that year's rows: an amended report's earlier reading, and
    the periodic filings' OCR'd rows, which the report's text supersedes."""
    annual = sorted((f for f in filings if f["kind"] == "annual"), key=lambda f: f["filing_date"] or "")
    if not annual:
        return 0
    newest = annual[-1]
    stored = db.query(PresidentTrade.id).filter(PresidentTrade.filing_id == newest["doc_id"]).first()
    if stored is not None:
        return 0
    result = await fetch_annual_transactions(db, newest)
    if result is None:
        return 0
    year, rows = result
    await _classify_rows_industry(db, client, rows)
    db.query(PresidentTrade).filter(
        PresidentTrade.president_id == president.id,
        PresidentTrade.transaction_date <= f"{year}-12-31",
        (PresidentTrade.report_kind == "periodic") | (PresidentTrade.transaction_date >= f"{year}-01-01"),
    ).delete(synchronize_session=False)
    # An undated periodic row filed by the year's end is one of the year's
    # (_read_president_filing's rule); one filed later may be of the next.
    db.query(PresidentTrade).filter(
        PresidentTrade.president_id == president.id,
        PresidentTrade.transaction_date.is_(None),
        PresidentTrade.report_kind == "periodic",
        PresidentTrade.disclosure_date <= f"{year}-12-31",
    ).delete(synchronize_session=False)
    for row in rows:
        db.add(_trade(PresidentTrade, president_id=president.id, row=row))
    db.commit()
    logger.info("Presidential annual report for %d: %d transactions stored", year, len(rows))
    return len(rows)


async def _ingest_president(db: Session, client: httpx.AsyncClient) -> int:
    """Ingest the sitting president's transactions: the newest annual
    report's Part 7 for the year it covers (president_fd), then the OGE
    278-T periodic reports for the months since.

    Current president only, and deliberately so: 278-T filings exist only
    from the STOCK Act's 2012 effective date onward, and a former
    president's filings stop at the end of their term, so there is nothing
    to keep refreshing for anyone else. Historical presidents get no
    disclosure section rather than an empty one implying they traded
    nothing.

    Unlike the House/Senate phases there is no filer-matching step: OGE
    indexes these filings under the office, and president_ptr.py already
    requires the row to name this president before returning it.
    """
    president = current_president(db)
    if president is None:
        logger.info("No current president row — skipping presidential PTR ingestion")
        return 0

    filings = await fetch_president_ptr_index(db, president.name)
    inserted = await _ingest_president_annual(db, client, president, filings)
    existing_filing_ids = {row[0] for row in db.query(PresidentTrade.filing_id).all()}
    for filing in filings:
        if filing["kind"] != "periodic" or filing["doc_id"] in existing_filing_ids:
            continue
        rows = await _read_president_filing(db, filing)
        if not rows:
            continue
        await _classify_rows_industry(db, client, rows)
        for row in rows:
            db.add(_trade(PresidentTrade, president_id=president.id, row=row))
            inserted += 1
        existing_filing_ids.add(filing["doc_id"])
    db.commit()
    return inserted


async def run_stock_trades_pipeline() -> dict:
    """Fetch, parse, classify, and store new House + Senate + presidential
    PTR filings.

    Best-effort per phase: a failure fetching/parsing one filer group's
    filings does not prevent the others from being ingested.
    """
    db: Session = SessionLocal()
    _run_token = None  # no run of ours for the finally to stop until start() below
    try:
        if _other_pipeline_running(db):
            logger.info("Stock trades pipeline skipped — a member pipeline is currently running")
            return {"status": "skipped", "reason": MEMBER_PIPELINE_RUNNING}

        # Same reasoning as senate_pipeline.py's own lock: until 2026-07-23
        # this was an unconditional insert with no lock at all, so a row
        # orphaned by a killed process stayed "running" forever, blocking
        # every future Stock run via _other_pipeline_running's check above
        # (which any OTHER pipeline's own stuck row would also trip) and
        # this one (a stuck STOCK row blocking Stock's own next attempt).
        run, _run_token, refused = acquire_tracked_run(db, StockTradesPipelineRun, STALE_PIPELINE_TIMEOUT, _tracker)
        if run is None:
            logger.warning("Stock trades pipeline not started: %s", skip_reason_text(refused))
            return {"status": "skipped", "reason": refused}

        start_time = time.time()
        progress = ProgressTracker(run, STOCK_PIPELINE_STEPS, db, start_time)

        house_count = 0
        senate_count = 0
        president_count = 0
        error_parts: list[str] = []
        failed_steps: set[str] = set()
        async with make_async_client() as client:
            progress.begin("house_ptr")
            try:
                house_count = await _ingest_house(db, client)
                progress.complete("house_ptr", detail=f"{house_count} rows")
            except Exception:
                logger.exception("House PTR ingestion failed")
                # Roll back the failed chamber's partial transaction so the
                # session is clean for the Senate phase — without this, a
                # House flush error leaves the session in a failed state and
                # the Senate phase's first query raises PendingRollbackError,
                # so the "best-effort per chamber" design failed BOTH.
                db.rollback()
                error_parts.append("House: failed — see server logs")
                progress.fail("house_ptr")
                failed_steps.add("house_ptr")
            progress.begin("senate_ptr")
            try:
                senate_count = await _ingest_senate(db, client)
                progress.complete("senate_ptr", detail=f"{senate_count} rows")
            except Exception:
                logger.exception("Senate PTR ingestion failed")
                db.rollback()
                error_parts.append("Senate: failed — see server logs")
                progress.fail("senate_ptr")
                failed_steps.add("senate_ptr")
            progress.begin("president_ptr")
            try:
                president_count = await _ingest_president(db, client)
                progress.complete("president_ptr", detail=f"{president_count} rows")
            except Exception:
                logger.exception("Presidential PTR ingestion failed")
                db.rollback()
                error_parts.append("President: failed — see server logs")
                progress.fail("president_ptr")
                failed_steps.add("president_ptr")
            # Stored filings an older parser read. Not a trade phase of its
            # own: a failure here is logged and reported, and leaves the
            # night's new trades, already committed, as they are.
            try:
                reread = await _reread_trades(db, client)
                if reread:
                    logger.info("PTR re-read: %d stored filings read again", reread)
            except Exception:
                logger.exception("PTR re-read failed")
                db.rollback()
                error_parts.append("Re-read of stored filings: failed — see server logs")
            # A trade two reports list counts once, at its first disclosure.
            # After the re-read, which rewrites a filing's rows in full.
            try:
                for model, key in ((StockTrade, "senator_id"), (RepStockTrade, "representative_id")):
                    collapsed = collapse_refiled_trades(db, model, key)
                    if collapsed:
                        logger.info("%s: %d trades listed again in a later report removed", model.__tablename__, collapsed)
            except Exception:
                logger.exception("Collapsing re-filed trades failed")
                db.rollback()
                error_parts.append("Collapsing re-filed trades: failed — see server logs")
            # Every stored trade's industry, whatever parser read it: not a
            # trade phase either.
            try:
                reclassified = await _reclassify_stored_trades(db, client)
                if reclassified:
                    logger.info("Trade industries: %d stored trades reclassified", reclassified)
            except Exception:
                logger.exception("Trade industry reclassification failed")
                db.rollback()
                error_parts.append("Industry reclassification of stored trades: failed — see server logs")
            # Annual-report holdings: same sources, same best-effort
            # isolation (a failure leaves the trade rows above committed and
            # the stored holdings untouched). The phases live in
            # holdings_pipeline.py, which reports its own failures.
            holdings_counts, holdings_errors = await run_holdings_phases(db, client, progress)
            error_parts.extend(holdings_errors)

        elapsed = round(time.time() - start_time, 1)
        logger.info(
            "Stock trades pipeline: %d House rows, %d Senate rows, %d presidential rows; "
            "%d House / %d Senate / %d presidential holdings",
            house_count, senate_count, president_count,
            holdings_counts["house_holdings"], holdings_counts["senate_holdings"],
            holdings_counts["president_holdings"],
        )

        # FAILED only when every trade phase failed — one source being down
        # still leaves the run's other rows valid. The holdings phases don't
        # count toward it either way: two healthy holdings phases must not
        # mark a run COMPLETED whose trade ingest is entirely dead (the
        # staleness alert would never fire), and a holdings-only outage must
        # not report stock trades as stale — run_holdings_phases raises its
        # own ops alert for that.
        trade_failures = sum(step in failed_steps for step in TRADE_STEPS)
        run.status = PipelineStatus.FAILED if trade_failures == len(TRADE_STEPS) else PipelineStatus.COMPLETED
        run.completed_at = utcnow()
        run.house_trades_ingested = house_count
        run.senate_trades_ingested = senate_count
        run.president_trades_ingested = president_count
        run.elapsed_seconds = elapsed
        run.error_message = "; ".join(error_parts) or None
        db.commit()

        return {
            "status": run.status, "house_trades": house_count, "senate_trades": senate_count,
            "president_trades": president_count,
            "house_holdings": holdings_counts["house_holdings"],
            "senate_holdings": holdings_counts["senate_holdings"],
            "president_holdings": holdings_counts["president_holdings"],
            "elapsed_seconds": elapsed,
        }
    finally:
        _tracker.stop(_run_token)
        db.close()
