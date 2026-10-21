"""
Engage with news outlet posts on Bluesky.

When a major outlet's recent post matches an active Civitas action center
issue (cosine similarity at or above the gate's threshold), we repost it and
like it. The threshold is fitted on labels the posts' own links supply
(link_label): a post linking an article the matched issue cites is right,
one linking an article another live issue cites is that story's coverage,
and the rest are unlabelled. The bundled fit (scripts/calibrate_engagement.py,
app/data/engagement_calibration.json) is pooled daily with every post the
gate has scored since (OutletPostLink, its link resolved through the
outlet's shortener) and refitted under the Action Center's precision rule
(refit). Until a fit meets the rule the gate runs at UNMEASURED_THRESHOLD,
a value nobody measured, kept by the user's decision (2026-10-21) so
reposting continues while labels gather; the data file says so beside the
measurement.
This keeps the Civitas feed active between original posts and surfaces
relevant reporting within the civic transparency context.

Design constraints:
- Only original posts (no replies, no reposts of others) from the last 24h
- At most MAX_REPOSTS_PER_RUN reposts per hourly pipeline run
- Each post is processed at most once (tracked via ApiCache)
- Runs silently if credentials are not configured
"""

import hashlib
import json
import logging
import pathlib
from datetime import datetime, timedelta, UTC
from urllib.parse import urlsplit

import httpx
import numpy as np
from sqlalchemy.orm import Session

from app import calibration_drift
from app.config import settings
from app.contact import BOT_USER_AGENT
from app.models import ActionIssue, ApiCache, OutletPostLink
from app.pipeline.cache import api_cache_get, api_cache_set
from app.precision_rule import LOWEST_PASSING, choose, summary
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

CALIBRATION_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "engagement_calibration.json"

# NOT A FITTED VALUE. The hand-set cosine the gate used before it was
# measured ("set conservatively"), kept by the user's decision (2026-10-21)
# while no fitted threshold meets the precision rule (the measurement is in
# engagement_calibration.json). A refit that meets the rule replaces it with
# no code change.
UNMEASURED_THRESHOLD = 0.78

# Which threshold the precision rule picks among those that clear its bar:
# the lowest, the most reposts the bar allows (app/precision_rule.py; the
# owner's decision, 2026-10-21: the best-bound choice cut reposts from about
# 37 a week to 9).
PRECISION_VARIANT = LOWEST_PASSING

# The cosines a fit may choose from, in the gate's units.
GRID = [round(0.30 + 0.01 * i, 2) for i in range(67)]

# The daily refit, kept in api_cache (which prunes at 60 days; it is
# rewritten daily). A stored refit older than _FIT_MAX_AGE_HOURS is not
# used: the bundled fit is.
_FIT_TIER = "engagement_fit"
_FIT_KEY = "fit"
_FIT_MAX_AGE_HOURS = 48
# Attempts at resolving a recorded post's link before it stays unlabelled.
RESOLVE_TRIES = 3
# Links resolved per run, one request at a time: the shorteners answer a
# burst with 429, and a run records a handful of new posts.
RESOLVE_PER_RUN = 20


def article_key(url: str | None) -> str | None:
    """An article's address without scheme, query or trailing slash."""
    if not url:
        return None
    parts = urlsplit(url)
    return f"{parts.netloc.lower().removeprefix('www.')}{parts.path.rstrip('/')}" or None


def _field(obj, name: str):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def post_link(post) -> str | None:
    """The address a post's link card points at (a dict from the public
    API or an atproto model), None for a post without one."""
    uri = _field(_field(_field(post, "embed"), "external"), "uri")
    return str(uri) if uri else None


def resolved(status: int) -> bool:
    """Whether the response at the end of a link's redirects names the
    article: a rate limit or a server error says nothing yet. (The article's
    own site may refuse a bot, 403, at an address that is still the article's.)"""
    return status != 429 and status < 500


def link_label(matched: int, citing) -> bool | None:
    """Right when the matched issue cites the post's article, wrong when only
    other live issues do, None (unlabelled) when no live issue cites it."""
    return (matched in citing) if citing else None


def labels_sha256(labels: list) -> str:
    return hashlib.sha256(json.dumps(sorted(labels)).encode()).hexdigest()


def bundled_fit() -> dict:
    return json.loads(CALIBRATION_PATH.read_text())


def engage_threshold(fit: dict) -> float:
    """The fitted cosine when the fit meets the precision rule, else the
    unmeasured one in use (UNMEASURED_THRESHOLD)."""
    value = fit.get("threshold")
    return float(value) if value is not None else UNMEASURED_THRESHOLD


def record_post(db: Session, *, uri: str, outlet: str, posted_at: datetime | None, link: str | None,
                sim: float, matched: int, live: list[int]) -> None:
    """Keep what the gate saw for a post, for the refit to label once its
    link is resolved. Not committed: the caller commits it with the post's
    processed mark."""
    db.merge(OutletPostLink(uri=uri, outlet=outlet, posted_at=posted_at, link=link, best_sim=sim,
                            issue_id=matched, live_issue_ids=json.dumps(sorted(live))))


def resolve_pending(db: Session, client: httpx.Client) -> int:
    """Resolve the links of recorded posts not resolved yet, one request at
    a time; each failure counts as a try. Reads, then fetches, then writes
    and commits: no write transaction is held across a request."""
    pending = [(r.uri, r.link) for r in db.query(OutletPostLink)
               .filter(OutletPostLink.link.is_not(None), OutletPostLink.article.is_(None),
                       OutletPostLink.resolve_tries < RESOLVE_TRIES)
               .order_by(OutletPostLink.recorded_at.desc()).limit(RESOLVE_PER_RUN)]
    found = {}
    for uri, link in pending:
        try:
            resp = client.head(link)
            found[uri] = article_key(str(resp.url)) if resolved(resp.status_code) else None
        except httpx.HTTPError:
            found[uri] = None
    for uri, key in found.items():
        row = db.get(OutletPostLink, uri)
        row.resolve_tries += 1
        row.article = key
    db.commit()
    return sum(1 for k in found.values() if k)


def refit(db: Session) -> dict:
    """The bundled labels pooled with every recorded post its link labels (a
    recorded post replaces a bundled one with the same uri), and the
    threshold the precision rule chooses on them (null when none meets it)."""
    pooled = {uri: (sim, link_label(matched, citing)) for uri, _, matched, citing, sim in bundled_fit()["labels"]}
    rows = db.query(OutletPostLink).filter(OutletPostLink.article.is_not(None)).all()
    live = {r.uri: json.loads(r.live_issue_ids or "[]") for r in rows}
    cited = {iid: {article_key(u) for u in json.loads(urls or "[]")} for iid, urls in
             db.query(ActionIssue.id, ActionIssue.source_urls)
             .filter(ActionIssue.id.in_({i for ids in live.values() for i in ids}))}
    recorded = []
    for r in rows:
        citing = sorted(i for i in live[r.uri] if r.article in cited.get(i, ()))
        right = link_label(r.issue_id, citing)
        if right is not None:
            pooled[r.uri] = (r.best_sim, right)
            recorded.append([r.uri, r.article, r.issue_id, citing, round(r.best_sim, 4)])
    labelled = [v for v in pooled.values() if v[1] is not None]
    scores = np.array([s for s, _ in labelled], float)
    correct = np.array([bool(c) for _, c in labelled], bool)
    threshold, best = choose(scores, correct, GRID, PRECISION_VARIANT)
    return {"date": utcnow().date().isoformat(), "precision_rule": PRECISION_VARIANT,
            "threshold": threshold, "best_bound_threshold": best,
            "at_threshold": summary(scores >= (threshold if threshold is not None else best), correct),
            "labelled_posts": len(labelled), "recorded_labelled": len(recorded),
            "recorded_labels_sha256": labels_sha256(recorded)}


def current_fit(db: Session) -> dict:
    """Today's refit (made on the first run of the UTC day), else the last
    stored one within _FIT_MAX_AGE_HOURS, else the bundled fit: a refit
    that fails keeps the fit in use."""
    stored = api_cache_get(db, _FIT_TIER, _FIT_KEY, max_age_hours=_FIT_MAX_AGE_HOURS)
    if stored and stored.get("date") == utcnow().date().isoformat():
        return stored
    try:
        fitted = refit(db)
        api_cache_set(db, _FIT_TIER, _FIT_KEY, fitted)
        logger.info("Engagement refit: %s", fitted)
        return fitted
    except Exception:
        db.rollback()
        logger.warning("Engagement refit failed; the fit in use stands", exc_info=True)
        return stored or bundled_fit()


# Hard cap on reposts per pipeline run (runs hourly) — keeps the feed human-paced
MAX_REPOSTS_PER_RUN = 3

# Only consider posts from within this window
POST_MAX_AGE_HOURS = 24

# Cache tier used to record which Bluesky posts have been processed
_CACHE_TIER = "bsky_engagement"

# Major news outlets with reliable Bluesky presence.
# Handles are resolved by atproto — use the domain-style handles that
# verified outlets register (e.g. apnews.com maps to their Bluesky account).
# "newshour.bsky.social" was listed until 2026-10-21: an account with no
# posts at all, so it contributed nothing; an outlet whose feed comes back
# empty is now logged as a warning.
NEWS_OUTLET_HANDLES: list[str] = [
    "apnews.com",
    "npr.org",
]


def _already_processed(cid: str, db: Session) -> bool:
    return db.get(ApiCache, (_CACHE_TIER, cid)) is not None


def _mark_processed(cid: str, db: Session, record: dict | None = None) -> None:
    """Mark a post processed, with what the gate saw for it (record_post)
    in the same commit."""
    if not _already_processed(cid, db):
        try:
            db.add(ApiCache(tier=_CACHE_TIER, cache_key=cid, data_json="1"))
            if record:
                record_post(db, **record)
            db.commit()
        except Exception:
            db.rollback()
            logger.warning("Engagement: post %s not recorded", cid, exc_info=True)


def engage_with_news_posts(db: Session) -> None:
    """Repost and like outlet posts that match active action center issues.

    Called from the action center refresh pipeline after issues are updated.
    No-op if Bluesky credentials are not configured.
    """
    handle = getattr(settings, "BSKY_HANDLE", "")
    app_password = getattr(settings, "BSKY_APP_PASSWORD", "")
    if not handle or not app_password:
        return

    # Load currently active issues
    active_issues = (
        db.query(ActionIssue)
        .filter(ActionIssue.is_current == True)  # noqa: E712
        .all()
    )
    if not active_issues:
        return
    threshold = engage_threshold(current_fit(db))
    live_ids = [i.id for i in active_issues]

    from app.pipeline.analyze.action_center import _embed_texts

    issue_titles = [i.title for i in active_issues]
    issue_embs = np.array(_embed_texts(issue_titles))  # shape (N, D)

    cutoff = datetime.now(UTC) - timedelta(hours=POST_MAX_AGE_HOURS)

    try:
        from atproto import Client
    except ImportError:
        logger.error("atproto not installed — cannot run news engagement")
        return

    client = Client()
    try:
        client.login(handle, app_password)
    except Exception:
        logger.exception("Bluesky login failed in engagement module")
        return

    reposts_this_run = 0
    logger.info(
        "Bluesky engagement: checking %d outlets against %d active issues",
        len(NEWS_OUTLET_HANDLES), len(active_issues),
    )

    for outlet_handle in NEWS_OUTLET_HANDLES:
        if reposts_this_run >= MAX_REPOSTS_PER_RUN:
            break

        try:
            feed_resp = client.get_author_feed(actor=outlet_handle, limit=25, filter="posts_no_replies")
        except Exception:
            logger.warning("Failed to fetch feed for %s", outlet_handle, exc_info=True)
            continue

        n_posts = len(feed_resp.feed or [])
        logger.info("@%s: %d posts in feed", outlet_handle, n_posts)
        if not n_posts:
            logger.warning("@%s returned no posts: a dead or renamed handle reads as nothing to repost",
                           outlet_handle)
        for feed_item in (feed_resp.feed or []):
            if reposts_this_run >= MAX_REPOSTS_PER_RUN:
                break

            # Skip reposts of other accounts' posts
            if getattr(feed_item, "reason", None) is not None:
                continue

            post = feed_item.post
            cid = str(post.cid)
            uri = str(post.uri)

            # Age filter — indexed_at is an ISO string like "2026-06-28T02:34:12.345Z"
            indexed_at = getattr(post, "indexed_at", None)
            post_time = None
            if indexed_at:
                try:
                    post_time = datetime.fromisoformat(str(indexed_at).replace("Z", "+00:00"))
                    if post_time < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass

            if _already_processed(cid, db):
                continue

            text = getattr(post.record, "text", "") or ""
            if not text.strip():
                _mark_processed(cid, db)
                continue

            # Embed post text and find best-matching active issue
            post_emb = _embed_texts([text])[0]
            sims = issue_embs @ post_emb
            best_idx = int(np.argmax(sims))
            best_sim = float(sims[best_idx])

            # Always mark as processed so we don't re-evaluate next run,
            # keeping what the gate saw so the post's link can label it.
            _mark_processed(cid, db, {
                "uri": uri, "outlet": outlet_handle, "link": post_link(post), "sim": best_sim,
                "posted_at": post_time.replace(tzinfo=None) if post_time else None,
                "matched": active_issues[best_idx].id, "live": live_ids,
            })
            calibration_drift.observe("engagement", [best_sim], threshold)

            if best_sim < threshold:
                logger.debug(
                    "@%s post below threshold (%.2f < %.2f): %s",
                    outlet_handle, best_sim, threshold, text[:60],
                )
                continue

            matched_issue = active_issues[best_idx]

            try:
                client.repost(uri=uri, cid=cid)
                client.like(uri=uri, cid=cid)
                reposts_this_run += 1
                logger.info(
                    "Engaged with @%s (sim=%.2f) → issue '%s' | post: %s",
                    outlet_handle, best_sim, matched_issue.title[:50], text[:80],
                )
            except Exception:
                logger.exception("Failed to repost/like post %s from %s", cid, outlet_handle)

    logger.info("Bluesky engagement done: %d repost(s) this run", reposts_this_run)
    try:
        with httpx.Client(timeout=10, follow_redirects=True, headers={"User-Agent": BOT_USER_AGENT}) as http:
            resolve_pending(db, http)
    except Exception:
        db.rollback()
        logger.warning("Engagement: resolving recorded links failed; retried next run", exc_info=True)
