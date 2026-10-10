"""Where pages load photos from: always this site (AGENTS.md §8).

A visitor's browser never requests an image from another host, so every
photo URL the API hands out is a same-origin path to the frontend's photo
route (`frontend/src/app/photo/[kind]/[id]/route.ts`), which takes a kind
and an id — never a URL — and fetches the picture server-side:

- `bioguide`: a member's official portrait, its source URL built from the
  bioguide id;
- `justice`: a justice's Oyez thumbnail, and `issue`: an Action Center
  issue's rights-cleared news photo — both stored by the pipeline, and
  looked up by the route through `GET /api/photo-sources/{kind}/{id}`
  (`app/api/photos.py`).

Paths are relative, so they work on whichever host serves the site.

The server fetches a photo only from the hosts its kind's data comes from
(`PHOTO_SOURCE_HOSTS`), over https: an issue's photo URL is read from an RSS
item, outside data, and must not be able to point the server at any host it
likes. The pipeline drops a source off these hosts before storing it, this
module's endpoint refuses one, and the frontend route checks again on every
redirect hop (its `PHOTO_HOSTS`, held equal to this by
tests/test_same_origin_photos.py).
"""
from urllib.parse import urlsplit

PHOTO_SOURCE_HOSTS: dict[str, frozenset[str]] = {
    # The route builds these URLs itself, from the id.
    "bioguide": frozenset({"bioguide.congress.gov"}),
    # Oyez's justice thumbnails (fetch/justice_votes.py): every sitting
    # justice's is on api.oyez.org (checked 2026-10-10).
    "justice": frozenset({"api.oyez.org"}),
    # Where the rights-cleared feed photos are (fetch/news_feeds.py's
    # _rights_cleared_image, which applies this list). Not derivable from
    # the feed list: the rights signal is per item, in any feed. Only Roll
    # Call's feed carries it, and every rights-cleared item there points at
    # rollcall.com (10 of 10, 2026-10-10). A feed that starts granting
    # rights on another host needs that host added here, deliberately.
    "issue": frozenset({"rollcall.com"}),
}


def photo_source_allowed(kind: str, url: str | None) -> bool:
    """True for an https URL on one of `kind`'s hosts (default port)."""
    if not url:
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and port in (None, 443)
        and not parts.username
        and parts.hostname in PHOTO_SOURCE_HOSTS.get(kind, frozenset())
    )


def bioguide_photo(bioguide_id: str | None) -> str | None:
    return f"/photo/bioguide/{bioguide_id}" if bioguide_id else None


def justice_photo(justice) -> str | None:
    return f"/photo/justice/{justice.id}" if justice.thumbnail_url else None


def issue_photo(issue) -> str | None:
    return f"/photo/issue/{issue.id}" if getattr(issue, "image_url", None) else None
