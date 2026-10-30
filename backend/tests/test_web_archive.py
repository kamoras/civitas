"""The Internet Archive route for a walled state's own document
(web_archive.py, ballot_measures_oh.fetch_archived,
ballot_measures_mt.fetch_archived). Index answers are the Archive's real
ones (fixtures_web_archive_cdx.json); the Archive holds no capture of Ohio's
2026 sample ballot, so the path that reads one serves the state's real
sample-ballot pages (fixtures_oh_sample_ballots.json) as the capture's
parsed body, under a digest computed here. Montana's are served from its
real page and statements (fixtures_mt_*), the page's links rewritten to
captures the way the Archive's replay rewrites them."""

import base64
import hashlib
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from app.pipeline.fetch import ballot_measures_mt as mt
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


ARCHIVE_HOSTS = {"web.archive.org", "wayback.archive-it.org", "partner.archive-it.org"}


@pytest.fixture(autouse=True)
def _no_remembered_collections(monkeypatch):
    monkeypatch.setattr(web_archive, "_collections_by_host", {})
    monkeypatch.setattr(web_archive, "_archive_it_rests_until", 0.0)


def _archive(rows_by_url: dict, bodies: dict, seen: list, seeds: list | None = None, archive_it: dict | None = None):
    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.url.host in ARCHIVE_HOSTS  # never the walled host
        assert "Civitas/1.0" in request.headers["user-agent"]
        if request.url.host == "partner.archive-it.org":
            return httpx.Response(200, json=seeds or [])
        if request.url.path.endswith("/timemap/cdx"):
            collection = request.url.path.split("/")[1]
            return httpx.Response(200, text=(archive_it or {}).get((collection, request.url.params["url"]), ""))
        if request.url.path == "/cdx/search/cdx":
            return httpx.Response(200, json=rows_by_url.get(request.url.params["url"], []))
        return httpx.Response(200, content=bodies[str(httpx.URL(str(request.url)))])
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
    assert all(urlparse(u).hostname in ARCHIVE_HOSTS for u in seen)


def _seed(collection, url, **over):
    return {"collection": collection, "url": url, "active": True, "publicly_visible": True, "deleted": False, **over}


def _timemap_line(url, body, stamp, mimetype="application/pdf"):
    return f"key {stamp} {url} {mimetype} 200 {_digest(body)} - - 10 20 ARCHIVEIT-1.warc.gz"


FRONT = {("5763", "https://www.ohiosos.gov/"): _timemap_line("https://www.ohiosos.gov/", b"front", "20261001000000", "text/html")}


def test_archive_it_capture_addresses_are_read_like_wayback_ones():
    page = f"https://wayback.archive-it.org/5763/20260915190620/{BALLOT}"
    assert web_archive.captured_on(page) == date(2026, 9, 15)
    assert web_archive.original_url(page) == BALLOT
    assert web_archive.original_url(f"https://wayback.archive-it.org/5763/20260915190620id_/{BALLOT}") == BALLOT


def test_an_archive_it_timemap_gives_its_newest_200_and_skips_revisits():
    text = "\n".join([
        _timemap_line(BALLOT, b"old", "20250101000000"),
        _timemap_line(BALLOT, b"new", "20260915190620"),
        f"key 20261001000000 {BALLOT} warc/revisit 200 {_digest(b'new')} - - 1 2 f.warc.gz",
        f"key 20261002000000 {BALLOT} text/html 403 {_digest(b'wall')} - - 1 2 f.warc.gz",
    ])
    capture = web_archive.newest_from_archive_it(text, "5763")
    assert capture.timestamp == "20260915190620"
    assert capture.raw_url == f"https://wayback.archive-it.org/5763/20260915190620id_/{BALLOT}"
    assert capture.page_url == f"https://wayback.archive-it.org/5763/20260915190620/{BALLOT}"


@pytest.mark.asyncio
async def test_a_state_librarys_capture_is_read_when_the_wayback_index_lacks_it(ballot_pages):
    # California's 2026 proposition pages, 2026-10-29: held by the state
    # library's Archive-It collection and absent from the Wayback index.
    seeds = [
        _seed(5763, "https://www.ohiosos.gov/"),
        _seed(965, "https://www.ohiosos.gov/", active=False),  # a closed collection is not asked
        _seed(17, "https://www.ohiosos.gov/", publicly_visible=False),  # nor a private one
        _seed(18, "https://elsewhere.example.org/www.ohiosos.gov"),  # nor one seeded on another host
    ]
    archive_it = {("5763", BALLOT): _timemap_line(BALLOT, b"%PDF ballot", "20260915190620"), **FRONT}
    bodies = {f"https://wayback.archive-it.org/5763/20260915190620id_/{BALLOT}": b"%PDF ballot"}
    seen = []
    async with _archive({}, bodies, seen, seeds, archive_it) as client:
        ((three, url),) = await oh.fetch_archived(client, 2026, [BALLOT])
    assert three["official_title"] == "TO REQUIRE VOTERS TO PRESENT PHOTO IDENTIFICATION IN ORDER TO VOTE"
    assert url == f"https://wayback.archive-it.org/5763/20260915190620/{BALLOT}"
    assert web_archive.captured_on(url) == date(2026, 9, 15)
    asked = {urlparse(u).path.split("/")[1] for u in seen if u.endswith("cdx") or "/timemap/" in u}
    assert asked <= {"5763", "cdx"}


@pytest.mark.asyncio
async def test_the_newer_capture_of_the_two_indexes_is_the_copy(ballot_pages):
    rows = {BALLOT: _row(BALLOT, b"%PDF ballot", stamp="20261021090000")}
    archive_it = {("5763", BALLOT): _timemap_line(BALLOT, b"%PDF older", "20260915190620"), **FRONT}
    bodies = {f"https://web.archive.org/web/20261021090000id_/{BALLOT}": b"%PDF ballot"}
    async with _archive(rows, bodies, [], [_seed(5763, "https://www.ohiosos.gov/")], archive_it) as client:
        ((_, url),) = await oh.fetch_archived(client, 2026, [BALLOT])
    assert url == f"https://web.archive.org/web/20261021090000/{BALLOT}"


@pytest.mark.asyncio
async def test_the_collection_with_the_newest_front_page_is_asked():
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "partner.archive-it.org":
            return httpx.Response(200, json=[_seed(c, "https://www.ohiosos.gov/") for c in (12, 5763, 99)])
        collection = request.url.path.split("/")[1]
        stamps = {"12": "20200101000000", "5763": "20261001000000"}  # 99 holds no copy
        front = request.url.params["url"]
        return httpx.Response(200, text=_timemap_line(front, b"f", stamps[collection], "text/html") if collection in stamps else "")

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        assert await web_archive._collections(client, "www.ohiosos.gov") == ["5763"]


@pytest.mark.asyncio
@pytest.mark.parametrize("unread", ["seeds", "front"])
async def test_an_unread_archive_it_index_is_not_remembered_as_none(unread):
    # Archive-It answers its rate limit with an HTML page and a 200.
    calls = []
    rate_limited = "<html><body>You have exceeded the limit for the number of requests</body></html>"

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        first = len([h for h in calls if h == request.url.host]) == 1
        if request.url.host == "partner.archive-it.org":
            if unread == "seeds" and first:
                return httpx.Response(200, text="not json")
            return httpx.Response(200, json=[_seed(5763, "https://www.ohiosos.gov/")])
        if unread == "front" and first:
            return httpx.Response(200, text=rate_limited)
        return httpx.Response(200, text=_timemap_line(request.url.params["url"], b"f", "20261001000000", "text/html"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        assert await web_archive._collections(client, "www.ohiosos.gov") == []
        if unread == "front":  # a rate-limit page rests Archive-It for the run's next pages
            assert await web_archive._archive_it_captures(client, BALLOT) == []
            web_archive._archive_it_rests_until = 0.0
        assert await web_archive._collections(client, "www.ohiosos.gov") == ["5763"]


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
        f"https://web.archive.org/web/{page}if_/{oh.DIRECTIVES_URL}": OH["directives_page"].encode(),
        f"https://web.archive.org/web/20261021090000id_/{BALLOT}": b"%PDF ballot",
    }
    async with _archive(rows, bodies, []) as client:
        ((three, url),) = await oh.fetch_archived(client, 2026, [])
    assert three["number"] == "3" and url.endswith(BALLOT)


MT_PAGE = (HERE / "fixtures_mt_proposed_ballot_issues_2026.html").read_text()
MT_LANGUAGE = json.loads((HERE / "fixtures_mt_ballot_language_2026.json").read_text())
MT_FILES = {"CI-132": "72919", "CI-133": "73545", "I-194": "73640"}
MT_PAGE_STAMP, MT_STAMP = "20261019182544", "20261003010815"


def _key(url: str) -> str:
    return str(httpx.URL(url))


def _mt_archive(*, pdf_body=lambda issue: f"%PDF-{issue}".encode(), pdf_host="https://sosmt.gov/"):
    """Index rows and bodies for Montana's page, its three preview
    wrappers (replayed, links rewritten) and their statement PDFs (raw)."""
    page_url = mt.URL_PATTERN.format(year=2026)
    page = web_archive.Capture(MT_PAGE_STAMP, page_url, "NOTTHEDECODEDBODY")
    rewritten = MT_PAGE.replace('href="https://sosmt.gov/', f'href="/web/{MT_PAGE_STAMP}/https://sosmt.gov/')
    rows = {page_url: [["timestamp", "original", "statuscode", "digest"], [page.timestamp, page_url, "200", page.digest]]}
    bodies = {_key(page.replay_url): rewritten.encode()}
    for issue, file_id in MT_FILES.items():
        wrapper_url = next(h for i, h in mt.qualified_issues(MT_PAGE, 2026) if i == issue)
        wrapper = web_archive.Capture(MT_STAMP, wrapper_url, "NOTTHEDECODEDBODY")
        pdf_url = f"{pdf_host}wp-content/uploads/wpfd/preview_files/{issue}(abc).pdf"
        rows[wrapper_url] = [["timestamp", "original", "statuscode", "digest"],
                             [MT_STAMP, wrapper_url, "200", wrapper.digest]]
        bodies[_key(wrapper.replay_url)] = (
            f'<html><body><iframe src="https://web.archive.org/web/{MT_STAMP}if_/{pdf_url}" '
            f'type="application/pdf"></iframe></body></html>'
        ).encode()
        rows[pdf_url] = _row(pdf_url, f"%PDF-{issue}".encode(), MT_STAMP)
        bodies[_key(f"https://web.archive.org/web/{MT_STAMP}id_/{pdf_url}")] = pdf_body(issue)
    return rows, bodies


@pytest.fixture
def mt_statements(monkeypatch):
    monkeypatch.setattr(mt, "_first_page_text", lambda raw: MT_LANGUAGE[raw[len(b"%PDF-"):].decode()])


@pytest.mark.asyncio
async def test_montana_is_read_from_its_archived_page_and_statements(mt_statements):
    seen = []
    rows, bodies = _mt_archive()
    async with _archive(rows, bodies, seen) as client:
        pairs = await mt.fetch_archived(client, 2026, [])
    assert [p["number"] for p, _ in pairs] == ["CI-132", "CI-133", "I-194"]
    for parsed, url in pairs:
        # The same statement, word for word, as the state's own copy gives.
        assert parsed == mt.parse_ballot_language(MT_LANGUAGE[parsed["number"]], parsed["number"])
        assert url == (f"https://web.archive.org/web/{MT_STAMP}/https://sosmt.gov/wp-content/uploads/wpfd/"
                       f"preview_files/{parsed['number']}(abc).pdf")
    assert not any("save" in u for u in seen)
    # Pages are read from the replay; the quoted PDFs raw, digest-checked.
    assert all("if_/" in u for u in seen if "admin-ajax" in u and "/cdx/" not in u)
    assert sum("id_/" in u for u in seen) == 3


@pytest.mark.asyncio
async def test_montana_fails_whole_when_one_statement_is_not_the_recorded_capture(mt_statements):
    rows, bodies = _mt_archive(pdf_body=lambda issue: b"%PDF-CI-132" if issue == "CI-133" else f"%PDF-{issue}".encode())
    async with _archive(rows, bodies, []) as client:
        assert await mt.fetch_archived(client, 2026, []) is None


@pytest.mark.asyncio
async def test_montana_statement_off_the_state_host_is_no_copy(mt_statements):
    rows, bodies = _mt_archive(pdf_host="https://mirror.example.org/")
    async with _archive(rows, bodies, []) as client:
        assert await mt.fetch_archived(client, 2026, []) is None


def test_montana_is_an_archived_strategy():
    from app.pipeline.fetch import ballot_measures_pdf as pdf

    assert pdf.ARCHIVED_STRATEGIES["mt_qualified_ballot_issues"] is mt.fetch_archived


@pytest.mark.asyncio
async def test_montana_without_a_capture_of_its_page_is_no_copy(mt_statements):
    async with _archive({}, {}, []) as client:
        assert await mt.fetch_archived(client, 2026, []) is None
