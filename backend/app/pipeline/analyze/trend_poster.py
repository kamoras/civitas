"""Trend posts, "On the record": when a U.S. policy topic trends on
Bluesky, the official record behind it, in the record's own words
(app.broadcast.publish, kind "trend").

Hourly, from the Action Center's scheduler job (scheduler._hourly_action_refresh):

 1. Read Bluesky's public getTrends (fetch/trending.fetch_bluesky_trends)
    and keep each listing as TrendObservation rows. A failed read is an
    outage, not an empty list: nothing is recorded, dropped or posted.
 2. Score each trend (trend_relevance): Bluesky's category, the tragedy and
    political-violence votes, and the neutrality gate on its display name
    (the only words a post quotes from Bluesky).
 3. Find its record on a pinned match only (trend_evidence: an issue,
    an Act's title, a nominee, or a named member's own bill).
 4. Write one audit row per trend (TrendCandidate): scores, match, record,
    the parties of the members named and of the record's sponsors, and the
    decision with its reason.
 5. Post at most one trend a run: inside the posting window
    (bluesky_post_windows.json), at most MAX_POSTS_PER_DAY an Eastern day,
    MIN_SPACING apart, the same record at most once in RECORD_COOLDOWN, and
    never a record an issue post already carried. Outside the window an
    eligible trend is held; a held trend that has left Bluesky's list by
    the next run is dropped.

Selection is party-blind: the score is the trend's rank weight times its
match strength, and the parties on the audit row are never read to choose
(test_trend_selection_is_party_blind). The admin view compares the posted
party share with the eligible pool's, with an exact binomial test, and an
ops alert fires on a divergence (check_party_balance).

Every word of a post is template text, a stored record field, or Bluesky's
trend name in quotation marks attributed to Bluesky. No model writes any
of it. A tragedy is eligible only when its political-violence vote is high
and the record is an official one (a vote, a bill, the day's record, an
agency's document), and the post states only what that record says.

The picture is the record's card (services/record_cards, rendered by the
frontend's /api/og): for the first TRIAL_DAYS of trend posts Bluesky gets
the image on even Eastern days and the link card on odd ones, recorded in
BroadcastPost.embed_kind, so the admin view can compare their engagement;
after the trial, the image.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy.orm import Session

from app import broadcast
from app.models import ActionIssue, BroadcastPost, TrendCandidate, TrendObservation
from app.ops_alerts import check_source_freshness, resolve_ops_alert, send_ops_alert
from app.pipeline.analyze.action_center import _embed_texts_sim
from app.pipeline.analyze.bluesky_utils import BSKY_MAX_CHARS, MAX_ALT_CHARS
from app.pipeline.analyze.trend_evidence import (
    OFFICIAL_KINDS,
    Evidence,
    RecordIndex,
    find_evidence,
)
from app.pipeline.analyze.trend_relevance import (
    POLITICS_CATEGORY,
    TrendScores,
    loaded_votes,
    neutrality_problems,
    score_trends,
    trend_text,
)
from app.pipeline.fetch.trending import BSKY_TRENDS_LIMIT, BlueskyTrend, fetch_bluesky_trends
from app.services.record_cards import RecordCard, card_for_key, long_date, og_query
from app.time_utils import COMMENT_DEADLINE_TZ, utcnow

logger = logging.getLogger(__name__)

KIND = "trend"
SUBJECT_PREFIX = "trend-record:"
MAX_POSTS_PER_DAY = 2
MIN_SPACING = timedelta(hours=4)
RECORD_COOLDOWN = timedelta(days=14)
OBSERVATION_RETENTION = timedelta(days=90)
# The image-vs-card trial (module docstring).
TRIAL_DAYS = 28
# The party-balance alarm: posted trends with a party lean needed before
# the test means anything, and the two-sided p-value that raises it.
PARTY_AUDIT_MIN_POSTED = 10
PARTY_AUDIT_ALPHA = 0.01
PARTY_ALERT_CONDITION = "trend-posts-party-balance"
_EASTERN = COMMENT_DEADLINE_TZ


# -- the post's words -------------------------------------------------------

def record_sentences(card: RecordCard) -> list[str]:
    """The record, in its card's fields, longest first: every version a
    post may use (the caller takes the first that fits). Never a cut field:
    a shorter version leaves whole fields out."""
    head = f"{card.eyebrow}, {long_date(card.date)}: {card.headline.rstrip('.')}."
    tally = " ".join(f"{row['label']} {row['total']}." for row in card.tally if row["position"] in ("yea", "nay"))
    lines = " ".join(line.rstrip(".") + "." for line in card.lines)
    versions = [" ".join(x for x in (head, lines, tally) if x), " ".join(x for x in (head, tally) if x)]
    return list(dict.fromkeys(versions))


def issue_sentence(issue: ActionIssue) -> str:
    return f"Civitas Action Center, {long_date(issue.date)}: {issue.title.rstrip('.')}."


def compose_text(trend_name: str, sentences: list[str], url: str) -> str | None:
    """`Trending on Bluesky: "<name>". On the record: <record>` — the first
    version that fits a post with its link, or None."""
    budget = BSKY_MAX_CHARS - len(url) - 1
    for sentence in sentences:
        text = f"Trending on Bluesky: “{trend_name}”. On the record: {sentence}"
        if len(text) <= budget:
            return text
    return None


# -- selection --------------------------------------------------------------

def rank_weight(rank: int) -> float:
    """1 for the first listed trend, falling linearly to 1/LIMIT for the last."""
    return (BSKY_TRENDS_LIMIT - min(rank, BSKY_TRENDS_LIMIT - 1)) / BSKY_TRENDS_LIMIT


def selection_score(rank: int, strength: float) -> float:
    return rank_weight(rank) * strength


def pick(candidates: list[TrendCandidate]) -> TrendCandidate | None:
    """The eligible candidate to post: highest selection score, then the
    higher-listed trend. Reads nothing else (no party, no record kind)."""
    eligible = [c for c in candidates if c.decision in ("eligible", "held") and c.selection_score is not None]
    if not eligible:
        return None
    return max(eligible, key=lambda c: (c.selection_score, -c.rank, c.topic))


# -- one trend --------------------------------------------------------------

def _record_posted_recently(db: Session, key: str, now: datetime) -> bool:
    return db.query(BroadcastPost.id).filter(
        BroadcastPost.subject == (SUBJECT_PREFIX + key)[:80],
        BroadcastPost.published_at >= now - RECORD_COOLDOWN,
    ).first() is not None


def _issue_post_covers(db: Session, ev: Evidence, now: datetime) -> bool:
    """Whether an issue post already carried this story or record: the
    anchoring issue (or the record, when it is an issue) was posted, or a
    posted issue of the last RECORD_COOLDOWN links the record's bill."""
    ids = {i for i in (ev.anchor_issue_id, ev.record.issue_id) if i is not None}
    if ids and db.query(ActionIssue.id).filter(ActionIssue.id.in_(ids), ActionIssue.bsky_posted_at.isnot(None)).first():
        return True
    if not ev.record.bill_id:
        return False
    since = (now - RECORD_COOLDOWN).date().isoformat()
    for (related,) in db.query(ActionIssue.related_bill_ids).filter(
            ActionIssue.bsky_posted_at.isnot(None), ActionIssue.date >= since).all():
        try:
            if any(b.get("id") == ev.record.bill_id for b in json.loads(related or "[]")):
                return True
        except ValueError:
            continue
    return False


class Draft:
    """A trend's chosen evidence, its words, and its picture."""

    def __init__(self, ev: Evidence, text: str, title: str, card: RecordCard | None, show_title: bool):
        self.ev = ev
        self.text = text
        self.title = title
        self.card = card
        self.show_title = show_title

    @property
    def image_url(self) -> str | None:
        q = og_query(self.ev.record.key, self.show_title) if self.card else None
        return f"{broadcast.SITE_URL}/api/og?{q}" if q else None

    @property
    def alt_text(self) -> str | None:
        return self.card.alt[:MAX_ALT_CHARS] if self.card else None


def _draft(db: Session, trend: BlueskyTrend, ev: Evidence) -> tuple[Draft | None, str]:
    """The post for this evidence, or None and why not."""
    record = ev.record
    card = None
    show_title = True
    if record.kind == "issue":
        issue = db.get(ActionIssue, record.issue_id)
        if issue is None:
            return None, "issue gone"
        sentences = [issue_sentence(issue)]
        if neutrality_problems(issue.title):
            return None, "issue headline fails the neutrality check"
    else:
        if record.kind in ("vote", "bill"):
            full = card_for_key(db, record.key, show_title=True)
            title_text = (full.headline if full else "") or ""
            problems = neutrality_problems(title_text, loaded_vote=(loaded_votes([title_text]) or [0.0])[0])
            show_title = not problems
        card = card_for_key(db, record.key, show_title=show_title)
        if card is None:
            return None, "record not stored"
        if neutrality_problems(card.alt):
            return None, "record card fails the neutrality check"
        sentences = record_sentences(card)
    text = compose_text(trend.display_name, sentences, record.url)
    if text is None and show_title and record.kind == "vote":
        # A long measure title: the bill number stands in for it, in the
        # post and the image alike (never a cut title).
        short = card_for_key(db, record.key, show_title=False)
        if short is not None:
            card, show_title = short, False
            text = compose_text(trend.display_name, record_sentences(card), record.url)
    if text is None:
        return None, "too long for a post"
    return Draft(ev, text, f"On the record: {trend.display_name}", card, show_title), ""


def evaluate(
    db: Session, trend: BlueskyTrend, scores: TrendScores, evidence: list[Evidence], now: datetime,
) -> tuple[str, str, Evidence | None, Draft | None]:
    """(decision, reason, evidence, draft) for one trend, before caps and
    the window: "eligible", "ineligible" or "skipped"."""
    problems = neutrality_problems(trend.display_name, trend.description, scores.loaded)
    if problems:
        return "skipped", "trend name: " + "; ".join(problems), None, None
    if not scores.relevant:
        return "ineligible", f"Bluesky category {trend.category or 'none'}, not {POLITICS_CATEGORY}", None, None
    if not evidence:
        return "ineligible", "no pinned record", None, None
    if scores.is_tragedy and not scores.political_tragedy:
        return "skipped", f"tragedy without a political reading (vote {scores.political_violence:.2f})", evidence[0], None
    reasons = []
    for ev in evidence:
        if scores.is_tragedy and ev.record.kind not in OFFICIAL_KINDS:
            reasons.append(f"{ev.record.key}: a tragedy needs an official record")
            continue
        if _record_posted_recently(db, ev.record.key, now):
            reasons.append(f"{ev.record.key}: posted in the last {RECORD_COOLDOWN.days} days")
            continue
        if _issue_post_covers(db, ev, now):
            reasons.append(f"{ev.record.key}: an issue post already covers it")
            continue
        draft, why = _draft(db, trend, ev)
        if draft is None:
            reasons.append(f"{ev.record.key}: {why}")
            continue
        return "eligible", "", ev, draft
    return "skipped", "; ".join(reasons), evidence[0], None


# -- the run ----------------------------------------------------------------

def _eastern_day_start(now: datetime) -> datetime:
    local = now.replace(tzinfo=timezone.utc).astimezone(_EASTERN)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)


def embed_kind_for(db: Session, now: datetime) -> str:
    """The trial's arm for a post made now: "image" on even Eastern days,
    "card" on odd ones, for TRIAL_DAYS from the first trend post; "image"
    after it."""
    first = db.query(BroadcastPost.published_at).filter(BroadcastPost.kind == KIND).order_by(
        BroadcastPost.published_at).first()
    if first is not None and now - first[0] >= timedelta(days=TRIAL_DAYS):
        return "image"
    day = now.replace(tzinfo=timezone.utc).astimezone(_EASTERN).date()
    return "image" if day.toordinal() % 2 == 0 else "card"


def _cap_reason(db: Session, now: datetime) -> str | None:
    if not broadcast.in_window(now):
        return "outside the posting window"
    posts = (db.query(BroadcastPost.published_at).filter(
        BroadcastPost.kind == KIND, BroadcastPost.published_at >= _eastern_day_start(now)).all())
    if len(posts) >= MAX_POSTS_PER_DAY:
        return f"{MAX_POSTS_PER_DAY} trend posts already today"
    last = db.query(BroadcastPost.published_at).filter(BroadcastPost.kind == KIND).order_by(
        BroadcastPost.published_at.desc()).first()
    if last is not None and now - last[0] < MIN_SPACING:
        return f"last trend post under {int(MIN_SPACING.total_seconds() // 3600)} hours ago"
    return None


def _record_observations(db: Session, trends: list[BlueskyTrend], now: datetime) -> None:
    for t in trends:
        db.add(TrendObservation(
            observed_at=now, topic=t.topic, display_name=t.display_name, description=t.description,
            rank=t.rank, status=t.status, category=t.category, started_at=t.started_at, post_count=t.post_count,
        ))
    db.query(TrendObservation).filter(TrendObservation.observed_at < now - OBSERVATION_RETENTION).delete()
    db.commit()


def _upsert(db: Session, trend: BlueskyTrend, scores: TrendScores, now: datetime) -> TrendCandidate:
    row = db.query(TrendCandidate).filter_by(topic=trend.topic).one_or_none()
    if row is None:
        row = TrendCandidate(topic=trend.topic, first_seen_at=now, decision="ineligible")
        db.add(row)
    row.display_name = trend.display_name
    row.description = trend.description
    row.category = trend.category
    row.started_at = trend.started_at
    row.last_seen_at = now
    row.rank = trend.rank
    row.relevance, row.tragedy = scores.relevance, scores.tragedy
    row.political_violence, row.loaded = scores.political_violence, scores.loaded
    return row


def _set_match(row: TrendCandidate, ev: Evidence | None, named_parties: list[str], score: float | None) -> None:
    row.named_parties = json.dumps(named_parties)
    if ev is None:
        row.match_type = row.match_strength = row.record_kind = row.record_key = None
        row.record_date = row.record_url = None
        row.sponsor_parties = "[]"
    else:
        row.match_type, row.match_strength = ev.match_type, ev.strength
        row.record_kind, row.record_key = ev.record.kind, ev.record.key
        row.record_date, row.record_url = ev.record.date, ev.record.url
        row.sponsor_parties = json.dumps(ev.sponsor_parties)
    row.selection_score = score


def run_trend_posts(db: Session, now: datetime | None = None) -> dict:
    """One hourly pass (module docstring). Returns counts for the log."""
    now = now or utcnow()
    trends = fetch_bluesky_trends()
    check_source_freshness(
        {"trending:bluesky_trends": ("bluesky_trends", None if trends is None else [now] if trends else [])},
        floor=timedelta(hours=24), kind="trending source",
    )
    if trends is None:
        logger.warning("Trend posts: Bluesky getTrends unreadable; nothing recorded, held or dropped this hour")
        return {"outage": 1}
    _record_observations(db, trends, now)

    listed = {t.topic for t in trends}
    for row in db.query(TrendCandidate).filter(TrendCandidate.decision == "held").all():
        if row.topic not in listed:
            row.decision, row.reason, row.decided_at = "dropped", "no longer trending when the window opened", now
    db.commit()

    scores = score_trends([(t.display_name, t.description, t.category) for t in trends])
    index: RecordIndex | None = None
    rows: list[TrendCandidate] = []
    drafts: dict[str, Draft] = {}
    for trend, sc in zip(trends, scores):
        existing = db.query(TrendCandidate.decision).filter_by(topic=trend.topic).scalar()
        if existing == "posted":
            continue
        row = _upsert(db, trend, sc, now)
        evidence, named = [], []
        if sc.relevant:
            index = index or RecordIndex(db, now)
            emb = _embed_texts_sim([trend_text(trend.display_name, trend.description)])[0]
            evidence, named = find_evidence(index, trend.display_name, trend.description,
                                            trend.started_at or now, emb)
        decision, reason, ev, draft = evaluate(db, trend, sc, evidence, now)
        _set_match(row, ev, [m["party"] for m in named if m.get("party")],
                   selection_score(trend.rank, ev.strength) if draft else None)
        row.decision, row.reason, row.decided_at = decision, reason, now
        if draft:
            drafts[trend.topic] = draft
        rows.append(row)
    db.commit()

    posted = 0
    cap = _cap_reason(db, now)
    eligible = [r for r in rows if r.decision == "eligible"]
    if eligible and cap == "outside the posting window":
        for r in eligible:
            r.decision, r.reason = "held", cap
    elif eligible and cap:
        for r in eligible:
            r.reason = cap
    elif eligible:
        chosen = pick(eligible)
        draft = drafts[chosen.topic]
        post = broadcast.publish(
            db, kind=KIND, subject=(SUBJECT_PREFIX + draft.ev.record.key)[:80], title=draft.title,
            text=draft.text, url=draft.ev.record.url, image_url=draft.image_url, alt_text=draft.alt_text,
            embed_kind=embed_kind_for(db, now) if draft.image_url else "card",
        )
        chosen.decision, chosen.reason, chosen.broadcast_post_id = "posted", "", post.id
        posted = 1
        for r in eligible:
            if r is not chosen:
                r.reason = "another trend scored higher this hour"
    db.commit()
    check_party_balance(db)
    return {"trends": len(trends), "eligible": len(eligible), "posted": posted}


# -- the party audit --------------------------------------------------------

def party_lean(parties: list[str]) -> str | None:
    """"D" or "R" when a candidate's named members and sponsors lean that
    way by count; None for none, a tie or neither party."""
    d, r = parties.count("D"), parties.count("R")
    return "D" if d > r else "R" if r > d else None


def binomial_two_sided(k: int, n: int, p: float) -> float:
    """Exact two-sided binomial p-value: the probability, under share p, of
    an outcome no likelier than k of n."""
    if n == 0:
        return 1.0
    if p <= 0 or p >= 1:
        return 1.0 if (k == 0 and p <= 0) or (k == n and p >= 1) else 0.0
    probs = [math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(n + 1)]
    return min(1.0, sum(q for q in probs if q <= probs[k] * (1 + 1e-9)))


def party_audit(db: Session, since: datetime | None = None) -> dict:
    """The posted trends' Democratic share against the eligible pool's,
    counting each candidate once by its lean (party_lean), with the exact
    binomial test of the posted count against the pool's share."""
    q = db.query(TrendCandidate).filter(TrendCandidate.selection_score.isnot(None))
    if since is not None:
        q = q.filter(TrendCandidate.first_seen_at >= since)
    pool = {"D": 0, "R": 0, "none": 0}
    posted = {"D": 0, "R": 0, "none": 0}
    for row in q.all():
        lean = party_lean(json.loads(row.named_parties or "[]") + json.loads(row.sponsor_parties or "[]")) or "none"
        pool[lean] += 1
        if row.decision == "posted":
            posted[lean] += 1
    pool_n, posted_n = pool["D"] + pool["R"], posted["D"] + posted["R"]
    share = pool["D"] / pool_n if pool_n else None
    p_value = binomial_two_sided(posted["D"], posted_n, share) if share is not None and posted_n else None
    return {
        "pool": pool, "posted": posted,
        "poolDemocraticShare": share,
        "postedDemocraticShare": posted["D"] / posted_n if posted_n else None,
        "pValue": p_value,
        "minPosted": PARTY_AUDIT_MIN_POSTED, "alpha": PARTY_AUDIT_ALPHA,
        "diverges": bool(p_value is not None and posted_n >= PARTY_AUDIT_MIN_POSTED and p_value < PARTY_AUDIT_ALPHA),
    }


def check_party_balance(db: Session) -> None:
    """Raise an ops alert when the posted trends' party share diverges from
    the eligible pool's (party_audit), and close it when it no longer does."""
    audit = party_audit(db)
    if audit["diverges"]:
        send_ops_alert(
            "Trend posts: party share diverges from the eligible pool",
            f"Posted Democratic share {audit['postedDemocraticShare']:.2f} of {sum(audit['posted'][k] for k in 'DR')} "
            f"leaning posts vs the pool's {audit['poolDemocraticShare']:.2f} (binomial p={audit['pValue']:.4f}). "
            "Selection is party-blind; check what the gates are letting through (admin, Trends tab).",
            condition=PARTY_ALERT_CONDITION,
        )
    else:
        resolve_ops_alert(PARTY_ALERT_CONDITION)


# -- the admin view ---------------------------------------------------------

def trend_lifetimes(db: Session, now: datetime | None = None) -> dict:
    """How long trends stay listed, from the hourly observations: for each
    topic that has left the list, hours from its startedAt (or first
    sighting) to its last sighting."""
    now = now or utcnow()
    spans: dict[str, list] = {}
    for topic, seen, started in db.query(TrendObservation.topic, TrendObservation.observed_at,
                                         TrendObservation.started_at).all():
        s = spans.setdefault(topic, [seen, seen, started])
        s[0], s[1] = min(s[0], seen), max(s[1], seen)
        s[2] = s[2] or started
    hours = sorted(((last - (started or first)).total_seconds() / 3600)
                   for first, last, started in spans.values() if last < now - timedelta(hours=2))
    def q(p: float) -> float | None:
        return round(float(np.percentile(hours, p)), 1) if hours else None
    return {"topics": len(spans), "ended": len(hours), "p25": q(25), "median": q(50), "p75": q(75)}


def engagement_by_embed(db: Session) -> list[dict]:
    """The image-vs-card trial: trend posts Bluesky took, by what they
    carried, with their public counts as last read."""
    rows = db.query(BroadcastPost).filter(
        BroadcastPost.kind == KIND, BroadcastPost.bsky_status == "sent").all()
    out = []
    for kind in ("image", "card", "text"):
        group = [r for r in rows if r.embed_kind == kind]
        read = [r for r in group if r.bsky_likes is not None]
        out.append({
            "embedKind": kind, "posts": len(group), "read": len(read),
            "meanLikes": round(float(np.mean([r.bsky_likes for r in read])), 2) if read else None,
            "medianLikes": float(np.median([r.bsky_likes for r in read])) if read else None,
            "meanReposts": round(float(np.mean([r.bsky_reposts or 0 for r in read])), 2) if read else None,
        })
    return out
