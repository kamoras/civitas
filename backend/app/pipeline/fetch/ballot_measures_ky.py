"""Kentucky's ballot-measure strategy — the Secretary of State's
"Constitutional Amendments" page (sos.ky.gov/elections/Pages/
Constitutional-Amendments.aspx).

The page's own title names the year ("2026 Constitutional Amendments")
and its body gives, for each amendment, a "CONSTITUTIONAL AMENDMENT"
heading followed by the question exactly as it is put to voters ("Are
you in favor of ...?") and the amended section's text. The page is
reused cycle to cycle at the same address, so the title's year is
checked: a page still titled for an earlier year is not this election's
list (None, not []).

Stored verbatim:
- title: the heading as printed ("CONSTITUTIONAL AMENDMENT").
- number: the heading's own number when it prints one; empty when it
  doesn't. 2026's single amendment is headed without a number here,
  while the county ballots print "CONSTITUTIONAL AMENDMENT 1" (checked
  against Adair County's 2026 general ballot) — no number is supplied
  that the page doesn't print; the record is keyed on its heading.
- official_summary: the question paragraph. Kentucky's General Assembly
  writes that question into the enacting bill (2026: SB 10), hence
  title_authority.

The page says in its own words that the Secretary of State "cannot
interpret, nor give guidance" — no YES/NO explanation or fiscal
statement exists, and those stay null.

A page titled for this year whose body has no amendment heading is a
checked answer ([]).

Verified live 2026-09-28: one amendment on the November 3, 2026 ballot
(gubernatorial pardon/commutation blackout, SB 10).
"""

import logging
import re

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measures_state_common import get_text

logger = logging.getLogger(__name__)

PAGE_URL = "https://www.sos.ky.gov/elections/Pages/Constitutional-Amendments.aspx"
TITLE_AUTHORITY = "Kentucky General Assembly"
ORIGIN = "Kentucky General Assembly"

# SharePoint drops zero-width spaces into the heading ("CONSTITUTIO​N​​AL").
_ZERO_WIDTH_RE = re.compile(r"[​‌‍﻿]")
_HEADING_RE = re.compile(r"^CONSTITUTIONAL AMENDMENT(?:\s+(\d+))?$", re.IGNORECASE)


def _text(el) -> str:
    return clean_text(_ZERO_WIDTH_RE.sub("", el.text_content())) or ""


def parse_page(page_html: str, year: int) -> list[dict] | None:
    tree = lxml_html.fromstring(page_html)
    page_title = tree.xpath("//h1[contains(@class,'pageTitle')]")
    if not page_title or not _text(page_title[0]).startswith(f"{year} "):
        return None
    body = tree.xpath("//div[contains(@class,'ms-rtestate-field')]")
    if not body:
        return None

    measures = []
    current = None
    for el in body[0].iter("h1", "h2", "p"):
        text = _text(el)
        m = _HEADING_RE.match(text) if el.tag in ("h1", "h2") else None
        if m:
            current = {"number": m.group(1) or "", "title": text, "question": None}
            measures.append(current)
        elif current is not None and current["question"] is None and el.tag == "p" and text.endswith("?"):
            current["question"] = text

    if not measures and any(_text(p).endswith("?") for p in body[0].iter("p")):
        # A question on the page with no heading this reader recognises:
        # an empty answer here would read as "no amendments" — refuse.
        return None

    results = []
    for item in measures:
        if not item["question"]:
            return None
        results.append({
            "number": item["number"],
            "id_key": item["number"] or item["title"],
            "title": item["title"],
            "origin": ORIGIN,
            "official_summary": item["question"],
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": TITLE_AUTHORITY,
            "fiscal_authority": None,
        })
    return results


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page = await get_text(client, PAGE_URL, "KY constitutional amendments")
    if page is None:
        return None
    try:
        parsed = parse_page(page, year)
    except Exception:
        logger.exception("KY constitutional amendments page was not parseable HTML")
        return None
    if parsed is None:
        logger.warning("KY constitutional amendments page is not titled for %d or is malformed", year)
        return None
    return [(p, PAGE_URL) for p in parsed]
