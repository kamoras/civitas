"""Project-wide httpx client with a hard wall-clock backstop per request.

Why this module exists — confirmed live 2026-08-02: a House pipeline run sat
wedged for 12h+ on a single congress.gov call. The connection was stuck in
CLOSE_WAIT (the peer had closed it; httpx had handed the dead connection back
out of its pool) and httpx's own `timeout=` never fired, because those
timeouts are enforced per socket *operation* — a socket that never becomes
readable and never errors has no operation to time out. The pipeline had no
upper bound on a single fetch, so one dead connection stopped everything
behind it indefinitely.

`asyncio.wait_for` around the send is the backstop that cannot be defeated
this way: it is wall-clock, enforced by the event loop, and completely
independent of what state the connection pool is in.

This lives at the top level (next to error_utils/time_utils), not under
`pipeline/fetch/`, so the API layer (health, admin, feedback) can import it
without pulling the pipeline package in.

## Why every client, not just `fetch_with_retry`

The wedge was reported through `fetch_with_retry`, but roughly twenty call
sites reach httpx directly (`await client.get(...)` in congress.py,
justice_votes.py, lda.py, govinfo.py, federal_register.py, …). They share the
exact failure mode, so the backstop belongs on the client every one of them
already uses rather than on one wrapper. `make_async_client()` is the single
constructor for the whole backend; constructing `httpx.AsyncClient` directly
anywhere in `app/` is a bug (there is a test that fails if one appears).

## Why a multiplier and not a fixed margin

The backstop must never fire on a request that is merely *slow but healthy*,
or it converts "this download takes a while" into a hard failure. Some fetches
legitimately run long against their own timeout: the House Clerk ZIP and the
presidential 278-T PDF both run with `timeout=60.0`, and a read timeout is per
chunk, so a large body streaming steadily can exceed the configured timeout
several times over without a single read ever stalling.

So the backstop is a *multiple* of whatever timeout the request already
carries, not timeout+delta. At 4x, a healthy transfer would have to be four
times slower than its own per-read budget to be cancelled, while a truly hung
request still dies in minutes instead of half a day — which is all this needs
to do. Streaming requests (`client.stream(...)`, the LLM token streams) are
unaffected past the response headers: `send()` returns once headers are in, so
a long generation is never on the clock here.

Two scope notes, stated rather than glossed:

- With `follow_redirects=True` the whole redirect chain resolves inside one
  `send()`, so the ceiling covers the chain rather than each hop. At 4x, a
  chain would need every hop running near its full timeout to trip it.
- The one *synchronous* httpx call left in the backend (news_feeds' RSS
  fetch) is not routed through here and does not need to be: module-level
  `httpx.get` builds a fresh client per call, so there is no pool to hand
  back a dead connection — the failure mode above structurally can't occur.
  Its own `timeout=` is a real per-socket-operation timeout and does fire.
"""

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Kept here rather than in pipeline/fetch/http_utils.py so this module has no
# imports from the pipeline package; http_utils re-exports it for its callers.
DEFAULT_FETCH_TIMEOUT_S = 30.0

# See the module docstring for why this is a multiplier. 4x is deliberately
# generous: the goal is bounding a hang, not tightening a timeout. Anything
# that would make a healthy-but-slow transfer fail belongs in the request's
# own `timeout=`, which is the knob that is actually meant for it.
HANG_BACKSTOP_MULTIPLIER = 4.0


def hang_backstop_seconds(timeout: Any) -> float:
    """Wall-clock ceiling for one request carrying `timeout`.

    Accepts anything httpx accepts as a timeout: a number, an
    `httpx.Timeout`, or the `{"connect": …, "read": …}` dict form that
    `Request.extensions["timeout"]` holds. The largest configured phase wins,
    since that is the longest any single socket operation is allowed to take.

    A request with no numeric timeout at all (`timeout=None`, "wait forever")
    falls back to the default budget rather than staying unbounded — an
    unbounded request is precisely the failure this module exists to stop.
    """
    if isinstance(timeout, httpx.Timeout):
        timeout = timeout.as_dict()
    if isinstance(timeout, dict):
        phases = [v for v in timeout.values() if isinstance(v, (int, float))]
        budget = max(phases) if phases else None
    elif isinstance(timeout, (int, float)):
        budget = timeout
    else:
        budget = None

    if budget is None or budget <= 0:
        budget = DEFAULT_FETCH_TIMEOUT_S
    return budget * HANG_BACKSTOP_MULTIPLIER


async def bounded(awaitable, timeout: Any, *, label: str = "request"):
    """Await `awaitable` under the hang backstop derived from `timeout`.

    Raises `TimeoutError` if the backstop fires — callers treat that as a
    failed attempt like any other transport error.
    """
    ceiling = hang_backstop_seconds(timeout)
    try:
        return await asyncio.wait_for(awaitable, timeout=ceiling)
    except TimeoutError:
        logger.error(
            "%s exceeded the %.1fs hang backstop and was cancelled — the "
            "connection never completed and never errored",
            label, ceiling,
        )
        raise


class BackstoppedAsyncClient(httpx.AsyncClient):
    """`httpx.AsyncClient` whose every request has a wall-clock ceiling.

    The bound is applied in `send()`, which is the single funnel every
    `get`/`post`/`request`/`stream` call goes through, so no call site can
    accidentally opt out of it.
    """

    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        url = request.url
        # Scheme/host/path only — deliberately never the query string, which
        # is where callers like congress.py carry their API key. Building the
        # label without the credential beats redacting it afterwards (see
        # error_utils' docstring on why the after-the-fact form doesn't
        # satisfy py/clear-text-logging-sensitive-data), and the path alone
        # is all anyone needs to identify which fetch hung.
        return await bounded(
            super().send(request, **kwargs),
            request.extensions.get("timeout"),
            label=f"{request.method} {url.scheme}://{url.host}{url.path}",
        )


def make_async_client(**kwargs: Any) -> BackstoppedAsyncClient:
    """Construct the backend's httpx client. Use this everywhere.

    Takes the same keyword arguments as `httpx.AsyncClient`.
    """
    return BackstoppedAsyncClient(**kwargs)
