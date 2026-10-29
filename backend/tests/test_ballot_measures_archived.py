"""Every measures reader falls back to the Internet Archive's copy of the
pages it reads when the state's own read fails
(ballot_measures_pdf._read_archived, web_archive.ArchiveTransport), and a
capture verifies whichever body its digest was taken over."""

import base64
import gzip
import hashlib

import httpx
import pytest

from app.models import BallotMeasure
from app.pipeline.fetch import ballot_measures_pdf as pdf
from app.pipeline.fetch import web_archive

INDEX = "https://sos.example.gov/2026/measures/"
PAGE = "https://sos.example.gov/2026/measures/1.htm"
BODY = b"<h1>Measure 1</h1><p>A YES vote means the tax is kept.</p>"


def _digest(body: bytes) -> str:
    return base64.b32encode(hashlib.sha1(body).digest()).decode()


def _archive(bodies: dict, seen: list, *, gzip_digest=False) -> httpx.AsyncClient:
    """The Archive: a CDX index and raw captures (served gzip-encoded),
    each digest over the decoded body or, with gzip_digest, the wire."""
    wire = {url: gzip.compress(body) for url, body in bodies.items()}

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.url.host == "web.archive.org"  # never the state's host
        if request.url.path == "/cdx/search/cdx":
            url = request.url.params["url"]
            if url not in bodies:
                return httpx.Response(200, json=[])
            digest = _digest(wire[url] if gzip_digest else bodies[url])
            return httpx.Response(200, json=[["timestamp", "original", "statuscode", "digest", "mimetype"],
                                             ["20261020120000", url, "200", digest, "text/html"]])
        original = str(request.url).split("id_/", 1)[1]
        return httpx.Response(200, stream=httpx.ByteStream(wire[original]), headers={"Content-Encoding": "gzip"})

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


async def _reader(client, year):
    """A state's reader: its index, then the page it links."""
    index = await client.get(INDEX)
    index.raise_for_status()
    page = await client.get(PAGE)
    page.raise_for_status()
    return [({"number": "1", "title": "Measure 1", "origin": None, "official_summary": page.text,
              "fiscal_impact": None, "yes_means": None, "no_means": None}, PAGE + "#m1")]


async def _walled(client, year):
    raise httpx.ConnectError("refused")


@pytest.fixture
def state(monkeypatch):
    monkeypatch.setattr(pdf, "source_for_state", lambda s: {"strategy": "fake_multi", "source_name": "Example SOS"})
    calls = {"n": 0}

    async def reader(client, year):
        calls["n"] += 1
        # The state's own read (the first) fails; the second is the Archive's.
        return await (_walled if calls["n"] == 1 else _reader)(client, year)

    monkeypatch.setitem(pdf.MULTI_DOCUMENT_STRATEGIES, "fake_multi", reader)


@pytest.mark.asyncio
@pytest.mark.parametrize("gzip_digest", [False, True])
async def test_a_failed_read_is_read_again_from_the_archive_and_credited(state, db_session, gzip_digest):
    seen = []
    async with _archive({INDEX: b"<a href='1.htm'>1</a>", PAGE: BODY}, seen, gzip_digest=gzip_digest) as client:
        measures = await pdf.fetch_state_measures_pdf(client, db_session, "ZZ", 2026, "2026-11-03")
    assert len(measures) == 1
    assert measures[0]["official_summary"] == BODY.decode()
    assert measures[0]["source_url"] == f"https://web.archive.org/web/20261020120000/{PAGE}#m1"
    assert measures[0]["republished_by"] == web_archive.PUBLISHER


@pytest.mark.asyncio
async def test_a_page_with_no_capture_fails_the_read(state, db_session):
    async with _archive({INDEX: b"index only"}, []) as client:
        assert await pdf.fetch_state_measures_pdf(client, db_session, "ZZ", 2026, "2026-11-03") is None


@pytest.mark.asyncio
async def test_a_capture_that_differs_from_what_is_stored_is_refused(state, db_session):
    db_session.add(BallotMeasure(id="ZZ-2026-11-03-1", state="ZZ", election_date="2026-11-03", number="1",
                                 title="Measure 1", official_summary="the wording read from the state", status="active"))
    db_session.commit()
    async with _archive({INDEX: b"i", PAGE: BODY}, []) as client:
        assert await pdf.fetch_state_measures_pdf(client, db_session, "ZZ", 2026, "2026-11-03") is None


@pytest.mark.asyncio
async def test_a_capture_that_matches_neither_body_is_refused():
    async with _archive({PAGE: BODY}, []) as client:
        capture = await web_archive.newest_capture(client, PAGE)
        tampered = web_archive.Capture(capture.timestamp, capture.original, _digest(b"other"), capture.mimetype)
        assert await web_archive.read_capture(client, capture) == BODY
        assert await web_archive.read_capture(client, tampered) is None
