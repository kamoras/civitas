"""New Mexico's ballot-measure strategy — the Secretary of State's
"<year> General Election Statewide Constitutional Amendments and General
Obligation Bonds" (one of MULTI_DOCUMENT_STRATEGIES in
ballot_measures_pdf.py: it finds its own document).

Before each general election the Secretary publishes the text of every
statewide question on the ballot — the constitutional amendments and the
general obligation bond questions — in one document, English then
Spanish. Its wording is the printed ballot's: Santa Fe County's 2026
sample ballots carry the same sentences under "CONSTITUTIONAL AMENDMENT
N" and "BOND QUESTION N" headings (checked 2026-10-08). Verified on the
2026 publication (4 pages, posted September 2026): Constitutional
Amendments 1-4 and Bond Questions 1-3.

This replaced two other documents that were read until 2026-10: the
Legislative Council Service's amendments publication, whose "ballot text"
is the joint resolutions' titles in capitals ("PROPOSING AN AMENDMENT TO
ARTICLE 4 ...") rather than the ballot's sentence case, and the GO bond
act's own ballot language, configured per year by bill number and
labelled "HB 248 (1)" where the ballot says "Bond Question 1". The
reader's old premise that the Secretary publishes no list before the
election is not true.

Stored verbatim:
- an amendment: number N, title "Constitutional Amendment N", its text as
  official_title (the joint resolution's title, drafted by the
  Legislature);
- a bond question: number "Bond Question N", its first sentence as title,
  the whole question as official_summary (the ballot language the bond
  act writes, the Legislature's).
Neither part publishes YES/NO framing or a fiscal statement.

Checks, each refusing the state (None): the document names `year`'s
general election; amendments and bond questions are each numbered 1..N
in order, every item has text and every bond question ends with "?"; and
the Spanish half carries the same numbers of each ("Enmienda
Constitucional N:", "Pregunta de Bonos N") — a half the English reading
missed shows up as a count that disagrees.

Discovery: the Secretary's home page links the publication as "<year>
Constitutional Amendments and Statewide Bond Questions" (the file name is
a document-management id, so no pattern is assumed). No such link is
NotYetPublished; a page that can't be fetched, or two different links,
is None.
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measure_text import NotYetPublished
from app.pipeline.fetch.ballot_measures_state_common import get_bytes, get_text

logger = logging.getLogger(__name__)

HOME_URL = "https://www.sos.nm.gov/"
AMENDMENT_AUTHORITY = "New Mexico Legislature"
BOND_AUTHORITY = "New Mexico Legislature (ballot language written into the bond act)"
ORIGIN = "New Mexico Legislature"

_AMENDMENT_RE = re.compile(r"^Constitutional Amendment (\d+):\s*$")
_BOND_RE = re.compile(r"^Bond Question (\d+)\s*$")
_ITEM_MENTION_RE = re.compile(r"^(?:Constitutional Amendment|Bond Question)\b", re.IGNORECASE)
_SPANISH_START = "Elección General"
_SPANISH_AMENDMENT_RE = re.compile(r"^Enmienda Constitucional (\d+):\s*$", re.MULTILINE)
_SPANISH_BOND_RE = re.compile(r"^Pregunta de Bonos (\d+)\s*$", re.MULTILINE)


def find_publication_url(home_html: str, year: int) -> tuple[str | None, bool]:
    """(url, page_ok): the one publication the home page links as
    "<year> Constitutional Amendments and Statewide Bond Questions".
    page_ok False: two different links."""
    tree = lxml_html.fromstring(home_html)
    hrefs = {
        urljoin(HOME_URL, a.get("href").strip())
        for a in tree.xpath("//a[@href]")
        if re.search(rf"\b{year} Constitutional Amendments\b.*\bBond Questions\b", " ".join(a.text_content().split()))
        and a.get("href").lower().split("?")[0].endswith(".pdf")
    }
    if len(hrefs) > 1:
        return None, False
    return (hrefs.pop() if hrefs else None), True


def _join(lines: list[str]) -> str:
    """Lines joined with spaces, except that a line ending in a hyphen
    continues its word on the next line ("twenty-" / "five")."""
    out = ""
    for ln in lines:
        out = out + ln if out.endswith("-") else (f"{out} {ln}" if out else ln)
    return out


def _numbered(found: list[str]) -> bool:
    return found == [str(n) for n in range(1, len(found) + 1)]


def parse_publication(pages_text: list[str], year: int) -> list[dict] | None:
    """Every statewide question in the publication (extract_text() per
    page), amendments then bonds, or None when it can't be read whole."""
    text = "\n".join(pages_text)
    if f"{year} General Election" not in text:
        logger.warning("NM publication does not name the %d general election", year)
        return None
    split = text.find(_SPANISH_START)
    english, spanish = (text[:split], text[split:]) if split >= 0 else (text, "")

    items: list[tuple[str, str, list[str]]] = []  # (kind, number, lines)
    for raw in english.splitlines():
        line = raw.strip()
        if not line:
            continue
        amendment, bond = _AMENDMENT_RE.match(line), _BOND_RE.match(line)
        if amendment or bond:
            items.append(("amendment" if amendment else "bond", (amendment or bond).group(1), []))
        elif _ITEM_MENTION_RE.match(line):
            logger.warning("NM publication: item line %r not in the verified form — refusing", line)
            return None
        elif items:
            items[-1][2].append(line)

    amendments = [n for kind, n, _ in items if kind == "amendment"]
    bonds = [n for kind, n, _ in items if kind == "bond"]
    if not amendments and not bonds:
        logger.warning("NM publication: no amendment or bond question read")
        return None
    if not (_numbered(amendments) and _numbered(bonds)):
        logger.warning("NM publication: amendments %s, bonds %s are not numbered 1..N", amendments, bonds)
        return None
    if (
        len(_SPANISH_AMENDMENT_RE.findall(spanish)) != len(amendments)
        or len(_SPANISH_BOND_RE.findall(spanish)) != len(bonds)
    ):
        logger.warning("NM publication: the Spanish half's counts differ from the English half's — refusing")
        return None

    results = []
    for kind, number, lines in items:
        body = clean_text(_join(lines))
        if not body:
            logger.warning("NM %s %s has no text — refusing", kind, number)
            return None
        if kind == "amendment":
            results.append({
                "number": number,
                "title": f"Constitutional Amendment {number}",
                "official_title": body,
                "origin": ORIGIN,
                "official_summary": None,
                "fiscal_impact": None,
                "yes_means": None,
                "no_means": None,
                "title_authority": AMENDMENT_AUTHORITY,
                "fiscal_authority": None,
            })
        else:
            if not body.endswith("?") or ". Shall " not in body:
                logger.warning("NM Bond Question %s is not one question — refusing", number)
                return None
            results.append({
                "number": f"Bond Question {number}",
                "title": body.split(". Shall ", 1)[0] + ".",
                "origin": ORIGIN,
                "official_summary": body,
                "fiscal_impact": None,
                "yes_means": None,
                "no_means": None,
                "title_authority": BOND_AUTHORITY,
                "fiscal_authority": None,
            })
    return results


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    home = await get_text(client, HOME_URL, "NM Secretary of State home page")
    if home is None:
        return None
    try:
        url, page_ok = find_publication_url(home, year)
    except Exception:
        logger.exception("NM home page was not parseable")
        return None
    if not page_ok:
        logger.warning("NM home page links more than one %d amendments publication", year)
        return None
    if url is None:
        raise NotYetPublished(f"the New Mexico Secretary of State's {year} constitutional amendments and bond questions")
    raw = await get_bytes(client, url, "NM amendments and bond questions")
    if raw is None or raw[:5] != b"%PDF-":
        return None
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            parsed = parse_publication([p.extract_text() or "" for p in pdf.pages], year)
    except Exception:
        logger.exception("NM amendments and bond questions publication was not parseable")
        return None
    if parsed is None:
        return None
    return [(m, url) for m in parsed]
