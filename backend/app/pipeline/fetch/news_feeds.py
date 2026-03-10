"""Fetch articles from low-bias news RSS feeds for the Action Center.

Sources chosen for factual reporting and minimal partisan lean:
  - AP News (via RSS)
  - NPR Politics
  - PBS NewsHour Politics
  - Reuters (via RSS)

Each source is fetched independently; failures are logged and skipped
so the system degrades gracefully if a feed goes down.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET

import httpx

logger = logging.getLogger(__name__)

FEED_TIMEOUT = 15.0
MAX_ARTICLE_AGE_HOURS = 48


@dataclass
class NewsArticle:
    title: str
    url: str
    source_name: str
    summary: str = ""
    published: datetime | None = None
    categories: list[str] = field(default_factory=list)


NEWS_FEEDS: list[dict[str, str]] = [
    {
        "name": "AP News",
        "url": "https://feedx.net/rss/ap.xml",
    },
    {
        "name": "NPR Politics",
        "url": "https://feeds.npr.org/1014/rss.xml",
    },
    {
        "name": "Reuters",
        "url": "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "PBS NewsHour",
        "url": "https://www.pbs.org/newshour/feeds/rss/headlines",
    },
]


def _parse_pub_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_text(el: Element | None) -> str:
    """Extract text from an XML element, stripping CDATA."""
    if el is None:
        return ""
    return (el.text or "").strip()


def _parse_rss_feed(xml_bytes: bytes, source_name: str) -> list[NewsArticle]:
    """Parse RSS 2.0 / Atom XML into NewsArticle objects."""
    articles: list[NewsArticle] = []
    try:
        root = SafeET.fromstring(xml_bytes)
    except Exception as e:
        logger.warning("XML parse error for %s: %s", source_name, e)
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_ARTICLE_AGE_HOURS)

    # RSS 2.0 items
    for item in root.iter("item"):
        title = _extract_text(item.find("title"))
        link = _extract_text(item.find("link"))
        desc = _extract_text(item.find("description"))
        pub_date = _parse_pub_date(_extract_text(item.find("pubDate")))
        categories = [_extract_text(c) for c in item.findall("category") if _extract_text(c)]

        if not title or not link:
            continue
        if pub_date and pub_date < cutoff:
            continue

        articles.append(NewsArticle(
            title=title,
            url=link,
            source_name=source_name,
            summary=desc[:500] if desc else "",
            published=pub_date,
            categories=categories,
        ))

    # Atom entries (fallback for Atom feeds)
    for entry in root.findall(".//atom:entry", ns):
        title = _extract_text(entry.find("atom:title", ns))
        link_el = entry.find("atom:link[@rel='alternate']", ns) or entry.find("atom:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        summary_el = entry.find("atom:summary", ns) or entry.find("atom:content", ns)
        desc = _extract_text(summary_el)
        pub_date = _parse_pub_date(_extract_text(entry.find("atom:updated", ns)))
        if not title or not link:
            continue
        if pub_date and pub_date < cutoff:
            continue
        articles.append(NewsArticle(
            title=title,
            url=link,
            source_name=source_name,
            summary=desc[:500] if desc else "",
            published=pub_date,
        ))

    return articles


def fetch_news_articles(
    feeds: list[dict[str, str]] | None = None,
) -> list[NewsArticle]:
    """Fetch articles from all configured RSS feeds.

    Returns deduplicated list of recent articles sorted newest-first.
    """
    feeds = feeds or NEWS_FEEDS
    all_articles: list[NewsArticle] = []

    for feed_info in feeds:
        name = feed_info["name"]
        url = feed_info["url"]
        t0 = time.perf_counter()
        try:
            resp = httpx.get(url, timeout=FEED_TIMEOUT, follow_redirects=True, headers={
                "User-Agent": "Civitas/1.0 (civic engagement platform)",
            })
            resp.raise_for_status()
            articles = _parse_rss_feed(resp.content, name)
            elapsed = time.perf_counter() - t0
            logger.info("Fetched %d articles from %s (%.1fs)", len(articles), name, elapsed)
            all_articles.extend(articles)
        except Exception as e:
            logger.warning("Failed to fetch feed %s: %s", name, e)

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique: list[NewsArticle] = []
    for a in all_articles:
        if a.url not in seen_urls:
            seen_urls.add(a.url)
            unique.append(a)

    unique.sort(key=lambda a: a.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    logger.info("Total unique articles: %d from %d feeds", len(unique), len(feeds))
    return unique
