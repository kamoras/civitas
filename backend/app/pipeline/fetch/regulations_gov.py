"""Client for the Regulations.gov API v4.

Supports fetching public comments on rulemaking documents and
submitting new comments on behalf of users.

API docs: https://open.gsa.gov/api/regulationsgov/
Rate limit: 1,000 requests/hour with an API key.
"""

import asyncio
import logging
import re
from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.http_client import make_async_client
from app.pipeline.cache import api_cache_get, api_cache_set
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
    or None; the HTTP status). A 404, or a document with no objectId, is
    Regulations.gov not having it; any other status is a failure of this
    request, not an answer about the document."""
    resp = await client.get(
        f"{REG_BASE}/documents/{document_id}",
        headers={"X-Api-Key": api_key},
        timeout=_COMMENTS_TIMEOUT_S,
    )
    if resp.status_code != 200:
        logger.warning("Regulations.gov document %s returned %d", document_id, resp.status_code)
        return None, resp.status_code
    return ((resp.json().get("data") or {}).get("attributes") or {}).get("objectId") or None, 200


def _failed(error: str, *, retryable: bool) -> dict:
    """An answer carrying no comments. `retryable` is whether asking again
    could succeed: a timeout or a rate limit can, an unknown document
    can't — which decides whether the answer may be cached (api/explore)."""
    return {"comments": [], "totalElements": 0, "error": error, "retryable": retryable}


_NOT_FOUND = "Document not found on Regulations.gov"


async def fetch_comments(
    comment_url: str,
    page_size: int = 25,
    page_number: int = 1,
    sort_by: str = "postedDate",
    sort_order: str = "desc",
    *,
    db: Session | None = None,
    spend: Callable[[int], None] | None = None,
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
        return _failed("API key not configured", retryable=False)

    document_id = _extract_document_id(comment_url)
    if not document_id:
        return _failed("Could not parse document ID", retryable=False)

    size = max(min(page_size, 25), 5)
    sort = f"{'-' if sort_order == 'desc' else ''}{sort_by}"
    id_key = f"objectid-{document_id}"
    page_key = f"comments-{document_id}-{size}-{page_number}-{sort}"
    object_id = page = None
    if db is not None:
        known = api_cache_get(db, _CACHE_TIER, id_key, max_age_hours=_OBJECT_ID_CACHE_HOURS) or {}
        if known.get("notFound"):
            # Asked before, and Regulations.gov has no such document: asking
            # again would only spend the shared budget.
            return _failed(_NOT_FOUND, retryable=False)
        object_id = known.get("objectId")
        page = api_cache_get(db, _CACHE_TIER, page_key, max_age_hours=_COMMENTS_CACHE_HOURS)
    if page is not None:
        return page
    if spend is not None:
        # A write to the shared budget (api/throttle.py): off the event loop.
        await asyncio.to_thread(spend, 1 if object_id else 2)

    async with make_async_client() as client:
        try:
            if not object_id:
                object_id, status = await _object_id(client, api_key, document_id)
                if status == 429:
                    return _failed("Rate limit reached", retryable=True)
                if status not in (200, 404):
                    return _failed(f"API error: {status}", retryable=True)
                if not object_id:
                    if db is not None:
                        api_cache_set(db, _CACHE_TIER, id_key, {"notFound": True},
                                      normal_ttl_hours=_OBJECT_ID_CACHE_HOURS)
                    return _failed(_NOT_FOUND, retryable=False)
                if db is not None:
                    api_cache_set(db, _CACHE_TIER, id_key, {"objectId": object_id},
                                  normal_ttl_hours=_OBJECT_ID_CACHE_HOURS)

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
                return _failed(f"API error: {resp.status_code}", retryable=True)

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
                api_cache_set(db, _CACHE_TIER, page_key, result, normal_ttl_hours=_COMMENTS_CACHE_HOURS)
            return result

        except httpx.TimeoutException:
            logger.warning("Regulations.gov request timed out")
            return _failed("Request timed out", retryable=True)
        except Exception as e:
            logger.warning("Regulations.gov fetch failed: %s", e)
            return _failed("Request failed", retryable=True)


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
