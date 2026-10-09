"""Georgia's ballot-measure strategy — the Secretary of State's
"Proposed Constitutional Amendments" booklet for the general election
(one of MULTI_DOCUMENT_STRATEGIES in ballot_measures_pdf.py).

Ga. Const. art. X, §I, ¶II has each proposed amendment published with a
summary prepared by the Attorney General, the Secretary of State and the
Legislative Counsel, and the Secretary prints those summaries, with the
full resolutions, in this booklet. Verified against the real 2026 booklet
(7 pages, "PROPOSED CONSTITUTIONAL AMENDMENTS GENERAL ELECTION NOVEMBER
3, 2026 / Constitutional Amendments 1-3 and Summaries of Amendments";
Amendment 1 HR 32, bona fide conservation use acreage; 2 HR 251,
nonpartisan probate judges; 3 HR 1243, Next Generation 9-1-1 Fund).

Read from its "SUMMARIES OF PROPOSED CONSTITUTIONAL AMENDMENTS" pages,
which are set in four newspaper columns (column k spans an equal quarter
of the text block; a word straddling a column edge refuses the
document), read column by column. Per amendment, in the order printed:

    - 1 -                                      <- the amendment's number
    Increases the maximum qualifying ...      <- bold: the short caption
    House Resolution No. 32                    <- the resolution
    Ga. L. 2025, p. 1081
    "( ) YES  Shall the Constitution of ...?"  <- the ballot question, set
     ( ) NO                                       beside the answer boxes
    Summary
    This proposal increases ...                <- the summary
    A copy of this entire proposed constitutional amendment is on file ...

Stored verbatim: the short caption as title (the booklet says its short
captions "are those adopted by the Constitutional Amendments Publication
Board" — a label here, not claimed as the ballot title); the ballot
question — the words after the answer boxes, without the quotation marks
that enclose boxes and question together — as official_title (it is the
text the resolution requires on the ballot, as Idaho's reader stores its
questions), with title_authority the General Assembly, whose resolution
writes it; the summary under the booklet's "Summary" heading as
official_summary, in its own field because it has other drafters (the
Attorney General, Secretary of State and Legislative Counsel, as the
booklet's introduction names them), credited as summary_authority. The
heading itself is not stored: it used to be written in as "Summary: ",
words and a colon the summary doesn't contain.
The booklet prints no yes/no explanation and no fiscal statement; those
stay None. All amendments are General Assembly resolutions (Georgia has
no initiative).

Checks, each refusing the state (None): the cover must name the
election ("NOVEMBER 3, 2026") and "Constitutional Amendments 1-N", and
N must equal the amendments read, numbered 1..N; a cover that also names
a statewide referendum question (2024's did) is refused — this reader
does not read those; every amendment needs a caption, a resolution line,
a question ending in "?" and a summary.

Discovery: sos.ga.gov/page/proposed-georgia-constitution-amendments, the
one PDF link naming `year` and "const" (the 2026 file is
.../2026-09/2026 Constitutional Summaries booklet FINAL.pdf; 2024's was
.../2024-09/Statewide_Const_Amendments_and_Ballot_Questions_Booklet.pdf,
so no filename pattern is assumed). sos.ga.gov answers every request
with a Cloudflare challenge (403) — from the production host too,
checked 2026-10-01 — so the landing page's markup has never been seen. A
challenge page, or a page that can't be fetched, is SourceBlocked, and
ballot_measures_pdf then reads the Secretary's booklet from a county
board's republication of it (fetch_republished; Augusta-Richmond's, in
the registry's `republished_by`, the same 152,039-byte file), with every
check above applied to that copy. The fixture is that copy. The real page
with no booklet link is NotYetPublished, with no deadline (the booklet
exists only in a year with an amendment).
"""

import logging
import re
from urllib.parse import unquote, urljoin

import httpx
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_text import NotYetPublished, SourceBlocked, join_lines
from app.pipeline.fetch.ballot_measures_state_common import (
    election_day,
    get_bytes,
    get_text,
    long_date,
    republished_candidates,
)

logger = logging.getLogger(__name__)

LANDING_URL = "https://sos.ga.gov/page/proposed-georgia-constitution-amendments"
ORIGIN = "Georgia General Assembly"
# The ballot question is the General Assembly's (its resolution writes it);
# the short caption above it is the Constitutional Amendments Publication
# Board's, as the booklet says of its captions. Both named beside the
# question, since the caption is the card's label line.
TITLE_AUTHORITY = (
    "Georgia General Assembly (ballot question); short caption adopted by the "
    "Constitutional Amendments Publication Board"
)
# Ga. Const. art. X, §I, ¶II, as the booklet's introduction names them.
SUMMARY_AUTHORITY = "Georgia Attorney General, Secretary of State and Legislative Counsel (Ga. Const. art. X, §I, ¶II)"

# Georgia generals usually also carry statute-referred "Statewide
# Referendum Question" items (2018-2024 all did). 2024's were printed in
# the same booklet ("PROPOSED CONSTITUTIONAL AMENDMENTS AND STATE-WIDE
# REFERENDUM QUESTION", .../Statewide_Const_Amendments_and_Ballot_
# Questions_Booklet.pdf), but that shape could not be fetched from the
# development environment, so this reader cannot read referendum
# questions. Any sign of them — on the booklet's cover, in its link, or
# as another same-year document linked from the page — refuses the state
# (ingest_failed) rather than publishing the amendments as the whole list.
# Blind spot: a referendum question published only on county sample
# ballots, named neither on the landing page nor on the booklet's cover,
# is invisible here. 2026 was checked complete by hand (2026-09-28)
# against Augusta-Richmond County's composite sample ballot: Amendments
# 1-3 and no statewide referendum question.
REFERENDUM_WORDS = ("referend", "ballot question")

COLUMNS = 4
# The booklet's section title pages carry a dozen centred words (2026:
# 13 on the summaries title page); a page of summaries carries hundreds.
_TITLE_PAGE_MAX_WORDS = 40
_LINE_TOL = 3.0
_WORD_GAP = 1.0
_NUMBER_RE = re.compile(r"^-\s*(\d+)\s*-$")
_RESOLUTION_RE = re.compile(r"^(House|Senate) Resolution No\. \d+$")
_SESSION_LAW_RE = re.compile(r"^Ga\. L\. \d{4}, p\. \d+$")
_QUOTE_CHARS = "\"ʺ“”"
_COPY_ON_FILE = "A copy of this entire proposed"


def find_booklet_url(page_html: str, year: int) -> tuple[str | None, bool]:
    """(url, page_ok): the year's one amendments booklet. page_ok False
    when there are two, or when the page links any other `year` PDF that
    names a referendum or ballot question — see REFERENDUM_WORDS."""
    tree = lxml_html.fromstring(page_html)
    hrefs = set()
    others = set()
    for a in tree.xpath("//a[@href]"):
        href = a.get("href").strip()
        haystack = unquote(f"{href} {' '.join(a.text_content().split())}").lower().replace("_", " ").replace("-", " ")
        if ".pdf" not in href.lower() or str(year) not in haystack or "primary" in haystack:
            continue
        if "const" in haystack and not any(w in haystack for w in REFERENDUM_WORDS):
            hrefs.add(urljoin(LANDING_URL, href))
        elif any(w in haystack for w in REFERENDUM_WORDS):
            others.add(urljoin(LANDING_URL, href))
    if len(hrefs) > 1 or others:
        if others:
            logger.warning(
                "GA %d: a referendum / ballot-question document is linked (%s); this reader reads only the "
                "amendments booklet — refusing rather than publishing the amendments alone", year, sorted(others),
            )
        return None, False
    return (hrefs.pop() if hrefs else None), True


def _lines(words: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= _LINE_TOL:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def column_lines(page: dict) -> list[tuple[float, list[dict]]] | None:
    """The page's lines in reading order — column 1 top to bottom, then
    column 2, ... — each with its column's left edge. None when a word
    straddles a column edge (not the four-column layout)."""
    words = page["words"]
    if not words:
        return []
    left = min(w["x0"] for w in words)
    width = (page["width"] - 2 * left) / COLUMNS
    edges = [left + k * width for k in range(COLUMNS + 1)]
    columns: list[list[dict]] = [[] for _ in range(COLUMNS)]
    for w in words:
        k = min(int((w["x0"] - left) // width), COLUMNS - 1)
        if w["x1"] > edges[k + 1] + 1.0:
            return None
        columns[k].append(w)
    return [(edges[k], line) for k in range(COLUMNS) for line in _lines(columns[k])]


def _text(line: list[dict]) -> str:
    """A line's words, spaced as printed: pdfplumber splits a word where
    its font changes ("property" bold, "." regular), and a space is only
    put back where the page leaves a gap."""
    out = ""
    prev = None
    for w in line:
        if prev is not None and w["x0"] - prev["x1"] > _WORD_GAP:
            out += " "
        out += w["text"]
        prev = w
    return out


def _is_caption_line(line: list[dict]) -> bool:
    """Bold, ignoring punctuation set in the regular face."""
    return all(w["bold"] for w in line if any(c.isalnum() for c in w["text"]))


def parse_booklet(pages: list[dict], year: int) -> list[dict] | None:
    cover = " ".join(w["text"] for w in (pages[0]["words"] if pages else []))
    if long_date(election_day(year)).upper() not in cover.upper() or "GENERAL ELECTION" not in cover.upper():
        logger.warning("GA booklet cover does not name the %d general election", year)
        return None
    m = re.search(r"Constitutional Amendments 1-(\d+)", cover)
    if m is None or any(w in cover.lower() for w in REFERENDUM_WORDS):
        logger.warning("GA booklet cover lists no amendment range, or a referendum this reader can't read")
        return None
    expected = int(m.group(1))

    start = next(
        (i for i, p in enumerate(pages)
         if "SUMMARIES OF PROPOSED" in " ".join(w["text"] for w in p["words"])),
        None,
    )
    if start is None:
        logger.warning("GA booklet has no summaries section")
        return None
    stream: list[tuple[float, list[dict]]] = []
    for page in pages[start:]:
        if len(page["words"]) < _TITLE_PAGE_MAX_WORDS:
            continue  # a section title page ("SUMMARIES OF ... NOVEMBER 3, 2026"), set centred
        lines = column_lines(page)
        if lines is None:
            logger.warning("GA booklet summaries page is not in the four-column layout — refusing")
            return None
        stream.extend(lines)

    results: list[dict] = []
    current: dict | None = None
    state = "intro"
    question: list[str] = []
    inner = 0.0
    for col_left, line in stream:
        text = _text(line)
        num = _NUMBER_RE.match(text)
        if num and state in ("intro", "copy"):
            if current is not None:
                results.append(current)
            current = {"number": num.group(1), "caption": [], "question": None, "summary": [], "resolution": None}
            state = "caption"
            continue
        if current is None:
            continue
        if state == "caption":
            if _RESOLUTION_RE.match(text):
                current["resolution"] = text
                state = "session_law"
            elif _is_caption_line(line):
                current["caption"].append(text)
            else:
                logger.warning("GA amendment %s: unexpected line %r before its resolution", current["number"], text[:60])
                return None
        elif state == "session_law":
            if not _SESSION_LAW_RE.match(text):
                logger.warning("GA amendment %s: no session-law line", current["number"])
                return None
            state = "boxes"
        elif state == "boxes":
            if not line[0]["text"].lstrip(_QUOTE_CHARS).startswith("("):
                logger.warning("GA amendment %s: ballot question not where expected", current["number"])
                return None
            # The question's words are those right of the answer boxes;
            # the first of them fixes the question's indent in its column.
            text_words = [w for w in line if w["text"] not in ("(", ")") and not w["text"].lstrip(_QUOTE_CHARS).startswith("(")
                          and w["text"] not in ("YE", "YES", "S", "NO")]
            if not text_words:
                logger.warning("GA amendment %s: no question text beside the boxes", current["number"])
                return None
            inner = text_words[0]["x0"] - col_left
            question = [_text(text_words)]
            state = "question"
        elif state == "question":
            kept = [w for w in line if w["x0"] - col_left >= inner - 2.0]
            if kept:
                question.append(_text(kept))
            joined = join_lines(question) or ""
            if joined.rstrip(_QUOTE_CHARS).endswith("?") and joined[-1] in _QUOTE_CHARS:
                current["question"] = joined.rstrip(_QUOTE_CHARS)
                state = "after_question"
        elif state == "after_question":
            if text != "Summary":
                logger.warning("GA amendment %s: no 'Summary' after its question", current["number"])
                return None
            state = "summary"
        elif state == "summary":
            if text.startswith(_COPY_ON_FILE):
                state = "copy"
            else:
                current["summary"].append(text)
        elif state == "copy":
            continue
    if current is not None:
        results.append(current)

    measures = []
    for r in results:
        if not (r["caption"] and r["question"] and r["summary"] and r["resolution"]):
            logger.warning("GA amendment %s incomplete — refusing", r["number"])
            return None
        measures.append({
            "number": r["number"],
            "title": join_lines(r["caption"]),
            "official_title": r["question"],
            "origin": ORIGIN,
            "official_summary": join_lines(r["summary"]),
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": TITLE_AUTHORITY,
            "summary_authority": SUMMARY_AUTHORITY,
            "fiscal_authority": None,
        })
    if [m["number"] for m in measures] != [str(i) for i in range(1, expected + 1)]:
        logger.warning(
            "GA booklet: cover says amendments 1-%d, read %s — refusing", expected, [m["number"] for m in measures],
        )
        return None
    return measures


def pdf_words(raw: bytes) -> list[dict]:
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return [
            {
                "width": float(page.width),
                "words": [
                    {"text": w["text"], "x0": w["x0"], "x1": w["x1"], "top": w["top"], "bold": "Bold" in w["fontname"]}
                    for w in page.extract_words(extra_attrs=["fontname"])
                ],
            }
            for page in pdf.pages
        ]


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page_html = await get_text(client, LANDING_URL, "GA proposed constitutional amendments")
    if page_html is None:
        raise SourceBlocked("the Georgia Secretary of State's amendments page could not be fetched")
    try:
        url, page_ok = find_booklet_url(page_html, year)
    except Exception:
        logger.exception("GA amendments page was not parseable")
        return None
    if not page_ok:
        logger.warning("GA amendments page links more than one %d booklet", year)
        return None
    if url is None:
        if "Constitution" not in page_html:
            # A challenge or error page, not the Secretary's page.
            raise SourceBlocked(
                "the Georgia Secretary of State's amendments page answered with something other than that page"
            )
        # The booklet exists only in a year with an amendment on the
        # ballot, so its absence can be the answer: no deadline.
        raise NotYetPublished(
            f"the Georgia Secretary of State's {year} constitutional amendments booklet", deadline_applies=False,
        )
    raw = await get_bytes(client, url, "GA constitutional amendments booklet")
    if raw is None:
        return None
    try:
        parsed = parse_booklet(pdf_words(raw), year)
    except Exception:
        logger.exception("GA constitutional amendments booklet was not parseable")
        return None
    if parsed is None:
        return None
    return [(m, url) for m in parsed]


async def fetch_republished(client: httpx.AsyncClient, year: int, page_url: str) -> list[tuple[dict, str]] | None:
    """The Secretary's booklet as a county election office republishes it
    (Augusta-Richmond: under a news item, at a /DocumentCenter/View/
    address). Every same-year "const..." document the county page or the
    year's pages it links offers is read as a booklet with parse_booklet's
    own checks — its cover names the election and "Constitutional
    Amendments 1-N", N amendments read, none missing a part, a cover that
    names a referendum refused. Every copy that passes must read the same,
    or the state is refused rather than one picked. None when nothing
    verifies: a county page without the booklet says nothing about the
    state's ballot.

    What the Secretary's own page could add is lost while it's blocked: a
    referendum question published there as a separate document (see
    REFERENDUM_WORDS) isn't visible from a county copy of the booklet. The
    booklet's cover still names one when the Secretary prints them
    together, as 2024's did."""
    urls = await republished_candidates(client, page_url, year, ("const",))
    if not urls:
        return None
    readings: dict[str, list[dict]] = {}
    for url in urls:
        raw = await get_bytes(client, url, "GA constitutional amendments booklet (county copy)")
        if raw is None or not raw.startswith(b"%PDF"):
            continue
        try:
            parsed = parse_booklet(pdf_words(raw), year)
        except Exception:
            logger.exception("GA county copy %s was not parseable", url)
            continue
        if parsed:
            readings[url] = parsed
    if not readings:
        logger.warning("GA: no document on %s reads as the %d amendments booklet", page_url, year)
        return None
    first_url, first = next(iter(readings.items()))
    if any(parsed != first for parsed in readings.values()):
        logger.warning("GA: %d documents on %s read as different %d booklets — refusing", len(readings), page_url, year)
        return None
    return [(m, first_url) for m in first]

