"""Feeds for one thing a reader follows: a member of Congress, a bill, a
federal agency, a national monitor (api/feed.py serves them as Atom).

Every entry is a fixed template around stored rows, read when the feed is
asked for (nginx caches it): no model, no fetch, nothing about the reader.
Each links the Civitas page and, where there is one, the official source.
An entry's id is a tag URI on what the entry is (a roll call and the
member's bioguide id, a bill action's date and text, a Register document
number), never on a URL or a member's slug, so a renamed member or a moved
page never makes a reader's feed show an entry twice.

What each feed holds:
- a member: each roll-call vote this Congress, the latest action on each
  bill the member sponsored, each change in the representation score
  between runs, and each new trade report or annual disclosure;
- a bill: each action Congress.gov lists (as last read by a pipeline run
  or a reader's visit) and each stored roll call on it;
- an agency (with its sub-agencies): each proposed and final rule, each
  comment period opening, and each one's last day;
- a monitor: each sourced update, under the outlet's own headline.
"""
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.broadcast import SITE_URL
from app.config import settings
from app.models import (
    Agency,
    ApiCache,
    ExploreDocument,
    FinancialDisclosure,
    MonitorUpdate,
    NationalMonitor,
    Representative,
    RepSponsoredBill,
    RepStockTrade,
    RollCall,
    RollCallPosition,
    ScoreSnapshot,
    Senator,
    SponsoredBill,
    StockTrade,
)
from app.pipeline.fetch.congress import congress_gov_url_for_bill_id
from app.pipeline.fetch.fr_rulemaking import FR_TYPE_LABELS, RULE_DOC_TYPES
from app.schemas import is_open_ended
from app.services import bill_journey
from app.services.bill_record import parse_bill_id
from app.services.congress_service import bill_label
from app.services.explore_keys import public_key
from app.services.member_positions import roll_call_member_ids, vote_word
from app.time_utils import comment_period_today

_TAG = "tag:civitas-research.org,2026:"

# Entries per feed (api/feed.FEED_LENGTH is the same number); each source a
# feed merges reads at most this many, newest first.
LENGTH = 50

# An agency feed's window: rules and comment periods published in the last
# AGENCY_DAYS, and last days to comment within it.
AGENCY_DAYS = 120

_CHAMBER_WORD = {"senate": "Senate", "house": "House"}
# The trade forms' transaction types (StockTrade.transaction_type), in words.
_TRADE_WORD = {"purchase": "Purchase", "sale_full": "Sale (full)", "sale_partial": "Sale (partial)",
               "exchange": "Exchange"}


@dataclass(frozen=True)
class Entry:
    id: str
    title: str
    url: str  # the Civitas page, a site path
    when: datetime
    category: str  # term
    label: str  # the category's words
    paragraphs: tuple[str, ...]
    source_url: str | None = None


@dataclass(frozen=True)
class EntityFeed:
    title: str
    subtitle: str
    page: str  # the Civitas page the feed follows, a site path
    entries: list[Entry]


def _day(iso: str | None) -> datetime | None:
    """A stored YYYY-MM-DD as midnight UTC (its entries carry no time)."""
    try:
        return datetime.combine(date.fromisoformat((iso or "")[:10]), datetime.min.time())
    except ValueError:
        return None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _newest(entries: list[Entry]) -> list[Entry]:
    return sorted(entries, key=lambda e: (e.when, e.id), reverse=True)[:LENGTH]


def bill_path(bill_id: str, congress: int) -> str:
    """The bill's page: `?congress=` only outside the sitting Congress, as
    the frontend's billCanonicalPath writes it."""
    path = f"/congress/bills/{bill_id}"
    return path if congress == settings.CURRENT_CONGRESS else f"{path}?congress={congress}"


def _vote_path(rc: RollCall) -> str:
    return f"/congress/votes/{rc.chamber}/{rc.congress}/{rc.session}/{rc.number}"


def _roll_call_ref(rc: RollCall) -> str:
    return f"{_CHAMBER_WORD.get(rc.chamber, rc.chamber)} roll call {rc.number}"


def _tally(rc: RollCall) -> str:
    return f"Result: {rc.result or 'not stated'}, {rc.yeas} to {rc.nays}."


# ── Member ────────────────────────────────────────────────────────

def _member(db: Session, member_id: str) -> tuple[str, Senator | Representative] | None:
    for chamber, model in (("senate", Senator), ("house", Representative)):
        row = db.get(model, member_id)
        if row is not None:
            return chamber, row
    return None


def _member_votes(db: Session, chamber: str, member, bio: str) -> list[Entry]:
    congress = settings.CURRENT_CONGRESS
    vote_id = roll_call_member_ids(db, chamber, congress, {"m": member.id}).get(member.id)
    if not vote_id:
        return []
    rows = (db.query(RollCall, RollCallPosition.position)
            .join(RollCallPosition, RollCallPosition.roll_call_id == RollCall.id)
            .filter(RollCall.chamber == chamber, RollCall.congress == congress,
                    RollCallPosition.member_id == vote_id)
            .order_by(RollCall.date.desc(), RollCall.session.desc(), RollCall.number.desc())
            .limit(LENGTH).all())
    out = []
    for rc, position in rows:
        when = _day(rc.date)
        if when is None:
            continue
        measure = bill_label(rc.bill_id)
        out.append(Entry(
            id=f"{_TAG}vote/{rc.chamber}-{rc.congress}-{rc.session}-{rc.number}/{bio}",
            title=f"{member.name} voted {vote_word(position)} on {_roll_call_ref(rc)}: {rc.question or 'a recorded vote'}",
            url=_vote_path(rc), when=when, category="vote", label="Roll-call vote",
            paragraphs=tuple(p for p in (
                f"{measure}: {rc.title}" if measure and rc.title else rc.title,
                _tally(rc),
                f"{member.name}'s vote, as the {_CHAMBER_WORD.get(rc.chamber, rc.chamber)} recorded it: "
                f"{vote_word(position)}.",
            ) if p),
            source_url=rc.source_url or None,
        ))
    return out


def _member_bills(db: Session, chamber: str, member) -> list[Entry]:
    model, owner = ((SponsoredBill, SponsoredBill.senator_id) if chamber == "senate"
                    else (RepSponsoredBill, RepSponsoredBill.representative_id))
    rows = (db.query(model).filter(owner == member.id, model.latest_action_date != "")
            .order_by(model.latest_action_date.desc()).limit(LENGTH).all())
    out = []
    for b in rows:
        when, text = _day(b.latest_action_date), " ".join((b.latest_action or "").split())
        if when is None or not text:
            continue
        label = bill_label(b.bill_id) or b.bill_id
        out.append(Entry(
            # The bill feed's id for the same action, so a reader following
            # both sees it once.
            id=_action_id(b.congress, b.bill_id, b.latest_action_date, text),
            title=f"{label}, sponsored by {member.name}: {text}",
            url=bill_path(b.bill_id, b.congress), when=when, category="bill-action", label="Bill action",
            paragraphs=(f"{label}: {b.title}", f"Latest action, {b.latest_action_date}: {text}"),
            source_url=congress_gov_url_for_bill_id(b.bill_id, b.congress),
        ))
    return out


def _score_changes(db: Session, chamber: str, member, bio: str, page: str) -> list[Entry]:
    entity = "senator" if chamber == "senate" else "representative"
    rows = (db.query(ScoreSnapshot).filter(ScoreSnapshot.entity_type == entity, ScoreSnapshot.entity_id == member.id)
            .order_by(ScoreSnapshot.date.asc(), ScoreSnapshot.id.asc()).all())
    # A day's last snapshot stands for the day: one entry per day at most,
    # so the entry id (on the date) is never shared.
    snaps = list({s.date: s for s in rows}.values())
    out = []
    for before, after in zip(snaps, snaps[1:]):
        old, new = round(before.overall_score), round(after.overall_score)
        when = _day(after.date)
        if old == new or when is None:
            continue
        method = (f" The scoring method changed from {before.algorithm_version} to {after.algorithm_version} "
                  "between the two runs, so part or all of the change may be the method's."
                  if before.algorithm_version and after.algorithm_version
                  and before.algorithm_version != after.algorithm_version else "")
        out.append(Entry(
            id=f"{_TAG}score/{entity}/{bio}/{after.date}",
            title=f"{member.name}'s representation score went from {old} to {new}",
            url=page, when=when, category="score", label="Score change",
            paragraphs=(f"Representation score {old} on {before.date}, {new} on {after.date}.{method}",),
            source_url=f"{SITE_URL}/about/scores",
        ))
    return out[-LENGTH:]


def _money(value: float) -> str:
    return f"${value:,.0f}"


def _amount(low: float, high: float) -> str:
    if is_open_ended(low, high):
        return f"{_money(low)}+"
    return f"{_money(low)} to {_money(high)}" if high else "amount not stated"


def _member_trades(db: Session, chamber: str, member) -> list[Entry]:
    model, owner = ((StockTrade, StockTrade.senator_id) if chamber == "senate"
                    else (RepStockTrade, RepStockTrade.representative_id))
    rows = (db.query(model).filter(owner == member.id)
            .order_by(model.disclosure_date.desc(), model.id.asc()).limit(LENGTH * 4).all())
    filings: dict[str, list] = {}
    for t in rows:
        filings.setdefault(t.filing_id, []).append(t)
    out = []
    for filing_id, trades in filings.items():
        first = trades[0]
        when = _day(first.disclosure_date)
        if when is None:
            continue
        n = len(trades)
        out.append(Entry(
            id=f"{_TAG}trades/{chamber}/{filing_id}",
            title=f"{member.name} disclosed {n} stock trade{'s' if n != 1 else ''} on {first.disclosure_date}",
            url=f"/politicians/{member.id}", when=when, category="trades", label="Trade disclosure",
            paragraphs=tuple(
                f"{_TRADE_WORD.get(t.transaction_type, t.transaction_type)}: {t.asset_name}"
                f"{f' ({t.ticker})' if t.ticker else ''}, {_amount(t.amount_low, t.amount_high)}, "
                f"traded {t.transaction_date or 'on a date the filing does not state legibly'}, owner: {t.owner}."
                for t in trades
            ) + ("Amounts are the brackets the form reports, as filed.",),
            source_url=first.source_url or None,
        ))
    return out


def _member_disclosures(db: Session, chamber: str, member) -> list[Entry]:
    owner = FinancialDisclosure.senator_id if chamber == "senate" else FinancialDisclosure.representative_id
    out = []
    for d in db.query(FinancialDisclosure).filter(owner == member.id).all():
        when = _day(d.filed_date) or d.ingested_at
        what = d.report_label or "Annual financial disclosure"
        out.append(Entry(
            id=f"{_TAG}disclosure/{chamber}/{d.filing_id}",
            title=f"{member.name} filed a financial disclosure: {what}",
            url=f"/politicians/{member.id}", when=when, category="disclosure", label="Financial disclosure",
            paragraphs=tuple(p for p in (
                f"{what}, filed {d.filed_date}." if d.filed_date else f"{what}.",
                None if d.parsed else "The report couldn't be read as text (a scanned filing): see the source.",
            ) if p),
            source_url=d.source_url or None,
        ))
    return out


def member_feed(db: Session, member_id: str) -> EntityFeed | None:
    found = _member(db, member_id)
    if found is None:
        return None
    chamber, m = found
    # The bioguide id, which a rename never changes; the slug for a member
    # stored without one.
    bio = m.bioguide_id or m.id
    page = f"/politicians/{m.id}"
    entries = (_member_votes(db, chamber, m, bio) + _member_bills(db, chamber, m)
               + _score_changes(db, chamber, m, bio, page) + _member_trades(db, chamber, m)
               + _member_disclosures(db, chamber, m))
    office = "Senator" if chamber == "senate" else "Representative"
    return EntityFeed(
        title=f"Civitas Research: {m.name}",
        subtitle=(f"{office} {m.name} ({m.party}-{m.state}) on the record: roll-call votes, sponsored bills' "
                  "latest actions, score changes, and stock trade and financial disclosures."),
        page=page, entries=_newest(entries),
    )


# ── Bill ──────────────────────────────────────────────────────────

def _action_id(congress: int, bill_id: str, day: str, text: str) -> str:
    return f"{_TAG}bill/{congress}/{bill_id}/action/{day}/{_digest(text)}"


def stored_actions(db: Session, congress: int, bill_id: str) -> list[dict] | None:
    """The bill's actions as last read from Congress.gov, by a pipeline run
    (bill-actions-) or a reader's visit to the bill page (bill-record-actions-),
    whichever is newer, at any age; None when neither was ever read."""
    type_path, number = parse_bill_id(bill_id)
    keys = {f"bill-actions-{congress}-{type_path}-{number}": False,
            f"bill-record-actions-{congress}-{type_path}-{number}": True}
    rows = (db.query(ApiCache).filter(ApiCache.tier == "congress", ApiCache.cache_key.in_(list(keys)))
            .order_by(ApiCache.cached_at.desc()).all())
    for row in rows:
        data = json.loads(row.data_json)
        actions = (data or {}).get("value") if keys[row.cache_key] else data
        if isinstance(actions, list):
            return actions
    return None


def bill_feed(db: Session, congress: int, bill_id: str) -> EntityFeed | None:
    if parse_bill_id(bill_id) is None:
        return None
    label = bill_label(bill_id) or bill_id
    page = bill_path(bill_id, congress)
    source = congress_gov_url_for_bill_id(bill_id, congress)
    raw = stored_actions(db, congress, bill_id)
    votes = (db.query(RollCall).filter(RollCall.bill_id == bill_id, RollCall.congress == congress)
             .order_by(RollCall.date.desc()).limit(LENGTH).all())
    sponsored = (db.query(SponsoredBill.title).filter_by(bill_id=bill_id, congress=congress).first()
                 or db.query(RepSponsoredBill.title).filter_by(bill_id=bill_id, congress=congress).first())
    if raw is None and not votes and sponsored is None:
        return None
    entries = []
    for a in bill_journey.fold_actions(raw or [], lambda a: []):
        when = _day(a["date"])
        if when is None or not a["text"]:
            continue
        entries.append(Entry(
            id=_action_id(congress, bill_id, a["date"], a["text"]),
            title=f"{label}: {a['text']}", url=page, when=when, category="bill-action", label="Bill action",
            paragraphs=tuple(p for p in (
                f"{a['date']}: {a['text']}",
                f"What this step means: {a['meaning']}" if a.get("meaning") else None,
            ) if p),
            source_url=source,
        ))
    for rc in votes:
        when = _day(rc.date)
        if when is None:
            continue
        entries.append(Entry(
            id=f"{_TAG}vote/{rc.chamber}-{rc.congress}-{rc.session}-{rc.number}",
            title=f"{label}: {_roll_call_ref(rc)}, {rc.question or 'a recorded vote'}",
            url=_vote_path(rc), when=when, category="vote", label="Roll-call vote",
            paragraphs=tuple(p for p in (rc.title, _tally(rc)) if p),
            source_url=rc.source_url or None,
        ))
    title = sponsored[0] if sponsored else None
    return EntityFeed(
        title=f"Civitas Research: {label}" + (f", {title}" if title else ""),
        subtitle=f"Each action Congress records on {label} and each roll call on it.",
        page=page, entries=_newest(entries),
    )


# ── Agency ────────────────────────────────────────────────────────

def agency_feed(db: Session, slug: str, slugs: list[str]) -> EntityFeed | None:
    """`slugs`: the agency and every agency under it (api/agencies.descendants)."""
    agency = db.get(Agency, slug)
    if agency is None:
        return None
    today = comment_period_today()
    since = (date.fromisoformat(today) - timedelta(days=AGENCY_DAYS)).isoformat()
    docs = (db.query(ExploreDocument.id, ExploreDocument.external_id, ExploreDocument.title,
                     ExploreDocument.doc_type, ExploreDocument.date, ExploreDocument.summary,
                     ExploreDocument.url, ExploreDocument.comment_url, ExploreDocument.comments_close_on,
                     ExploreDocument.agency_name)
            .filter(ExploreDocument.agency_slug.in_(slugs), ExploreDocument.chamber == "Regulatory",
                    ExploreDocument.date >= since)
            .order_by(ExploreDocument.date.desc()).limit(LENGTH * 4).all())
    entries = []
    for d in docs:
        when = _day(d.date)
        if when is None:
            continue
        page = f"/explore/{public_key(d.external_id, d.id)}"
        stable = d.external_id or f"doc-{d.id}"
        commentable = bool(d.comment_url and d.comments_close_on)
        is_rule = d.doc_type in RULE_DOC_TYPES
        if not (is_rule or commentable):
            continue
        kind = ("Proposed rule" if d.doc_type == FR_TYPE_LABELS["Proposed Rule"]
                else "Final rule" if d.doc_type == FR_TYPE_LABELS["Rule"] else "Comment period opens")
        abstract = " ".join((d.summary or "").split())
        entries.append(Entry(
            id=f"{_TAG}fr/{stable}", title=f"{kind}: {d.title}", url=page, when=when,
            category="rule" if is_rule else "comment-period", label=kind,
            paragraphs=tuple(p for p in (
                f"{d.agency_name or agency.name}, published in the Federal Register on {d.date}.",
                abstract or None,
                f"Comments close {d.comments_close_on}." if commentable else None,
            ) if p),
            source_url=d.url or None,
        ))
        # The last day to comment, as its own entry once it arrives.
        if commentable and d.comments_close_on <= today and (closes := _day(d.comments_close_on)):
            entries.append(Entry(
                id=f"{_TAG}fr/{stable}/last-day", title=f"Last day to comment: {d.title}", url=page,
                when=closes, category="comment-period", label="Comment period closes",
                paragraphs=(f"The comment period on this {d.doc_type.lower()} closes on {d.comments_close_on}.",),
                source_url=d.comment_url,
            ))
    return EntityFeed(
        title=f"Civitas Research: {agency.name}",
        subtitle=(f"{agency.name}'s proposed and final rules in the Federal Register, and each comment period "
                  "as it opens and on its last day."),
        page=f"/agencies/{slug}", entries=_newest(entries),
    )


# ── Monitor ───────────────────────────────────────────────────────

def monitor_feed(db: Session, slug: str) -> EntityFeed | None:
    monitor = db.query(NationalMonitor).filter(NationalMonitor.slug == slug).first()
    if monitor is None:
        return None
    page = f"/action?tab=monitors&monitor={quote(slug)}"
    rows = (db.query(MonitorUpdate).filter(MonitorUpdate.monitor_id == monitor.id)
            .order_by(MonitorUpdate.date.desc(), MonitorUpdate.id.desc()).limit(LENGTH).all())
    entries = []
    for u in rows:
        when = _day(u.date)
        if when is None:
            continue
        outlet = u.source_name or "the source"
        entries.append(Entry(
            id=f"{_TAG}monitor-update/{u.id}",
            # The outlet's own headline; the update's summary is not used,
            # since older ones were model-written.
            title=u.article_title or f"{monitor.title}: an update from {outlet}",
            url=page, when=when, category="monitor-update", label="Monitor update",
            paragraphs=(f"Reported by {outlet} on {u.date}, for the national monitor “{monitor.title}”.",),
            source_url=u.source_url or None,
        ))
    return EntityFeed(
        title=f"Civitas Research: {monitor.title}",
        subtitle=f"Each sourced update to the national monitor “{monitor.title}”, under the outlet's own headline.",
        page=page, entries=entries,
    )

