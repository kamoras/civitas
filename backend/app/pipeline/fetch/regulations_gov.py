"""Client for the Regulations.gov API v4.

Supports fetching public comments on rulemaking documents and
submitting new comments on behalf of users.

API docs: https://open.gsa.gov/api/regulationsgov/
Rate limit: 1,000 requests/hour with an API key.
"""

import logging
import re
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.http_client import make_async_client
from app.database import off_loop
from app.pipeline.cache import api_cache_get, api_cache_set_async
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S

logger = logging.getLogger(__name__)

REG_BASE = "https://api.regulations.gov/v4"

# The comments listing is a lighter query than a full document fetch, so it
# uses a shorter timeout than DEFAULT_FETCH_TIMEOUT_S (used for the POST below).
_COMMENTS_TIMEOUT_S = 15.0


def _extract_document_id(comment_url: str) -> str | None:
    """The regulations.gov documentId ("EPA-HQ-OAR-2021-0208-0001") from a
    document's comment or page URL:
      https://www.regulations.gov/commenton/EPA-HQ-OAR-2021-0208-0001
      https://www.regulations.gov/document/EPA-HQ-OAR-2021-0208-0001
    """
    match = re.search(r"regulations\.gov/(?:commenton|document)/([A-Za-z0-9_-]+)", comment_url)
    return match.group(1) if match else None


_CACHE_TIER = "regulations"
# A document's objectId never changes once assigned.
_OBJECT_ID_CACHE_HOURS = 24 * 365
# Comments post slowly; an hour-old page is current enough to read.
_COMMENTS_CACHE_HOURS = 1


async def _object_id(client: httpx.AsyncClient, api_key: str, document_id: str) -> tuple[str | None, int]:
    """(the document's objectId, which the comments listing is keyed on,
    or None; the HTTP status). A 404 or 410, or a document with no
    objectId, is Regulations.gov not having it; any other status is not an
    answer about whether the document exists."""
    resp = await client.get(
        f"{REG_BASE}/documents/{document_id}",
        headers={"X-Api-Key": api_key},
        timeout=_COMMENTS_TIMEOUT_S,
    )
    if resp.status_code != 200:
        logger.warning("Regulations.gov document %s returned %d", document_id, resp.status_code)
        return None, resp.status_code
    return ((resp.json().get("data") or {}).get("attributes") or {}).get("objectId") or None, 200


def _retryable(status: int) -> bool:
    """Whether a failed status could succeed if asked again without the
    document changing: a rate limit or a server error can, and so can a
    refused key (401/403) — the operator's to fix, and once fixed a cached
    refusal would keep being served. Any other refusal (a malformed id) is
    the same answer next time."""
    return status in (401, 403, 429) or status >= 500


def _failed(error: str, *, retryable: bool) -> dict:
    """An answer carrying no comments. `retryable` is whether asking again
    could succeed: a timeout or a rate limit can, an unknown document
    can't — which decides whether the answer may be cached (api/explore)."""
    return {"comments": [], "totalElements": 0, "error": error, "retryable": retryable}


_NOT_FOUND = "Document not found on Regulations.gov"

# How long "Regulations.gov has no such document" is remembered: long enough
# that repeats don't spend the shared budget, short enough that a document
# published before Regulations.gov indexed it is found once it has — not the
# year a found objectId is kept.
_NOT_FOUND_CACHE_HOURS = 6


async def fetch_comments(
    comment_url: str,
    page_size: int = 25,
    page_number: int = 1,
    sort_by: str = "postedDate",
    sort_order: str = "desc",
    *,
    db: Session | None = None,
    spend: Callable[[int], Awaitable[None]] | None = None,
) -> dict:
    """Fetch public comments for a document from regulations.gov.

    The listing is filtered on the document's objectId (a hex id such as
    0900006483a6cba3), not the documentId in its URL: filter[commentOnId]
    takes the objectId, and given the documentId it matched nothing, so every
    document read as having no comments. The objectId is looked up once and
    kept. With `db`, the objectId and each page are cached; `spend(n)` is
    charged with the requests a call will make before it makes them (the
    public route's upstream budget; it raises to refuse).

    Returns dict with keys: comments, totalElements, pageSize, pageNumber
    """
    api_key = settings.DATA_GOV_API_KEY
    if not api_key:
        # The operator's to fix: not an answer about the document.
        return _failed("API key not configured", retryable=True)

    document_id = _extract_document_id(comment_url)
    if not document_id:
        return _failed("Could not parse document ID", retryable=False)

    size = max(min(page_size, 25), 5)
    sort = f"{'-' if sort_order == 'desc' else ''}{sort_by}"
    id_key = f"objectid-{document_id}"
    missing_key = f"objectid-missing-{document_id}"
    page_key = f"comments-{document_id}-{size}-{page_number}-{sort}"
    object_id = page = None
    if db is not None:
        def cached(session):
            return (
                api_cache_get(session, _CACHE_TIER, missing_key, max_age_hours=_NOT_FOUND_CACHE_HOURS),
                api_cache_get(session, _CACHE_TIER, id_key, max_age_hours=_OBJECT_ID_CACHE_HOURS),
                api_cache_get(session, _CACHE_TIER, page_key, max_age_hours=_COMMENTS_CACHE_HOURS),
            )

        # One thread hop for the three reads: off the event loop.
        missing, known_id, page = await off_loop(db, cached)
        if missing:
            # Asked recently, and Regulations.gov had no such document:
            # asking again so soon would only spend the shared budget.
            return _failed(_NOT_FOUND, retryable=False)
        object_id = (known_id or {}).get("objectId")
    if page is not None:
        return page
    async def charge() -> None:
        # One unit before each request: the objectId lookup can end the call
        # (not found, refused), and the page it would have paid for with it
        # was never asked for. A refusal is the caller's answer (a 503), so
        # it passes through the handlers below untouched.
        if spend is not None:
            try:
                await spend(1)
            except Exception as refusal:
                raise _Refused(refusal) from refusal

    async with make_async_client() as client:
        try:
            if not object_id:
                await charge()
                object_id, status = await _object_id(client, api_key, document_id)
                if status == 429:
                    return _failed("Rate limit reached", retryable=True)
                if status not in (200, 404, 410):
                    return _failed(f"API error: {status}", retryable=_retryable(status))
                if not object_id:
                    if db is not None:
                        await api_cache_set_async(
                            db, _CACHE_TIER, missing_key, {"notFound": True}, normal_ttl_hours=_NOT_FOUND_CACHE_HOURS,
                        )
                    return _failed(_NOT_FOUND, retryable=False)
                if db is not None:
                    await api_cache_set_async(
                        db, _CACHE_TIER, id_key, {"objectId": object_id}, normal_ttl_hours=_OBJECT_ID_CACHE_HOURS,
                    )

            await charge()
            resp = await client.get(
                f"{REG_BASE}/comments",
                params={
                    "filter[commentOnId]": object_id,
                    "page[size]": size,
                    "page[number]": page_number,
                    "sort": sort,
                },
                headers={"X-Api-Key": api_key},
                timeout=_COMMENTS_TIMEOUT_S,
            )

            if resp.status_code == 429:
                logger.warning("Regulations.gov rate limit hit")
                return _failed("Rate limit reached", retryable=True)

            if resp.status_code != 200:
                logger.warning("Regulations.gov returned %d", resp.status_code)
                return _failed(f"API error: {resp.status_code}", retryable=_retryable(resp.status_code))

            data = resp.json()
            raw_comments = data.get("data", [])
            meta = data.get("meta", {})

            comments = []
            for item in raw_comments:
                attrs = item.get("attributes", {})
                comments.append({
                    "id": item.get("id", ""),
                    "title": attrs.get("title", ""),
                    "body": (attrs.get("comment", "") or "")[:2000],
                    "postedDate": attrs.get("postedDate", ""),
                    "submitterName": attrs.get("firstName", "Anonymous"),
                    "organization": attrs.get("organization", ""),
                    "category": attrs.get("category", ""),
                })

            result = {
                "comments": comments,
                "totalElements": meta.get("totalElements", len(comments)),
                "pageSize": size,
                "pageNumber": page_number,
            }
            if db is not None:
                await api_cache_set_async(db, _CACHE_TIER, page_key, result, normal_ttl_hours=_COMMENTS_CACHE_HOURS)
            return result

        except _Refused as refused:
            raise refused.refusal from None
        except httpx.TimeoutException:
            logger.warning("Regulations.gov request timed out")
            return _failed("Request timed out", retryable=True)
        except Exception as e:
            logger.warning("Regulations.gov fetch failed: %s", e)
            return _failed("Request failed", retryable=True)


class _Refused(Exception):
    """The upstream budget refused a request (fetch_comments' charge)."""

    def __init__(self, refusal: Exception):
        super().__init__(str(refusal))
        self.refusal = refusal


async def submit_comment(
    comment_url: str,
    comment_text: str,
    submitter_name: str = "Anonymous",
    organization: str = "",
) -> dict:
    """Submit a public comment to regulations.gov.

    The comment becomes part of the official public record.

    Returns dict with keys: success, commentId, message
    """
    api_key = settings.DATA_GOV_API_KEY
    if not api_key:
        return {"success": False, "message": "API key not configured"}

    doc_id = _extract_document_id(comment_url)
    if not doc_id:
        return {"success": False, "message": "Could not parse document ID"}

    if not comment_text or len(comment_text.strip()) < 10:
        return {"success": False, "message": "Comment must be at least 10 characters"}

    if len(comment_text) > 5000:
        return {"success": False, "message": "Comment must be 5000 characters or fewer"}

    payload = {
        "data": {
            "type": "comments",
            "attributes": {
                "commentOnDocumentId": doc_id,
                "comment": comment_text.strip(),
                "submitterType": "INDIVIDUAL",
                "firstName": submitter_name.strip() or "Anonymous",
            },
        }
    }

    if organization.strip():
        payload["data"]["attributes"]["organization"] = organization.strip()
        payload["data"]["attributes"]["submitterType"] = "ORGANIZATION"

    async with make_async_client() as client:
        try:
            resp = await client.post(
                f"{REG_BASE}/comments",
                json=payload,
                headers={
                    "Content-Type": "application/vnd.api+json",
                    "X-Api-Key": api_key,
                },
                timeout=DEFAULT_FETCH_TIMEOUT_S,
            )

            if resp.status_code == 201:
                result = resp.json()
                comment_id = result.get("data", {}).get("id", "")
                logger.info(
                    "Comment submitted to regulations.gov: %s on %s",
                    comment_id, doc_id,
                )
                return {
                    "success": True,
                    "commentId": comment_id,
                    "message": "Your comment has been submitted to the official public record.",
                }

            if resp.status_code == 429:
                return {"success": False, "message": "Rate limit reached. Please try again later."}

            body = resp.text[:500]
            logger.warning(
                "Regulations.gov comment submission failed (%d): %s",
                resp.status_code, body,
            )

            error_detail = ""
            try:
                err_data = resp.json()
                errors = err_data.get("errors", [])
                if errors:
                    error_detail = errors[0].get("detail", "")
            except Exception:
                pass

            return {
                "success": False,
                "message": error_detail or f"Submission failed (status {resp.status_code})",
            }

        except httpx.TimeoutException:
            return {"success": False, "message": "Request timed out. Please try again."}
        except Exception as e:
            logger.warning("Regulations.gov submission error: %s", e)
            return {"success": False, "message": "Submission failed. Please try again."}
