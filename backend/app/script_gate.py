"""The crawler's manners for the scripts the data refresh runs.

Each generator script runs as its own process (data_refresh.run_command),
and most read their sources with plain httpx, requests or urllib calls, so
until 2026-10-28 none of them went through crawl_policy: no pacing, no
backoff after a wall, no host's terms of use. `python -m app.script_gate
<script> [args]` runs the script with every blocking request of those three
libraries sent through http_client.source_gate and its answer recorded
(observe_answer), the same turn a pipeline reader's request takes; a refused
host raises crawl_policy.HostRefusing (an httpx.TransportError) before
anything is sent, which the scripts read as the failed fetch it is.

Async clients are left alone: the scripts that use one build it with
make_async_client, which is governed already.
"""

import runpy
import socket
import sys
import threading
import urllib.error
import urllib.request

import httpx

from app.crawl_policy import HostRefusing
from app.http_client import gate_held, observe_answer, observe_unanswered, source_gate

_local = threading.local()


def _wall_like(status: int, headers) -> bool:
    """Whether an answer may be a wall's, so its body is worth reading: a
    refusal, or a wall vendor's header on any answer."""
    lower = {k.lower() for k in dict(headers or {})}
    return status in (401, 403, 429, 503) or "x-iinfo" in lower or "cf-mitigated" in lower


def _gated(url, send):
    """Run `send()` in the host's turn, recording its answer. A request made
    while another is in its turn (the robots.txt read source_gate makes) is
    sent as it is."""
    if getattr(_local, "inside", False) or gate_held():
        # The robots.txt read a gate makes, or a request a caller already
        # gated (http_client.source_request): sent in that turn, recorded by it.
        return send()
    _local.inside = True
    try:
        with source_gate(url):
            try:
                return send()
            except HostRefusing:
                raise
            except urllib.error.HTTPError:
                raise  # answered: open_ records it
            except Exception as exc:
                observe_unanswered(url, exc)
                raise
    finally:
        _local.inside = False


def _install_httpx() -> None:
    original = httpx.HTTPTransport.handle_request

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        nested = getattr(_local, "inside", False) or gate_held()
        response = _gated(request.url, lambda: original(self, request))
        if not nested:
            body = b""
            if _wall_like(response.status_code, response.headers):
                response.read()
                body = response.content
            observe_answer(request.url, response.status_code, response.headers, body)
        return response

    httpx.HTTPTransport.handle_request = handle_request


def _install_requests() -> None:
    try:
        import requests.adapters
    except ImportError:
        return
    original = requests.adapters.HTTPAdapter.send

    def send(self, request, **kwargs):
        nested = getattr(_local, "inside", False) or gate_held()
        response = _gated(request.url, lambda: original(self, request, **kwargs))
        if nested:
            return response
        body = response.content if _wall_like(response.status_code, response.headers) else b""
        observe_answer(request.url, response.status_code, response.headers, body)
        return response

    requests.adapters.HTTPAdapter.send = send


def _install_urllib() -> None:
    original = urllib.request.OpenerDirector.open

    def open_(self, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        url = fullurl.full_url if isinstance(fullurl, urllib.request.Request) else str(fullurl)
        call = lambda: original(self, fullurl, data, timeout)  # noqa: E731
        if not url.startswith(("http://", "https://")) or getattr(_local, "inside", False) or gate_held():
            return call()
        try:
            response = _gated(url, call)
        except urllib.error.HTTPError as answer:
            observe_answer(url, answer.code, dict(answer.headers or {}))
            raise
        observe_answer(url, getattr(response, "status", 200), dict(response.headers or {}))
        return response

    urllib.request.OpenerDirector.open = open_


def install() -> None:
    _install_httpx()
    _install_requests()
    _install_urllib()


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit("usage: python -m app.script_gate <script.py> [args...]")
    install()
    sys.argv = list(argv)
    runpy.run_path(argv[0], run_name="__main__")


if __name__ == "__main__":
    main(sys.argv[1:])
