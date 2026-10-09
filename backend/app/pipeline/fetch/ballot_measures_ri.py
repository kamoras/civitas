"""Rhode Island's ballot-measure strategy — the Department of State's
"Voter Information Handbook: A Guide to State Referenda" (one of
MULTI_DOCUMENT_STRATEGIES in ballot_measures_pdf.py).

The handbook is filed at vote.sos.ri.gov/Forms/Elections/Guides/
VoterRef<yy>.pdf; 2026's (18 pages, "General Election November 3, 2026")
is the only year on file under that name (VoterRef24 and VoterRef22 answer
404, checked 2026-10-08), so a 404 reads as not yet published, with no
deadline: Rhode Island may hold a general with no state question.

Its "What's in this guide" page lists each state question ("Question 1 -
Higher Education Facilities ....9"); each has an "About Question N:" page
whose "What it will look like on the ballot:" box is set in two columns:
on the left the question as printed — "1. HIGHER EDUCATION FACILITIES -
$275,000,000" and its description — and on the right the state's own
framing, "Your vote to “Approve” means that ..." and "Your vote to
“Reject” means that ...". The columns are cut at the right column's
"Approve" heading and read separately (read across, pdfplumber interleaves
them line by line). Stored verbatim:
- number N, title the printed heading after "N. ";
- official_summary: the description, its "a." / "b." allocation lines kept
  one per line;
- yes_means / no_means: the two "Your vote to ..." sentences, whole;
- official_title: the question the handbook says the ballot asks of the
  bond referenda ("Shall the act passed by the General Assembly ...?"),
  for the questions its "Referenda Questions N – M involve ..." sentence
  names — never for a question outside that range.
The handbook names no drafter for any of these, so none is credited. Its
"Explanation and purpose" and cost tables are the Department's own
explanation, not ballot text, and are not stored.

Checks, each refusing the state (None): the cover names the general
election and its date; the contents list and the "About Question" pages
name the same questions, numbered 1..N; each ballot box's heading carries
its own question's number; a word straddling the column cut, or a framing
sentence that doesn't open "Your vote to “Approve” / “Reject” means", is
not the verified shape.
"""

import io
import logging
import re

import httpx
import pdfplumber

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measures_state_common import election_day, get_bytes_unless_missing, names_date

logger = logging.getLogger(__name__)

URL_PATTERN = "https://vote.sos.ri.gov/Forms/Elections/Guides/VoterRef{yy}.pdf"
ORIGIN = "Rhode Island General Assembly"

_CONTENTS_RE = re.compile(r"^Question (\d+) - .+?\s*\.{3,}\s*\d+$")
_ABOUT_RE = re.compile(r"^About Question (\d+):$")
_HEADING_RE = re.compile(r"^(\d+)\.\s+(.+)$")
_ITEM_RE = re.compile(r"^[a-z]\.\s")
_YES_START = "Your vote to “Approve” means"
_NO_START = "Your vote to “Reject” means"
_RANGE_RE = re.compile(r"Referenda Questions (\d+) [–-] (\d+) involve")
_ASKED_RE = re.compile(r"On the ballot, you will be asked:\s*“(.+?\?)”", re.DOTALL)


def ballot_columns(page) -> tuple[str, str] | None:
    """(left, right) text of an "About Question" page's ballot box, or
    None if the page has no box in the verified shape."""
    words = page.extract_words()
    top = next((w for w in words if w["text"] == "What"), None)
    end = next((w for w in words if w["text"] == "Explanation"), None)
    heads = [w["x0"] for w in words if w["text"] == "Approve" and w["x0"] > page.width / 2]
    if top is None or end is None or not heads:
        return None
    cut = min(heads) - 2
    if any(w["x0"] < cut < w["x1"] for w in words if top["bottom"] < w["top"] < end["top"]):
        logger.warning("RI ballot box: a word straddles the column cut — refusing")
        return None
    left = page.crop((0, top["bottom"] + 1, cut, end["top"] - 1)).extract_text() or ""
    right = page.crop((cut, top["top"] - 1, page.width, end["top"] - 1)).extract_text() or ""
    return left, right


def _description(lines: list[str]) -> str | None:
    """The question's description: wrapped lines rejoined, each "a." item
    on its own line."""
    out: list[str] = []
    for line in lines:
        if out and not _ITEM_RE.match(line):
            out[-1] = out[-1] + line if out[-1].endswith("-") and not out[-1].endswith(" -") else f"{out[-1]} {line}"
        else:
            out.append(line)
    return "\n".join(clean_text(x) or "" for x in out).strip() or None


def _framing(right: str) -> tuple[str, str] | None:
    lines = [ln.strip() for ln in right.splitlines() if ln.strip()]
    if lines[:1] != ["Approve"] or "Reject" not in lines:
        return None
    cut = lines.index("Reject")
    yes, no = clean_text(" ".join(lines[1:cut])), clean_text(" ".join(lines[cut + 1:]))
    if not (yes and no and yes.startswith(_YES_START) and no.startswith(_NO_START)):
        return None
    if not (yes.endswith(".") and no.endswith(".")):
        return None
    return yes, no


def parse_handbook(page_texts: list[str], columns: dict[int, tuple[str, str]], year: int) -> list[dict] | None:
    """Every state question in the handbook. `page_texts` is extract_text()
    per page; `columns` maps a page index to that page's ballot_columns()."""
    cover = page_texts[0] if page_texts else ""
    if "General Election" not in cover or not names_date(cover, election_day(year)):
        logger.warning("RI handbook cover does not name the %d general election", year)
        return None
    lines_by_page = [[ln.strip() for ln in t.splitlines() if ln.strip()] for t in page_texts]
    contents = [m.group(1) for lines in lines_by_page for ln in lines if (m := _CONTENTS_RE.match(ln))]
    about = {
        i: m.group(1) for i, lines in enumerate(lines_by_page)
        if lines and (m := _ABOUT_RE.match(lines[0]))
    }
    numbers = list(about.values())
    if not numbers or numbers != contents or numbers != [str(n) for n in range(1, len(numbers) + 1)]:
        logger.warning("RI handbook: contents list %s, question pages %s — refusing", contents, numbers)
        return None

    full = "\n".join(page_texts)
    asked = _ASKED_RE.search(full)
    span = _RANGE_RE.search(full)
    bond_range = range(int(span.group(1)), int(span.group(2)) + 1) if span and asked else range(0)

    results = []
    for index, number in about.items():
        cols = columns.get(index)
        if cols is None:
            logger.warning("RI Question %s: no ballot box in the verified shape — refusing", number)
            return None
        left = [ln.strip() for ln in cols[0].splitlines() if ln.strip()]
        while left and left[-1] in ("Approve", "Reject"):
            left.pop()
        heading = _HEADING_RE.match(left[0]) if left else None
        framing = _framing(cols[1])
        description = _description(left[1:])
        if heading is None or heading.group(1) != number or framing is None or not description:
            logger.warning("RI Question %s: ballot box not in the verified shape — refusing", number)
            return None
        results.append({
            "number": number,
            "title": clean_text(heading.group(2)),
            "official_title": clean_text(asked.group(1)) if int(number) in bond_range else None,
            # The bond referenda are the General Assembly's acts; a
            # question outside the handbook's stated range has no origin
            # this reader can vouch for.
            "origin": ORIGIN if int(number) in bond_range else None,
            "official_summary": description,
            "fiscal_impact": None,
            "yes_means": framing[0],
            "no_means": framing[1],
            "title_authority": None,
            "fiscal_authority": None,
        })
    return results


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    url = URL_PATTERN.format(yy=f"{year % 100:02d}")
    raw = await get_bytes_unless_missing(
        client, url, "RI voter information handbook",
        f"the Rhode Island Department of State's {year} Voter Information Handbook", deadline_applies=False,
    )
    if raw is None:
        return None
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            texts = [p.extract_text() or "" for p in pdf.pages]
            columns = {
                i: cols for i, p in enumerate(pdf.pages)
                if _ABOUT_RE.match((texts[i].strip().splitlines() or [""])[0].strip()) and (cols := ballot_columns(p))
            }
        parsed = parse_handbook(texts, columns, year)
    except Exception:
        logger.exception("RI voter information handbook was not parseable")
        return None
    if parsed is None:
        return None
    return [(m, url) for m in parsed]
