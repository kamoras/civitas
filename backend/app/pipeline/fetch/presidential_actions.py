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


def _identifiers(doc_num: str, citation: str | None, eo_num) -> list[str]:
    """Canonical names other federal documents can cite this one by.

    Namespaced to match `document_authority.extract_citations`, which
    parses the same forms out of document text. An executive order is
    cited by number for decades after it issues; the "89 FR 12345" form
    is how one Federal Register document points at another.
    """
    from app.pipeline.analyze.document_authority import extract_citations

    ids: list[str] = []
    if doc_num:
        ids.append(f"frdoc:{doc_num}")
    if citation:
        ids.extend(sorted(extract_citations(citation)))
    if eo_num:
        ids.append(f"eo:{int(eo_num)}")
    return ids


async def fetch_recent_presidential_actions(
    client: httpx.AsyncClient,
    pages: int = 5,
) -> list[dict]:
    """Fetch recent presidential documents from the Federal Register.

    Returns a list of dicts with keys: external_id, title, summary, body,
    date, doc_type, url, politician_name, identifiers.
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
                    # The "89 FR 12345" form other federal documents cite
                    # this one by — the edge the citation graph is built
                    # from (pipeline/analyze/document_authority.py).
                    "citation",
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
                    # eo_num is only meaningful for executive orders — the
                    # same condition the title above uses. Declaring
                    # "eo:14110" on a memorandum that merely referenced the
                    # order would make every citation of that order point at
                    # the wrong document.
                    "identifiers": _identifiers(
                        doc_num, doc.get("citation"),
                        eo_num if doc_type == "executive_order" else None,
                    ),
                })

            if len(docs) < 20:
                break

    logger.info("Fetched %d presidential actions from Federal Register", len(results))
    return results
