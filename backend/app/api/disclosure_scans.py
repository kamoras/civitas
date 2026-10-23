"""A scanned disclosure's page images, served from this site.

`/api/disclosure-scans/<filing_id>/<page>.webp` is a page the pipeline
stored (app/disclosure_scans.py). It takes a filing id and a page number,
never a URL, and reads only the data volume. nginx caches it like any
cacheable /api response, for as long as Cache-Control says.
"""

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import FileResponse

from app import disclosure_scans

router = APIRouter()

# A filing's pages don't change once stored (a report, once filed, never
# does), but a day keeps a re-render after a fix from lingering long.
_CACHE_CONTROL = "public, max-age=86400"


@router.get("/disclosure-scans/{filing_id}/{page}.webp")
def get_disclosure_scan_page(
    filing_id: str = Path(pattern=disclosure_scans.FILING_ID_RE.pattern),
    page: int = Path(ge=1, le=disclosure_scans.MAX_PAGES),
) -> FileResponse:
    path = disclosure_scans.page_path(filing_id, page)
    if path is None:
        raise HTTPException(status_code=404, detail="No such page")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": _CACHE_CONTROL})
