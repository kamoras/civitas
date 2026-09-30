"""Tests for http_utils' two fetch helpers.

Two things are covered here: the wall-clock hang backstop that both helpers
apply (see app/http_client.py for what it's for), and fetch_with_retry_requests
itself — the requests-based helper UCSB-sourced president fetchers use instead
of fetch_with_retry (httpx), after presidency.ucsb.edu started blanket-403ing
httpx's requests while leaving `requests` unaffected (confirmed live,
2026-07-25). That second group mirrors fetch_with_retry's retry/backoff
contract closely enough that those tests mostly double-check the swap didn't
change caller-visible behavior.
"""

import asyncio
import io
import re
import threading
import tokenize
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.pipeline.fetch.http_utils import (
    BROWSER_HEADERS,
    fetch_bytes_with_retry,
    fetch_json_with_retry,
    fetch_text_with_retry,
    fetch_with_retry,
    fetch_with_retry_requests,
)
from app.pipeline.rate_limiter import RateLimiter


def _limiter():
    return RateLimiter(rps=1000.0)  # effectively unthrottled for tests


class TestFetchWithRetryHangBackstop:
    """Confirmed live 2026-08-02: a House pipeline run sat wedged 12h+ on a
    congress.gov call that never returned and never raised — a stale pooled
    httpx connection in CLOSE_WAIT that the client's own `timeout=` failed to
    catch. fetch_with_retry applies the shared wall-clock backstop (see
    app/http_client.py) so a call that never completes is treated as a failed
    attempt within a bounded time instead of hanging the pipeline forever.

    The backstop also lives on the client itself (make_async_client), which
    is what covers the ~20 call sites that use httpx directly; it is kept
    here as well because this function takes whatever AsyncClient a caller
    hands it — including the plain ones these tests pass.
    """

    @staticmethod
    def _hanging_client():
        async def _hang(*args, **kwargs):
            await asyncio.sleep(3600)

        client = MagicMock()
        client.request = AsyncMock(side_effect=_hang)
        return client

    @pytest.mark.asyncio
    async def test_hung_request_is_bounded_and_gives_up(self):
        client = self._hanging_client()
        result = await fetch_with_retry(
            client, _limiter(), "GET", "https://example.test",
            retries=1, timeout=0.01,
        )
        assert result is None
        assert client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_hung_request_is_retried_like_any_other_failed_attempt(self):
        """The backstop raises into the same `except` the retry loop already
        uses, so a hang burns attempts rather than aborting the fetch on the
        first one."""
        client = self._hanging_client()
        result = await fetch_with_retry(
            client, _limiter(), "GET", "https://example.test",
            retries=3, backoff_s=0.001, timeout=0.01,
        )
        assert result is None
        assert client.request.call_count == 3

    @pytest.mark.asyncio
    async def test_hung_requests_backstop_is_bounded_in_wall_clock(self):
        client = self._hanging_client()
        start = asyncio.get_running_loop().time()
        await fetch_with_retry(
            client, _limiter(), "GET", "https://example.test",
            retries=2, backoff_s=0.001, timeout=0.01,
        )
        elapsed = asyncio.get_running_loop().time() - start
        assert elapsed < 1.0  # vs. the unbounded 12h+ this replaces

    @pytest.mark.asyncio
    async def test_requests_variant_is_bounded_too(self):
        """The `requests`-based sibling (used by every UCSB-sourced president
        fetcher) has the same unbounded shape and the same bound.

        The worker thread is released explicitly at the end rather than left
        sleeping: `asyncio.to_thread` runs on the default ThreadPoolExecutor,
        whose threads are joined at interpreter exit — a test that abandoned a
        sleeping one would hang the whole pytest process on the way out. That
        is also the honest shape of the production caveat documented in
        http_utils: the bound frees the event loop, not the thread.
        """
        release = threading.Event()

        def _hang(*args, **kwargs):
            release.wait(30)
            raise AssertionError("test did not release the worker thread")

        try:
            with patch("app.pipeline.fetch.http_utils.requests.request", side_effect=_hang):
                start = asyncio.get_running_loop().time()
                result = await fetch_with_retry_requests(
                    _limiter(), "GET", "https://example.test",
                    retries=1, timeout=0.01,
                )
                elapsed = asyncio.get_running_loop().time() - start
        finally:
            release.set()
        assert result is None
        assert elapsed < 1.0


class TestFetchWithRetryRequests:
    @pytest.mark.asyncio
    async def test_returns_response_on_success(self):
        resp = MagicMock(status_code=200, text="ok")
        with patch("app.pipeline.fetch.http_utils.requests.request", return_value=resp):
            result = await fetch_with_retry_requests(_limiter(), "GET", "https://example.test")
        assert result is resp

    @pytest.mark.asyncio
    async def test_returns_none_after_exhausting_retries_on_4xx(self):
        resp = MagicMock(status_code=403, text="")
        with patch("app.pipeline.fetch.http_utils.requests.request", return_value=resp):
            result = await fetch_with_retry_requests(
                _limiter(), "GET", "https://example.test", retries=2, backoff_s=0.001,
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_recovers_after_a_transient_failure(self):
        ok = MagicMock(status_code=200, text="ok")
        with patch(
            "app.pipeline.fetch.http_utils.requests.request",
            side_effect=[ConnectionError("boom"), ok],
        ):
            result = await fetch_with_retry_requests(
                _limiter(), "GET", "https://example.test", retries=2, backoff_s=0.001,
            )
        assert result is ok

    @pytest.mark.asyncio
    async def test_429_retries_then_succeeds(self):
        limited = MagicMock(status_code=429, text="")
        ok = MagicMock(status_code=200, text="ok")
        with patch(
            "app.pipeline.fetch.http_utils.requests.request",
            side_effect=[limited, ok],
        ):
            result = await fetch_with_retry_requests(
                _limiter(), "GET", "https://example.test", retries=2, backoff_s=0.001,
            )
        assert result is ok


class TestFetchJsonWithRetry:
    """Extracted from three near-identical per-state _get_json copies
    (Arkansas's own Tally ENR module, since generalized into
    state_candidates_tally_enr.py; state_candidates_ct.py; state_candidates_in.py)
    — the two things those copies each did on top of fetch_with_retry:
    parse the response body, and turn a bad body into a logged None
    instead of a raised ValueError."""

    @pytest.mark.asyncio
    async def test_returns_the_parsed_json_body_on_success(self):
        resp = MagicMock(status_code=200, json=lambda: {"ok": True})
        client = MagicMock()
        client.request = AsyncMock(return_value=resp)
        result = await fetch_json_with_retry(client, _limiter(), "https://example.test", "label")
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_a_body_that_is_not_valid_json_returns_none_not_a_raise(self):
        def _raise():
            raise ValueError("no JSON object could be decoded")

        resp = MagicMock(status_code=200, json=_raise)
        client = MagicMock()
        client.request = AsyncMock(return_value=resp)
        result = await fetch_json_with_retry(client, _limiter(), "https://example.test", "label")
        assert result is None

    # A fetch failure (fetch_with_retry returning None) is not retested
    # here -- fetch_json_with_retry's only new behavior over fetch_with_
    # retry is the .json() parse and its error handling, both covered
    # above; fetch_with_retry's own retry/failure behavior is covered by
    # TestFetchWithRetryHangBackstop.


class TestFetchTextWithRetry:
    """Extracted from three byte-identical per-module _get_text/_fetch_html
    copies (ballot_measures_mo.py, ballot_measures_va.py,
    state_candidates_totalvote.py)."""

    @pytest.mark.asyncio
    async def test_returns_the_response_text_on_success(self):
        resp = MagicMock(status_code=200, text="<html>hello</html>")
        client = MagicMock()
        client.request = AsyncMock(return_value=resp)
        result = await fetch_text_with_retry(client, _limiter(), "https://example.test", "label")
        assert result == "<html>hello</html>"

    @pytest.mark.asyncio
    async def test_a_fetch_failure_returns_none(self):
        client = MagicMock()
        client.request = AsyncMock(side_effect=Exception("boom"))
        result = await fetch_text_with_retry(
            client, _limiter(), "https://example.test", "label", retries=0,
        )
        assert result is None


class TestFetchBytesWithRetry:
    """Extracted from three copies of the same fetch-then-return-content
    wrapper (ballot_measures_va.py's `_get_bytes`, house_ptr.py's
    `_fetch_bytes_with_retry`, state_candidates_wy.py's `_fetch_zip`)."""

    @pytest.mark.asyncio
    async def test_returns_the_response_content_on_success(self):
        resp = MagicMock(status_code=200, content=b"\x50\x4b\x03\x04fake-zip-bytes")
        client = MagicMock()
        client.request = AsyncMock(return_value=resp)
        result = await fetch_bytes_with_retry(client, _limiter(), "https://example.test", "label")
        assert result == b"\x50\x4b\x03\x04fake-zip-bytes"

    @pytest.mark.asyncio
    async def test_a_fetch_failure_returns_none(self):
        client = MagicMock()
        client.request = AsyncMock(side_effect=Exception("boom"))
        result = await fetch_bytes_with_retry(
            client, _limiter(), "https://example.test", "label", retries=0,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_retry_kwargs_forward_to_fetch_with_retry(self):
        # house_ptr.py's own copy set retry_on_4xx=False -- this proves
        # that per-caller difference is still honored through the shared
        # helper rather than lost in extraction.
        resp = MagicMock(status_code=404, content=b"not found")
        client = MagicMock()
        client.request = AsyncMock(return_value=resp)
        result = await fetch_bytes_with_retry(
            client, _limiter(), "https://example.test", "label", retry_on_4xx=False,
        )
        assert result is None
        client.request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_headers_default_to_browser_headers_but_none_opts_out(self):
        # house_ptr.py passes headers=None deliberately to keep its
        # pre-extraction behavior (httpx's own bare defaults, never
        # BROWSER_HEADERS) -- this proves that override actually reaches
        # the request rather than the default silently winning anyway.
        resp = MagicMock(status_code=200, content=b"ok")
        client = MagicMock()
        client.request = AsyncMock(return_value=resp)

        await fetch_bytes_with_retry(client, _limiter(), "https://example.test", "label")
        assert client.request.await_args.kwargs["headers"] == BROWSER_HEADERS

        client.request.reset_mock()
        await fetch_bytes_with_retry(client, _limiter(), "https://example.test", "label", headers=None)
        assert client.request.await_args.kwargs["headers"] is None


if __name__ == "__main__":
    import asyncio

    async def demo():
        await TestFetchWithRetryHangBackstop().test_hung_request_is_bounded_and_gives_up()
        await TestFetchWithRetryRequests().test_returns_response_on_success()
        await TestFetchJsonWithRetry().test_returns_the_parsed_json_body_on_success()
        await TestFetchWithRetryRequests().test_returns_none_after_exhausting_retries_on_4xx()
        await TestFetchWithRetryRequests().test_recovers_after_a_transient_failure()
        await TestFetchWithRetryRequests().test_429_retries_then_succeeds()
        print("OK")

    asyncio.run(demo())


class TestExpectedStatuses:
    """`expected_statuses` exists for a caller needing three outcomes,
    not two: Google Civic's voterinfo answers 404 "No information for
    this address" for a precinct whose ballot isn't published yet, which
    is its normal answer -- while None there must keep meaning the fetch
    broke. `no_retry_statuses` can't express that, since it returns the
    same None a real failure does."""

    def _client(self, status):
        client = MagicMock()
        client.request = AsyncMock(return_value=httpx.Response(
            status, request=httpx.Request("GET", "https://example.test/x"),
        ))
        return client

    @pytest.mark.asyncio
    async def test_an_expected_status_comes_back_as_the_response(self):
        client = self._client(404)
        resp = await fetch_with_retry(
            client, _limiter(), "GET", "https://example.test/x", expected_statuses=(404,),
        )
        assert resp is not None and resp.status_code == 404

    @pytest.mark.asyncio
    async def test_an_expected_status_is_not_retried(self):
        """The cost half of the bug: 4xx is retried by default, so every
        one of these was 3 requests plus backoff sleeps per address."""
        client = self._client(404)
        await fetch_with_retry(
            client, _limiter(), "GET", "https://example.test/x", expected_statuses=(404,),
        )
        assert client.request.await_count == 1

    @pytest.mark.asyncio
    async def test_an_unlisted_status_still_fails_the_normal_way(self):
        client = self._client(500)
        resp = await fetch_with_retry(
            client, _limiter(), "GET", "https://example.test/x",
            retries=2, backoff_s=0, expected_statuses=(404,),
        )
        assert resp is None
        assert client.request.await_count == 2


@pytest.mark.asyncio
async def test_a_moved_page_is_followed_not_read_as_an_empty_success():
    # Alaska's /candidates/ answers 301 to /election-candidates/ with an
    # empty body; returning that 301 as success read as a page listing
    # nothing.
    def handler(request):
        if request.url.path == "/candidates/":
            return httpx.Response(301, headers={"location": "/election-candidates/"})
        return httpx.Response(200, text="the list")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resp = await fetch_with_retry(client, RateLimiter(rps=1000), "GET", "https://sos.test/candidates/")
    assert resp is not None and resp.text == "the list"


# The address the pipeline and scripts used to give, which never existed.
_OLD_CONTACT = "contact@civitas-research.org"
# The repo's contact lines for people (security reports, the code of
# conduct): the address given to readers, not to the servers we fetch from.
_FOR_PEOPLE = ("SECURITY.md", "CODE_OF_CONDUCT.md")


def _checked_in(backend):
    """The files git tracks in the checkout around `backend`; outside a git checkout (the
    backend image) the backend's own code instead. In CI a git failure is
    an error, never a quietly narrower sweep."""
    import os
    import subprocess

    repo = backend.parent
    # A git hook's GIT_DIR / GIT_INDEX_FILE would point elsewhere.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True, timeout=30, env=env,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        if os.environ.get("CI"):
            raise
        out = b""
    files = [repo / os.fsdecode(name) for name in out.split(b"\0") if name]
    if files:
        return files
    return [
        p for d in ("app", "scripts", "migrations") for p in (backend / d).rglob("*")
        if p.suffix in (".py", ".json") and "__pycache__" not in p.parts
    ]


def _text(path) -> str:
    """A file's text, lowercased, with adjacent string literals run
    together ("x@" "y.com" reads as "x@y.com"); UTF-16 by its byte-order
    mark; "" for a directory entry or a tracked file deleted locally.
    (A copy built with + or an f-string is out of reach of a text sweep.)"""
    import re

    try:
        data = path.read_bytes()
    except OSError:
        return ""
    encoding = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    text = data.decode(encoding, errors="ignore").lower()
    return re.sub(r"""["']\s*["']""", "", text)


def test_contact_address_is_real_and_held_once():
    """Sources are told how to reach us; the address has to be one that is
    read. contact@civitas-research.org never existed, and SEC's fair-access
    policy asks for a working email."""
    import re
    from pathlib import Path

    from app.contact import CONTACT_EMAIL
    from app.pipeline.fetch import sec_tickers, state_candidates_tx
    from app.pipeline.fetch.http_utils import BROWSER_HEADERS

    assert re.fullmatch(r"[^@\s()]+@[^@\s()]+\.[a-z]{2,}", CONTACT_EMAIL)
    assert BROWSER_HEADERS["User-Agent"].endswith(f"(+{CONTACT_EMAIL})")
    assert sec_tickers._HEADERS["User-Agent"].endswith(CONTACT_EMAIL)
    assert state_candidates_tx._HEADERS["User-Agent"].endswith(CONTACT_EMAIL)

    this = Path(__file__).resolve()
    backend = this.parents[1]
    files = _checked_in(backend)
    contact_py = (backend / "app" / "contact.py").resolve()
    assert contact_py in {p.resolve() for p in files}, "app/contact.py is not among the files swept (untracked?)"

    # The old address nowhere; the real one only in contact.py (everything
    # else names it through the constant) and the contact lines for people.
    allowed = {contact_py, *((backend.parent / name).resolve() for name in _FOR_PEOPLE)}
    for path in files:
        if path.resolve() == this:  # names the old address, to look for it
            continue
        text = _text(path)
        assert _OLD_CONTACT not in text, path
        assert path.resolve() in allowed or CONTACT_EMAIL.lower() not in text, path


# User-Agents sent without the contact on purpose, each explained where it
# is written: (file, a fragment of the line). New Hampshire's filter refuses
# the "(+contact)" comment; the bioguide photo host refuses anything but a
# browser; fetch_site_fonts.py sends next/font's own browser string, so
# Google Fonts serves the files next/font would.
_BROWSER_ONLY_USER_AGENTS = {
    ("backend/app/pipeline/fetch/state_candidates_nh.py", "Chrome/151"),
    ("frontend/src/lib/remoteImage.ts", "Chrome/128"),
    ("backend/scripts/fetch_site_fonts.py", "Chrome/104"),
}
# Where a User-Agent is set: a header key ("User-Agent": / ["User-Agent"] =
# / ("User-Agent", ...) — but not .get("User-Agent", default), which reads
# one — or a user_agent= / USER_AGENT = / UA = name.
_UA_SITE = re.compile(
    r"""(["']user-agent["']\s*(?:\]\s*=|:)|(?<!\.get)\(\s*["']user-agent["']\s*,|\[\s*["']user-agent["']\s*,)""",
    re.IGNORECASE,
)
# A name given a value (NAME = / NAME: type = / name: in a dict or object /
# .get("NAME", default)); it is a User-Agent site when one of its parts
# (split at "_" and at case changes) is ua or user+agent(s): _UA, botUA,
# DEFAULT_USER_AGENT, USER_AGENT_CHROME, defaultUserAgent, USER_AGENTS.
_NAME_SITE = re.compile(
    r"""(?<![\w$.])([A-Za-z_$][\w$]*)\s*(?::\s*[\w\[\]|. ]+?\s*)?(?<![=!<>])[:=](?!=)"""
    r"""|\.get\(\s*["']([A-Za-z_][\w]*)["']\s*,"""
)


def _names_a_user_agent(name: str) -> bool:
    parts = [p.lower() for p in re.findall(r"[A-Z]{2,}(?![a-z])|[A-Z]?[a-z0-9]+|[A-Z]", name)]
    joined = [a + b for a, b in zip(parts, parts[1:])]
    return "ua" in parts or any(p in ("useragent", "useragents") for p in parts + joined)


def _ua_sites(line: str):
    """Where on a line a User-Agent value starts."""
    ends = [m.end() for m in _UA_SITE.finditer(line)]
    ends += [m.end() for m in _NAME_SITE.finditer(line) if _names_a_user_agent(m.group(1) or m.group(2))]
    return sorted(ends)
# A string literal naming Civitas as a client: "Civitas/1.0", "Civitas-OG/1".
_CIVITAS_TOKEN = re.compile(r"""(?:^|[=:(,{\[]\s*)(?:[rbfu]{1,2})?["'`][^"'`\n]*\bcivitas[\w-]*/\d""", re.IGNORECASE)
# What counts as naming the contact in a value (or a constant built from it).
_NAMES_CONTACT = re.compile(
    r"CONTACT_EMAIL|BOT_USER_AGENT|SELF_FETCH_USER_AGENT|CIVIC_CONTACT|BROWSER_HEADERS|\+\$\{SITE_URL\}"
)


def _code_lines(path) -> list[str]:
    """A source file's lines with what sends nothing blanked: comments, and
    in Python triple-quoted strings (docstrings and prose, which may quote
    a User-Agent). Line numbers are kept."""
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".py":
        return [
            "" if line.lstrip().startswith(("//", "*", "/*")) else line
            for line in text.splitlines()
        ]
    lines = text.splitlines()
    blank = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        is_prose = tok.type == tokenize.STRING and re.match(r"[rbfu]{0,2}(\"\"\"|\'\'\')", tok.string, re.I)
        if tok.type == tokenize.COMMENT or is_prose:
            blank.append((tok.start, tok.end))
    for (r0, c0), (r1, c1) in reversed(blank):
        for r in range(r0, r1 + 1):
            line = lines[r - 1]
            a = c0 if r == r0 else 0
            b = c1 if r == r1 else len(line)
            lines[r - 1] = line[:a] + " " * (b - a) + line[b:]
    return lines


def _value_from(lines, i, rest):
    """The text of a value starting at `rest` on line i, carried onto the
    next lines while it is plainly unfinished: nothing yet but "(", an open
    bracket before any string, or a string ending the line that the next
    line continues (implicit concatenation). A value already holding a
    whole string stops there, so a neighbouring entry can't excuse it."""
    value, j = rest, i
    for _ in range(3):
        nxt = next((k for k in range(j + 1, len(lines)) if lines[k].strip()), None)
        if nxt is None:
            break
        opened = sum(value.count(c) for c in "([{") - sum(value.count(c) for c in ")]}")
        continues = re.match(r"""\s*(?:[rbfu]{1,2})?["'`]""", lines[nxt], re.I)
        has_string = re.search(r"""["'`]""", value)
        ends_in_string = re.search(r"""["'`]\s*$""", value)
        if value.strip() in ("", "(") or (opened > 0 and not has_string) or (continues and ends_in_string):
            value, j = value + " " + lines[nxt].strip(), nxt
        else:
            break
    return value


def _user_agent_offenders(backend, files):
    """(offenders, files read): every User-Agent value written as a
    literal, and every literal naming Civitas as a client, whose value
    doesn't name the contact. Paths are named from the repo root
    ("backend/app/…"), inside a git checkout or not. A browser string kept
    in a constant under some other name is out of reach of a text sweep;
    the reviews are the check there."""
    from app.contact import CONTACT_EMAIL

    offenders, read = [], 0
    for path in files:
        rel = (
            f"backend/{path.relative_to(backend).as_posix()}" if path.is_relative_to(backend)
            else path.relative_to(backend.parent).as_posix()
        )
        if path.suffix not in (".py", ".ts", ".tsx", ".mjs", ".js") or not (
            rel.startswith(("backend/app/", "backend/scripts/", "frontend/src/"))
        ) or ".test." in path.name or "/tests/" in rel:
            continue
        try:
            lines = _code_lines(path)
        except (OSError, SyntaxError, tokenize.TokenError):
            continue
        read += 1
        for i, line in enumerate(lines):
            values = [
                _value_from(lines, i, line[end:]) for end in _ua_sites(line)
            ]
            values = [v for v in values if re.match(r"""\s*[(\[]?\s*(?:[rbfu]{1,2})?["'`]""", v, re.I)]
            values += [_value_from(lines, i, line[m.start():]) for m in _CIVITAS_TOKEN.finditer(line)]
            for value in values:
                if _NAMES_CONTACT.search(value) or CONTACT_EMAIL in value:
                    continue
                if any(rel == f and frag in value for f, frag in _BROWSER_ONLY_USER_AGENTS):
                    continue
                offenders.append(f"{rel}:{i + 1}: {line.strip()}")
                break  # one entry per line
    return offenders, read


def test_every_user_agent_names_the_contact():
    """A User-Agent written as a string literal, or a literal naming Civitas
    as a client, bypasses the contact: every request says how to reach us,
    through CIVIC_CONTACT, BROWSER_HEADERS, BOT_USER_AGENT or
    SELF_FETCH_USER_AGENT (the site's URL in the frontend, which can't
    import the address) — the few browser-only strings aside."""
    from pathlib import Path

    from app.contact import BOT_USER_AGENT, CONTACT_EMAIL, SELF_FETCH_USER_AGENT

    assert BOT_USER_AGENT.endswith(f"+{CONTACT_EMAIL})")
    assert SELF_FETCH_USER_AGENT.endswith(f"(+{CONTACT_EMAIL})")
    backend = Path(__file__).resolve().parents[1]
    offenders, read = _user_agent_offenders(backend, _checked_in(backend))
    assert read > 50, "the sweep read almost nothing"
    assert offenders == []
    # Each exemption still points at a line that exists.
    for rel, frag in _BROWSER_ONLY_USER_AGENTS:
        if (backend.parent / rel).exists():
            assert frag in (backend.parent / rel).read_text(encoding="utf-8"), rel


@pytest.mark.parametrize("source", [
    'headers = {"User-Agent": "Civitas/1.0"}',
    'headers["User-Agent"] = "Mozilla/5.0"',
    'h = [("User-Agent", "x")]',
    'client = httpx.Client(headers={\n    "User-Agent": (\n        "Civitas/1.0 (research)"\n    ),\n})',
    'h = {\n    "User-Agent":\n        "Mozilla/5.0 Foo",\n}',
    'UA = "Civitas/1.0 (bill title calibration)"',
    'USER_AGENT = rf"Civitas/1.0"',
    'H = {"Accept": "text/html#x", "User-Agent": "Civitas/1.0"}',
    'A = {"User-Agent": "Mozilla/5.0 Foo"}\nB = BROWSER_HEADERS',
    'ua = get(); h = {"User-Agent": "Mozilla/5.0 Foo"}',
    'H = {"User-Agent": "Foo/1.0"}; DOC = """x"""',
    '_UA = "Mozilla/5.0 (X11) Foo"',
    'DEFAULT_USER_AGENT = "Foo/1.0"',
    'USER_AGENT: str = "Mozilla/5.0 Foo"',
    'def f(user_agent: str = "Mozilla/5.0 Foo"):\n    pass',
    'h = [["User-Agent", "Mozilla/5.0 Foo"]]',
    '_H = {\n    "a.gov": {"User-Agent": "Mozilla/5.0 Foo"},\n    "b.gov": {"User-Agent": BOT_USER_AGENT},\n}',
    'USER_AGENT_CHROME = "Mozilla/5.0 Foo"',
    'USER_AGENTS = [\n    "Mozilla/5.0 Foo",\n]',
    'ua = os.environ.get("USER_AGENT", "Mozilla/5.0 Foo")',
    'h = dict(user_agent="Mozilla/5.0 Foo")',
])
def test_the_user_agent_sweep_sees_every_shape(tmp_path, source):
    path = tmp_path / "backend" / "app" / "x.py"
    path.parent.mkdir(parents=True)
    path.write_text(source + "\n")
    offenders, read = _user_agent_offenders(tmp_path / "backend", [path])
    assert read == 1 and offenders != []


@pytest.mark.parametrize("source", [
    'fetch(u, { headers: { "User-Agent": `civitas-og` } });',
    'new Headers([["User-Agent", "Mozilla/5.0 Foo"]]);',
    'const BROWSER_UA = "Mozilla/5.0 Foo";',
    'const defaultUserAgent = "Mozilla/5.0 Foo";',
    'const botUA = "Mozilla/5.0 Foo";',
    'const opts = { userAgent: "Mozilla/5.0 Foo" };',
])
def test_the_user_agent_sweep_sees_the_frontend(tmp_path, source):
    path = tmp_path / "frontend" / "src" / "x.ts"
    path.parent.mkdir(parents=True)
    path.write_text(source + "\n")
    assert _user_agent_offenders(tmp_path / "backend", [path])[0] != []


@pytest.mark.parametrize("source", [
    'H = {"User-Agent": BOT_USER_AGENT}',
    'H = {\n    "User-Agent": "CivitasCivicPlatform/1.0 (x; "\n    f"contact: {CONTACT_EMAIL})",\n}',
    'def f():\n    """Sends "Civitas/1.0" as its User-Agent: "x"."""\n',
    '# "User-Agent": "Civitas/1.0"',
])
def test_the_user_agent_sweep_passes_what_names_the_contact(tmp_path, source):
    path = tmp_path / "backend" / "app" / "x.py"
    path.parent.mkdir(parents=True)
    path.write_text(source + "\n")
    assert _user_agent_offenders(tmp_path / "backend", [path]) == ([], 1)


def test_the_sweep_names_paths_from_the_repo_root_outside_git(tmp_path):
    """The backend image has no .git and no repo root around it: the sweep
    still reads the backend's own files there, under the same names."""
    backend = tmp_path / "app_root"
    path = backend / "app" / "x.py"
    path.parent.mkdir(parents=True)
    path.write_text('H = {"User-Agent": "Civitas/1.0"}\n')
    offenders, read = _user_agent_offenders(backend, [path])
    assert read == 1 and offenders == ["backend/app/x.py:1: H = {\"User-Agent\": \"Civitas/1.0\"}"]


def _healthcheck_probes(root):
    """(file, probe) for every container healthcheck: compose `test:` lists
    and Dockerfile HEALTHCHECK commands (continuation lines joined)."""
    probes = []
    for path in [*root.glob("docker-compose*.yml"), *root.glob("*/Dockerfile")]:
        text = path.read_text(encoding="utf-8").replace("\\\n", " ")
        probes += [(path.name, m.group(0)) for m in re.finditer(r"test: \[[^\]]*\]", text)]
        probes += [(str(path.relative_to(root)), m.group(0)) for m in re.finditer(r"HEALTHCHECK[^\n]*", text)]
    return probes


def test_healthchecks_that_load_a_page_are_self_fetches():
    """A probe of a page (anything but the API or a /health endpoint) goes
    through the site's middleware; without the marker every probe, every
    15s, is counted as a visit."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    middleware = root / "frontend" / "src" / "middleware.ts"
    if not middleware.exists():
        pytest.skip("no full checkout")
    marker = re.search(r'userAgent\.startsWith\("([^"]+)"\)', middleware.read_text(encoding="utf-8")).group(1)
    probes = _healthcheck_probes(root)
    page_probes = [
        (name, probe) for name, probe in probes
        if (url := re.search(r"https?://[^\s\"']+", probe))
        and not re.match(r"https?://[^/]+/(api/|health\b)", url.group(0))
    ]
    assert len(page_probes) >= 2  # the frontend's and nginx's
    for name, probe in page_probes:
        agent = re.search(r"""(?:-U|--user-agent|-A)["',\s]+["']?([^"']+)""", probe)
        assert agent and agent.group(1).startswith(marker), (name, probe)


def test_the_link_card_reader_is_the_user_agent_the_site_skips():
    """The site's middleware doesn't count a request whose User-Agent starts
    with its self-fetch marker as a visit (or an issue view, which feeds
    trending); the link-card reader must still send it."""
    from pathlib import Path

    from app.contact import SELF_FETCH_USER_AGENT

    middleware = Path(__file__).resolve().parents[2] / "frontend" / "src" / "middleware.ts"
    if not middleware.exists():  # the backend image carries no frontend
        pytest.skip("no frontend checkout")
    marker = re.search(r'userAgent\.startsWith\("([^"]+)"\)', middleware.read_text(encoding="utf-8"))
    assert marker and SELF_FETCH_USER_AGENT.startswith(marker.group(1))
