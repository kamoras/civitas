"""Fetch recent Supreme Court decisions from the Oyez API.

The Oyez project (api.oyez.org) provides free, unauthenticated access to
Supreme Court case metadata including case names, docket numbers, questions
presented, and decision descriptions.

Links point to the official supremecourt.gov docket page for each case,
which contains all filings, proceedings, and opinion PDFs.

We fetch cases from recent terms (SCOTUS terms run October-June) and
format them for the explore document store.
"""

import asyncio
import logging
from datetime import UTC, datetime

import httpx

from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S
from app.pipeline.fetch.oyez_common import OYEZ_BASE, strip_html as _strip_html, unix_to_date as _unix_to_date

logger = logging.getLogger(__name__)


async def fetch_scotus_cases(
    client: httpx.AsyncClient,
    terms: list[str] | None = None,
    per_page: int = 100,
) -> list[dict]:
    """Fetch Supreme Court cases from Oyez for the given terms.

    Args:
        client: httpx async client.
        terms: List of SCOTUS term years (e.g. ["2024", "2023"]).
               Defaults to last 3 terms.
        per_page: Number of cases per request.

    Returns:
        List of dicts ready for explore document ingestion with keys:
        external_id, title, summary, body, date, doc_type, url,
        politician_name, chamber.
    """
    if terms is None:
        current_year = datetime.now(tz=UTC).year
        terms = [str(y) for y in range(current_year, current_year - 3, -1)]

    results: list[dict] = []
    seen_ids: set[str] = set()

    for term in terms:
        try:
            resp = await client.get(
                f"{OYEZ_BASE}/cases",
                params={"per_page": per_page, "filter": f"term:{term}"},
                timeout=DEFAULT_FETCH_TIMEOUT_S,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Oyez API returned %d for term %s", resp.status_code, term,
                )
                continue

            cases = resp.json()
            if not isinstance(cases, list):
                continue

            for case in cases:
                docket = (case.get("docket_number") or "").strip()
                ext_id = f"scotus-{term}-{docket}" if docket else f"scotus-{term}-{case.get('ID', '')}"

                if ext_id in seen_ids:
                    continue
                seen_ids.add(ext_id)

                decided_date = ""
                for event in (case.get("timeline") or []):
                    if event.get("event") == "Decided":
                        dates = event.get("dates", [])
                        if dates:
                            decided_date = _unix_to_date(dates[-1])
                        break

                if not decided_date:
                    continue

                name = case.get("name", "")
                question = _strip_html(case.get("question") or "")
                description = _strip_html(case.get("description") or "")

                citation = case.get("citation") or {}
                volume = citation.get("volume", "")
                page = citation.get("page", "")
                cite_str = f"{volume} U.S. {page}" if volume and page else ""

                title = name
                if docket:
                    title = f"{name} (No. {docket})"

                body_parts = []
                if question:
                    body_parts.append(f"Question Presented:\n{question}")
                if description:
                    body_parts.append(f"Decision:\n{description}")
                if cite_str:
                    body_parts.append(f"Citation: {cite_str}")

                body = "\n\n".join(body_parts)
                summary = description or question or ""

                scotus_url = (
                    f"https://www.supremecourt.gov/docket/docketfiles/html/public/{docket}.html"
                    if docket
                    else ""
                )

                results.append({
                    "external_id": ext_id,
                    "title": title,
                    "summary": summary[:500],
                    "body": body,
                    "date": decided_date,
                    "doc_type": "Supreme Court Opinion",
                    "url": scotus_url,
                    "politician_name": None,
                    "chamber": "Judicial",
                })

        except httpx.TimeoutException:
            logger.warning("Oyez API timed out for term %s", term)
        except Exception as e:
            logger.warning("Oyez fetch failed for term %s: %s", term, e)

        await asyncio.sleep(0.5)

    logger.info("Fetched %d Supreme Court cases from Oyez (%s)", len(results), ", ".join(terms))
    return results
