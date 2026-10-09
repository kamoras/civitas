"""Fetch recent Supreme Court decisions from the Oyez API.

The Oyez project (api.oyez.org) provides free, unauthenticated access to
Supreme Court case metadata including case names, docket numbers, questions
presented, and decision descriptions.

Each decided case links to the Court's own slip opinion where the Court has
posted one (supremecourt.gov/opinions/slipopinion/<yy>, read per term),
names its author and leads with the Court's one-sentence statement of the
holding; otherwise it links to the case's docket page. Until 2026-10 every
case linked to its docket page under an "opinion" label with no author.

We fetch cases from recent terms (SCOTUS terms run October-June) and
format them for the explore document store.
"""

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from lxml import etree
from lxml import html as lxml_html

from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S
from app.pipeline.fetch.oyez_common import OYEZ_BASE, strip_html as _strip_html, unix_to_date as _unix_to_date

logger = logging.getLogger(__name__)

SLIP_OPINIONS_URL = "https://www.supremecourt.gov/opinions/slipopinion/{yy}"
SCOTUS_BASE = "https://www.supremecourt.gov"


def parse_slip_opinions(page: str) -> dict[str, dict]:
    """{docket: {"pdf", "author", "holding", "date"}} from a term's
    slip-opinion table: the opinion's PDF, the author's code ("BK", "R";
    "PC" for per curiam), the holding the Court states in the link's title,
    and the date the Court gives (ISO; None if unreadable)."""
    out: dict[str, dict] = {}
    for row in lxml_html.fromstring(page).xpath("//tr[td]"):
        cells = row.xpath("./td")
        link = row.xpath(".//a[contains(@href, '.pdf')]")
        if len(cells) < 5 or not link:
            continue
        docket = cells[2].text_content().strip()
        out.setdefault(docket, {
            "pdf": SCOTUS_BASE + link[0].get("href") if link[0].get("href", "").startswith("/") else link[0].get("href"),
            "author": cells[4].text_content().strip(),
            "holding": " ".join((link[0].get("title") or "").split()),
            "date": _slip_date(cells[1].text_content()),
        })
    return out


def _slip_date(text: str) -> str | None:
    """The slip-opinion table's date ("6/29/26") as ISO."""
    try:
        return datetime.strptime(text.strip(), "%m/%d/%y").date().isoformat()
    except ValueError:
        return None


def justice_for_code(code: str, justices: list[tuple[str, str]]) -> str | None:
    """The full name of the justice a slip-opinion author code names: the
    Court writes its three most senior members' last-name initial ("R",
    "T", "A") and the others' first and last initials ("BK", "KJ"). None
    for per curiam, a code no sitting justice fits, or one two fit.
    `justices`: (full name, last name)."""
    code = code.strip().upper()
    if len(code) not in (1, 2) or code == "PC":
        return None
    fits = [name for name, last in justices if last and (
        (len(code) == 1 and last[0].upper() == code)
        or (len(code) == 2 and name[:1].upper() == code[0] and last[0].upper() == code[1])
    )]
    return fits[0] if len(fits) == 1 else None


async def fetch_slip_opinions(client: httpx.AsyncClient, term: str) -> dict[str, dict] | None:
    """parse_slip_opinions for one term, or None when the page can't be read."""
    try:
        resp = await client.get(SLIP_OPINIONS_URL.format(yy=f"{int(term) % 100:02d}"),
                                timeout=DEFAULT_FETCH_TIMEOUT_S, follow_redirects=True)
    except httpx.HTTPError as e:
        logger.warning("Slip opinions for term %s unavailable: %s", term, e)
        return None
    if resp.status_code != 200:
        logger.warning("Slip opinions for term %s returned %d", term, resp.status_code)
        return None
    try:
        return parse_slip_opinions(resp.text)
    except (etree.LxmlError, ValueError, TypeError) as e:  # not a page lxml can read
        logger.warning("Slip opinions for term %s unreadable: %s", term, e)
        return None


async def fetch_scotus_cases(
    client: httpx.AsyncClient,
    terms: list[str] | None = None,
    per_page: int = 100,
    justices: list[tuple[str, str]] | None = None,
) -> list[dict]:
    """Fetch Supreme Court cases from Oyez for the given terms.

    Args:
        client: httpx async client.
        terms: List of SCOTUS term years (e.g. ["2024", "2023"]).
               Defaults to last 3 terms.
        per_page: Number of cases per request.
        justices: (full name, last name) of the sitting justices, to name
               each opinion's author (justice_for_code).

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
        slips = await fetch_slip_opinions(client, term) or {}
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

                slip = slips.get(docket) or {}
                # The Court's own date where it has posted the opinion: Oyez's
                # "Decided" event is a copy, and one case of 114 read a month
                # late there (2026-10-09).
                decided_date = slip.get("date") or decided_date
                if slip.get("holding"):
                    body_parts.insert(0, f"Holding (the Court's summary):\n{slip['holding']}")
                body = "\n\n".join(body_parts)
                summary = slip.get("holding") or description or question or ""

                scotus_url = slip.get("pdf") or (
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
                    "politician_name": justice_for_code(slip.get("author", ""), justices or []),
                    "chamber": "Judicial",
                })

        except httpx.TimeoutException:
            logger.warning("Oyez API timed out for term %s", term)
        except Exception as e:
            logger.warning("Oyez fetch failed for term %s: %s", term, e)

        await asyncio.sleep(0.5)

    logger.info("Fetched %d Supreme Court cases from Oyez (%s)", len(results), ", ".join(terms))
    return results
