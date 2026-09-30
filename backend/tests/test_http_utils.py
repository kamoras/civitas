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
import re
import threading
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
    @pytest.mark.parametrize("retries", [
        pytest.param(1, id="gives_up"),
        # The backstop raises into the same `except` the retry loop already
        # uses, so a hang burns attempts rather than aborting the fetch on
        # the first one.
        pytest.param(3, id="retried_like_any_other_failed_attempt"),
    ])
    async def test_hung_request_is_bounded_in_wall_clock(self, retries):
        client = self._hanging_client()
        start = asyncio.get_running_loop().time()
        result = await fetch_with_retry(
            client, _limiter(), "GET", "https://example.test",
            retries=retries, backoff_s=0.001, timeout=0.01,
        )
        elapsed = asyncio.get_running_loop().time() - start
        assert result is None
        assert client.request.call_count == retries
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
    async def test_an_expected_status_comes_back_as_the_response_unretried(self):
        client = self._client(404)
        resp = await fetch_with_retry(
            client, _limiter(), "GET", "https://example.test/x", expected_statuses=(404,),
        )
        assert resp is not None and resp.status_code == 404
        # The cost half of the bug: 4xx is retried by default, so every
        # one of these was 3 requests plus backoff sleeps per address.
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
# Names that carry the contact: a value built from one names it.
_CONTACT_NAMES = {"CONTACT_EMAIL", "BOT_USER_AGENT", "SELF_FETCH_USER_AGENT", "CIVIC_CONTACT", "BROWSER_HEADERS"}
# A string naming Civitas as a client: "Civitas/1.0", "Civitas-OG/1".
_CIVITAS_TOKEN_TEXT = re.compile(r"\bcivitas[\w-]*/\d", re.IGNORECASE)


def _names_a_user_agent(name: str) -> bool:
    """A name, key or header one of whose parts (split at "_", "-" and case
    changes) is ua or user+agent(s): _UA, botUA, DEFAULT_USER_AGENT,
    USER_AGENT_CHROME, defaultUserAgent, USER_AGENTS, "User-Agent"."""
    parts = [p.lower() for p in re.findall(r"[A-Z]{2,}(?![a-z])|[A-Z]?[a-z0-9]+|[A-Z]", name)]
    joined = [a + b for a, b in zip(parts, parts[1:])]
    return "ua" in parts or any(p in ("useragent", "useragents") for p in parts + joined)


def _ua_key(value) -> bool:
    if isinstance(value, bytes):
        value = value.decode("latin-1")
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z][\w-]*", value)) and _names_a_user_agent(value)


def _python_ua_values(nodes):
    """(line, value node) for every place Python code gives a User-Agent a
    value: an assignment, annotation, keyword argument or parameter default
    to a name or attribute with a ua part; a dict key, a (key, value) pair
    or a subscript naming the header; a .get / getenv / setdefault default
    for one."""
    import ast

    def named(target):
        if isinstance(target, ast.Name):
            return _names_a_user_agent(target.id)
        if isinstance(target, ast.Attribute):
            return _names_a_user_agent(target.attr)
        if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
            return _ua_key(target.slice.value)
        return False

    for node in nodes:
        if isinstance(node, ast.Assign):
            yield from ((node.value.lineno, node.value) for t in node.targets if named(t))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and node.value is not None:
            if named(node.target):
                yield node.value.lineno, node.value
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and _ua_key(key.value):
                    yield value.lineno, value
        elif isinstance(node, ast.keyword):
            if node.arg and _names_a_user_agent(node.arg):
                yield node.value.lineno, node.value
        elif isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) >= 2:
            first, second = node.elts[:2]
            # ("User-Agent", "user-agent") pairs two spellings of the key.
            if isinstance(first, ast.Constant) and _ua_key(first.value) and not (
                isinstance(second, ast.Constant) and _ua_key(second.value)
            ):
                yield second.lineno, second
        elif isinstance(node, ast.Call) and len(node.args) >= 2:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            first = node.args[0]
            # A default for one, or a header setter (urllib's add_header,
            # http.client's putheader).
            setters = ("get", "getenv", "setdefault", "pop", "add_header", "add_unredirected_header", "putheader")
            if name in setters and isinstance(first, ast.Constant) and _ua_key(first.value):
                yield node.args[1].lineno, node.args[1]
        elif isinstance(node, ast.arguments):
            positional = node.posonlyargs + node.args
            pairs = list(zip(positional[len(positional) - len(node.defaults):], node.defaults))
            pairs += [(a, d) for a, d in zip(node.kwonlyargs, node.kw_defaults) if d is not None]
            yield from ((d.lineno, d) for a, d in pairs if _names_a_user_agent(a.arg))


def _python_offenders(rel, text):
    """Lines of Python source that send a User-Agent without the contact."""
    import ast

    from app.contact import CONTACT_EMAIL

    tree = ast.parse(text)
    lines = text.splitlines()
    # One walk: every node, the innermost statement holding it, and the
    # docstrings (prose).
    nodes, statement_of, docstrings = [], {}, set()
    stack = [(tree, None)]
    while stack:
        node, stmt = stack.pop()
        if isinstance(node, ast.stmt):
            stmt = node
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                docstrings.add(id(node.value))
        nodes.append(node)
        statement_of[id(node)] = stmt
        stack.extend((child, stmt) for child in ast.iter_child_nodes(node))

    def strings(node):
        out = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, (str, bytes)):
                out.append(sub.value.decode("latin-1") if isinstance(sub.value, bytes) else sub.value)
        return out

    def names_contact(node):
        found = {getattr(sub, "id", None) or getattr(sub, "attr", None) for sub in ast.walk(node)}
        return bool(found & _CONTACT_NAMES) or any(CONTACT_EMAIL in s for s in strings(node))

    def exempt(node):
        return any(rel == f and any(frag in s for s in strings(node)) for f, frag in _BROWSER_ONLY_USER_AGENTS)

    def literal(node):
        """Whether a value is written out here: a non-empty string, or one
        built from one (f-string, +, %, .format / .join on one, a list of
        them, a conditional). A name, a call or a header read isn't."""
        if isinstance(node, ast.Constant):
            return isinstance(node.value, (str, bytes)) and bool(node.value.strip())
        if isinstance(node, ast.JoinedStr):
            return True
        if isinstance(node, ast.BinOp):
            return literal(node.left) or literal(node.right)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return node.func.attr in ("format", "join") and literal(node.func.value)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return any(literal(e) for e in node.elts)
        if isinstance(node, ast.IfExp):
            return literal(node.body) or literal(node.orelse)
        return False

    bad = set()
    for line, value in _python_ua_values(nodes):
        if literal(value) and not names_contact(value) and not exempt(value):
            bad.add(line)
    # A string naming Civitas as a client, wherever it sits, outside prose:
    # the innermost statement holding it must name the contact.
    for sub in nodes:
        if id(sub) in docstrings or not isinstance(sub, ast.Constant) or not isinstance(sub.value, (str, bytes)):
            continue
        text = sub.value.decode("latin-1") if isinstance(sub.value, bytes) else sub.value
        stmt = statement_of.get(id(sub))
        if _CIVITAS_TOKEN_TEXT.search(text) and not (stmt and (names_contact(stmt) or exempt(stmt))):
            bad.add(sub.lineno)
    return [f"{rel}:{n}: {lines[n - 1].strip()}" for n in sorted(bad)]


# The frontend (TypeScript) is read as text, a line at a time: a header key
# ("User-Agent": / ["User-Agent"] = / ["User-Agent", ...]), or a name with a
# ua part given a value (NAME = / name: type = / name: in an object).
_TS_UA_SITE = re.compile(
    r"""(["']user-agent["']\s*(?:\]\s*=|:)|\[\s*["']user-agent["']\s*,|\.(?:set|append)\(\s*["']user-agent["']\s*,)""",
    re.IGNORECASE,
)
_TS_NAME_SITE = re.compile(r"""(?<![\w$])([A-Za-z_$][\w$]*)\s*(?::\s*[\w\[\]|.<>]+(?:\s+[\w\[\]|.<>]+)*)?\s*(?<![=!<>])[:=](?!=)""")
_TS_CIVITAS_TOKEN = re.compile(r"""["'`][^"'`\n]*\bcivitas[\w-]*/\d""", re.IGNORECASE)
# The site's address in a template literal (the frontend can't import the
# email): `…(+${SITE_URL})`.
_TS_NAMES_CONTACT = re.compile(r"`[^`\n]*\+\$\{SITE_URL\}[^`\n]*`")


def _ts_offenders(rel, text):
    # Comments blanked: a whole // or block-comment line, or a /* … */
    # closed on the line it opens (code after it is still read).
    lines = []
    for line in text.splitlines():
        line = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), line)
        lines.append("" if line.lstrip().startswith(("//", "*", "/*")) else line)
    bad = []
    for i, line in enumerate(lines):
        ends = [m.end() for m in _TS_UA_SITE.finditer(line)]
        ends += [m.end() for m in _TS_NAME_SITE.finditer(line) if _names_a_user_agent(m.group(1))]
        values = []
        for end in ends:
            # The value: the rest of the line, or the next line when the
            # line ends at the key.
            rest = line[end:]
            if not rest.strip() and i + 1 < len(lines):
                rest = lines[i + 1]
            if re.match(r"""\s*[(\[]?\s*["'`]""", rest):
                values.append(rest)
        values += [line[m.start():] for m in _TS_CIVITAS_TOKEN.finditer(line)]
        for value in values:
            if _TS_NAMES_CONTACT.search(value) or any(rel == f and frag in value for f, frag in _BROWSER_ONLY_USER_AGENTS):
                continue
            bad.append(f"{rel}:{i + 1}: {line.strip()}")
            break
    return bad


def _user_agent_offenders(backend, files):
    """(offenders, files read): every User-Agent given a literal value, and
    every literal naming Civitas as a client, that doesn't name the contact.
    Python is read through its syntax tree; the frontend line by line. Paths
    are named from the repo root ("backend/app/…"), inside a git checkout or
    not. A browser string kept in a constant under some other name is out of
    reach of any sweep; the reviews are the check there."""
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
            text = path.read_text(encoding="utf-8")
            found = _python_offenders(rel, text) if path.suffix == ".py" else _ts_offenders(rel, text)
        except (OSError, SyntaxError):
            continue
        read += 1
        offenders += found
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
    'self.user_agent = "Mozilla/5.0 Foo"',
    'settings.USER_AGENT = "Mozilla/5.0 Foo"',
    'ua = os.getenv("USER_AGENT", "Mozilla/5.0 Foo")',
    'KW = {"user_agent": "Mozilla/5.0 Foo"}',
    'h = [(b"user-agent", b"Mozilla/5.0 Foo")]',
    'UA: Final[str] = "Mozilla/5.0 Foo"',
    'log.info("sent as Civitas/1.0")',
    'req.add_header("User-Agent", "Mozilla/5.0 Foo")',
    'x = b"Civitas/1.0"',
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
    'this.userAgent = "Mozilla/5.0 Foo";',
    'client.defaults.ua = "Mozilla/5.0 Foo";',
    'const h = {\n  "User-Agent":\n    "Mozilla/5.0 Foo",\n};',
    'headers.set("User-Agent", "Mozilla/5.0 Foo");',
    'h.append("user-agent", "Mozilla/5.0 Foo");',
    '/* x */ const UA = "Mozilla/5.0 Foo";',
    'const h = { "User-Agent": "Foo/1.0 (+${SITE_URL})" };',
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
    'H = {\n    "User-Agent": (\n        "Mozilla/5.0 (compatible; x; +"\n        + CONTACT_EMAIL\n        + ")"\n    ),\n}',
    'UA = "Civitas/1.0 (+{})".format(\n    CONTACT_EMAIL)',
    'agent = request.headers.get("User-Agent", "")',
    'if x:\n    pass\nelse:  # ' + "x" * 3000 + "\n    pass",
])
def test_the_user_agent_sweep_passes_what_names_the_contact(tmp_path, source):
    path = tmp_path / "backend" / "app" / "x.py"
    path.parent.mkdir(parents=True)
    path.write_text(source + "\n")
    assert _user_agent_offenders(tmp_path / "backend", [path]) == ([], 1)


def test_the_frontend_sweep_is_linear_on_long_lines(tmp_path):
    """The frontend is read with regular expressions; a long run of spaces
    (a blanked comment, minified code) must not make them backtrack."""
    import time

    path = tmp_path / "frontend" / "src" / "x.ts"
    path.parent.mkdir(parents=True)
    path.write_text("const a = b ?" + " " * 20000 + "c :" + " " * 20000 + "d;\nx:" + " " * 20000 + "y\n")
    started = time.monotonic()
    _user_agent_offenders(tmp_path / "backend", [path])
    assert time.monotonic() - started < 1


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
