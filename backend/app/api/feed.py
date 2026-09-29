"""The Atom feeds: every post Civitas publishes (app/broadcast.py), for
anyone to read with anything that reads a feed.

Public at `/feed.xml` (everything), `/feed/<topic>.xml` (broadcast.FEEDS)
and `/feed/states/<ST>.xml` (posts about one state: its races and its
members' spotlights). nginx maps those onto these routes and caches them;
Next's rewrites do the same where nginx isn't in front (`next dev`).

A feed is pulled, so serving one learns nothing about who reads it beyond
what any page request does, and nothing here records a request
(AGENTS.md §8). Picking a topic or a state is the reader's filter, carried
in the URL they subscribe to.

Read path only: one indexed query, no model.
"""
import hashlib
import re
from datetime import datetime
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.response_helpers import CACHE_TTL_LIST_S, cached_json
from app.broadcast import FEEDS, KINDS, SITE_URL, withdrawn_subjects
from app.database import get_db
from app.models import BroadcastPost
from app.services.senator_service import STATE_NAMES

router = APIRouter()

ATOM_NS = "http://www.w3.org/2005/Atom"
ATOM_MEDIA_TYPE = "application/atom+xml; charset=utf-8"

# Entries per feed. A reader polling every few minutes never misses one: the
# busiest day on record was well under this across every kind combined.
FEED_LENGTH = 50

# The feed's `updated` before its first entry: the day feeds launched, so
# an empty feed still validates and never claims to change.
_EMPTY_FEED_UPDATED = "2026-09-29T00:00:00Z"

# XML 1.0 forbids these outright; one in a news source's text must not
# make the whole feed unparseable.
_XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def feed_path(slug: str) -> str:
    """Public path of a topic feed."""
    return "/feed.xml" if slug == "all" else f"/feed/{slug}.xml"


def state_feed_path(state: str) -> str:
    return f"/feed/states/{state}.xml"


def _clean(text: str | None) -> str:
    return _XML_INVALID.sub("", text or "")


def _rfc3339(moment: datetime) -> str:
    """Stored timestamps are naive UTC (app/time_utils.py)."""
    return moment.replace(microsecond=0).isoformat() + "Z"


def _sub(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    el = ET.SubElement(parent, tag, attrs)
    if text is not None:
        el.text = _clean(text)
    return el


def render_atom(*, title: str, subtitle: str, path: str, posts: list[BroadcastPost]) -> bytes:
    """An Atom 1.0 document (RFC 4287) for `posts`, newest first."""
    self_url = f"{SITE_URL}{path}"
    feed = ET.Element("feed", {"xmlns": ATOM_NS})
    _sub(feed, "id", self_url)
    _sub(feed, "title", title)
    _sub(feed, "subtitle", subtitle)
    _sub(feed, "link", rel="self", type="application/atom+xml", href=self_url)
    _sub(feed, "link", rel="alternate", type="text/html", href=f"{SITE_URL}/")
    _sub(feed, "updated", _rfc3339(posts[0].published_at) if posts else _EMPTY_FEED_UPDATED)
    author = _sub(feed, "author")
    _sub(author, "name", "Civitas")
    _sub(author, "uri", SITE_URL)
    _sub(feed, "icon", f"{SITE_URL}/icon.svg")
    for post in posts:
        entry = _sub(feed, "entry")
        # A tag URI on the row id, which is never reused: stable across
        # edits to the site's URLs, unique across every feed.
        _sub(entry, "id", f"tag:civitas-research.org,2026:post/{post.id}")
        _sub(entry, "title", post.title)
        _sub(entry, "link", rel="alternate", type="text/html", href=post.url)
        stamp = _rfc3339(post.published_at)
        _sub(entry, "published", stamp)
        _sub(entry, "updated", stamp)
        _sub(entry, "category", term=post.kind, label=KINDS.get(post.kind, post.kind))
        _sub(entry, "content", post.text, type="text")
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(feed, encoding="utf-8")


def _matches(if_none_match: str | None, etag: str) -> bool:
    """If-None-Match's weak comparison (RFC 9110 §13.1.2): any listed tag,
    with or without its W/ prefix, or "*". A client given this feed through
    nginx's gzip holds the weak form of the tag."""
    if not if_none_match:
        return False
    tags = {t.strip().removeprefix("W/") for t in if_none_match.split(",")}
    return "*" in tags or etag in tags


def _atom_response(request: Request, body: bytes, posts: list[BroadcastPost]) -> Response:
    """The feed with validators, so a poller that sends them back gets a 304
    instead of the document (nginx answers these from its cache too)."""
    etag = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
    headers = {
        "Cache-Control": f"public, max-age={CACHE_TTL_LIST_S}, stale-while-revalidate={CACHE_TTL_LIST_S}",
        "ETag": etag,
    }
    if posts:
        headers["Last-Modified"] = posts[0].published_at.strftime("%a, %d %b %Y %H:%M:%S GMT")
    if _matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type=ATOM_MEDIA_TYPE, headers=headers)


def _newest(db: Session, *filters) -> list[BroadcastPost]:
    """The newest posts matching `filters`, leaving out any about a
    withdrawn issue: its page answers 410, and the feed must not keep
    serving what Civitas retracted."""
    return (
        db.query(BroadcastPost)
        .filter(*filters, BroadcastPost.subject.notin_(withdrawn_subjects()))
        .order_by(BroadcastPost.published_at.desc(), BroadcastPost.id.desc())
        .limit(FEED_LENGTH)
        .all()
    )


@router.get("/feeds")
def feed_index():
    """Every feed, for the page that lists them (`/feeds`)."""
    return cached_json({
        "feeds": [
            {"slug": f.slug, "title": f.title, "description": f.description, "path": feed_path(f.slug)}
            for f in FEEDS.values()
        ],
        "states": [
            {"code": code, "name": name, "path": state_feed_path(code)}
            for code, name in sorted(STATE_NAMES.items(), key=lambda kv: kv[1])
        ],
    }, max_age=CACHE_TTL_LIST_S)


# HEAD, which some feed readers send before a GET, is answered by the same
# function; registered apart from the GET so the schema lists each once.
@router.get("/feed/states/{name}")
def state_feed(name: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """Posts about one state: its races and its members' spotlights."""
    code = name.removesuffix(".xml").upper()
    if not name.endswith(".xml") or code not in STATE_NAMES:
        raise HTTPException(status_code=404, detail="No such feed")
    posts = _newest(db, BroadcastPost.state == code)
    body = render_atom(
        title=f"Civitas: {STATE_NAMES[code]}",
        subtitle=f"Civitas posts about {STATE_NAMES[code]}: its races on the ballot and its members of Congress.",
        path=state_feed_path(code),
        posts=posts,
    )
    return _atom_response(request, body, posts)


@router.get("/feed/{name}")
def topic_feed(name: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """`all.xml` (everything) or one topic's feed."""
    slug = name.removesuffix(".xml")
    feed = FEEDS.get(slug)
    if not name.endswith(".xml") or feed is None:
        raise HTTPException(status_code=404, detail="No such feed")
    posts = _newest(db, BroadcastPost.kind.in_(feed.kinds))
    body = render_atom(title=feed.title, subtitle=feed.description, path=feed_path(slug), posts=posts)
    return _atom_response(request, body, posts)


router.add_api_route("/feed/states/{name}", state_feed, methods=["HEAD"], include_in_schema=False)
router.add_api_route("/feed/{name}", topic_feed, methods=["HEAD"], include_in_schema=False)
