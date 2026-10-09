"""Arizona's ballot-measure strategy — the Secretary of State's general-
election Publicity Pamphlet (one of MULTI_DOCUMENT_STRATEGIES in
ballot_measures_pdf.py).

The pamphlet (A.R.S. 19-123) carries, for every statewide proposition, a
"Ballot Format" page: the measure as printed on the official ballot
(A.R.S. 19-125) — its designation ("PROPOSED AMENDMENT TO THE
CONSTITUTION BY THE LEGISLATURE RELATING TO TAXATION"), OFFICIAL TITLE,
DESCRIPTIVE TITLE, and the 'A “yes” vote shall have the effect of ...' /
'A “no” vote shall have the effect of ...' statements. Verified against
the real 2026 pamphlet (164 pages, "ARIZONA 2026 GENERAL ELECTION
PUBLICITY PAMPHLET NOVEMBER 3, 2026"): Propositions 141, 142, 144, 316,
317, 318, 319 and 320.

Per proposition, verbatim from its Ballot Format page:
- the designation line(s) as title (a display label, not the ballot
  title: A.R.S. 19-125(C) prescribes the designation);
- the DESCRIPTIVE TITLE as official_summary — the summary printed on the
  ballot, which A.R.S. 19-125(D) has the Secretary of State prepare and
  the Attorney General approve, so title_authority names both;
- the yes/no statements, after 'A “yes” vote ' / 'A “no” vote ', as
  yes_means / no_means — the state's own framing, which A.R.S. 19-125(D)
  requires on the ballot.
The OFFICIAL TITLE ("AMENDING ARTICLE IX, CONSTITUTION OF ARIZONA, BY
ADDING SECTION 26.") is the measure's own title, written by its
proponents, not by the Secretary: a measure carries one drafter for its
quoted text, so it is not stored (official_title stays None).

Layout handled by word geometry, every rule checked on all eight 2026
pages: the side tab ("BALLOT FORMAT PROPOSITION 141", rotated, and its
large upright number) and the page number are dropped, as are the
ballot's "YES"/"NO" oval labels — each the last word before a
private-use-area oval glyph on its row — so they never join the text.
Only words right of the oval column are dropped this way, never by a
fixed margin: left- and right-hand pages set the body at different
offsets, and a long designation line starts left of the rest.

Checks, each refusing the state (None): the cover must name the year's
general election and say PUBLICITY PAMPHLET; the table of contents'
"Ballot Format for Proposition N" entries must equal the Ballot Format
pages found, in order; every page must yield a designation, descriptive
title and both vote statements, each once, in that order.

Discovery: azsos.gov/elections/ballot-measures, every link naming the
year and "publicity pamphlet", and the one whose cover is the English
pamphlet's (is_publicity_pamphlet) is read. azsos.gov and apps.azsos.gov
(where the pamphlet itself is filed) answer a request with a Cloudflare
managed challenge (403, `cf-mitigated: challenge`) with or without a
browser User-Agent and full browser headers, robots.txt included —
checked 2026-10-08. The same client's NEXT request, carrying the
`__cf_bm` cookie the challenge response set, was let through; the reader
does not lean on that. A challenge is the state's answer, and retrying
through it is getting past it, so these requests are made once
(retry_on_4xx=False): a challenge or an unfetchable page is
SourceBlocked, and ballot_measures_pdf then reads the
Secretary's pamphlet from its republication by another office
(fetch_republished; `republished_by` in the registry), every check above
applied to that copy. The copy is the Arizona Citizens Clean Elections
Commission's (a state commission whose Voter Education Guide page links
"SOS Publicity Pamphlet", an 8,609,721-byte InDesign PDF whose own pages
are the Secretary's: the Secretary's message, the Secretary's table of
contents). The county election sites checked (Maricopa, Yavapai,
Coconino, Pinal, Yuma, Navajo, Santa Cruz, Apache, Graham) answered 403 to
the same requests, and Pima County posts its own compilation of the
ballot language ("Pima County Proposition Text"), not the Secretary's
document. A Spanish edition beside it ("FOLLETO PUBLICITARIO") has a
Spanish cover and is passed over; a document that looks like the
pamphlet (names the year and "Publicity Pamphlet", or has no text) but
lacks its verified cover, or two documents with that cover, refuse the
state.
"""

import io
import logging
import re
from urllib.parse import unquote, urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import lines_from_words, rows
from app.pipeline.fetch.ballot_measure_text import NotYetPublished, SourceBlocked, join_lines
from app.pipeline.fetch.ballot_measures_state_common import (
    election_day,
    get_bytes,
    get_text,
    long_date,
    republished_candidates,
)

logger = logging.getLogger(__name__)

BALLOT_MEASURES_URL = "https://azsos.gov/elections/ballot-measures"
TITLE_AUTHORITY = "Arizona Secretary of State, approved by the Arizona Attorney General"

_LINK_WORDS = ("publicity pamphlet",)
_CONTENTS_RE = re.compile(r"Ballot Format for Proposition (\d+)\s*\.{2,}")
_HEADING_RE = re.compile(r"^PROPOSITION (\d+)$")
_YES_START = "A “yes” vote "
_NO_START = "A “no” vote "
_EFFECT = "shall have the effect of"


def _is_oval(word: dict) -> bool:
    """The ballot's mark oval, set in a private-use-area glyph."""
    return bool(word["text"]) and all("" <= ch <= "" for ch in word["text"])


def page_words(raw: bytes) -> list[list[dict]]:
    """Each page's words with what the reader needs: text, x0, top, and
    whether the word is upright (the side tab's lettering is rotated)."""
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return [
            [
                {"text": w["text"], "x0": round(w["x0"], 2), "top": round(w["top"], 2), "upright": bool(w["upright"])}
                for w in page.extract_words(extra_attrs=["upright"])
            ]
            for page in pdf.pages
        ]


def _page_lines(words: list[dict]) -> list[str]:
    return lines_from_words([w for w in words if w["upright"] and not _is_oval(w)])


def is_publicity_pamphlet(pages: list[list[dict]], year: int) -> bool:
    """The English pamphlet's cover (2026: "What’s on my ballot? / ARIZONA
    2026 / GENERAL ELECTION / PUBLICITY PAMPHLET / NOVEMBER 3, 2026")."""
    cover = " ".join(_page_lines(pages[0])) if pages else ""
    return (
        f"ARIZONA {year}" in cover
        and "GENERAL ELECTION" in cover
        and "PUBLICITY PAMPHLET" in cover
        and long_date(election_day(year)).upper() in cover
    )


def looks_like_pamphlet(pages: list[list[dict]], year: int) -> bool:
    """A loose reading: no text at all, or a first page naming the year and
    "Publicity Pamphlet". A candidate like this that fails
    is_publicity_pamphlet refuses the state rather than being passed over;
    the Spanish edition ("FOLLETO PUBLICITARIO") names neither."""
    if not any(w["text"].strip() for page in pages for w in page):
        return True
    first = " ".join(_page_lines(pages[0])).lower() if pages else ""
    return str(year) in first and "publicity pamphlet" in first


def _ballot_format(words: list[dict]) -> dict | None:
    """One Ballot Format page, or None when the page isn't one in the
    verified shape (logged)."""
    upright = [w for w in words if w["upright"]]
    bands = rows(upright)
    order = sorted(bands)
    texts = {rid: " ".join(w["text"] for w in sorted(bands[rid], key=lambda w: w["x0"]) if not _is_oval(w))
             for rid in order}
    heading = next(((rid, m.group(1)) for rid in order if (m := _HEADING_RE.match(texts[rid]))), None)
    footer = next((rid for rid in order if "GENERAL ELECTION GUIDE" in texts[rid]), None)
    if heading is None or footer is None:
        logger.warning("AZ Ballot Format page: no PROPOSITION heading or page footer")
        return None
    head_rid, number = heading
    # The oval column: the YES label is the word before the oval glyph on
    # the 'A “yes” vote' row.
    oval_x = None
    for rid in order:
        row = sorted(bands[rid], key=lambda w: w["x0"])
        if texts[rid].startswith(_YES_START) and len(row) >= 2 and _is_oval(row[-1]) and row[-2]["text"] == "YES":
            oval_x = row[-2]["x0"] - 1
    if oval_x is None:
        logger.warning("AZ Proposition %s: no YES oval", number)
        return None
    body = [
        w for rid in order if head_rid < rid < footer for w in bands[rid]
        if not _is_oval(w)
        and not (w["x0"] >= oval_x and (w["text"] in ("YES", "NO") or w["text"].isdigit()))
    ]
    lines = lines_from_words(body)
    # Every line with only digits is a page or tab number that sat inside
    # the body's span.
    lines = [ln for ln in lines if not ln.replace(" ", "").isdigit()]
    marks = {
        "official": [i for i, ln in enumerate(lines) if ln == "OFFICIAL TITLE"],
        "descriptive": [i for i, ln in enumerate(lines) if ln == "DESCRIPTIVE TITLE"],
        "yes": [i for i, ln in enumerate(lines) if ln.startswith(_YES_START + _EFFECT)],
        "no": [i for i, ln in enumerate(lines) if ln.startswith(_NO_START + _EFFECT)],
    }
    if any(len(v) != 1 for v in marks.values()):
        logger.warning("AZ Proposition %s: sections %s", number, {k: len(v) for k, v in marks.items()})
        return None
    off, desc, yes, no = (marks[k][0] for k in ("official", "descriptive", "yes", "no"))
    # Each section non-empty: designation, official title, descriptive title.
    if not (0 < off and off + 1 < desc and desc + 1 < yes < no):
        logger.warning("AZ Proposition %s: sections out of order", number)
        return None
    yes_text = join_lines(lines[yes:no])
    no_text = join_lines(lines[no:])
    if not (yes_text.endswith(".") and no_text.endswith(".")):
        logger.warning("AZ Proposition %s: a vote statement doesn't end its sentence", number)
        return None
    return {
        "number": number,
        "title": join_lines(lines[:off]),
        "official_title": None,
        "origin": None,
        "official_summary": join_lines(lines[desc + 1:yes]),
        "fiscal_impact": None,
        # The whole statement, "A “yes” vote shall have the effect of ...":
        # without its opening words it is a fragment.
        "yes_means": yes_text,
        "no_means": no_text,
        "title_authority": TITLE_AUTHORITY,
        "fiscal_authority": None,
    }


def parse_pamphlet(pages: list[list[dict]], year: int) -> list[dict] | None:
    """Every proposition's Ballot Format page, or None when this isn't
    `year`'s pamphlet or it can't be read whole."""
    if not is_publicity_pamphlet(pages, year):
        logger.warning("AZ pamphlet cover does not name the %d general election", year)
        return None
    contents: list[str] = []
    format_pages = []
    for words in pages:
        lines = _page_lines(words)
        for ln in lines:
            for n in _CONTENTS_RE.findall(ln):
                if n not in contents:
                    contents.append(n)
        if "BALLOT FORMAT" in lines and "OFFICIAL TITLE" in lines and "DESCRIPTIVE TITLE" in lines:
            format_pages.append(words)
    results = []
    for words in format_pages:
        parsed = _ballot_format(words)
        if parsed is None:
            return None
        results.append(parsed)
    numbers = [m["number"] for m in results]
    if not numbers or numbers != contents:
        logger.warning("AZ pamphlet: contents list %s, Ballot Format pages %s — refusing", contents, numbers)
        return None
    return results


async def _choose_pamphlet(
    client: httpx.AsyncClient, urls: list[str], year: int,
) -> tuple[str, list[list[dict]]] | None:
    """The one candidate whose cover is the English pamphlet's. None (a
    failure) when a candidate can't be fetched or read, looks like the
    pamphlet without its verified cover, or two carry it. NotYetPublished
    when none does."""
    found = []
    for url in urls:
        raw = await get_bytes(client, url, "AZ publicity pamphlet candidate", retry_on_4xx=False)
        if raw is None:
            return None
        try:
            pages = page_words(raw)
        except Exception:
            logger.exception("AZ publicity pamphlet candidate %s was not parseable", url)
            return None
        if is_publicity_pamphlet(pages, year):
            found.append((url, pages))
        elif looks_like_pamphlet(pages, year):
            logger.warning("AZ %s looks like the %d pamphlet but its cover isn't the verified one — refusing", url, year)
            return None
    if not found:
        # A general with no statewide proposition may have no pamphlet.
        raise NotYetPublished(f"the Arizona Secretary of State's {year} Publicity Pamphlet", deadline_applies=False)
    if len(found) > 1:
        logger.warning("AZ: %d documents carry the %d pamphlet's cover — refusing", len(found), year)
        return None
    return found[0]


def _read(url: str, pages: list[list[dict]], year: int) -> list[tuple[dict, str]] | None:
    try:
        parsed = parse_pamphlet(pages, year)
    except Exception:
        logger.exception("AZ publicity pamphlet was not parseable")
        return None
    return [(m, url) for m in parsed] if parsed is not None else None


def pamphlet_urls(page_html: str, year: int) -> list[str] | None:
    """Every link on the Secretary's ballot-measures page naming `year` and
    "publicity pamphlet", or None when this isn't that page (a challenge
    has no such title)."""
    tree = lxml_html.fromstring(page_html)
    title = " ".join(" ".join(t.text_content().split()) for t in tree.xpath("//title"))
    if "Ballot Measures" not in title:
        return None
    urls: list[str] = []
    for a in tree.xpath("//a[@href]"):
        href = a.get("href").strip()
        haystack = unquote(f"{href} {' '.join(a.text_content().split())}").lower().replace("-", " ").replace("_", " ")
        if str(year) in haystack and all(w in haystack for w in _LINK_WORDS):
            url = urljoin(BALLOT_MEASURES_URL, href)
            if url not in urls:
                urls.append(url)
    return urls


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page_html = await get_text(client, BALLOT_MEASURES_URL, "AZ ballot measures page", retry_on_4xx=False)
    if page_html is None:
        raise SourceBlocked("the Arizona Secretary of State's ballot measures page could not be fetched")
    try:
        urls = pamphlet_urls(page_html, year)
    except Exception:
        logger.exception("AZ ballot measures page was not parseable")
        return None
    if urls is None:
        raise SourceBlocked(
            "the Arizona Secretary of State's ballot measures page answered with something other than that "
            "page (a bot challenge)"
        )
    chosen = await _choose_pamphlet(client, urls, year)
    return _read(*chosen, year) if chosen else None


async def fetch_republished(client: httpx.AsyncClient, year: int, page_url: str) -> list[tuple[dict, str]] | None:
    """The Secretary's pamphlet as another office republishes it, with
    every check the Secretary's own copy gets. None when the page can't be
    read or no copy verifies — an office that hasn't posted it is no word
    on the state's ballot."""
    urls = await republished_candidates(client, page_url, year, _LINK_WORDS)
    if not urls:
        return None
    try:
        chosen = await _choose_pamphlet(client, urls, year)
    except NotYetPublished:
        logger.warning("AZ: no document on %s carries the %d pamphlet's cover", page_url, year)
        return None
    return _read(*chosen, year) if chosen else None
