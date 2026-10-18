"""The Atom feeds: every post Civitas publishes (app/broadcast.py), for
anyone to read with anything that reads a feed.

Public at `/feed.xml` (everything), `/feed/<topic>.xml` (broadcast.FEEDS)
and `/feed/states/<ST>.xml` (posts about one state: its races and its
members' spotlights). One followed thing has a feed too, built from stored
rows when asked for (app/entity_feeds.py): `/feed/members/<id>.xml`,
`/feed/bills/<congress>/<bill>.xml`, `/feed/agencies/<slug>.xml` and
`/feed/monitors/<slug>.xml`. nginx maps those onto these routes and caches
them, its misses through the api-misses limit; Next's rewrites do the same
where nginx isn't in front (`next dev`).

A feed is pulled, so serving one learns nothing about who reads it beyond
what any page request does, and nothing here records a request
(AGENTS.md §8). Picking a topic or a state is the reader's filter, carried
in the URL they subscribe to.

Read path only: indexed queries, no model, no fetch.
"""
import hashlib
import re
from datetime import datetime
from html import escape
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app import entity_feeds
from app.api.agencies import children_of, descendants
from app.api.response_helpers import CACHE_TTL_LIST_S, cached_json
from app.broadcast import FEEDS, KINDS, SITE_URL, not_withdrawn
from app.database import get_db
from app.entity_feeds import EntityFeed
from app.member_ids import resolve_member_id
from app.models import Agency, BroadcastPost
from app.services.bill_record import canonical_bill_id
from app.services.senator_service import STATE_NAMES
from app import whats_new as whats_new_module

router = APIRouter()

ATOM_NS = "http://www.w3.org/2005/Atom"
ATOM_MEDIA_TYPE = "application/atom+xml; charset=utf-8"
# Media RSS: the picture most feed readers and chat bots show with an entry
# (media:thumbnail), the way a Bluesky link card shows it.
MEDIA_NS = "http://search.yahoo.com/mrss/"
ET.register_namespace("media", MEDIA_NS)

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


# One followed thing's feed (app/entity_feeds.py). The frontend writes the
# same paths (src/lib/site.ts memberFeed, billFeed, agencyFeed, monitorFeed).
def member_feed_path(member_id: str) -> str:
    return f"/feed/members/{quote(member_id)}.xml"


def bill_feed_path(congress: int, bill_id: str) -> str:
    return f"/feed/bills/{congress}/{bill_id}.xml"


def agency_feed_path(slug: str) -> str:
    return f"/feed/agencies/{quote(slug)}.xml"


def monitor_feed_path(slug: str) -> str:
    return f"/feed/monitors/{quote(slug)}.xml"


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


def _picture(post: BroadcastPost) -> tuple[str | None, str | None]:
    """The entry's picture and its alt text: the post's own image when it
    carries one (a trend post's record image, the one Bluesky shows),
    otherwise the linked page's card."""
    if post.image_url:
        return post.image_url, post.alt_text
    return post.card_image, post.card_image_alt


def _content_html(post: BroadcastPost) -> str:
    """The entry as a reader shows it, like the Bluesky post with its card:
    the card's picture, the post's words, a link to the page on Civitas,
    and the source it restates, when it restates one."""
    parts = []
    image, image_alt = _picture(post)
    if image:
        alt = escape(image_alt or post.title)
        parts.append(f'<p><a href="{escape(post.url)}"><img src="{escape(image)}" alt="{alt}"></a></p>')
    parts += [f"<p>{escape(p)}</p>" for p in post.text.split("\n") if p.strip()]
    parts.append(f'<p><a href="{escape(post.url)}">Read on Civitas Research</a></p>')
    if post.source_url:
        host = urlsplit(post.source_url).hostname or post.source_url
        parts.append(f'<p>Source: <a href="{escape(post.source_url)}">{escape(host.removeprefix("www."))}</a></p>')
    return "".join(parts)


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
    _sub(author, "name", "Civitas Research")
    _sub(author, "uri", SITE_URL)
    # A PNG, not the SVG favicon: Discord and most feed readers don't draw
    # SVG, and a feed reader or bot shows this beside every entry.
    _sub(feed, "icon", f"{SITE_URL}/apple-icon.png")
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
        if post.source_url:
            _sub(entry, "link", rel="related", type="text/html", href=post.source_url)
        if post.card_description:
            _sub(entry, "summary", post.card_description, type="text")
        _sub(entry, "content", _content_html(post), type="html")
        image, image_alt = _picture(post)
        if image:
            # The picture, for readers that show one beside the entry.
            # No type: the page names the image, not its format.
            _sub(entry, "link", rel="enclosure", href=image)
            _sub(entry, f"{{{MEDIA_NS}}}thumbnail", url=image)
            media = _sub(entry, f"{{{MEDIA_NS}}}content", url=image, medium="image")
            if image_alt:
                _sub(media, f"{{{MEDIA_NS}}}description", image_alt, type="plain")
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(feed, encoding="utf-8")


def _matches(if_none_match: str | None, etag: str) -> bool:
    """If-None-Match's weak comparison (RFC 9110 §13.1.2): any listed tag,
    with or without its W/ prefix, or "*". A client given this feed through
    nginx's gzip holds the weak form of the tag."""
    if not if_none_match:
        return False
    tags = {t.strip().removeprefix("W/") for t in if_none_match.split(",")}
    return "*" in tags or etag in tags


def render_entity_atom(feed: EntityFeed, path: str) -> bytes:
    """An Atom 1.0 document for one followed thing (app/entity_feeds.py),
    newest first: each entry links the Civitas page and its official source."""
    self_url = f"{SITE_URL}{path}"
    root = ET.Element("feed", {"xmlns": ATOM_NS})
    _sub(root, "id", self_url)
    _sub(root, "title", feed.title)
    _sub(root, "subtitle", feed.subtitle)
    _sub(root, "link", rel="self", type="application/atom+xml", href=self_url)
    _sub(root, "link", rel="alternate", type="text/html", href=f"{SITE_URL}{feed.page}")
    _sub(root, "updated", _rfc3339(feed.entries[0].when) if feed.entries else _EMPTY_FEED_UPDATED)
    author = _sub(root, "author")
    _sub(author, "name", "Civitas Research")
    _sub(author, "uri", SITE_URL)
    _sub(root, "icon", f"{SITE_URL}/apple-icon.png")
    for e in feed.entries:
        url = f"{SITE_URL}{e.url}"
        entry = _sub(root, "entry")
        _sub(entry, "id", e.id)
        _sub(entry, "title", e.title)
        _sub(entry, "link", rel="alternate", type="text/html", href=url)
        stamp = _rfc3339(e.when)
        _sub(entry, "published", stamp)
        _sub(entry, "updated", stamp)
        _sub(entry, "category", term=e.category, label=e.label)
        if e.source_url:
            _sub(entry, "link", rel="related", type="text/html", href=e.source_url)
        parts = [f"<p>{escape(p)}</p>" for p in e.paragraphs]
        parts.append(f'<p><a href="{escape(url)}">Read on Civitas Research</a></p>')
        if e.source_url:
            host = urlsplit(e.source_url).hostname or e.source_url
            parts.append(f'<p>Source: <a href="{escape(e.source_url)}">{escape(host.removeprefix("www."))}</a></p>')
        _sub(entry, "content", "".join(parts), type="html")
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="utf-8")


def _atom_response(request: Request, body: bytes, newest: datetime | None) -> Response:
    """The feed with validators, so a poller that sends them back gets a 304
    instead of the document (nginx answers these from its cache too)."""
    etag = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
    headers = {
        "Cache-Control": f"public, max-age={CACHE_TTL_LIST_S}, stale-while-revalidate={CACHE_TTL_LIST_S}",
        "ETag": etag,
    }
    if newest is not None:
        headers["Last-Modified"] = newest.strftime("%a, %d %b %Y %H:%M:%S GMT")
    if _matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type=ATOM_MEDIA_TYPE, headers=headers)


def _newest(db: Session, *filters) -> list[BroadcastPost]:
    """The newest posts matching `filters`, leaving out any withdrawn (a
    withdrawn issue's page now shows only the withdrawal): the feed must not
    keep serving what Civitas retracted."""
    return (
        db.query(BroadcastPost)
        .filter(*filters, not_withdrawn())
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
        # A feed for one followed thing, linked from its page.
        "followed": FOLLOWED,
    }, max_age=CACHE_TTL_LIST_S)


FOLLOWED = [
    {"kind": "member", "title": "A member of Congress",
     "description": "Each roll-call vote this Congress with the member's position, the latest action on each bill "
                    "the member sponsored, each change in the representation score between runs, and each new stock "
                    "trade report or annual financial disclosure.",
     "pattern": "/feed/members/<member id>.xml", "where": "Linked from each member's profile."},
    {"kind": "bill", "title": "A bill",
     "description": "Each action Congress records on the bill, with what the step means, and each roll call on it.",
     "pattern": "/feed/bills/<congress>/<bill>.xml", "where": "Linked from each bill's page."},
    {"kind": "agency", "title": "A federal agency",
     "description": "Each proposed and final rule the agency or one under it publishes in the Federal Register, each "
                    "comment period as it opens, and each one's last day.",
     "pattern": "/feed/agencies/<agency>.xml", "where": "Linked from each agency's page."},
    {"kind": "monitor", "title": "A story",
     "description": "Each sourced update to a national monitor, an ongoing story the Action Center tracks, under the "
                    "outlet's own headline.",
     "pattern": "/feed/monitors/<monitor>.xml",
     "where": "Linked from each monitor on the Action Center's Ongoing tab and from the issues it tracks."},
]


@router.get("/whats-new")
def whats_new():
    """New pages and features, newest first (app/data/whats_new.json): the
    list /changelog shows. The same entries are posted once each to the
    What's new feed (app/whats_new.py)."""
    return cached_json({
        "entries": [{k: e[k] for k in ("id", "date", "title", "summary", "url")} for e in whats_new_module.entries()],
        "feed": feed_path("new"),
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
        title=f"Civitas Research: {STATE_NAMES[code]}",
        subtitle=f"Civitas posts about {STATE_NAMES[code]}: its races on the ballot and its members of Congress.",
        path=state_feed_path(code),
        posts=posts,
    )
    return _atom_response(request, body, posts[0].published_at if posts else None)


@router.get("/feed/{name}")
def topic_feed(name: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """`all.xml` (everything) or one topic's feed."""
    slug = name.removesuffix(".xml")
    feed = FEEDS.get(slug)
    if not name.endswith(".xml") or feed is None:
        raise HTTPException(status_code=404, detail="No such feed")
    posts = _newest(db, BroadcastPost.kind.in_(feed.kinds))
    body = render_atom(title=feed.title, subtitle=feed.description, path=feed_path(slug), posts=posts)
    return _atom_response(request, body, posts[0].published_at if posts else None)


def _entity_response(request: Request, feed: EntityFeed | None, path: str) -> Response:
    if feed is None:
        raise HTTPException(status_code=404, detail="No such feed")
    return _atom_response(request, render_entity_atom(feed, path), feed.entries[0].when if feed.entries else None)


def _xml_name(name: str) -> str:
    if not name.endswith(".xml"):
        raise HTTPException(status_code=404, detail="No such feed")
    return name.removesuffix(".xml")


@router.get("/feed/members/{name}")
def member_feed(name: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """One member of Congress: roll-call votes, sponsored bills' latest
    actions, score changes and disclosures. A renamed member's old id
    serves the member's feed under its current address."""
    member_id = resolve_member_id(db, _xml_name(name))
    return _entity_response(request, entity_feeds.member_feed(db, member_id), member_feed_path(member_id))


@router.get("/feed/bills/{congress}/{name}")
def bill_feed(congress: int, name: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """One bill of one Congress ("/feed/bills/119/HR.1.xml"): its actions and roll calls."""
    bill_id = canonical_bill_id(_xml_name(name))
    if bill_id is None:
        raise HTTPException(status_code=404, detail="No such feed")
    return _entity_response(request, entity_feeds.bill_feed(db, congress, bill_id), bill_feed_path(congress, bill_id))


@router.get("/feed/agencies/{name}")
def agency_feed(name: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """One agency with its sub-agencies: rules and comment periods."""
    slug = _xml_name(name)
    slugs = descendants(children_of(db.query(Agency).all()), slug)
    return _entity_response(request, entity_feeds.agency_feed(db, slug, slugs), agency_feed_path(slug))


@router.get("/feed/monitors/{name}")
def monitor_feed(name: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """One national monitor: each sourced update."""
    slug = _xml_name(name)
    return _entity_response(request, entity_feeds.monitor_feed(db, slug), monitor_feed_path(slug))


for _path, _route in (("/feed/members/{name}", member_feed), ("/feed/bills/{congress}/{name}", bill_feed),
                      ("/feed/agencies/{name}", agency_feed), ("/feed/monitors/{name}", monitor_feed)):
    router.add_api_route(_path, _route, methods=["HEAD"], include_in_schema=False)
router.add_api_route("/feed/states/{name}", state_feed, methods=["HEAD"], include_in_schema=False)
router.add_api_route("/feed/{name}", topic_feed, methods=["HEAD"], include_in_schema=False)
