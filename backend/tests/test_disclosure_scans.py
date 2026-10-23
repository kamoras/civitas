"""The page images of scanned disclosures: rendering (scan_render.py, run as
its own process), keeping and replacing them (scan_pages.py, the
ingest_scan_pages step), and serving them (holdings_service's `scan`, the
/api/disclosure-scans route)."""

import ast
import io
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import disclosure_scans
from app.models import FinancialDisclosure, Representative, Senator
from app.pipeline import holdings_pipeline
from app.pipeline.fetch import scan_pages
from app.services.holdings_service import get_rep_holdings

BACKEND = Path(__file__).resolve().parents[1]


def _scan_pdf(pages: int = 2) -> bytes:
    """A PDF like the Clerk's scans: 1-bit page images at 200 dpi."""
    images = []
    for i in range(pages):
        image = Image.new("1", (1700, 2200), 1)
        image.paste(0, (100 + 50 * i, 100, 600, 160))  # a bar of "ink"
        images.append(image)
    buf = io.BytesIO()
    images[0].save(buf, "PDF", resolution=200, save_all=True, append_images=images[1:])
    return buf.getvalue()


def _gif(size=(3400, 4400)) -> bytes:
    """Like eFD's page images: a palette GIF whose ink entry is marked
    transparent."""
    image = Image.new("1", size, 1)
    image.paste(0, (200, 200, 1200, 320))
    buf = io.BytesIO()
    image.convert("P").save(buf, "GIF", transparency=0)
    return buf.getvalue()


@pytest.fixture()
def scanned_rep(db_session):
    db_session.add(Representative(id="R1", name="John Doe", state="TX", district=1, party="R", is_current=True))
    db_session.add(FinancialDisclosure(
        representative_id="R1", filing_id="9116162", report_label="2025 annual report",
        source_url="https://clerk.example/2025/9116162.pdf", parsed=False, unreadable_reason="scanned",
        parser_version=2,
    ))
    db_session.commit()


class TestRender:
    async def test_a_pdf_renders_at_200_dpi_as_bilevel_webp(self):
        sizes = (await scan_pages.store_scan("9116162", "https://clerk.example/x.pdf", "pdf", [_scan_pdf()], 120))["sizes"]
        assert sizes == [[1700, 2200], [1700, 2200]]
        page = Image.open(disclosure_scans.page_path("9116162", 1))
        assert page.format == "WEBP" and page.size == (1700, 2200)
        assert {c for _, c in page.convert("L").getcolors()} <= {0, 255}

    async def test_senate_images_are_halved_to_200_dpi(self):
        manifest = await scan_pages.store_scan("a5124b02-61d5", "https://efd.example/p/", "images", [_gif(), _gif()], 120)
        assert manifest["pages"] == 2 and manifest["sizes"][0] == [1700, 2200]
        page = Image.open(disclosure_scans.page_path("a5124b02-61d5", 1))
        # Opaque, the ink black: a transparent ink entry once came through.
        assert page.mode == "RGB" and page.getpixel((300, 130)) == (0, 0, 0)

    async def test_a_render_that_fails_raises_and_leaves_no_directory(self):
        with pytest.raises(scan_pages.ScanUnavailable):
            await scan_pages.store_scan("9116162", "https://clerk.example/x.pdf", "pdf", [b"%PDF-1.4 not a pdf"], 120)
        assert not disclosure_scans.SCANS_DIR.exists() or list(disclosure_scans.SCANS_DIR.iterdir()) == []


class TestKeeping:
    async def test_a_failure_keeps_the_pages_already_stored(self):
        await scan_pages.store_scan("9116162", "https://clerk.example/x.pdf", "pdf", [_scan_pdf()], 120)
        scan_pages.record_failure("9116162", "https://clerk.example/x.pdf", "House Clerk did not serve it")
        manifest = disclosure_scans.read_manifest("9116162")
        assert manifest["pages"] == 2 and manifest["failed_at"] and manifest["fetched_at"]
        assert disclosure_scans.page_path("9116162", 2) is not None

    def test_a_failure_with_nothing_stored_says_no_pages(self):
        scan_pages.record_failure("9116162", "https://clerk.example/x.pdf", "down")
        assert disclosure_scans.read_manifest("9116162")["pages"] == 0

    async def test_prune_removes_filings_no_page_shows(self):
        await scan_pages.store_scan("111", "u", "pdf", [_scan_pdf(1)], 120)
        await scan_pages.store_scan("222", "u", "pdf", [_scan_pdf(1)], 120)
        assert scan_pages.prune({"222"}) == 1
        assert disclosure_scans.read_manifest("111") is None and disclosure_scans.read_manifest("222")

    def test_ids_that_are_not_filing_ids_name_no_directory(self):
        for bad in ("../etc", "a/b", "", "x" * 65, ".hidden"):
            assert disclosure_scans.scan_dir(bad) is None
        assert disclosure_scans.page_path("9116162", 0) is None


class TestSenatePage:
    def test_only_efd_image_host_pages_in_order(self):
        page = """<html><body>
          <img class="filingImage" src="https://efd-media-public.senate.gov/media/2026/2/000/000/000000212.gif">
          <img class="logo" src="/static/images/logo.svg">
          <img class="filingImage" src="https://evil.example/x.gif">
          <img class="filingImage" src="http://efd-media-public.senate.gov/plain.gif">
          <img class="filingImage" src="https://efd-media-public.senate.gov/media/2026/2/000/000/000000213.gif">
        </body></html>"""
        assert scan_pages.senate_image_urls(page) == [
            "https://efd-media-public.senate.gov/media/2026/2/000/000/000000212.gif",
            "https://efd-media-public.senate.gov/media/2026/2/000/000/000000213.gif",
        ]


class TestIngestStep:
    async def test_stores_then_skips_a_filing_already_kept(self, db_session, scanned_rep):
        fetch = AsyncMock(return_value=("pdf", [_scan_pdf()]))
        with patch.object(scan_pages, "fetch_house_scan", fetch):
            assert await holdings_pipeline.ingest_scan_pages(db_session, None) == 2
            assert await holdings_pipeline.ingest_scan_pages(db_session, None) == 0
        assert fetch.await_count == 1

    async def test_every_filing_failing_fails_the_step(self, db_session, scanned_rep):
        fetch = AsyncMock(side_effect=scan_pages.ScanUnavailable("House Clerk did not serve it"))
        with patch.object(scan_pages, "fetch_house_scan", fetch), pytest.raises(RuntimeError):
            await holdings_pipeline.ingest_scan_pages(db_session, None)
        assert disclosure_scans.read_manifest("9116162")["failed_at"]

    async def test_a_senate_paper_filing_is_read_through_the_session(self, db_session):
        db_session.add(Senator(id="S1", name="Tess Bradwell", state="WI", party="D", is_current=True))
        db_session.add(FinancialDisclosure(
            senator_id="S1", filing_id="a5124b02", report_label="annual report filed 2026-08-13",
            source_url="https://efdsearch.senate.gov/search/view/paper/a5124b02/", parsed=False,
            unreadable_reason="scanned",
        ))
        db_session.commit()
        fetch = AsyncMock(return_value=("images", [_gif()]))
        with patch.object(scan_pages, "fetch_senate_scan", fetch):
            assert await holdings_pipeline.ingest_scan_pages(db_session, None) == 1

    async def test_a_readable_report_has_no_pages_fetched(self, db_session, scanned_rep):
        db_session.query(FinancialDisclosure).update({"parsed": True, "unreadable_reason": None})
        db_session.commit()
        await scan_pages.store_scan("9116162", "u", "pdf", [_scan_pdf(1)], 120)
        fetch = AsyncMock()
        with patch.object(scan_pages, "fetch_house_scan", fetch):
            assert await holdings_pipeline.ingest_scan_pages(db_session, None) == 0
        assert not fetch.called and disclosure_scans.read_manifest("9116162") is None  # pruned


class TestReadPath:
    async def test_holdings_name_the_pages_and_alt_title(self, db_session, scanned_rep):
        await scan_pages.store_scan("9116162", "https://clerk.example/2025/9116162.pdf", "pdf", [_scan_pdf()], 120)
        scan = get_rep_holdings(db_session, "R1").scan
        assert (scan.pages, scan.title, scan.page_url_prefix) == (
            2, "John Doe's 2025 annual report", "/api/disclosure-scans/9116162/",
        )
        assert scan.fetched_from == "https://clerk.example/2025/9116162.pdf" and scan.failed_at is None

    def test_no_pages_yet_is_said_not_hidden(self, db_session, scanned_rep):
        scan = get_rep_holdings(db_session, "R1").scan
        assert scan is not None and scan.pages == 0

    async def test_route_serves_a_page_and_refuses_anything_else(self, db_session, scanned_rep):
        from app.api import disclosure_scans as route

        await scan_pages.store_scan("9116162", "u", "pdf", [_scan_pdf(1)], 120)
        app = FastAPI()
        app.include_router(route.router, prefix="/api")
        client = TestClient(app)
        ok = client.get("/api/disclosure-scans/9116162/1.webp")
        assert ok.status_code == 200 and ok.headers["content-type"] == "image/webp"
        assert "max-age" in ok.headers["cache-control"]
        assert client.get("/api/disclosure-scans/9116162/2.webp").status_code == 404
        assert client.get("/api/disclosure-scans/..%2F..%2Fetc/1.webp").status_code == 404


def test_nothing_in_the_app_imports_the_render_module():
    """The renderer runs only in its own process (scan_pages starts it)."""
    for path in (BACKEND / "app").rglob("*.py"):
        if path.name == "scan_render.py":
            continue
        imported = {
            (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not {m for m in imported if m.endswith("scan_render")}, path

