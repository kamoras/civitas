"""Missouri's ballot-measure strategy — the Secretary of State's own
"Ballot Measures" HTML page (one of potentially many per-state
strategies; see ballot_measures_pdf.py for the shared single-PDF
contract this module does NOT use, and why).

No PDF at all: sos.mo.gov publishes the certified measures directly as
HTML at a stable, evergreen url_pattern
(sos.mo.gov/petitions/{year}ballotmeasures — verified live across 3 real
cycles, 2022/2024/2026, all 200). The page carries BOTH the state's
August primary measures and its November general measures under two
"The following ballot measures ... <election>" <h2> headings — this
module locates the "general election" heading specifically (matching
wording, not a hardcoded date) and reads only what falls under it, up
to the next <h2> (or end of document if there is none — a cycle with
no measures on the general ballot is a real, legitimate answer, not a
parse failure).

Each measure's elements (a "Official Ballot TitleAmendment N" or
"Official Ballot TitleProposition X" heading — both kinds are read, and a
measure heading in any other shape refuses the page,
link paragraphs, an "Official Ballot Title:" blockquote with the
question + fiscal note, and a "Fair Ballot Language:" blockquote with
real "A 'yes' vote will .../A 'no' vote will ..." framing — richer than
most states registered so far, which don't publish this) are NOT
reliably grouped one-div-per-measure: verified live that Missouri's own
page nests Amendment 8's heading paragraph as the LAST child of
Amendment 7's <div>, not the first child of its own — a real site
inconsistency, not a parsing bug. This module flattens every element
between the two headings into one ordered list (ignoring which <div>
each happens to sit in) before splitting on the heading text itself,
which is unaffected by that inconsistency.

Origin is read per-measure from its "[Proposed by ...]" line rather
than hardcoded fixed, unlike Louisiana/Virginia — Missouri, unlike
those two, has a real citizen-initiative process for constitutional
amendments (the 2024 abortion-rights amendment was one), so a future
amendment referred by petition rather than the General Assembly is a
real possibility this module must not misattribute.
"""

import logging
import re

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measure_text import NotYetPublished
from app.pipeline.fetch.http_utils import fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

URL_PATTERN = "https://www.sos.mo.gov/petitions/{year}ballotmeasures"

_rate_limiter = RateLimiter(rps=1.0)

_GENERAL_HEADING_RE = re.compile(r"general\s+election", re.IGNORECASE)
# "Official Ballot TitleAmendment 3" — no space, since text_content()
# doesn't insert one across a <br>. The colon-bearing "Official Ballot
# Title:" heading (a different element, later in the same measure) must
# not match this — the (?!:) lookahead after "Title" rules it out.
#
# Two kinds are printed: "Amendment N" (constitutional) and "Proposition
# X" (a statutory initiative or a referendum on an act — 2026's
# Proposition A is the referendum petition on the congressional map,
# which the Amendment-only pattern this once had skipped without a
# trace). The kind is kept, so the label is the page's own.
_MEASURE_START_RE = re.compile(
    r"^Official Ballot Title(?!:)\s*(Amendment|Proposition)\s+(\d+|[A-Z])\b", re.IGNORECASE,
)
# Any measure-start heading at all. One this reader can't number
# refuses the page.
_ANY_MEASURE_START_RE = re.compile(r"^Official Ballot Title(?!:)", re.IGNORECASE)
# The page's own section headings ("The following ballot measures ...
# <election>"): present means this is the page we know.
_SECTION_HEADING_RE = re.compile(r"The following ballot measures", re.IGNORECASE)
_SUMMARY_HEADING_RE = re.compile(r"^Official Ballot Title:\s*$", re.IGNORECASE)
_FAIR_LANGUAGE_HEADING_RE = re.compile(r"Fair Ballot Language:", re.IGNORECASE)
_FISCAL_START_RE = re.compile(r"^State\b.*\bestimate", re.IGNORECASE)
# Each group is the source's whole sentence, "A “yes” vote will ...": it
# used to start after "will", storing a fragment that read "A YES VOTE
# repeal Article I ..." under the page's heading (§7: verbatim).
_YES_NO_RE = re.compile(
    r'(A\s*[“"]?yes[”"]?\s*vote will\s*.*?)\s*(A\s*[“"]?no[”"]?\s*vote will\s*.*)$',
    re.IGNORECASE | re.DOTALL,
)


def _origin_for(proposed_by_text: str) -> str | None:
    """Read from the measure's own "[Proposed by ...]" line, never a
    fixed constant — Missouri has both legislature-referred and real
    citizen-initiated constitutional amendments."""
    text = (proposed_by_text or "").lower()
    if "general assembly" in text:
        return "Missouri General Assembly"
    if "initiative petition" in text:
        return "Missouri voters (initiative petition)"
    if "referendum petition" in text:
        return "Missouri voters (referendum petition)"
    return None


def _general_section_elements(tree) -> list:
    """Every element between the "general election" <h2> and the next
    <h2> (or end of document), flattened across whatever <div>
    boundaries the page happens to use — see module docstring for why
    trusting div-per-measure grouping is unsafe on this real page."""
    heading = next(
        (h for h in tree.xpath("//h2") if _GENERAL_HEADING_RE.search(h.text_content())), None,
    )
    if heading is None:
        return []
    elements = []
    el = heading.getnext()
    while el is not None and el.tag != "h2":
        elements.extend(el) if el.tag == "div" else elements.append(el)
        el = el.getnext()
    return elements


def split_measures(elements: list) -> dict[str, tuple[str, list]]:
    """{number: (kind, elements)} — "Amendment"/"Proposition" as printed.
    Raises on a measure heading it can't number, or a number printed
    twice (an "Amendment 1" and a "Proposition 1" would be one record)."""
    measures: dict[str, tuple[str, list]] = {}
    current_number = None
    for el in elements:
        text = el.text_content().strip() if el.tag == "p" else ""
        m = _MEASURE_START_RE.match(text)
        if not m and _ANY_MEASURE_START_RE.match(text):
            raise ValueError(f"MO: measure heading this reader can't number: {text[:80]!r}")
        if m:
            kind, current_number = m.group(1).capitalize(), m.group(2).upper()
            if current_number in measures:
                raise ValueError(f"MO: measure number {current_number} printed twice")
            measures[current_number] = (kind, [])
            continue
        if current_number is not None:
            measures[current_number][1].append(el)
    return measures


def _fiscal_split(blockquote) -> tuple[str | None, str | None]:
    """(official_summary, fiscal_impact) from the "Official Ballot
    Title:" blockquote — its own trailing child is the fiscal note
    (verified structurally on 3 real measures, both with and without a
    bulleted question list ahead of it), never guessed from wording
    alone. A blockquote whose last child doesn't look like a real fiscal
    sentence is treated as having none, rather than misclassifying real
    question text as a fiscal note."""
    children = list(blockquote)
    if not children:
        return clean_text(blockquote.text_content()), None
    last_text = clean_text(children[-1].text_content())
    if len(children) > 1 and last_text and _FISCAL_START_RE.match(last_text):
        summary = clean_text(" ".join(c.text_content() for c in children[:-1]))
        return summary, last_text
    return clean_text(blockquote.text_content()), None


def _parse_measure(number: str, elements: list, kind: str = "Amendment") -> dict | None:
    proposed_by = next(
        (e.text_content() for e in elements if e.tag == "p" and "proposed by" in e.text_content().lower()),
        "",
    )
    origin = _origin_for(proposed_by)

    summary_idx = next(
        (i for i, e in enumerate(elements) if e.tag == "p" and _SUMMARY_HEADING_RE.match(e.text_content().strip())),
        None,
    )
    if summary_idx is None or summary_idx + 1 >= len(elements) or elements[summary_idx + 1].tag != "blockquote":
        return None
    official_summary, fiscal_impact = _fiscal_split(elements[summary_idx + 1])
    if not official_summary:
        return None

    fair_idx = next(
        (i for i, e in enumerate(elements) if e.tag == "p" and _FAIR_LANGUAGE_HEADING_RE.search(e.text_content())),
        None,
    )
    yes_means = no_means = None
    if fair_idx is not None and fair_idx + 1 < len(elements) and elements[fair_idx + 1].tag == "blockquote":
        fair_text = clean_text(elements[fair_idx + 1].text_content()) or ""
        m = _YES_NO_RE.search(fair_text)
        if m:
            yes_means, no_means = clean_text(m.group(1)), clean_text(m.group(2))

    return {
        "number": number,
        "title": f"{kind} {number}",
        "origin": origin,
        "official_summary": official_summary,
        "fiscal_impact": fiscal_impact,
        "yes_means": yes_means,
        "no_means": no_means,
        "title_authority": "Missouri Secretary of State",
        "fiscal_authority": "Missouri Secretary of State" if fiscal_impact else None,
    }


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    """[(parsed, source_url), ...] for every measure on `year`'s general-
    election ballot, or None on a fetch/HTML-parse failure or a measure it
    can't read. [] only when the general-election section is there and
    names no measure. A page with only its primary section so far raises
    NotYetPublished (nothing certified to November yet is not "none"); a
    page with no section heading this reader knows is None."""
    url = URL_PATTERN.format(year=year)
    page_html = await fetch_text_with_retry(client, _rate_limiter, url, f"MO ballot measures {year}")
    if page_html is None:
        return None
    try:
        tree = lxml_html.fromstring(page_html)
    except Exception:
        logger.exception("MO ballot measures page for %d was not parseable HTML", year)
        return None

    has_general = any(_GENERAL_HEADING_RE.search(h.text_content()) for h in tree.xpath("//h2"))
    if not has_general:
        if any(_SECTION_HEADING_RE.search(h.text_content()) for h in tree.xpath("//h2")):
            # The page we know, carrying only the primary's section so far:
            # nothing is certified to November YET. Not "none".
            raise NotYetPublished(
                f"Missouri's {year} general-election ballot measures section", deadline_applies=False,
            )
        logger.warning("MO ballot measures page for %d has no section heading this reader knows", year)
        return None
    try:
        by_number = split_measures(_general_section_elements(tree))
    except ValueError:
        logger.exception("MO ballot measures page for %d", year)
        return None
    if not by_number:
        # The general-election section is there and lists nothing.
        return []

    results = []
    # Amendments by number, then Propositions by letter.
    for number in sorted(by_number, key=lambda n: (not n.isdigit(), int(n) if n.isdigit() else 0, n)):
        kind, elements = by_number[number]
        parsed = _parse_measure(number, elements, kind)
        if parsed is None:
            # Skipping it would publish the general-election list one
            # measure short, as "covered" — refuse the whole page instead.
            logger.warning("MO Amendment %s: didn't match the expected section shape — refusing the page", number)
            return None
        results.append((parsed, url))
    return results
