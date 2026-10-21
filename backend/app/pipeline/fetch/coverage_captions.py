"""Stored race coverage whose summary opens with a photo caption, found and
repaired from the article's own page.

Until 2026-10-21 the feed parser read a WordPress feed's lead
<figure><figcaption> as the article's first sentence (news_feeds.
_FIGURE_ELEMENTS): 632 of 2,522 stored race-coverage summaries opened with a
caption, and race posts quoted twelve of them as the news. Items ingested
since are caption-free by construction (`caption_checked_at` set at ingest),
and an article still in its feed is re-read by the ingest. The rest are no
longer in any feed, so their markup is gone; this re-reads each one's page.

A caption is told by the page's own markup, never its wording: the text of
its <figcaption> elements and the `caption` of every image in its JSON-LD
(the featured image a States Newsroom feed puts in its figure; their article
pages render it without a <figcaption>). An item whose summary opens with
one gets the page's own description (og:description, else the meta
description: the publisher's excerpt, which is the feed's <description>) in
its place, is rescored (relevance NULL until the next ingest scores it), and
is marked verified. One that doesn't is marked verified as it stands.

Failure is never clean. A page that can't be read, or that has a caption and
no description to put in its place, keeps its summary and stays unverified,
to be tried on a later night (a read page is cached for PAGE_TTL_HOURS, so a
page without a description isn't fetched again every night).

Bounded: at most MAX_PER_NIGHT pages a night at RPS, newest items first, so
the backlog spreads over a few nights and a night's pass is minutes.
"""
import json
import logging
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser

from sqlalchemy.orm import Session

from app.contact import BOT_USER_AGENT
from app.http_client import make_async_client
from app.models import RaceCoverageItem
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.pipeline.fetch.news_feeds import MAX_SUMMARY_CHARS, _strip_html
from app.pipeline.rate_limiter import RateLimiter
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# Pages a night, and how fast. Measured 2026-10-21: 1,301 stored news items
# (1,300 pages), all unverified when this shipped. At 400 a night the
# backlog is read in four nights, newest first, the 632 caption-led items in
# the first three (248, 242, 142), and a night's pass is under seven minutes
# at one a second.
MAX_PER_NIGHT = 400
RPS = 1.0
_LIMITER = RateLimiter(RPS, name="coverage_captions")

CACHE_TIER = "article_captions"
# A read page's captions and description, kept a week: a page with a caption
# and no description is tried again weekly, not nightly.
PAGE_TTL_HOURS = 24 * 7
# A page that couldn't be read is left alone this long before it is tried
# again, so a walled or dead site's items don't take every night's budget
# from the rest. They stay unverified meanwhile. On 2026-10-21, 413 items'
# pages refused this User-Agent (The Hill, Politico, AP, Roll Call: bot
# walls, which are not worked around). Their feeds put no figure in the
# text: 631 of the 632 caption-led items have a readable page and a
# description to replace the caption with.
UNREADABLE_TIER = "article_captions_unreadable"
RETRY_UNREADABLE_HOURS = 24 * 7

# A summary opens with a caption when the two share at least this many
# leading characters (or the whole caption, if it is shorter). The page's
# JSON-LD caption and the feed's <figcaption> can differ after the first
# sentence (a credit moved, a full stop dropped), so the whole caption is
# not required.
MIN_SHARED_PREFIX = 40
# Or when the summary opens with the whole caption before its parenthesised
# credit (an abbreviation like "Rep." rules out splitting on full stops), if
# at least this long: "Rep. Pat Doe responds to questions." is shorter than
# the prefix rule but is still the caption.
MIN_FIRST_SENTENCE = 15
_FIRST_SENTENCE_END = re.compile(r" \(")


@dataclass
class PageText:
    captions: list[str]
    description: str | None


class _PageParser(HTMLParser):
    """<figcaption> text, JSON-LD blocks and the description meta tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.captions: list[str] = []
        self.ld_json: list[str] = []
        self.meta: dict[str, str] = {}
        self._figcaption_depth = 0
        self._in_ld = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "figcaption":
            if self._figcaption_depth == 0:
                self._buf = []
            self._figcaption_depth += 1
        elif tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_ld = True
            self._buf = []
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in ("og:description", "description") and a.get("content"):
                self.meta.setdefault(key, a["content"])

    def handle_endtag(self, tag):
        if tag == "figcaption" and self._figcaption_depth:
            self._figcaption_depth -= 1
            if self._figcaption_depth == 0:
                self.captions.append(" ".join("".join(self._buf).split()))
        elif tag == "script" and self._in_ld:
            self._in_ld = False
            self.ld_json.append("".join(self._buf))

    def handle_data(self, data):
        if self._figcaption_depth or self._in_ld:
            self._buf.append(data)


def _ld_captions(node) -> list[str]:
    """Every `caption` string in a JSON-LD document."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "caption" and isinstance(value, str):
                found.append(_strip_html(value))
            else:
                found += _ld_captions(value)
    elif isinstance(node, list):
        for value in node:
            found += _ld_captions(value)
    return found


def page_text(html: str) -> PageText:
    parser = _PageParser()
    parser.feed(html)
    captions = list(parser.captions)
    for block in parser.ld_json:
        try:
            captions += _ld_captions(json.loads(block))
        except ValueError:
            continue
    description = parser.meta.get("og:description") or parser.meta.get("description")
    description = _strip_html(unescape(description)).strip() if description else None
    return PageText(captions=[c for c in captions if c], description=description or None)


def _norm(text: str) -> str:
    text = unescape(text or "")
    for curly, plain in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), (" ", " ")):
        text = text.replace(curly, plain)
    return " ".join(text.split()).lower()


def opens_with_caption(summary: str, captions: list[str]) -> bool:
    s = _norm(summary)
    for caption in captions:
        c = _norm(caption)
        if not c:
            continue
        shared = 0
        for a, b in zip(s, c):
            if a != b:
                break
            shared += 1
        if shared >= min(len(c), MIN_SHARED_PREFIX):
            return True
        first = _FIRST_SENTENCE_END.split(c, maxsplit=1)[0]
        if len(first) >= MIN_FIRST_SENTENCE and shared >= len(first):
            return True
    return False


def _cached_page(db: Session, url: str) -> PageText | None:
    cached = api_cache_get(db, CACHE_TIER, url, max_age_hours=PAGE_TTL_HOURS)
    return PageText(captions=cached["captions"], description=cached["description"]) if cached else None


async def _read_page(client, db: Session, url: str) -> PageText | None:
    """The page's captions and description; None when it can't be read (a
    failed request, an error status or a bot wall's challenge), which is
    recorded so the page waits RETRY_UNREADABLE_HOURS before another try."""
    resp = await fetch_with_retry(
        client, _LIMITER, "GET", url, retries=2, retry_on_4xx=False, log_label="coverage caption check",
    )
    if resp is None or not resp.text:
        api_cache_set(db, UNREADABLE_TIER, url, {"failed_at": utcnow().isoformat()},
                      normal_ttl_hours=RETRY_UNREADABLE_HOURS)
        return None
    text = page_text(resp.text)
    api_cache_set(db, CACHE_TIER, url, {"captions": text.captions, "description": text.description},
                  normal_ttl_hours=PAGE_TTL_HOURS)
    return text


def _unverified(db: Session):
    return db.query(RaceCoverageItem).filter(
        RaceCoverageItem.caption_checked_at.is_(None), RaceCoverageItem.source_type == "news",
    )


async def recheck_stored_captions(db: Session, *, limit: int = MAX_PER_NIGHT, deadline=None) -> dict:
    """One night's pass: at most `limit` page requests, newest items first.
    Returns counts: pages requested, items fixed, items verified as they
    stood, items whose page couldn't be read, caption with no description,
    and how many unverified items remain."""
    by_url: dict[str, list[RaceCoverageItem]] = {}
    for row in _unverified(db).order_by(RaceCoverageItem.fetched_at.desc(), RaceCoverageItem.id.desc()):
        by_url.setdefault(row.url, []).append(row)
    counts = {"pages": 0, "fixed": 0, "clean": 0, "unreadable": 0, "no_description": 0}
    async with make_async_client(headers={"User-Agent": BOT_USER_AGENT}) as client:
        for url, items in by_url.items():
            if deadline is not None and deadline():
                break
            page = _cached_page(db, url)
            if page is None:
                if api_cache_get(db, UNREADABLE_TIER, url, max_age_hours=RETRY_UNREADABLE_HOURS) is not None:
                    continue  # failed recently; its turn comes again later
                if counts["pages"] >= limit:
                    break
                counts["pages"] += 1
                page = await _read_page(client, db, url)
            if page is None:
                counts["unreadable"] += len(items)
                db.commit()  # the marker; nothing held across the next fetch
                continue
            replacement = (page.description or "")[:MAX_SUMMARY_CHARS]
            if replacement and opens_with_caption(replacement, page.captions):
                replacement = ""  # a description that is itself the caption
            for item in items:
                if not opens_with_caption(item.summary or "", page.captions):
                    item.caption_checked_at = utcnow()
                    counts["clean"] += 1
                elif replacement:
                    logger.info("Coverage item %s opened with a photo caption; replaced with the page's description", item.id)
                    item.summary = replacement
                    item.relevance = None
                    item.caption_checked_at = utcnow()
                    counts["fixed"] += 1
                else:
                    counts["no_description"] += 1
            db.commit()
    counts["remaining"] = _unverified(db).count()
    logger.info("Coverage caption re-check: %s", counts)
    return counts
