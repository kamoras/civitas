"""Fetch a scanned disclosure's pages and keep them as images on this site.

A House annual report scanned with no text layer, or a Senate paper filing,
can't be read into the holdings breakdown. Its pages are still shown on the
member's page (app/disclosure_scans.py says where they live and why), so
this module fetches them server-side and renders them:

- House: the PDF the index links (house_ptr.download_pdf, the Clerk policy),
  rendered page by page.
- Senate: the paper filing's eFD page lists its page images (img.filingImage,
  on efd-media-public.senate.gov); each is downloaded on the accepted-terms
  session and converted.

Rendering runs in a child process (scan_render.py). A failure keeps the
pages already stored and records when it happened; the page then says the
images couldn't be loaded and links the filing.
"""

import asyncio
import json
import logging
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from lxml import html as lxml_html

from app import disclosure_scans
from app.atomic_write import write_text_atomic
from app.error_utils import output_summary
from app.pipeline.fetch.house_ptr import download_pdf
from app.pipeline.fetch.senate_fd import SessionLapsed, _redirects_to_terms, is_terms_page
from app.pipeline.fetch.senate_ptr import _request_with_retry
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

BACKEND = Path(__file__).resolve().parents[3]
# Where eFD serves a paper filing's page images. Only these are fetched: the
# page's markup names the URLs, and nothing else it names is followed.
SENATE_IMAGE_HOST = "efd-media-public.senate.gov"
_IMAGE_MAGIC = (b"GIF87a", b"GIF89a", b"\x89PNG", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*")


class ScanUnavailable(Exception):
    """The filing's pages couldn't be fetched or rendered this run."""


async def fetch_house_scan(client: httpx.AsyncClient, pdf_url: str) -> tuple[str, list[bytes]]:
    body = await download_pdf(client, pdf_url)
    if body is None:
        raise ScanUnavailable(f"House Clerk did not serve {pdf_url}")
    return "pdf", [body]


def senate_image_urls(page_html: str) -> list[str]:
    """The paper filing's page images, in page order, on eFD's image host."""
    doc = lxml_html.fromstring(page_html)
    urls = [src.strip() for src in doc.xpath("//img[contains(concat(' ', @class, ' '), ' filingImage ')]/@src")]
    return [u for u in urls if (p := urlparse(u)).scheme == "https" and p.hostname == SENATE_IMAGE_HOST and not p.port]


async def fetch_senate_scan(client: httpx.AsyncClient, report_url: str) -> tuple[str, list[bytes]]:
    """Raises SessionLapsed when eFD answers with its terms page (the caller
    re-accepts and retries), ScanUnavailable for anything else that fails."""
    resp = await _request_with_retry(client, "GET", report_url, follow_redirects=False)
    if resp is None:
        raise ScanUnavailable(f"Senate eFD did not answer for {report_url}")
    if _redirects_to_terms(resp) or (resp.status_code == 200 and is_terms_page(resp.text)):
        raise SessionLapsed(f"HTTP {resp.status_code} for {report_url}")
    if resp.status_code != 200:
        raise ScanUnavailable(f"Senate eFD answered HTTP {resp.status_code} for {report_url}")
    urls = senate_image_urls(resp.text)
    if not urls:
        raise ScanUnavailable(f"no page images on {report_url}")
    images = []
    for url in urls:
        image = await _request_with_retry(client, "GET", url)
        if image is None or image.status_code != 200 or not image.content.startswith(_IMAGE_MAGIC):
            raise ScanUnavailable(f"page image {url} did not load")
        images.append(image.content)
    return "images", images


async def _render(kind: str, inputs: list[str], out: Path, timeout_s: float) -> list[list[int]]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.pipeline.fetch.scan_render",
        cwd=BACKEND, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    job = json.dumps({"kind": kind, "inputs": inputs, "out": str(out)}).encode()
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(job), timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ScanUnavailable(f"rendering took over {timeout_s:.0f}s") from None
    if proc.returncode != 0:
        text = stderr.decode(errors="replace")
        logger.warning("Scan rendering exited %s; its stderr:\n%s", proc.returncode, text)
        raise ScanUnavailable(f"rendering exited {proc.returncode}: {output_summary(text) or 'no output'}")
    sizes = json.loads(stdout)
    if not sizes or len(sizes) > disclosure_scans.MAX_PAGES:
        raise ScanUnavailable(f"rendering produced {len(sizes)} pages")
    return sizes


async def store_scan(filing_id: str, source_url: str, kind: str, blobs: list[bytes], timeout_s: float) -> dict:
    """Render the fetched pages and put them in place of any stored for the
    filing, in one rename: a reader sees the old pages or the new, never
    half of each. Returns the new manifest."""
    final = disclosure_scans.scan_dir(filing_id)
    if final is None:
        raise ScanUnavailable(f"not a filing id: {filing_id!r}")
    disclosure_scans.SCANS_DIR.mkdir(parents=True, exist_ok=True)
    new = disclosure_scans.SCANS_DIR / f".new-{filing_id}-{uuid.uuid4().hex}"
    new.mkdir()
    try:
        with tempfile.TemporaryDirectory(dir=disclosure_scans.SCANS_DIR, prefix=".in-") as incoming:
            inputs = []
            for i, blob in enumerate(blobs):
                path = Path(incoming) / str(i)
                path.write_bytes(blob)
                inputs.append(str(path))
            sizes = await _render(kind, inputs, new, timeout_s)
        manifest = {
            "source_url": source_url,
            "fetched_at": utcnow().strftime("%Y-%m-%d"),
            "pages": len(sizes),
            "sizes": sizes,
        }
        write_text_atomic(new / disclosure_scans.MANIFEST, json.dumps(manifest))
        old = final.with_name(f".old-{filing_id}-{uuid.uuid4().hex}")
        if final.exists():
            final.rename(old)
        new.rename(final)
        shutil.rmtree(old, ignore_errors=True)
        return manifest
    finally:
        shutil.rmtree(new, ignore_errors=True)


def record_failure(filing_id: str, source_url: str, reason: str) -> None:
    """Note a failed attempt. Pages already stored stay, and so does their
    manifest's source and date; with none, the manifest says only that the
    pages couldn't be loaded."""
    directory = disclosure_scans.scan_dir(filing_id)
    if directory is None:
        return
    manifest = disclosure_scans.read_manifest(filing_id) or {"source_url": source_url, "pages": 0, "sizes": []}
    manifest["failed_at"] = utcnow().strftime("%Y-%m-%d")
    manifest["failure"] = reason[:300]
    directory.mkdir(parents=True, exist_ok=True)
    write_text_atomic(directory / disclosure_scans.MANIFEST, json.dumps(manifest))


def prune(keep: set[str]) -> int:
    """Delete stored scans of filings no member's page shows any more (a
    readable report replaced the scanned one, or the member left), and any
    leftovers of an interrupted render. Returns directories removed."""
    if not disclosure_scans.SCANS_DIR.is_dir():
        return 0
    removed = 0
    for entry in disclosure_scans.SCANS_DIR.iterdir():
        if entry.is_dir() and entry.name not in keep:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed
