"""Match already-fetched news + freshly-searched Bluesky posts to Races by
candidate-name string match.

Deliberately deterministic string matching, not embedding similarity:
candidate names are specific enough that exact matching is both simpler
and safer (zero false-association risk) than a cosine-similarity
classifier here. Coverage items store the source's own title/summary
verbatim — never LLM-generated — so the on-site feed has no hallucination
surface at all; only the separate Bluesky-posting path (election_bluesky.py)
generates any text, and that text is grounding-checked.
"""

import logging
import re

import httpx
from sqlalchemy.orm import Session

from app.models import Candidate, RaceCoverageItem
from app.pipeline.fetch.bluesky_search import search_posts
from app.pipeline.fetch.news_feeds import fetch_news_articles

logger = logging.getLogger(__name__)

# Below this length a surname is too likely to produce false-positive
# matches in general news/social text ("OZ", "LEE", "ROE") to trust alone.
MIN_SURNAME_LENGTH = 4


def _surname(name: str) -> str:
    """FEC candidate names are "LAST, FIRST MIDDLE" — the surname is what
    news coverage and social posts actually use, not the full FEC string."""
    return name.split(",")[0].strip()


def _name_pattern(surname: str) -> "re.Pattern[str] | None":
    if len(surname) < MIN_SURNAME_LENGTH:
        return None
    return re.compile(r"\b" + re.escape(surname) + r"\b", re.IGNORECASE)


def _already_ingested(db: Session, race_id: str, url: str) -> bool:
    return (
        db.query(RaceCoverageItem.id)
        .filter(RaceCoverageItem.race_id == race_id, RaceCoverageItem.url == url)
        .first()
        is not None
    )


def _races_by_surname(db: Session) -> dict[str, tuple["re.Pattern[str]", list[str]]]:
    """surname -> (compiled pattern, [race_id, ...]) — a surname can belong
    to more than one candidate across different races (e.g. multiple
    candidates named "Smith" nationally); grouping means a matching
    article/post gets attached to every race that name genuinely belongs
    to, and the search itself only runs once per unique surname."""
    grouped: dict[str, list[str]] = {}
    for name, race_id in db.query(Candidate.name, Candidate.race_id).all():
        if not name:
            continue
        surname = _surname(name)
        if _name_pattern(surname) is None:
            continue
        grouped.setdefault(surname, []).append(race_id)

    return {
        surname: (_name_pattern(surname), race_ids)
        for surname, race_ids in grouped.items()
    }


def _store_if_new(db: Session, race_id: str, **fields) -> bool:
    if _already_ingested(db, race_id, fields["url"]):
        return False
    db.add(RaceCoverageItem(race_id=race_id, **fields))
    return True


async def ingest_race_coverage(db: Session, client: httpx.AsyncClient) -> int:
    """Match existing RSS articles + fresh Bluesky search results to races
    by candidate-surname match. Returns the number of NEW coverage items
    stored (a url already ingested for that race is skipped, not
    duplicated).
    """
    by_surname = _races_by_surname(db)
    if not by_surname:
        return 0

    ingested = 0

    # ── News: re-classifies articles the Action Center already fetched
    # (fetch_news_articles is cheap/idempotent — it hits the same RSS
    # feeds news_feeds.py always has, no new source added here) ──
    articles = fetch_news_articles()
    for article in articles:
        haystack = f"{article.title} {article.summary}"
        for pattern, race_ids in by_surname.values():
            if not pattern.search(haystack):
                continue
            for race_id in race_ids:
                if _store_if_new(
                    db, race_id,
                    source_type="news", source_name=article.source_name,
                    title=article.title, url=article.url,
                    summary=article.summary, published_at=article.published,
                ):
                    ingested += 1

    # ── Bluesky: one search per unique surname ──
    for surname, (pattern, race_ids) in by_surname.items():
        for post in search_posts(surname):
            if not pattern.search(post.text):
                continue
            for race_id in race_ids:
                if _store_if_new(
                    db, race_id,
                    source_type="bluesky", source_name=f"@{post.author_handle}",
                    title=post.text[:200], url=post.url,
                    summary=post.text, author=post.author_handle,
                    published_at=post.published,
                ):
                    ingested += 1

    if ingested:
        db.commit()
    return ingested
