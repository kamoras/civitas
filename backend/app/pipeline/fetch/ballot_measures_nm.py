"""New Mexico's ballot-measure strategy — two official documents,
because New Mexico's general-election ballot carries two kinds of
statewide question and no single state publication lists both (one of
potentially many per-state strategies; see ballot_measures_pdf.py,
MULTI_DOCUMENT_STRATEGIES).

1. Constitutional amendments: the Legislative Council Service's
   "Summary of and Arguments For and Against the Constitutional
   Amendments" (nmlegis.gov, evergreen {year} URL verified on two real
   generals, 2024 and 2026 — same layout both years). Its "Proposed
   Amendments to Appear on the <date> General Election Ballot (ballot
   text)" page lists each "Constitutional Amendment N:" with its ballot
   text in quotes (the document states that this title "is the only
   language that will appear on the ballot"); each amendment's
   "▸ SUMMARY of Proposed Constitutional Amendment N" section (up to
   "▸ BACKGROUND AND INFORMATION") is the LCS's own summary. The ballot
   text is stored verbatim as the official ballot title, drafted by the
   Legislature. The LCS summary is a DIFFERENT drafter's text and a
   measure carries one drafter for its quoted text, so it is not stored
   (joining the two under one attribution credited the LCS's words to
   the Legislature or the reverse); it is still read, to check that the
   same amendments appear in both places. The arguments for/against are
   staff-suggested arguments by the document's own disclaimer and are
   not used.

2. General obligation bond questions: New Mexico's GO bond acts write
   the ballot question into the statute itself ("The ballots used at
   the 2026 general election shall contain substantially the following
   language: (1) "The 2026 Capital Projects General Obligation Bond Act
   authorizes ... Shall the state be authorized ...?" ..."). The
   Secretary of State publishes no list of them before the election (its
   2026 general-election proclamation names offices only), so the source
   is the enacted act's final version on nmlegis.gov. Which bill carries
   the act changes every session (HB 248 in 2026), and nothing on the
   legislature's site names it by a stable path, so it is configured per
   year in ballot_measure_pdf_sources.json ("bond_acts"). A year with no
   configured act returns None (ingest_failed), never an amendments-only
   list: New Mexico has put GO bond questions on every even-year general
   ballot, and a list missing them would read as the whole ballot.
   Verified 2026-09-28 on HB 248's final version (signed March 10, 2026):
   three questions (senior citizen facilities, libraries, higher
   education). The amounts differ from the bill as introduced, which is
   exactly why the final version — not the introduced text — is read.

Verified live 2026-09-28: 4 amendments + 3 bond questions = 7, matching
Ballotpedia's count (whose bond amounts are still the introduced bill's).
Neither document publishes "A YES vote means / A NO vote means"
framing, so yes_means/no_means stay null; no fiscal statement either.
"""

import io
import logging
import re

import httpx
import pdfplumber

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measure_pdf_sources import source_for_state
from app.pipeline.fetch.http_utils import fetch_bytes_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

AMENDMENTS_URL_PATTERN = (
    "https://www.nmlegis.gov/Publications/New_Mexico_State_Government/"
    "Constitutional_Amendment/Constitutional_Amendments_{year}.pdf"
)
AMENDMENT_AUTHORITY = "New Mexico Legislature"
BOND_AUTHORITY = "New Mexico Legislature (ballot language written into the bond act)"

_rate_limiter = RateLimiter(rps=1.0)

_FOOTER_RES = (
    re.compile(r"^CONSTITUTIONAL AMENDMENTS PROPOSED IN .*\d+$"),
    re.compile(r"^\d+ SUMMARY OF AND ARGUMENTS FOR AND AGAINST$"),
)
_AMENDMENT_ITEM_RE = re.compile(r"^Constitutional Amendment (\d+):\s*$")
_SUMMARY_HEADING_RE = re.compile(r"^▸\s*SUMMARY of Proposed Constitutional Amendment (\d+)\s*$")
_SECTION_HEADING_RE = re.compile(r"^▸\s*")


def _join(lines: list[str]) -> str:
    """Lines joined with spaces, except that a line ending in a hyphen
    continues its word on the next line ("twenty-" / "five")."""
    out = ""
    for ln in lines:
        out = out + ln if out.endswith("-") else (f"{out} {ln}" if out else ln)
    return out


def _lines(pages_text: list[str]) -> list[str]:
    out = []
    for text in pages_text:
        for ln in text.splitlines():
            ln = ln.strip()
            if ln and not any(r.match(ln) for r in _FOOTER_RES):
                out.append(ln)
    return out


_AMENDMENT_MENTION_RE = re.compile(r"^Constitutional Amendment\b", re.IGNORECASE)
_SUMMARY_MENTION_RE = re.compile(r"^▸\s*SUMMARY\b", re.IGNORECASE)
_QUOTED_RE = re.compile(r'^["“].*["”]$', re.DOTALL)


def parse_amendments(pages_text: list[str]) -> list[dict]:
    """Every amendment's ballot text. Raises — so the caller fails the
    state rather than publishing a partial or mixed-up list — when:
    - a line in the ballot-text section opens "Constitutional Amendment"
      but isn't the exact "Constitutional Amendment N:" item line (it
      would otherwise be folded into the previous amendment's text);
    - an item's text isn't one quoted passage (two amendments run
      together read as one quote that doesn't close where it should);
    - the amendments are not numbered 1..N;
    - the set of amendments with ballot text differs from the set with a
      "▸ SUMMARY" section, in either direction.

    Only the ballot text is stored, as official_title: the document says
    it "is the only language that will appear on the ballot", and the
    Legislature wrote it (the joint resolution's title). The Legislative
    Council Service's summary is a different drafter's text; it is read
    here only to cross-check the list, never stored under the
    Legislature's name (see module docstring).
    """
    lines = _lines(pages_text)
    start = next((i for i, ln in enumerate(lines) if ln == "(ballot text)"), None)
    if start is None:
        raise ValueError("NM amendments: no '(ballot text)' page")
    end = next((i for i in range(start, len(lines)) if lines[i] == "General Information"), len(lines))

    ballot_text: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for ln in lines[start + 1:end] + ["Constitutional Amendment 0:"]:
        m = _AMENDMENT_ITEM_RE.match(ln)
        if m:
            if current is not None:
                if current in ballot_text:
                    raise ValueError(f"NM Constitutional Amendment {current}: listed twice")
                text = clean_text(_join(buf)) or ""
                if not _QUOTED_RE.match(text):
                    raise ValueError(f"NM Constitutional Amendment {current}: ballot text is not one quoted passage")
                ballot_text[current] = text.strip('"“”')
            current, buf = m.group(1), []
            continue
        if _AMENDMENT_MENTION_RE.match(ln):
            raise ValueError(f"NM amendments: unrecognised item line {ln!r}")
        if current is None:
            raise ValueError(f"NM amendments: text before the first item: {ln!r}")
        buf.append(ln)

    summaries: dict[str, str] = {}
    for i, ln in enumerate(lines):
        m = _SUMMARY_HEADING_RE.match(ln)
        if not m:
            if _SUMMARY_MENTION_RE.match(ln):
                raise ValueError(f"NM amendments: unrecognised summary heading {ln!r}")
            continue
        j = next((k for k in range(i + 1, len(lines)) if _SECTION_HEADING_RE.match(lines[k])), len(lines))
        summaries[m.group(1)] = clean_text(_join(lines[i + 1:j])) or ""

    numbers = sorted(ballot_text, key=int)
    if numbers != [str(n) for n in range(1, len(numbers) + 1)]:
        raise ValueError(f"NM amendments: ballot-text items numbered {numbers}")
    if set(ballot_text) != set(summaries):
        raise ValueError(
            f"NM amendments: ballot text for {sorted(ballot_text)} but summaries for {sorted(summaries)}",
        )

    results = []
    for number in numbers:
        text, summary = ballot_text[number], summaries[number]
        if not text or not summary:
            raise ValueError(f"NM Constitutional Amendment {number}: missing ballot text or summary")
        results.append({
            "number": number,
            "title": f"Constitutional Amendment {number}",
            "official_title": text,
            "origin": "New Mexico Legislature",
            "official_summary": None,
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": AMENDMENT_AUTHORITY,
            "fiscal_authority": None,
        })
    return results


_BILL_LINE_NUMBER_RE = re.compile(r"^\d{1,2}\s+")
_BILL_RUNNING_TAG_RE = re.compile(r"\s+\S+/[HS]B \d+/\S+$")
_BILL_PAGE_RE = re.compile(r"^Page \d+$")
_BOND_QUESTION_RE = re.compile(
    r"\((\d+)\)\s*\"(The \d{4} Capital Projects General Obligation Bond Act authorizes .+?\?)\s*For_+\s*Against_+\"",
    re.DOTALL,
)


def parse_bond_act(pages_text: list[str], bill_label: str) -> list[dict]:
    """Every ballot question the GO bond act writes into its ELECTION
    section. The legislature's bill PDFs carry a line number at the
    start of every line and a running committee tag ("HTRC/HB 248/ec")
    at the end of each page's last line; both are layout, not text."""
    cleaned = []
    for text in pages_text:
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln or _BILL_PAGE_RE.match(ln):
                continue
            ln = _BILL_RUNNING_TAG_RE.sub("", _BILL_LINE_NUMBER_RE.sub("", ln))
            cleaned.append(ln)
    joined = _join(cleaned)
    marker = joined.find("shall contain substantially the following language")
    if marker < 0:
        raise ValueError(f"NM {bill_label}: no ballot-language section")
    section = joined[marker:]
    # Every "(n) "..." item the section opens, numbered 1, 2, 3 ... — the
    # list the questions read below must match exactly. findall alone only
    # noticed ZERO matches; an item whose wording the question pattern
    # doesn't fit would have dropped out of an otherwise-published list.
    listed: list[str] = []
    for m in re.finditer(r"\((\d+)\)\s*\"", section):
        if m.group(1) != str(len(listed) + 1):
            break
        listed.append(m.group(1))
    found = _BOND_QUESTION_RE.findall(section)
    if [n for n, _ in found] != listed:
        raise ValueError(f"NM {bill_label}: ballot-language items {listed} but questions read for {[n for n, _ in found]}")
    results = []
    for n, question in found:
        question = clean_text(question)
        title = question.split(". Shall ", 1)[0] + "."
        results.append({
            "number": f"{bill_label} ({n})",
            "title": title,
            "origin": "New Mexico Legislature",
            "official_summary": question,
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": BOND_AUTHORITY,
            "fiscal_authority": None,
        })
    if not results:
        raise ValueError(f"NM {bill_label}: ballot-language section has no questions")
    return results


async def _pdf_pages_text(client: httpx.AsyncClient, url: str, label: str) -> list[str] | None:
    body = await fetch_bytes_with_retry(client, _rate_limiter, url, label)
    if body is None or body[:5] != b"%PDF-":
        return None
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    source = source_for_state("NM") or {}
    bond_act = (source.get("bond_acts") or {}).get(str(year))
    if not bond_act:
        logger.warning("NM %d: no GO bond act configured — refusing an amendments-only list", year)
        return None

    amendments_url = AMENDMENTS_URL_PATTERN.format(year=year)
    try:
        amend_pages = await _pdf_pages_text(client, amendments_url, f"NM amendments {year}")
        bond_pages = await _pdf_pages_text(client, bond_act["url"], f"NM bond act {year}")
        if amend_pages is None or bond_pages is None:
            return None
        amendments = parse_amendments(amend_pages)
        bonds = parse_bond_act(bond_pages, bond_act["bill"])
    except Exception:
        logger.exception("NM %d ballot measures failed to parse", year)
        return None
    return [(a, amendments_url) for a in amendments] + [(b, bond_act["url"]) for b in bonds]
