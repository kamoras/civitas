"""Fetch recent presidential actions from the Federal Register API.

Retrieves executive orders, presidential memoranda, and proclamations
for ingestion into the explore document store. The Federal Register API
is free and requires no API key.
"""

import asyncio
import logging
import re

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S

logger = logging.getLogger(__name__)

FR_BASE = "https://www.federalregister.gov/api/v1"

DOC_TYPES = [
    "executive_order",
    "presidential_memorandum",
    "proclamation",
]

DOC_TYPE_LABELS = {
    "executive_order": "Executive Order",
    "presidential_memorandum": "Presidential Memorandum",
    "proclamation": "Proclamation",
}

_COLLAPSE_WS = re.compile(r"[ \t]+")
_COLLAPSE_NL = re.compile(r"\n{3,}")

MAX_BODY_LEN = 15_000


_ALLOWED_HOSTS = {"www.federalregister.gov", "federalregister.gov"}


async def _fetch_body_text(client: httpx.AsyncClient, url: str) -> str:
    """Fetch the full-text HTML from Federal Register and extract plain text."""
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
        return text[:MAX_BODY_LEN]
    except Exception as e:
        logger.debug("Failed to fetch body from %s: %s", url, e)
        return ""


async def fetch_recent_presidential_actions(
    client: httpx.AsyncClient,
    pages: int = 5,
) -> list[dict]:
    """Fetch recent presidential documents from the Federal Register.

    Returns a list of dicts with keys: external_id, title, summary, body,
    date, doc_type, url, politician_name.
    """
    results: list[dict] = []
    seen_ids: set[str] = set()

    for doc_type in DOC_TYPES:
        for page in range(1, pages + 1):
            params = {
                "conditions[type][]": "PRESDOCU",
                "conditions[presidential_document_type][]": doc_type,
                "per_page": 20,
                "page": page,
                "order": "newest",
                "fields[]": [
                    "document_number",
                    "title",
                    "abstract",
                    "body_html_url",
                    "html_url",
                    "publication_date",
                    "signing_date",
                    "president",
                    "executive_order_number",
                ],
            }

            try:
                resp = await client.get(
                    f"{FR_BASE}/documents.json",
                    params=params,
                    timeout=DEFAULT_FETCH_TIMEOUT_S,
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
            except Exception as e:
                logger.warning("Federal Register fetch failed (%s p%d): %s", doc_type, page, e)
                break

            docs = data.get("results", [])
            if not docs:
                break

            body_tasks = []
            pending_docs = []

            for doc in docs:
                doc_num = doc.get("document_number", "")
                if not doc_num or doc_num in seen_ids:
                    continue
                seen_ids.add(doc_num)
                pending_docs.append(doc)
                body_tasks.append(
                    _fetch_body_text(client, doc.get("body_html_url", ""))
                )

            bodies = await asyncio.gather(*body_tasks)

            for doc, body_text in zip(pending_docs, bodies):
                doc_num = doc.get("document_number", "")
                president_info = doc.get("president", {}) or {}
                president_name = president_info.get("name", "")

                eo_num = doc.get("executive_order_number")
                title = doc.get("title", "Untitled")
                if eo_num and doc_type == "executive_order":
                    title = f"EO {eo_num}: {title}"

                abstract = (doc.get("abstract") or "").strip()
                summary = abstract[:1000] if abstract else body_text[:500]

                results.append({
                    "external_id": f"fr-{doc_num}",
                    "title": title,
                    "summary": summary,
                    "body": body_text,
                    "date": doc.get("signing_date") or doc.get("publication_date", ""),
                    "doc_type": DOC_TYPE_LABELS.get(doc_type, doc_type),
                    "url": doc.get("html_url", ""),
                    "politician_name": president_name,
                })

            if len(docs) < 20:
                break

    logger.info("Fetched %d presidential actions from Federal Register", len(results))
    return results
