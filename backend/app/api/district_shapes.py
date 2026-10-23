"""GET /api/district-shapes/{build}/{path}: the district shapes a data
refresh built on the data volume (app/district_shapes.py), for maps the
shapes bundled with the frontend don't cover. Fetched whole, by a path the
API itself named, with no query (AGENTS.md §8)."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app import district_shapes

router = APIRouter()

# A build's files never change (a new build gets a new directory), so they
# keep as long as the bundled ones do.
_IMMUTABLE = "public, max-age=31536000, immutable"


@router.get("/district-shapes/{build}/{path:path}", include_in_schema=False)
def get_district_shapes(build: str, path: str) -> FileResponse:
    target = district_shapes.served_file(build, path)
    if target is None:
        raise HTTPException(status_code=404, detail="No such shapes")
    return FileResponse(target, media_type="application/json", headers={"Cache-Control": _IMMUTABLE})
