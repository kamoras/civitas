"""The Internet Archive route for a walled state's own document
(web_archive.py, ballot_measures_oh.fetch_archived). Index answers are the
Archive's real ones (fixtures_web_archive_cdx.json); the Archive holds no
capture of Ohio's 2026 sample ballot, so the path that reads one serves the
state's real sample-ballot pages (fixtures_oh_sample_ballots.json) as the
capture's parsed body, under a digest computed here."""

import base64
import hashlib
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from app.pipeline.fetch import ballot_measures_oh as oh
from app.pipeline.fetch import web_archive

HERE = Path(__file__).parent
CDX = json.loads((HERE / "fixtures_web_archive_cdx.json").read_text())
OH = json.loads((HERE / "fixtures_oh_sample_ballots.json").read_text())
BALLOT = "https://www.ohiosos.gov/assets/dir2026-45-official-sample-ballot-november-3-2026.pdf"


def _digest(body: bytes) -> str:
    return base64.b32encode(hashlib.sha1(body).digest()).decode()


def test_the_newest_200_capture_is_chosen():
    capture = web_archive.newest_from_cdx(CDX["ohiosos_rules_2025"])
    assert capture.timestamp == "20250823141739"
    assert capture.captured_on == date(2025, 8, 23)
    assert capture.raw_url == f"https://web.archive.org/web/20250823141739id_/{oh.DIRECTIVES_URL}"


def test_a_capture_of_the_wall_is_never_a_copy():
    # The Archive's crawler was refused too: its 403s are the block page.
    assert web_archive.newest_from_cdx(CDX["ohiosos_rules_2026"]) is None
    assert web_archive.newest_from_cdx(CDX["ohiosos_sample_ballot_2026"]) is None


def test_captured_on_reads_only_a_wayback_address():
    assert web_archive.captured_on(f"https://web.archive.org/web/20261020123456/{BALLOT}") == date(2026, 10, 20)
    assert web_archive.captured_on(BALLOT) is None
    assert web_archive.captured_on(None) is None
    assert web_archive.original_url(f"https://web.archive.org/web/20261020123456/{BALLOT}") == BALLOT
    assert web_archive.original_url(BALLOT) == BALLOT


def _archive(rows_by_url: dict, bodies: dict, seen: list):
    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.url.host == "web.archive.org"  # never the walled host
        assert "Civitas/1.0" in request.headers["user-agent"]
        if request.url.path == "/cdx/search/cdx":
            return httpx.Response(200, json=rows_by_url.get(request.url.params["url"], []))
        return httpx.Response(200, content=bodies[str(request.url)])
    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


def _row(url, body, stamp="20261021090000"):
    return [["timestamp", "original", "statuscode", "digest"], [stamp, url, "200", _digest(body)]]


@pytest.fixture
def ballot_pages(monkeypatch):
    pages = [{"width": p["width"], "words": [dict(zip(("text", "x0", "x1", "top"), w)) for w in p["words"]]}
             for p in OH["sample_2026"]]
    monkeypatch.setattr(oh, "pdf_words", lambda raw: pages if raw == b"%PDF ballot" else [])


@pytest.mark.asyncio
async def test_a_verified_capture_of_the_known_address_is_read_and_credited(ballot_pages):
    seen = []
    rows = {oh.DIRECTIVES_URL: CDX["ohiosos_rules_2026"], BALLOT: _row(BALLOT, b"%PDF ballot")}
    bodies = {f"https://web.archive.org/web/20261021090000id_/{BALLOT}": b"%PDF ballot"}
    async with _archive(rows, bodies, seen) as client:
        pairs = await oh.fetch_archived(client, 2026, [BALLOT])
    ((three, url),) = pairs
    assert three["official_title"] == "TO REQUIRE VOTERS TO PRESENT PHOTO IDENTIFICATION IN ORDER TO VOTE"
    assert url == f"https://web.archive.org/web/20261021090000/{BALLOT}"
    assert not any("save" in u for u in seen)  # never asks the Archive to fetch the state now


@pytest.mark.asyncio
async def test_a_capture_that_is_not_the_recorded_body_is_refused(ballot_pages):
    rows = {BALLOT: _row(BALLOT, b"%PDF something else")}
    bodies = {f"https://web.archive.org/web/20261021090000id_/{BALLOT}": b"%PDF ballot"}
    async with _archive(rows, bodies, []) as client:
        assert await oh.fetch_archived(client, 2026, [BALLOT]) is None


@pytest.mark.asyncio
async def test_no_capture_and_off_host_addresses_are_no_copy(ballot_pages):
    seen = []
    async with _archive({}, {}, seen) as client:
        assert await oh.fetch_archived(client, 2026, [BALLOT, "https://county.example.gov/ballot.pdf"]) is None
    assert all(urlparse(u).hostname == "web.archive.org" for u in seen)


@pytest.mark.asyncio
async def test_another_elections_capture_is_refused(ballot_pages):
    rows = {BALLOT: _row(BALLOT, b"%PDF ballot")}
    bodies = {f"https://web.archive.org/web/20261021090000id_/{BALLOT}": b"%PDF ballot"}
    async with _archive(rows, bodies, []) as client:
        assert await oh.fetch_archived(client, 2028, [BALLOT]) is None


@pytest.mark.asyncio
async def test_the_ballot_is_found_from_an_archived_directives_page(ballot_pages):
    # A page read only for its link is not held to the digest (a compressed
    # page comes back decoded); the document it links is.
    page = "20261020120000"
    rows = {
        oh.DIRECTIVES_URL: [["timestamp", "original", "statuscode", "digest"],
                            [page, oh.DIRECTIVES_URL, "200", "NOTTHEDECODEDBODY"]],
        BALLOT: _row(BALLOT, b"%PDF ballot"),
    }
    bodies = {
        f"https://web.archive.org/web/{page}id_/{oh.DIRECTIVES_URL}": OH["directives_page"].encode(),
        f"https://web.archive.org/web/20261021090000id_/{BALLOT}": b"%PDF ballot",
    }
    async with _archive(rows, bodies, []) as client:
        ((three, url),) = await oh.fetch_archived(client, 2026, [])
    assert three["number"] == "3" and url.endswith(BALLOT)
