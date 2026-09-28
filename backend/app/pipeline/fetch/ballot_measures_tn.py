"""Tennessee's ballot-measure strategy — the Secretary of State's
"Proposed Constitutional Amendments" page
(sos.tn.gov/elections/announcements/{year}-proposed-constitutional-amendments).

Tennessee submits amendments only at gubernatorial elections (Tenn.
Const. art. XI §3), and the Secretary of State publishes one page per
such election. Its "Proposed Amendments on the November <year> Ballot"
section (the page also previews amendments pending for a LATER ballot,
under their own heading — those are not read) opens with the state's
own description of what each vote does:

    A “yes” vote is a vote to amend the Constitution and adopt the
    language in the proposed amendment.
    A “no” vote is a vote to keep the current language in the
    Constitution unchanged.

Those two sentences are the state's framing for every amendment in the
section, and are stored verbatim as each one's yes_means / no_means.

Then, per amendment: "Constitutional Amendment #N", the resolutions
that proposed it, "Summary:" (the Attorney General's — the page says
"The Attorney General has provided a short summary of each amendment
on the ballot"), and "Question:" (the full ballot question with the
amended text). Stored verbatim:

- official_summary: the Attorney General's summary. The Question is
  the amendment's full legal text and is not stored (the same scope
  ballot_measures_va.py takes, and for the same reason: joining two
  different sections under a label this module wrote would blend
  sourced and authored text).
- title_authority: the Tennessee Attorney General, the drafter of the
  only prose quoted here beyond the heading.

No fiscal statement is published — null.

No page for `year` (the address answers 404 in non-gubernatorial years
and before publication) raises NotYetPublished — not yet covered, no
alert — never []; any other fetch failure is None. A page whose section
lists no amendment is a checked answer ([]).

- number: N; title: the heading as printed — a label ("Constitutional
  Amendment #1"), not a ballot title, so no official_title is claimed.

Verified live 2026-09-28: Amendments #1-#3 on the November 3, 2026
ballot (bail, state property tax prohibition, victims' rights).
"""

import logging
import re

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measures_state_common import get_text_unless_missing

logger = logging.getLogger(__name__)

URL_PATTERN = "https://sos.tn.gov/elections/announcements/{year}-proposed-constitutional-amendments"
TITLE_AUTHORITY = "Tennessee Attorney General"
ORIGIN = "Tennessee General Assembly"

_HEADING_RE = re.compile(r"^Constitutional Amendment\s*#\s*(\d+)$", re.IGNORECASE)
_YES_RE = re.compile(r"^A\s+“yes”\s+vote\b", re.IGNORECASE)
_NO_RE = re.compile(r"^A\s+“no”\s+vote\b", re.IGNORECASE)


def _is_gubernatorial_year(year: int) -> bool:
    """Tennessee elects its governor every four years: 2022, 2026, 2030."""
    return year % 4 == 2


def _text(el) -> str:
    return clean_text(el.text_content()) or ""


def parse_page(page_html: str, year: int) -> list[dict] | None:
    tree = lxml_html.fromstring(page_html)
    # "on the", not "for the": the page heads the amendments pending a
    # second legislative passage "Proposed Amendments FOR the November
    # 2030 Ballot" (verified), and those are not yet on any ballot. If the
    # wording changes, this finds no section and the answer is None (not
    # covered) — never a pending amendment presented as certified.
    wanted = f"on the november {year} ballot"
    heading = next((h for h in tree.xpath("//h3") if wanted in _text(h).lower()), None)
    if heading is None:
        return None
    section = []
    el = heading.getnext()
    while el is not None and el.tag not in ("h3", "hr"):
        section.append(el)
        el = el.getnext()

    yes_means = no_means = None
    for li in (li for ul in section if ul.tag == "ul" for li in ul.xpath("./li")):
        text = _text(li)
        if _YES_RE.match(text):
            yes_means = text
        elif _NO_RE.match(text):
            no_means = text

    results = []
    current = None
    mode = None
    for el in section:
        text = _text(el)
        m = _HEADING_RE.match(text) if el.tag == "h4" else None
        if el.tag == "h4" and not m:
            # An amendment heading in a shape this reader doesn't know —
            # skipping it could turn a real amendment into "none".
            return None
        if m:
            current = {"number": m.group(1), "title": text, "summary": []}
            results.append(current)
            mode = None
        elif current is not None and el.tag == "p" and text == "Summary:":
            mode = "summary"
        elif current is not None and el.tag == "p" and text == "Question:":
            mode = "question"
        elif current is not None and mode == "summary" and el.tag == "p" and text:
            current["summary"].append(text)

    parsed = []
    for r in results:
        summary = "\n".join(r["summary"])
        if not summary:
            return None
        parsed.append({
            "number": r["number"],
            "title": r["title"],
            "origin": ORIGIN,
            "official_summary": summary,
            "fiscal_impact": None,
            "yes_means": yes_means,
            "no_means": no_means,
            "title_authority": TITLE_AUTHORITY,
            "fiscal_authority": None,
        })
    return parsed


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    url = URL_PATTERN.format(year=year)
    # The Secretary of State creates this page only for an election that
    # has amendments, and only once it posts them: a 404 is "not yet"
    # (every non-gubernatorial year, and a gubernatorial one before
    # publication), not an outage.
    page = await get_text_unless_missing(
        client, url, f"TN proposed constitutional amendments {year}",
        awaited=f"Tennessee's {year} proposed constitutional amendments page",
        # Amendments go only to a gubernatorial general (art. XI §3) —
        # 2026, 2030, ... — and the Secretary has posted this page for
        # each one, so past the expected-by cutoff its absence then is a
        # failure. In any other year a 404 is the normal state.
        deadline_applies=_is_gubernatorial_year(year),
    )
    if page is None:
        return None
    try:
        parsed = parse_page(page, year)
    except Exception:
        logger.exception("TN proposed constitutional amendments page for %d was not parseable HTML", year)
        return None
    if parsed is None:
        logger.warning("TN amendments page for %d has no section for that year's ballot", year)
        return None
    return [(p, url) for p in parsed]
