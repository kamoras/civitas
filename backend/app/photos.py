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
"""


def bioguide_photo(bioguide_id: str | None) -> str | None:
    return f"/photo/bioguide/{bioguide_id}" if bioguide_id else None


def justice_photo(justice) -> str | None:
    return f"/photo/justice/{justice.id}" if justice.thumbnail_url else None


def issue_photo(issue) -> str | None:
    return f"/photo/issue/{issue.id}" if getattr(issue, "image_url", None) else None
