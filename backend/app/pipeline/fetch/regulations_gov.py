"""Client for the Regulations.gov API v4.

Supports fetching public comments on rulemaking documents and
submitting new comments on behalf of users.

API docs: https://open.gsa.gov/api/regulationsgov/
Rate limit: 1,000 requests/hour with an API key.
"""

import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

REG_BASE = "https://api.regulations.gov/v4"


def _extract_document_object_id(comment_url: str) -> str | None:
    """Extract the regulations.gov document objectId from a comment URL.

    URLs look like:
      https://www.regulations.gov/commenton/EPA-HQ-OAR-2021-0208-0001
      https://www.regulations.gov/document/EPA-HQ-OAR-2021-0208-0001
    """
    match = re.search(r"regulations\.gov/(?:commenton|document)/([A-Z0-9_-]+)", comment_url)
    return match.group(1) if match else None


async def fetch_comments(
    comment_url: str,
    page_size: int = 25,
    page_number: int = 1,
    sort_by: str = "postedDate",
    sort_order: str = "desc",
) -> dict:
    """Fetch public comments for a document from regulations.gov.

    Returns dict with keys: comments, totalElements, pageSize, pageNumber
    """
    api_key = settings.DATA_GOV_API_KEY
    if not api_key:
        return {"comments": [], "totalElements": 0, "error": "API key not configured"}

    doc_id = _extract_document_object_id(comment_url)
    if not doc_id:
        return {"comments": [], "totalElements": 0, "error": "Could not parse document ID"}

    params = {
        "filter[commentOnId]": doc_id,
        "page[size]": max(min(page_size, 25), 5),
        "page[number]": page_number,
        "sort": f"{'-' if sort_order == 'desc' else ''}{sort_by}",
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{REG_BASE}/comments",
                params=params,
                headers={"X-Api-Key": api_key},
                timeout=15.0,
            )

            if resp.status_code == 429:
                logger.warning("Regulations.gov rate limit hit")
                return {"comments": [], "totalElements": 0, "error": "Rate limit reached"}

            if resp.status_code != 200:
                logger.warning("Regulations.gov returned %d", resp.status_code)
                return {"comments": [], "totalElements": 0, "error": f"API error: {resp.status_code}"}

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

            return {
                "comments": comments,
                "totalElements": meta.get("totalElements", len(comments)),
                "pageSize": page_size,
                "pageNumber": page_number,
            }

        except httpx.TimeoutException:
            logger.warning("Regulations.gov request timed out")
            return {"comments": [], "totalElements": 0, "error": "Request timed out"}
        except Exception as e:
            logger.warning("Regulations.gov fetch failed: %s", e)
            return {"comments": [], "totalElements": 0, "error": "Request failed"}


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

    doc_id = _extract_document_object_id(comment_url)
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

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{REG_BASE}/comments",
                json=payload,
                headers={
                    "Content-Type": "application/vnd.api+json",
                    "X-Api-Key": api_key,
                },
                timeout=30.0,
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
