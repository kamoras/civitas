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
# body that ended from one that was cut (see action_center._split_body_items) —
# which is why NewsArticle.truncated exists rather than a caller re-deriving
# it from length against this one constant: two different caps (this one and
# MAX_FULL_TEXT_CHARS below) can each apply depending on the source.
MAX_SUMMARY_CHARS = 500

# <content:encoded> (the RSS content module, http://purl.org/rss/1.0/modules/
# content/) is meant to carry an item's complete body, distinct from
# <description>'s short teaser — some sources (confirmed live: Politico,
# Roll Call) publish genuine multi-paragraph article text there for free,
# already in their own public syndication feed; no scraping, no ToS
# question. Others (AP, PBS, BBC, The Hill) don't populate it at all, and
# NPR's is only marginally longer than its teaser — this is used whenever
# present and non-empty regardless, since by the module's own spec it's
# never meant to be worse than the teaser. Capped higher than
# MAX_SUMMARY_CHARS since it's real prose, not a one-line blurb, but still
# bounded so one long article can't dominate a cluster's LLM prompt budget.
MAX_FULL_TEXT_CHARS = 3000
_CONTENT_ENCODED_TAG = "{http://purl.org/rss/1.0/modules/content/}encoded"

# MRSS (media:*) and Microsoft's ingestion namespace (mi:*) — confirmed live
# against Roll Call's own feed, the only one of the configured sources that
# carries an explicit, machine-readable redistribution-rights signal.
# <media:content> images are common in WordPress-backed feeds purely as
# attribution metadata (photographer/wire-service credit), which is NOT
# permission to redistribute — only an item whose <mi:hasSyndicationRights>
# is explicitly "1" is used as an issue's image; every other feed either
# lacks media:content entirely or lacks this rights field, and is silently
# skipped by the same check.
_MEDIA_CONTENT_TAG = "{http://search.yahoo.com/mrss/}content"
_SYNDICATION_RIGHTS_TAG = "{http://schemas.ingestion.microsoft.com/common/}hasSyndicationRights"
# <media:text> is the actual photo caption (e.g. "Supreme Court Chief
# Justice John G. Roberts Jr. attends...") — real accessible alt text
# straight from the source, not a generic/empty fallback. <mi:licensorName>
# is the photographer/wire-service credit (e.g. "Tom Williams/CQ Roll
# Call"), sometimes empty even on a rights-cleared item.
_MEDIA_TEXT_TAG = "{http://search.yahoo.com/mrss/}text"
_LICENSOR_NAME_TAG = "{http://schemas.ingestion.microsoft.com/common/}licensorName"


@dataclass
class _RightsClearedImage:
    url: str
    alt: str = ""
    credit: str = ""


def _rights_cleared_image(item: Element) -> "_RightsClearedImage | None":
    for media in item.findall(_MEDIA_CONTENT_TAG):
        rights = media.find(_SYNDICATION_RIGHTS_TAG)
        if rights is not None and (rights.text or "").strip() == "1":
            url = media.get("url", "").strip()
            if not url:
                continue
            text_el = media.find(_MEDIA_TEXT_TAG)
            credit_el = media.find(_LICENSOR_NAME_TAG)
            return _RightsClearedImage(
                url=url,
                alt=(text_el.text or "").strip() if text_el is not None else "",
                credit=(credit_el.text or "").strip() if credit_el is not None else "",
            )
    return None


@dataclass
class NewsArticle:
    title: str
    url: str
    source_name: str
    summary: str = ""
    # True if `summary` was cut at whichever cap applied (MAX_SUMMARY_CHARS
    # for a plain teaser, MAX_FULL_TEXT_CHARS when content:encoded was used)
    # rather than ending where the source text actually ended.
    truncated: bool = False
    published: datetime | None = None
    categories: list[str] = field(default_factory=list)
    # Only ever populated where the source explicitly granted redistribution
    # rights — see _rights_cleared_image. None for every other article.
    image_url: str | None = None
    # The source's own photo caption — real accessible alt text, not a
    # generic fallback. Empty string (not None) when the source supplied
    # no caption for an otherwise rights-cleared image.
    image_alt: str = ""
    # Photographer/wire-service credit, shown alongside the image. Empty
    # string when the source didn't supply one.
    image_credit: str = ""


# Title prefixes for known multi-story digest segments — one feed item
# whose description briefly covers several UNRELATED headlines, unlike
# every other item this pipeline ingests (one item = one story). Live
# 2026-08-25 incident: PBS's "News Wrap: Wildfire forces evacuations near
# Reno" clustered on its wildfire mention (correctly, by itself), but its
# description was "...tens of thousands have been urged to evacuate amid a
# wildfire near Reno... the Supreme Court cleared the way for President
# Trump's mail-in voting order... the Pentagon says it struck a boat in
# the eastern Pacific..." — three unrelated stories in one paragraph. The
# LLM prompt includes an article's full description (see
# action_center._build_llm_prompt), so all three reached the LLM as if
# they were one topic, producing an issue titled "Wildfire evacuations
# near Reno and Indonesia" with Supreme Court and Pentagon facts mixed in.
# Dedicated single-topic PBS articles exist for the same stories (e.g.
# "Supreme Court clears the way for Trump mail voting order ahead of
# midterms") and are ingested normally — this only drops the compilation
# format, not the coverage.
_MULTI_TOPIC_DIGEST_TITLE_PREFIXES = ("news wrap:", "news wrap -")


def _is_multi_topic_digest(title: str) -> bool:
    normalized = title.strip().lower()
    return normalized.startswith(_MULTI_TOPIC_DIGEST_TITLE_PREFIXES)


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
_BLOCK_TAGS = frozenset({
    "p", "div", "li", "ul", "ol", "br", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6",
})
_HTML_BLOCK_RE = re.compile(
    r"<br\s*/?>|</?(?:p|div|li|ul|ol|h[1-6]|blockquote)\b[^>]*>",
    re.IGNORECASE,
)
# A tag name (or "!" for comments/doctypes) must follow the "<", so a
# less-than sign used as prose survives: "<[^>]*>" would swallow the middle
# of "the margin was < 6 > the forecast" as if it were markup.
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z!][^>]*>")
# A source truncated mid-tag (this can only happen to a description that
# reaches storage BEFORE stripping) leaves a tag with no closing ">" for
# _HTML_TAG_RE to match, e.g. "...(AP Photo/Chuck Burton)</s" — same
# letter-must-follow-"<" guard as above so trailing prose like "score < 6"
# survives. Rows ingested in the week before this fix landed needed a
# one-time cleanup for exactly this; long since applied.
_DANGLING_TAG_RE = re.compile(r"</?[a-zA-Z][^<>]*$")

# Block boundaries are marked with a sentinel rather than written as "; "
# straight away, so the cleanup below can tell a boundary WE inserted from
# a semicolon the newsroom actually wrote. Rewriting the latter changes the
# story: "resumed in the U.S.; officials said a deal was near" became
# "resumed in the U.S. officials said a deal was near", which reads as a
# different sentence and reaches the LLM prompt that way. NUL is not legal
# in XML 1.0, so no feed can contain one and collide with this.
_BLOCK_SEP = "\x00"
_SEPARATOR_RUN_RE = re.compile(r"(?:\s*\x00\s*)+")
# A block that already ended in sentence punctuation does not need the
# separator on top of it — "voted Thursday.; Grassley objected" is the
# boundary stated twice. Both forms split identically downstream (the
# action center's item splitter takes ". " and "; " alike), so this is
# purely about the text that reaches the embedding and the LLM prompt.
_REDUNDANT_SEPARATOR_RE = re.compile(r"([.!?])\s*\x00\s*")


def _collapse(text: str) -> str:
    """Normalize whitespace and render inserted block boundaries as "; "."""
    text = " ".join(text.split())  # the sentinel is not whitespace, so it survives
    text = _SEPARATOR_RUN_RE.sub(_BLOCK_SEP, text)
    text = _REDUNDANT_SEPARATOR_RE.sub(r"\1 ", text)
    text = text.replace(_BLOCK_SEP, "; ")
    return text.strip().strip(";").strip()


def _local_name(tag: object) -> str:
    """An element's tag without its "{namespace}" prefix, lowercased."""
    if not isinstance(tag, str):
        return ""  # comments and processing instructions carry a callable tag
    return tag.rsplit("}", 1)[-1].lower()


def _extract_body_text(el: Element | None) -> str:
    """Extract an element's full text, including any nested markup's.

    .text stops at the first child element, which is empty for Atom's
    <content type="xhtml">: there the body is real child elements rather
    than escaped text. That produced the same invisible failure the
    truthiness trap did — an article with a title and no description,
    silently — so descriptions read their text this way. For RSS, where
    the description is CDATA or escaped HTML and therefore childless, this
    is identical to .text.

    Block boundaries become "; " for the same reason _strip_html does it,
    and this is the whole reason a plain "".join(itertext()) is not enough:
    joining the pieces of "<p>He voted Thursday.</p><p>Grassley objected.</p>"
    yields "Thursday.Grassley", which welds two sentences into a token no
    downstream stage can split — the action center's item splitter needs
    whitespace after the period to see a boundary at all.
    """
    if el is None:
        return ""
    parts: list[str] = []

    def walk(node: Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            name = _local_name(child.tag)
            if not name:
                # A comment or processing instruction. Its text is markup,
                # not content ("<!-- tracking beacon -->"), so only the tail
                # — which belongs to the parent's prose — carries on. This
                # parser drops comments today; the extractor should not
                # depend on that to avoid quoting one into an article.
                if child.tail:
                    parts.append(child.tail)
                continue
            is_block = name in _BLOCK_TAGS
            if is_block:
                parts.append(_BLOCK_SEP)
            walk(child)
            if is_block:
                parts.append(_BLOCK_SEP)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return _collapse("".join(parts))


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
    # Unescape BEFORE stripping. The XML parser already decoded one layer,
    # so a feed that escaped its markup twice (a real WordPress pathology)
    # still holds "&lt;p&gt;" here — stripping first would leave literal
    # tag text in the summary that no later stage knows to remove.
    text = unescape(raw)
    text = _HTML_BLOCK_RE.sub(_BLOCK_SEP, text)
    text = _HTML_TAG_RE.sub("", text)
    text = _DANGLING_TAG_RE.sub("", text)
    # "</p><p>" collapsed to ";  ;" above, and a leading/trailing block tag
    # leaves a dangling separator.
    return _collapse(text)


def _resolve_summary(desc: str, full_text: str) -> tuple[str, bool]:
    """(summary, truncated) — prefers full_text (content:encoded) over the
    teaser whenever it's present and non-empty, per the module's own intent
    that it never be worse. Each candidate is checked against its OWN cap
    before slicing, so `truncated` reflects whichever cap actually applied.

    Emptiness is judged AFTER stripping, not on the raw string: some feeds
    populate content:encoded with only an <img>/embed and no prose, which
    is non-empty raw text that strips to nothing — falling back to desc
    in that case instead of returning an empty summary.
    """
    if full_text:
        stripped = _strip_html(full_text)
        if stripped:
            return stripped[:MAX_FULL_TEXT_CHARS], len(stripped) > MAX_FULL_TEXT_CHARS
    if desc:
        stripped = _strip_html(desc)
        return stripped[:MAX_SUMMARY_CHARS], len(stripped) > MAX_SUMMARY_CHARS
    return "", False


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
        desc = _extract_body_text(item.find("description"))
        full_text = _extract_body_text(item.find(_CONTENT_ENCODED_TAG))
        pub_date = _parse_pub_date(_extract_text(item.find("pubDate")))
        categories = [_extract_text(c) for c in item.findall("category") if _extract_text(c)]

        if not title or not link:
            continue
        if pub_date and pub_date < cutoff:
            continue
        if _is_multi_topic_digest(title):
            continue

        summary, truncated = _resolve_summary(desc, full_text)
        image = _rights_cleared_image(item)
        articles.append(NewsArticle(
            title=title,
            url=link,
            source_name=source_name,
            summary=summary,
            truncated=truncated,
            published=pub_date,
            categories=categories,
            image_url=image.url if image else None,
            image_alt=image.alt if image else "",
            image_credit=image.credit if image else "",
        ))

    # Atom entries (fallback for Atom feeds)
    for entry in root.findall(".//atom:entry", ns):
        title = _extract_text(entry.find("atom:title", ns))
        # `or` between two Element results is the ElementTree truthiness
        # trap: an element with no CHILDREN is falsy regardless of its text,
        # so `find(a) or find(b)` always evaluated find(b). <summary> holds
        # text and no children, which meant every Atom entry's description
        # was silently discarded and the article reached the policy filter,
        # the digest detector, and the LLM prompt with a title and nothing
        # else. <link> is childless too, so the rel="alternate" preference
        # never applied either — it only looked right because the first
        # <link> is usually the alternate one.
        link_el = entry.find("atom:link[@rel='alternate']", ns)
        if link_el is None:
            link_el = entry.find("atom:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        # Atom's <summary> is the short synopsis, <content> the full entry
        # body when the feed provides one — same short-teaser/full-text
        # split as RSS's <description>/<content:encoded>, so the same
        # prefer-full-text-when-present resolution applies. Checked
        # independently (not summary_el-falls-back-to-content_el) so a
        # feed carrying both gets the fuller one even when <summary> is
        # also populated, not just when it's missing entirely.
        desc = _extract_body_text(entry.find("atom:summary", ns))
        full_text = _extract_body_text(entry.find("atom:content", ns))
        # <published> is when the story ran; <updated> is when the entry was
        # last touched. Reading <updated> alone made a lightly-edited old
        # story look fresh, and left pub_date None for the many feeds that
        # emit only <published> — which silently exempts the entry from the
        # MAX_ARTICLE_AGE_HOURS cutoff below and sorts it last. (This `or`
        # is between two strings, not two find() results — see the note on
        # the truthiness trap above.)
        pub_date = _parse_pub_date(
            _extract_text(entry.find("atom:published", ns))
            or _extract_text(entry.find("atom:updated", ns))
        )
        # Atom puts the label in @term rather than in the element's text.
        # Populated so the two branches produce the same shape of article:
        # nothing reads categories today, and a field that is silently
        # always-empty for one feed format is how the first reader of it
        # gets a wrong answer with no error.
        categories = [
            term for c in entry.findall("atom:category", ns)
            if (term := (c.get("term") or "").strip())
        ]
        if not title or not link:
            continue
        if pub_date and pub_date < cutoff:
            continue
        if _is_multi_topic_digest(title):
            continue
        summary, truncated = _resolve_summary(desc, full_text)
        image = _rights_cleared_image(entry)
        articles.append(NewsArticle(
            title=title,
            url=link,
            source_name=source_name,
            summary=summary,
            truncated=truncated,
            published=pub_date,
            categories=categories,
            image_url=image.url if image else None,
            image_alt=image.alt if image else "",
            image_credit=image.credit if image else "",
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
            # Count empty descriptions, don't just fetch. A feed whose
            # articles all arrive body-less is the signature of a parse
            # mismatch, and it is otherwise completely silent: the articles
            # still exist, still cluster, still reach the LLM prompt — with
            # a headline and nothing else. That is exactly how the Atom
            # truthiness bug survived, so it gets a log line of its own.
            blank = sum(1 for a in articles if not a.summary)
            logger.info(
                "Fetched %d articles from %s (%.1fs, %d without a description)",
                len(articles), name, elapsed, blank,
            )
            if articles and blank == len(articles):
                logger.warning(
                    "Every article from %s came back with no description — "
                    "the feed's body element is probably not being read",
                    name,
                )
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
