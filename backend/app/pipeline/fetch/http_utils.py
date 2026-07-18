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

from app.error_utils import redact_sensitive_params
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_S = 2.0
DEFAULT_FETCH_TIMEOUT_S = 30.0

# Two callers (congress.py, congressional_record.py) build their URL with an
# API key embedded directly in the query string, per this module's own
# docstring ("URL construction ... stay in each caller"). That means the raw
# key was being written to application logs on every request/retry/failure
# (CodeQL py/clear-text-logging-sensitive-data, 2026-07). Redacting once,
# here, covers those two callers and any future one that does the same
# thing, instead of relying on every caller to remember not to log its URL.
redact_url = redact_sensitive_params


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
    timeout: float = DEFAULT_FETCH_TIMEOUT_S,
    log_label: str = "",
    **request_kwargs,
) -> httpx.Response | None:
    """Rate-limited HTTP request with retry+backoff.

    Returns the raw Response on success (any status < 400), or None if
    retries are exhausted or a non-retried 4xx is hit. Callers extract
    .json() / .content / .text as needed for their source.
    """
    await rate_limiter.acquire()
    safe_url = redact_url(url)
    label = log_label or safe_url
    for attempt in range(1, retries + 1):
        try:
            logger.debug("%s: %s (attempt %d)", label, safe_url, attempt)
            resp = await client.request(method, url, timeout=timeout, **request_kwargs)

            if resp.status_code == 429:
                wait = backoff_s * attempt * rate_limit_backoff_multiplier
                logger.warning("%s rate limited, waiting %.1fs...", label, wait)
                await asyncio.sleep(wait)
                continue

            if resp.status_code >= 400:
                if not retry_on_4xx and 400 <= resp.status_code < 500:
                    logger.error("%s client error (no retry): %s — HTTP %d", label, safe_url, resp.status_code)
                    return None
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp,
                )

            return resp
        except Exception as e:
            if attempt == retries:
                # The exception's own message can embed the request URL
                # (e.g. httpx.ConnectError/ReadTimeout do) — redact it too.
                logger.error(
                    "%s failed after %d attempts: %s — %s",
                    label, retries, safe_url, redact_url(str(e)),
                )
                return None
            await asyncio.sleep(backoff_s * attempt)

    return None
