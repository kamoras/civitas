"""Fetch trending topics from public social media sources.

Used by the Action Center to cross-reference news articles with what
people are actually discussing, so issue ranking reflects real public
interest rather than just editorial coverage breadth.

Sources:
  - Google Trends (daily trending searches RSS)
  - Reddit (top posts from policy-relevant subreddits via public JSON)
  - Bluesky (getTrendingTopics via AT Protocol, requires BSKY credentials)
"""

import logging
import time
from dataclasses import dataclass

from defusedxml import ElementTree as SafeET

import httpx

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 15.0


@dataclass
class TrendingTopic:
    title: str
    source: str
    traffic_score: float = 0.0


_GOOGLE_TRENDS_RSS = (
    "https://trends.google.com/trending/rss?geo=US"
)

_REDDIT_SUBREDDITS = [
    "politics",
    "news",
    "neutralpolitics",
    "uspolitics",
]

_REDDIT_HEADERS = {
    "User-Agent": "Civitas/1.0 (civic engagement platform; educational use)",
}


def _fetch_google_trends() -> list[TrendingTopic]:
    """Parse Google Trends daily trending searches RSS."""
    topics: list[TrendingTopic] = []
    try:
        resp = httpx.get(
            _GOOGLE_TRENDS_RSS,
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "Civitas/1.0"},
        )
        resp.raise_for_status()
        root = SafeET.fromstring(resp.content)
    except Exception as e:
        logger.warning("Google Trends fetch failed: %s", e)
        return []

    ns = {"ht": "https://trends.google.com/trending/rss"}

    for item in root.iter("item"):
        title_el = item.find("title")
        if title_el is None or not (title_el.text or "").strip():
            continue
        title = (title_el.text or "").strip()

        traffic = 0.0
        traffic_el = item.find("ht:approx_traffic", ns)
        if traffic_el is not None and traffic_el.text:
            raw = traffic_el.text.strip().replace(",", "").replace("+", "")
            try:
                traffic = float(raw)
            except ValueError:
                pass

        topics.append(TrendingTopic(
            title=title,
            source="google_trends",
            traffic_score=traffic,
        ))

    logger.info("Google Trends: fetched %d trending topics", len(topics))
    return topics


def _fetch_reddit_trending() -> list[TrendingTopic]:
    """Fetch top post titles from policy-relevant subreddits."""
    topics: list[TrendingTopic] = []
    seen_titles: set[str] = set()

    for sub in _REDDIT_SUBREDDITS:
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit=15"
        try:
            resp = httpx.get(
                url,
                timeout=FETCH_TIMEOUT,
                follow_redirects=True,
                headers=_REDDIT_HEADERS,
            )
            if resp.status_code == 429:
                logger.warning("Reddit rate-limited on r/%s, skipping", sub)
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning("Reddit r/%s fetch failed: %s", sub, e)
            continue

        posts = data.get("data", {}).get("children", [])
        for post in posts:
            pd = post.get("data", {})
            title = pd.get("title", "").strip()
            if not title or title.lower() in seen_titles:
                continue
            if pd.get("stickied", False):
                continue

            score = float(pd.get("score", 0))
            seen_titles.add(title.lower())
            topics.append(TrendingTopic(
                title=title,
                source=f"reddit_r/{sub}",
                traffic_score=score,
            ))

        time.sleep(0.5)

    logger.info("Reddit: fetched %d trending topics across %d subreddits",
                len(topics), len(_REDDIT_SUBREDDITS))
    return topics


def _fetch_bluesky_trending() -> list[TrendingTopic]:
    """Fetch trending topics from Bluesky via the AT Protocol.

    Uses the same credentials as the Bluesky poster. Returns empty list
    if credentials aren't configured or the call fails.
    """
    try:
        from app.config import settings
        handle = getattr(settings, "BSKY_HANDLE", "")
        app_password = getattr(settings, "BSKY_APP_PASSWORD", "")
        if not handle or not app_password:
            return []

        from atproto import Client
        client = Client()
        client.login(handle, app_password)
        resp = client.app.bsky.unspecced.get_trending_topics(params={"limit": 20})
        topics_raw = getattr(resp, "topics", []) or []
    except Exception as e:
        logger.warning("Bluesky trending fetch failed: %s", e)
        return []

    topics: list[TrendingTopic] = []
    for t in topics_raw:
        display = getattr(t, "display_name", None) or getattr(t, "topic", None) or ""
        if not display:
            continue
        # Bluesky doesn't expose a numeric traffic score; use 1.0 as a uniform
        # signal weight so these topics influence ranking alongside Reddit/Trends.
        topics.append(TrendingTopic(
            title=display,
            source="bluesky",
            traffic_score=1.0,
        ))

    logger.info("Bluesky: fetched %d trending topics", len(topics))
    return topics


def fetch_trending_topics() -> list[TrendingTopic]:
    """Fetch trending topics from all social media sources.

    Returns combined list sorted by traffic score descending.
    """
    all_topics: list[TrendingTopic] = []

    all_topics.extend(_fetch_google_trends())
    all_topics.extend(_fetch_reddit_trending())
    all_topics.extend(_fetch_bluesky_trending())

    all_topics.sort(key=lambda t: t.traffic_score, reverse=True)
    logger.info("Total trending topics: %d", len(all_topics))
    return all_topics
