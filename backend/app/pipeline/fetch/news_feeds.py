"""Fetch articles from low-bias news RSS feeds for the Action Center.

Sources chosen for factual reporting and minimal partisan lean:
  - AP News (via RSS)
  - NPR Politics / World
  - PBS NewsHour
  - BBC World News (direct URLs, no redirect wrapping)

Each source is fetched independently; failures are logged and skipped
so the system degrades gracefully if a feed goes down.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

from xml.etree.ElementTree import Element

from defusedxml import ElementTree as SafeET

import httpx

logger = logging.getLogger(__name__)

FEED_TIMEOUT = 15.0
MAX_ARTICLE_AGE_HOURS = 48
# Descriptions are capped here, which routinely cuts mid-sentence. Consumers
# that reason about a description's STRUCTURE need to know the cap to tell a
# body that ended from one that was cut (see action_center._split_body_items).
MAX_SUMMARY_CHARS = 500


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
    # Both NPR feeds share ONE source name (2026-07 fix): source-name
    # counts drive the action center's coverage-breadth ranking signal and
    # the National Monitor unique-source promotion bar, and two feeds from
    # the same newsroom counting as two independent sources gave any topic
    # NPR runs on both its politics and world desks a systematic ranking
    # bonus. Same articles, same dedup — only the attribution is unified.
    {
        "name": "NPR",
        "url": "https://feeds.npr.org/1014/rss.xml",
    },
    {
        "name": "NPR",
        "url": "https://feeds.npr.org/1004/rss.xml",
    },
    {
        "name": "PBS NewsHour",
        "url": "https://www.pbs.org/newshour/feeds/rss/headlines",
    },
    {
        "name": "BBC World",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
    },
    {
        "name": "The Hill",
        "url": "https://thehill.com/rss/syndicator/19110",
    },
    {
        "name": "Politico",
        "url": "https://rss.politico.com/congress.xml",
    },
    {
        "name": "Roll Call",
        "url": "https://rollcall.com/feed/",
    },
]


def _parse_pub_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    parsed: datetime | None = None
    try:
        parsed = parsedate_to_datetime(raw)
    except Exception:
        pass
    if parsed is None:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    # parsedate_to_datetime returns a NAIVE datetime for "-0000"-style
    # zones; comparing that against the aware cutoff raised TypeError
    # inside _parse_rss_feed, which the caller's blanket except logged as
    # "Failed to fetch feed" — one malformed item silently dropped the
    # entire source. Treat naive as UTC.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _extract_text(el: Element | None) -> str:
    """Extract text from an XML element, stripping CDATA."""
    if el is None:
        return ""
    return (el.text or "").strip()


# Block-level markup carries an item boundary the plain text does not: a
# WordPress feed's <li> or </p> is where one thought ends, and dropping it
# silently welds two sentences together. Mapped to "; " so the boundary
# survives as punctuation.
_HTML_BLOCK_RE = re.compile(
    r"<br\s*/?>|</?(?:p|div|li|ul|ol|h[1-6]|blockquote)\b[^>]*>",
    re.IGNORECASE,
)
_HTML_TAG_RE = re.compile(r"<[^>]*>")
_HTML_SEPARATOR_RUN_RE = re.compile(r"(?:\s*;\s*)+")


def _strip_html(raw: str) -> str:
    """Plain text from a feed description that arrived as HTML.

    Several feeds here are WordPress-backed (Roll Call, The Hill) and put
    real markup in <description> — paragraph tags, tracking pixels, an
    <img> lead. Left in, that markup reaches three places that all read the
    description as prose: the policy-relevance embedding (which scores
    ``title. summary[:200]`` — for an image-led item that budget is spent
    entirely on markup), the LLM prompt for issue generation, and the
    digest detector's entity extraction, where a filename like
    "Trump-Rally.jpg" reads as a named entity that appears in no other
    sentence. Tags are removed rather than escaped because none of those
    three consumers renders HTML.
    """
    if "<" not in raw and "&" not in raw:
        return raw
    text = _HTML_BLOCK_RE.sub("; ", raw)
    text = _HTML_TAG_RE.sub("", text)
    text = unescape(text)
    text = " ".join(text.split())
    # "</p><p>" collapsed to ";  ;" above, and a leading/trailing block tag
    # leaves a dangling separator.
    text = _HTML_SEPARATOR_RUN_RE.sub("; ", text)
    return text.strip(" ;")


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
            summary=_strip_html(desc)[:MAX_SUMMARY_CHARS] if desc else "",
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
            summary=_strip_html(desc)[:MAX_SUMMARY_CHARS] if desc else "",
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

    # Drop opinion/editorial/blog pieces — these carry partisan framing that
    # pollutes the action center.  Match on URL path segments that news outlets
    # use to categorise non-news content.
    _OPINION_PATH_SEGMENTS = frozenset({
        "/opinion/", "/opinions/", "/blogs/", "/blog/",
        "/commentary/", "/op-ed/", "/editorial/", "/editorials/",
        "/column/", "/columns/", "/contributor/", "/contributors/",
    })
    def _is_opinion(url: str) -> bool:
        from urllib.parse import urlparse
        path = urlparse(url).path.lower()
        return any(seg in path for seg in _OPINION_PATH_SEGMENTS)

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique: list[NewsArticle] = []
    opinion_dropped = 0
    for a in all_articles:
        if a.url not in seen_urls:
            seen_urls.add(a.url)
            if _is_opinion(a.url):
                opinion_dropped += 1
            else:
                unique.append(a)

    if opinion_dropped:
        logger.info("Dropped %d opinion/editorial articles", opinion_dropped)

    unique.sort(key=lambda a: a.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    logger.info("Total unique articles: %d from %d feeds", len(unique), len(feeds))
    return unique
