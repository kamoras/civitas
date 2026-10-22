"""The page images of scanned and paper financial disclosure reports, as
this site keeps them.

A report that couldn't be read (a House filing scanned with no text layer,
a Senate paper filing that eFD serves as page images) is still shown on the
member's page, page by page, so a reader isn't sent off to look it up. The
pipeline fetches and renders the pages (pipeline/fetch/scan_pages.py) and
writes them here, one directory per filing:

    /data/disclosure_scans/<filing_id>/manifest.json
    /data/disclosure_scans/<filing_id>/1.webp, 2.webp, ...

The manifest records where the pages came from and when (source_url,
fetched_at), each page's pixel size, and the last failed attempt
(failed_at). A failed fetch never removes pages already kept.

Stdlib only: the read path (holdings_service, api/disclosure_scans.py)
imports this, and must never pull in the rendering libraries. Every page
is served from this site (AGENTS.md §8): the browser never loads a scan
from the Clerk's or eFD's hosts.
"""

import json
import os
import re
from pathlib import Path

SCANS_DIR = Path("/data/disclosure_scans")

# A House document id ("9116162") or a Senate eFD report id (a UUID). The
# id names a directory, so nothing else is accepted.
FILING_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
# Far above any real report (the longest stored, 2026-10: 353 pages).
MAX_PAGES = 4096

MANIFEST = "manifest.json"


def scan_dir(filing_id: str) -> Path | None:
    if not FILING_ID_RE.fullmatch(filing_id or ""):
        return None
    return SCANS_DIR / filing_id


def read_manifest(filing_id: str) -> dict | None:
    """The filing's manifest, or None when none was written (never fetched,
    or a malformed file — both read as "no pages")."""
    directory = scan_dir(filing_id)
    if directory is None:
        return None
    try:
        manifest = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def page_path(filing_id: str, page: int) -> Path | None:
    """The stored image of page `page` (1-based), or None."""
    directory = scan_dir(filing_id)
    if directory is None or not 1 <= page <= MAX_PAGES:
        return None
    # The id is already one path segment of safe characters; checked again
    # on the resolved path, so no request can name a file outside the store.
    root = os.path.realpath(SCANS_DIR)
    path = os.path.realpath(os.path.join(root, filing_id, f"{page}.webp"))
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        return None
    return Path(path)
