"""Fetch Federal Register rulemaking, proposed rules, and notices.

The Federal Register API (federalregister.gov/api/v1) is free with no key.
This fetcher covers the non-presidential document types:
  - RULE: Final rules and regulations
  - PRORULE: Proposed rules (often open for public comment)
  - NOTICE: Agency notices (hearings, comment requests, guidance)

Each document may have a comment_url and comments_close_on date when
the public can submit comments via regulations.gov.
"""

import asyncio
import logging
import re

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S

logger = logging.getLogger(__name__)

_COLLAPSE_WS = re.compile(r"[ \t]+")
_COLLAPSE_NL = re.compile(r"\n{3,}")
MAX_BODY_LEN = 15_000

FR_BASE = "https://www.federalregister.gov/api/v1"

FR_DOC_TYPES = ["RULE", "PRORULE", "NOTICE"]

FR_TYPE_LABELS = {
    "Rule": "Final Rule",
    "Proposed Rule": "Proposed Rule",
    "Notice": "Notice",
}

FIELDS = [
    "document_number",
    "title",
    "abstract",
    "type",
    "subtype",
    "publication_date",
    "comment_url",
    "comments_close_on",
    "agencies",
    "html_url",
    "body_html_url",
    "action",
    "dates",
    "regulation_id_numbers",
]


def _primary_agency(agencies: list[dict]) -> str:
    """Extract the top-level agency name from the agencies list."""
    if not agencies:
        return ""
    for agency in agencies:
        if not agency.get("parent_id"):
            return agency.get("name", agency.get("raw_name", ""))
    return agencies[0].get("name", agencies[0].get("raw_name", ""))


_BOILERPLATE_END = re.compile(
    r"for more details\.\s*$", re.MULTILINE
)


_ALLOWED_HOSTS = {"www.federalregister.gov", "federalregister.gov"}


async def _fetch_body_text(client: httpx.AsyncClient, url: str) -> str:
    """Fetch full-text HTML from Federal Register and extract plain text."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.hostname not in _ALLOWED_HOSTS or parsed.scheme != "https":
            logger.debug("Rejected non-FR URL: %s", url[:100])
            return ""
        resp = await client.get(url, timeout=DEFAULT_FETCH_TIMEOUT_S)
        if resp.status_code != 200:
            return ""
        tree = lxml_html.fromstring(resp.text)
        for el in tree.iter("script", "style", "svg", "img"):
            el.drop_tree()
        raw = tree.text_content()
        text = _COLLAPSE_WS.sub(" ", raw)
        text = _COLLAPSE_NL.sub("\n\n", text).strip()
        m = _BOILERPLATE_END.search(text[:1500])
        if m:
            text = text[m.end():].strip()
        return text[:MAX_BODY_LEN]
    except Exception as e:
        logger.debug("Failed to fetch FR body from %s: %s", url, e)
        return ""


async def fetch_fr_rulemaking(
    client: httpx.AsyncClient,
    pages: int = 3,
    per_page: int = 20,
) -> list[dict]:
    """Fetch recent rulemaking documents from the Federal Register.

    Returns list of dicts with keys: external_id, title, summary, body,
    date, doc_type, url, agency_name, comment_url, comments_close_on, chamber.
    """
    pending: list[dict] = []
    seen_ids: set[str] = set()

    for fr_type in FR_DOC_TYPES:
        for page in range(1, pages + 1):
            params: dict = {
                "conditions[type][]": fr_type,
                "per_page": per_page,
                "page": page,
                "order": "newest",
                "fields[]": FIELDS,
            }

            try:
                resp = await client.get(
                    f"{FR_BASE}/documents.json",
                    params=params,
                    timeout=DEFAULT_FETCH_TIMEOUT_S,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Federal Register returned %d for %s page %d",
                        resp.status_code, fr_type, page,
                    )
                    break
                data = resp.json()
            except httpx.TimeoutException:
                logger.warning("Federal Register timed out for %s page %d", fr_type, page)
                break
            except Exception as e:
                logger.warning("Federal Register fetch failed (%s p%d): %s", fr_type, page, e)
                break

            docs = data.get("results", [])
            if not docs:
                break

            for doc in docs:
                doc_num = doc.get("document_number", "")
                if not doc_num or doc_num in seen_ids:
                    continue
                seen_ids.add(doc_num)
                pending.append(doc)

            if len(docs) < per_page:
                break

        await asyncio.sleep(0.3)

    BATCH = 8
    results: list[dict] = []
    for i in range(0, len(pending), BATCH):
        batch = pending[i : i + BATCH]
        bodies = await asyncio.gather(
            *[_fetch_body_text(client, doc.get("body_html_url", "")) for doc in batch]
        )
        for doc, body_text in zip(batch, bodies):
            raw_type = doc.get("type", "")
            doc_type = FR_TYPE_LABELS.get(raw_type, raw_type)

            agencies = doc.get("agencies") or []
            agency_name = _primary_agency(agencies)

            abstract = (doc.get("abstract") or "").strip()
            action = (doc.get("action") or "").strip()
            dates_text = (doc.get("dates") or "").strip()

            summary = abstract[:500] if abstract else action[:300]

            if body_text:
                body = body_text
            else:
                body_parts = []
                if action:
                    body_parts.append(f"Action: {action}")
                if abstract:
                    body_parts.append(abstract)
                if dates_text:
                    body_parts.append(f"Dates: {dates_text}")
                body = "\n\n".join(body_parts)

            comment_url = doc.get("comment_url") or None
            comments_close = doc.get("comments_close_on") or None

            results.append({
                "external_id": f"fr-reg-{doc.get('document_number', '')}",
                "title": doc.get("title", "Untitled"),
                "summary": summary,
                "body": body,
                "date": doc.get("publication_date", ""),
                "doc_type": doc_type,
                "url": doc.get("html_url", ""),
                "agency_name": agency_name,
                "comment_url": comment_url,
                "comments_close_on": comments_close,
                "chamber": "Regulatory",
            })

        await asyncio.sleep(0.3)

    logger.info(
        "Fetched %d Federal Register rulemaking documents (%d open for comment)",
        len(results),
        sum(1 for r in results if r.get("comment_url")),
    )
    return results
