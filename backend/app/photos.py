"""Where pages load photos from: always this site (AGENTS.md §8).

A visitor's browser never requests an image from another host, so every
photo URL the API hands out is a same-origin path to the frontend's photo
route (`frontend/src/app/photo/[kind]/[id]/route.ts`), which takes a kind
and an id — never a URL — and fetches the picture server-side:

- `bioguide`: a member's official photo: the one on the member's
  Congress.gov record (its `depiction`) when that record has a credited
  one, else bioguide.congress.gov's portrait for the id
  (`bioguide_photo_source`, looked up through the endpoint below like the
  stored kinds);
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
import json
import re
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.models import ApiCache
from app.pipeline.fetch.congress import member_detail_cache_key

PHOTO_SOURCE_HOSTS: dict[str, frozenset[str]] = {
    # bioguide_photo_source names one of these: Congress.gov's member
    # image, or bioguide's portrait.
    "bioguide": frozenset({"www.congress.gov", "bioguide.congress.gov"}),
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


_BIOGUIDE_ID = re.compile(r"[A-Z]\d{6}")
# Where Congress.gov keeps member images: every depiction on a current
# member's record was here (532 of 532, 2026-10-13).
_DEPICTION_URL = re.compile(r"https://www\.congress\.gov/img/member/[A-Za-z0-9_]+\.(?:jpe?g|png)")
_TAG = re.compile(r"<[^>]*>")


def _depiction(record: dict) -> str | None:
    """The record's official photo URL, if it is one we can credit: on
    Congress.gov's member-image path, with the attribution Congress.gov
    gives it (most read "Image courtesy of the Member"; the rest name the
    Senate's photographer or Historical Office, the House collection, the
    Pictorial Directory or the GPO Member Guide). A depiction without an
    attribution is passed over, so no photo is shown whose provenance its
    source didn't state."""
    depiction = record.get("depiction")
    if not isinstance(depiction, dict):
        return None
    url, attribution = depiction.get("imageUrl"), depiction.get("attribution")
    if not isinstance(url, str) or not _DEPICTION_URL.fullmatch(url):
        return None
    if not isinstance(attribution, str) or not _TAG.sub("", attribution).strip():
        return None
    return url


def bioguide_photo_source(db: Session, bioguide_id: str) -> str | None:
    """Where member `bioguide_id`'s photo is fetched from.

    The Congress.gov record's headshot first: bioguide's file for some
    members is a full-length painted portrait (an official committee
    chair's), mostly background once cropped to an avatar, while the
    record's depiction is a head-and-shoulders photo. The record is the one
    the pipeline stored (api_cache, written by fetch/congress.
    fetch_member_detail), read at any age: an older record still names an
    official photo. A member with no credited depiction there, or no record
    at all (a bill sponsor from before the pipeline's time), gets bioguide's
    portrait."""
    if not _BIOGUIDE_ID.fullmatch(bioguide_id):
        return None
    row = db.get(ApiCache, ("congress", member_detail_cache_key(bioguide_id)))
    try:
        record = json.loads(row.data_json) if row else None
    except ValueError:
        record = None
    if isinstance(record, dict) and (url := _depiction(record)):
        return url
    # bioguide.congress.gov/bioguide/photo/<L>/<id>.jpg 301s to this.
    return f"https://bioguide.congress.gov/photo/{bioguide_id}.jpg"


def bioguide_photo(bioguide_id: str | None) -> str | None:
    return f"/photo/bioguide/{bioguide_id}" if bioguide_id else None


def justice_photo(justice) -> str | None:
    return f"/photo/justice/{justice.id}" if justice.thumbnail_url else None


def issue_photo(issue) -> str | None:
    return f"/photo/issue/{issue.id}" if getattr(issue, "image_url", None) else None
