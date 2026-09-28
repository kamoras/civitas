"""Oklahoma's ballot-measure strategy — the Secretary of State's own
"Search State Questions" register (sos.ok.gov/gov/questions.aspx), read
as HTML (one of MULTI_DOCUMENT_STRATEGIES in ballot_measures_pdf.py).

In Oklahoma the Secretary of State numbers every State Question and,
once one qualifies, notifies the State Election Board of its number,
ballot title and election date (the Election Board's own State
Questions page says exactly this). The register lists every State
Question newest-first as a table — SQ number (linking the question's
filed documents), type, petition/legislative-referendum numbers, the
resolution or bill number, a citation line, a short subject line, and
the election date. Verified live 2026-09-28: two rows dated "ELECTION
DATE: November 3, 2026" — SQ 845 (HJR1024, "Judicial Nominating
Commission") and SQ 847 (SJR 39, "Real Property Valuation") — matching
the Election Board's own "Two state questions have qualified for the
November 3, 2026 election."

What is NOT read: the ballot title itself. It exists only inside each
question's linked PDF, and those are scans — SQ 845's has no text layer
at all, SQ 847's has an OCR layer with character errors ("ta1rable",
"e1rceed"). OCR'd text is not verbatim text, so official_summary,
official_title and the yes/no framing stay None, and each measure links
its filed PDF as the source. What IS stored is verbatim from the
register: the SQ number, and the Secretary of State's own subject line
as the title (not presented as the official ballot title —
official_title is None for exactly that reason).

Only the register's first page (the newest questions) is read. A State
Question set for a general election is filed months ahead of it and so
sits among the highest numbers; a first page with no row dated `year`'s
general election reads as None (not established), never [].
"""

import logging
import re
from datetime import date
from urllib.parse import urljoin

import httpx
from lxml import html as lxml_html

from app.election_calendar import next_election_day
from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.http_utils import fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

URL = "https://www.sos.ok.gov/gov/questions.aspx"

_rate_limiter = RateLimiter(rps=1.0)

_DATE_RE = re.compile(r"ELECTION DATE:\s*([A-Z][a-z]+ \d{1,2}, \d{4})")
_ORIGINS = {
    "legislative": "Oklahoma Legislature",
    "initiative": "Oklahoma voters (initiative petition)",
    "referendum": "Oklahoma voters (referendum petition)",
}


def _general_election_label(year: int) -> str:
    day = next_election_day(date(year, 1, 1))
    return f"{day.strftime('%B')} {day.day}, {day.year}"


def parse_register(page_html: str, year: int) -> list[tuple[dict, str]] | None:
    tree = lxml_html.fromstring(page_html)
    target = _general_election_label(year)
    header_row = next(
        (tr for tr in tree.xpath("//tr[th]") if "SQ Num" in " ".join(tr.text_content().split())),
        None,
    )
    if header_row is None:
        return None
    headers = [" ".join(th.text_content().split()) for th in header_row.xpath("th")]
    try:
        col = {name: headers.index(name) for name in ("SQ Num", "Type", "Summary", "Status")}
    except ValueError:
        return None

    results = []
    table = next(header_row.iterancestors("table"))
    for tr in table.xpath(".//tr[td]"):
        cells = tr.xpath("td")
        if len(cells) != len(headers):
            continue
        status = " ".join(cells[col["Status"]].text_content().split())
        m = _DATE_RE.search(status)
        if m is None or m.group(1) != target:
            continue
        link = cells[col["SQ Num"]].xpath(".//a[@href]")
        number = clean_text(cells[col["SQ Num"]].text_content())
        subject = clean_text(cells[col["Summary"]].text_content())
        if not link or not number or not number.isdigit() or not subject:
            logger.warning("OK register row for %s didn't match the verified shape — refusing", target)
            return None
        kind = (clean_text(cells[col["Type"]].text_content()) or "").lower()
        results.append((
            {
                "number": number,
                "title": subject,
                "official_title": None,
                "origin": _ORIGINS.get(kind),
                "official_summary": None,
                "fiscal_impact": None,
                "yes_means": None,
                "no_means": None,
                "title_authority": None,
                "fiscal_authority": None,
            },
            urljoin(URL, link[0].get("href")),
        ))
    return results or None


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page_html = await fetch_text_with_retry(client, _rate_limiter, URL, "OK state questions register")
    if page_html is None:
        return None
    try:
        return parse_register(page_html, year)
    except Exception:
        logger.exception("OK state questions register was not parseable")
        return None
