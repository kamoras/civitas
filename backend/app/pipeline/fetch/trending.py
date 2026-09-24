"""Fetch trending topics from public social media sources.

Used by the Action Center to cross-reference news articles with what
people are actually discussing, so issue ranking reflects real public
interest rather than just editorial coverage breadth.

Sources:
  - Google Trends (daily trending searches RSS)
  - Bluesky (getTrendingTopics via AT Protocol, requires BSKY credentials)

Reddit was a third source until 2026-09-24, reading the public
/r/<sub>/hot.json endpoints. Reddit now requires OAuth for datacenter
traffic and returns 403 to everything else: verified from the production
host, with and without a custom User-Agent, on every configured
subreddit. It had been contributing 0 of 30 topics while looking exactly
like a working source. Removed rather than left to fail, because the
platform has no Reddit credentials and an endpoint that always 403s is
not a source.

A source that FAILS must never look like a source with nothing to say —
that is what made the Reddit outage invisible for as long as it was. Each
fetcher therefore returns None for "I could not fetch" and [] for "I
fetched, there was nothing", and fetch_trending_topics reports the
difference.
"""

import logging
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

def _fetch_google_trends() -> list[TrendingTopic] | None:
    """Parse Google Trends daily trending searches RSS.

    None means the fetch failed; [] means it succeeded and had nothing.
    """
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
        return None

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


def _fetch_bluesky_trending() -> list[TrendingTopic] | None:
    """Fetch trending topics from Bluesky via the AT Protocol.

    Uses the same credentials as the Bluesky poster. None when the call
    fails or credentials are absent — an unconfigured source is not a
    source that found nothing; [] when it fetched and had nothing.
    """
    try:
        from app.config import settings
        handle = getattr(settings, "BSKY_HANDLE", "")
        app_password = getattr(settings, "BSKY_APP_PASSWORD", "")
        if not handle or not app_password:
            logger.warning("Bluesky trending skipped — no BSKY credentials configured")
            return None

        from atproto import Client
        client = Client()
        client.login(handle, app_password)
        resp = client.app.bsky.unspecced.get_trending_topics(params={"limit": 20})
        topics_raw = getattr(resp, "topics", []) or []
    except Exception as e:
        logger.warning("Bluesky trending fetch failed: %s", e)
        return None

    topics: list[TrendingTopic] = []
    for t in topics_raw:
        display = getattr(t, "display_name", None) or getattr(t, "topic", None) or ""
        if not display:
            continue
        # Bluesky doesn't expose a numeric traffic score; use 1.0 as a uniform
        # signal weight so these topics influence ranking alongside Google Trends.
        topics.append(TrendingTopic(
            title=display,
            source="bluesky",
            traffic_score=1.0,
        ))

    logger.info("Bluesky: fetched %d trending topics", len(topics))
    return topics


def fetch_trending_topics() -> list[TrendingTopic]:
    """Fetch trending topics from every configured source.

    Returns the combined list sorted by traffic score descending, and
    reports any source that FAILED rather than letting it read as a
    source with nothing to say. Reddit 403'd on every run for an unknown
    stretch while this function logged only a cheerful total, which is
    exactly the failure this reporting exists to prevent.

    A dead source is logged at ERROR and counted on action_metrics, so it
    shows up in the same run counters the Action Center already reports
    instead of needing someone to read the logs.
    """
    from app.pipeline.analyze import action_metrics

    all_topics: list[TrendingTopic] = []
    failed: list[str] = []
    for name, fetch in (("google_trends", _fetch_google_trends),
                        ("bluesky", _fetch_bluesky_trending)):
        topics = fetch()
        if topics is None:
            failed.append(name)
            action_metrics.increment(f"trending_source_failed_{name}")
            continue
        action_metrics.increment(f"trending_topics_{name}", len(topics))
        all_topics.extend(topics)

    if failed:
        logger.error(
            "Trending sources FAILED (not empty — no data was retrieved): %s. "
            "Ranking is running on %d of %d sources.",
            ", ".join(failed), 2 - len(failed), 2,
        )

    all_topics.sort(key=lambda t: t.traffic_score, reverse=True)
    logger.info("Total trending topics: %d (from %d of %d sources)",
                len(all_topics), 2 - len(failed), 2)
    return all_topics
