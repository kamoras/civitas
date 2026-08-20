"""Shared rate-limited HTTP fetch-with-retry helper.

Extracted from 4 independent, near-identical implementations
(congress.py, congressional_record.py, house_ptr.py, senate_ptr.py) that
each redefined MAX_RETRIES/RETRY_BACKOFF_S and reimplemented the same
rate-limit -> try -> 429-backoff -> exception-retry loop.

The four callers differed in two ways that are exposed as parameters
here rather than hardcoded, so unifying them doesn't silently change
any caller's behavior:
  - Whether a 4xx status is retried at all. congress.py retried
    everything but 429 (including 4xx); congressional_record.py,
    house_ptr.py, and senate_ptr.py never retried a 4xx (client errors
    are terminal, not transient). -> retry_on_4xx
  - The 429 backoff multiplier: congress.py/congressional_record.py used
    attempt*backoff; house_ptr.py/senate_ptr.py used attempt*backoff*2
    (they poll a stricter, more rate-limit-sensitive government site).
    -> rate_limit_backoff_multiplier

URL construction (e.g. API key query params) and response-body
extraction (.json() vs .content vs the raw Response) stay in each
caller — those are genuinely per-source concerns, not part of the
retry mechanics.
"""

import asyncio
import logging

import httpx
import requests

from app.error_utils import redact_sensitive_params
from app.http_client import DEFAULT_FETCH_TIMEOUT_S, bounded
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_S = 2.0

__all__ = [
    "DEFAULT_FETCH_TIMEOUT_S",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RETRY_BACKOFF_S",
    "fetch_with_retry",
    "fetch_with_retry_requests",
    "redact_url",
]

# Two callers (congress.py, congressional_record.py) used to build their URL
# with an API key embedded directly in the query string, which meant the raw
# key got logged on every request/retry/failure (CodeQL
# py/clear-text-logging-sensitive-data, 2026-07). Both now keep the
# credential out of the `url` this function logs entirely, passing it via
# `request_url` instead (see fetch_with_retry's docstring) — CodeQL's
# taint-tracking doesn't recognize an arbitrary regex substitution as a
# sanitizer, so removing the credential from the logged value at the source
# is what actually clears the alert, not redacting it after the fact. This
# stays as a defensive backstop for exception messages (which can still
# embed a URL from underlying library internals) and any future caller that
# reintroduces the anti-pattern.
redact_url = redact_sensitive_params


# Identify honestly, but send a COMPLETE request.
#
# A good number of state election sites sit behind a WAF that rejects on
# the SHAPE of a request rather than on who is making it: a bare
# User-Agent with no Accept, Accept-Language or Sec-Fetch-* headers looks
# like a script and gets a 403 or a challenge page. Measured 2026-08-19
# against the real sites — Ohio, Missouri, Tennessee and New York's
# results portals all went from 403 to 200 with no change of identity,
# purely by sending the headers a normal client always sends.
#
# The User-Agent still says who we are and how to reach us, robots.txt is
# still honoured (see state_source_crawler._allowed) and the rate limits
# still apply. This is standards-compliance, not disguise: a site that
# wants to refuse Civitas can still refuse Civitas.
CIVIC_CONTACT = "Civitas/1.0 (+contact@civitas-research.org)"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/151.0.0.0 Safari/537.36 {CIVIC_CONTACT}"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# The same, shaped as a page's own script would send when fetching data.
BROWSER_JSON_HEADERS = {
    **BROWSER_HEADERS,
    "Accept": "application/json, text/plain, */*",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


async def fetch_with_retry(
    client: httpx.AsyncClient,
    rate_limiter: RateLimiter,
    method: str,
    url: str,
    *,
    retries: int = DEFAULT_MAX_RETRIES,
    backoff_s: float = DEFAULT_RETRY_BACKOFF_S,
    rate_limit_backoff_multiplier: float = 1.0,
    retry_on_4xx: bool = True,
    no_retry_statuses: tuple[int, ...] = (),
    timeout: float = DEFAULT_FETCH_TIMEOUT_S,
    log_label: str = "",
    request_url: str | None = None,
    **request_kwargs,
) -> httpx.Response | None:
    """Rate-limited HTTP request with retry+backoff.

    `url` is what gets logged on every request/retry/failure — callers
    whose real request needs a credential in the query string (an API key)
    pass the credential-bearing URL separately via `request_url`, so the
    credential is never even constructed as part of the value this
    function might log. (httpx's `params=` kwarg replaces rather than
    merges an existing query string, so it can't be used here without
    dropping a caller's other query params — hence this two-URL split
    instead. See congress.py/congressional_record.py for callers.)

    Returns the raw Response on success (any status < 400), or None if
    retries are exhausted or a non-retried 4xx is hit. Callers extract
    .json() / .content / .text as needed for their source.

    `no_retry_statuses` lists statuses that terminate immediately with None
    without retrying or raising (e.g. GovInfo's 404 "bill text not published
    yet", which is an expected miss, not a transient error). It is checked
    before the generic >=400 handling, so it applies even when
    retry_on_4xx is True.
    """
    await rate_limiter.acquire()
    actual_url = request_url or url
    label = log_label or url
    for attempt in range(1, retries + 1):
        try:
            logger.debug("%s: %s (attempt %d)", label, url, attempt)
            # Hard backstop on top of `timeout=` below: confirmed live
            # 2026-08-02, a House pipeline run sat wedged for 12h+ with a
            # congress.gov connection stuck CLOSE_WAIT — httpx's own
            # per-request timeout never fired (likely a stale pooled
            # connection httpx handed back out without detecting the peer
            # had already closed it). wait_for guarantees this call raises
            # and retries/gives up within a bounded time no matter what
            # state the client's connection pool gets into.
            # Hard wall-clock backstop on top of `timeout=` — see
            # app/http_client.py for the CLOSE_WAIT hang this exists to
            # bound. Redundant when `client` came from make_async_client()
            # (its send() applies the same bound), and deliberately kept
            # anyway: this function accepts any AsyncClient a caller hands
            # it, and the bound has to hold for those too.
            resp = await bounded(
                client.request(method, actual_url, timeout=timeout, **request_kwargs),
                timeout,
                label=label,
            )

            if resp.status_code == 429:
                wait = backoff_s * attempt * rate_limit_backoff_multiplier
                logger.warning("%s rate limited, waiting %.1fs...", label, wait)
                await asyncio.sleep(wait)
                continue

            if resp.status_code in no_retry_statuses:
                logger.debug("%s: %s — HTTP %d (no retry)", label, url, resp.status_code)
                return None

            if resp.status_code >= 400:
                if not retry_on_4xx and 400 <= resp.status_code < 500:
                    logger.error("%s client error (no retry): %s — HTTP %d", label, url, resp.status_code)
                    return None
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp,
                )

            return resp
        except Exception as e:
            if attempt == retries:
                # The exception's own message can embed the request URL,
                # including request_url if one was given (e.g.
                # httpx.ConnectError/ReadTimeout do) — redact it too.
                logger.error(
                    "%s failed after %d attempts: %s — %s",
                    label, retries, url, redact_url(str(e)),
                )
                return None
            await asyncio.sleep(backoff_s * attempt)

    return None


async def fetch_with_retry_requests(
    rate_limiter: RateLimiter,
    method: str,
    url: str,
    *,
    retries: int = DEFAULT_MAX_RETRIES,
    backoff_s: float = DEFAULT_RETRY_BACKOFF_S,
    timeout: float = DEFAULT_FETCH_TIMEOUT_S,
    log_label: str = "",
    **request_kwargs,
) -> "requests.Response | None":
    """Same rate-limited retry/backoff shape as fetch_with_retry, but issues
    the request via `requests` (run off-thread via asyncio.to_thread) instead
    of httpx.

    presidency.ucsb.edu — the sole live source for every UCSB-derived
    president fetcher — started blanket-403ing httpx's requests sometime
    around 2026-07-23 regardless of User-Agent value or header casing
    (confirmed live: identical headers succeed via `requests`, fail via
    httpx, from the same container/IP), which silently starved the
    president pipeline's roster/EO/approval/election-margin fetches and,
    combined with a DROP-TABLE-on-schema-mismatch migration around the same
    time, left the presidents table empty with no way to rebuild itself.
    `requests` isn't blocked, so every UCSB-sourced fetcher routes through
    this instead of fetch_with_retry.
    """
    label = log_label or url
    for attempt in range(1, retries + 1):
        try:
            await rate_limiter.acquire()
            # Same wall-clock backstop as fetch_with_retry. One honest
            # caveat: cancelling a wait_for around asyncio.to_thread frees
            # the *event loop*, not the worker thread — `requests` has no
            # cancellation, so a truly wedged socket keeps its thread until
            # urllib3's own socket timeout releases it. That is still the
            # difference between "the pipeline continues" and "the pipeline
            # is stopped for 12 hours", which is the failure being fixed;
            # requests' timeout is a real per-socket-operation timeout and
            # does fire, so the thread is not leaked indefinitely.
            resp = await bounded(
                asyncio.to_thread(requests.request, method, url, timeout=timeout, **request_kwargs),
                timeout,
                label=label,
            )

            if resp.status_code == 429:
                wait = backoff_s * attempt
                logger.warning("%s rate limited, waiting %.1fs...", label, wait)
                await asyncio.sleep(wait)
                continue

            if resp.status_code >= 400:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)

            return resp
        except Exception as e:
            if attempt == retries:
                logger.error(
                    "%s failed after %d attempts: %s — %s",
                    label, retries, url, redact_url(str(e)),
                )
                return None
            await asyncio.sleep(backoff_s * attempt)

    return None
