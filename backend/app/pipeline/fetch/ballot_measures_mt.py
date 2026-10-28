"""Montana's ballot-measure strategy — the Secretary of State's
"Proposed <year> Ballot Issues" page plus each qualified issue's own
ballot-language PDF (one of potentially many per-state strategies; see
ballot_measures_pdf.py, MULTI_DOCUMENT_STRATEGIES, for why several
documents per state need their own fetch).

The page lives at an evergreen, {year}-substituted URL, verified live
for three real cycles (2022, 2024, 2026 — all linked from the SOS
ballot-issues index). Its "Issues Qualified for the <year> General
Election Ballot" section is the certified list: it carried both
legislative referrals (2022: C-48, LR-131) and citizen initiatives
(2024: CI-126/127/128; 2026: CI-132, CI-133, I-194), each as a
paragraph whose first link is the issue's identifier. The section ends
at the next "Issues ..." / "Submitted Ballot Issues" heading (issues
still gathering signatures, submitted, or not qualified are never
read). Verified live 2026-09-28: three issues qualified for November 3,
2026, matching the page's own statuses ("Qualified for 2026 General
Election ballot (8/17/2026)").

The page's "Subject" text is explicitly NOT the ballot text — its own
footnote says "The subject of a ballot issue may be paraphrased on this
page" and points to the linked statement instead. So every field here
comes from the linked "BALLOT LANGUAGE FOR ..." PDF (reached through the
site's file-preview wrapper page, whose <iframe> names the PDF): its
title line ("CONSTITUTIONAL INITIATIVE NO. 132"), its origin line ("A
CONSTITUTIONAL AMENDMENT PROPOSED BY INITIATIVE PETITION"), and the
statement between that line and the "[] YES on ..." box. The YES/NO
boxes are option labels ("YES on Constitutional Amendment CI-132"), not
a description of what each vote does, so yes_means/no_means stay null;
none of the three 2026 statements carries a fiscal statement. When one
does, its drafter is named only if the statement itself names one — the
Secretary of State files these documents but does not write them.

The "CONSTITUTIONAL INITIATIVE NO. 132" line is a label, not a ballot
title, so no official_title is claimed; the statement is the quoted
text, attributed to the Attorney General who approves it.

Any qualified entry whose header has no recognisable issue-id link, or
whose statement can't be fetched or read, makes the whole state return
None (ingest_failed): a partial list would read as the complete ballot.

From 2026-10-27 sosmt.gov answers Civitas with Cloudflare's firewall page
(403, "Firewall Event Triggered", no cf-mitigated header; a browser's
User-Agent gets cf-mitigated: challenge). That is a wall (AGENTS.md §7),
read as one from Cloudflare's script in the page (is_bot_challenge), so
the reads raise SourceBlocked (get_text_unless_walled /
get_bytes_unless_walled), and ballot_measures_pdf then reads the Internet
Archive's captures instead (fetch_archived, web_archive.py): the newest
capture of this page, each preview wrapper it links and each statement
PDF, the PDF matched to its capture's digest, with every check above.
Measured 2026-10-28: the 2026-10-19 capture of the page and captures of
all three statements give CI-132, CI-133 and I-194 word for word as the
state's own read of 2026-09-28.
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch import web_archive
from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.ballot_measures_state_common import get_bytes_unless_walled, get_text_unless_walled

logger = logging.getLogger(__name__)

URL_PATTERN = "https://sosmt.gov/elections/ballot_issues/proposed-{year}-ballot-issues/"
TITLE_AUTHORITY = "Montana Attorney General (approved ballot statement), as filed with the Montana Secretary of State"
# An archived copy is read only of an address on the state's own host.
_STATE_HOST = "https://sosmt.gov/"

_ISSUE_ID_RE = re.compile(r"^(?:CI|I|LR|C|IR|R)-\d+$")
_SECTION_END_RE = re.compile(r"^(?:Issues\b.*\bBallot|Submitted Ballot Issues\b)", re.IGNORECASE)
_PROPOSED_BY_RE = re.compile(r"\bPROPOSED BY (?:THE )?(.+)$")
# An entry's own header: "CI-132 (Ballot Issue #5)", "CI-128 Ballot Issue
# #14" (2024, no parentheses), "C-48 ( SB 203 )" (2022 referral). Matched
# at the start of a paragraph only — the "Subject"/"Status" lines are
# separate paragraphs on some years' pages.
_ENTRY_MARKER_RE = re.compile(r"^.{0,40}?(?:Ballot Issue #\s*\d+|\(\s*[HS]B\s*\d+\s*\))", re.IGNORECASE)
_YES_BOX_RE = re.compile(r"^\[\s*\]\s*YES\b")
_FISCAL_RE = re.compile(r"^FISCAL (?:STATEMENT|NOTE)\b", re.IGNORECASE)
_PREPARED_BY_RE = re.compile(r"\bprepared by (?:the )?([A-Z][^.,;]*)", re.IGNORECASE)


def qualified_issues(page_html: str, year: int) -> list[tuple[str, str]] | None:
    """[(issue id, link href), ...] under the "Issues Qualified for the
    <year> General Election Ballot" heading, in page order. None when the
    heading itself is absent (the page's shape changed — not a "none")."""
    tree = lxml_html.fromstring(page_html)
    heading_re = re.compile(rf"^Issues Qualified for the {year} General Election Ballot$", re.IGNORECASE)
    in_section = False
    found_heading = False
    issues: list[tuple[str, str]] = []
    for el in tree.xpath("//p | //h1 | //h2 | //h3 | //h4 | //h5 | //h6"):
        text = clean_text(el.text_content()) or ""
        if not in_section:
            if heading_re.match(text):
                in_section = found_heading = True
            continue
        if _SECTION_END_RE.match(text):
            break
        before = len(issues)
        for a in el.xpath(".//a[@href]"):
            label = clean_text(a.text_content()) or ""
            if _ISSUE_ID_RE.match(label):
                issues.append((label, a.get("href")))
        if len(issues) == before and _ENTRY_MARKER_RE.search(text):
            # An entry header with no linked issue id we recognise
            # — reading the rest would publish a list missing it.
            logger.warning("MT %d: unrecognised qualified-issue entry %r", year, text[:80])
            return None
    return issues if found_heading else None


def parse_ballot_language(text: str, issue_id: str) -> dict | None:
    """One issue's statement from its "BALLOT LANGUAGE FOR ..." page
    text, or None if the page isn't that shape."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[0].upper().startswith("BALLOT LANGUAGE FOR"):
        return None
    proposed_idx = next((i for i, ln in enumerate(lines) if _PROPOSED_BY_RE.search(ln)), None)
    yes_idx = next((i for i, ln in enumerate(lines) if _YES_BOX_RE.match(ln)), None)
    if proposed_idx is None or yes_idx is None or yes_idx <= proposed_idx + 1 or proposed_idx < 2:
        return None
    body = lines[proposed_idx + 1:yes_idx]
    fiscal_idx = next((i for i, ln in enumerate(body) if _FISCAL_RE.match(ln)), None)
    summary = clean_text(" ".join(body if fiscal_idx is None else body[:fiscal_idx]))
    fiscal = None if fiscal_idx is None else clean_text(" ".join(body[fiscal_idx:]))
    # The fiscal statement's drafter only when the statement names one
    # ("... prepared by the Governor's Office of Budget and Program
    # Planning"). The Secretary of State files these statements; it does
    # not write them, so it is never assumed.
    prepared = _PREPARED_BY_RE.search(fiscal) if fiscal else None
    fiscal_authority = clean_text(prepared.group(1)) if prepared else None
    if not summary:
        return None
    proposer = _PROPOSED_BY_RE.search(lines[proposed_idx]).group(1).upper()
    if "INITIATIVE" in proposer:
        origin = "Montana voters (initiative petition)"
    elif "LEGISLATURE" in proposer:
        origin = "Montana Legislature"
    elif "REFERENDUM" in proposer:
        origin = "Montana voters (referendum petition)"
    else:
        origin = None
    return {
        "number": issue_id,
        "title": clean_text(lines[1]),
        "origin": origin,
        "official_summary": summary,
        "fiscal_impact": fiscal,
        "yes_means": None,
        "no_means": None,
        "title_authority": TITLE_AUTHORITY,
        "fiscal_authority": fiscal_authority,
    }


def _pdf_src(wrapper: bytes) -> str | None:
    """The PDF a preview wrapper page's <iframe> names, as written."""
    try:
        tree = lxml_html.fromstring(wrapper)
    except Exception:
        return None
    return next((s for s in tree.xpath("//iframe/@src") if s.lower().split("?")[0].endswith(".pdf")), None)


async def _resolve_pdf(client: httpx.AsyncClient, href: str, base: str) -> tuple[bytes, str] | None:
    """The site links a preview wrapper page, not the PDF; its <iframe>
    names the PDF. A direct PDF link is used as-is."""
    url = urljoin(base, href)
    body = await get_bytes_unless_walled(client, url, "MT ballot issue link")
    if body is None:
        return None
    if body[:5] == b"%PDF-":
        return body, url
    src = _pdf_src(body)
    if src is None:
        return None
    pdf_url = urljoin(url, src)
    pdf = await get_bytes_unless_walled(client, pdf_url, "MT ballot language PDF")
    if pdf is None or pdf[:5] != b"%PDF-":
        return None
    return pdf, pdf_url


def _first_page_text(pdf_bytes: bytes) -> str | None:
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return pdf.pages[0].extract_text() or ""
    except Exception:
        logger.exception("MT ballot-language PDF unreadable")
        return None


def _issues(page_html: str, year: int) -> list[tuple[str, str]] | None:
    try:
        issues = qualified_issues(page_html, year)
    except Exception:
        logger.exception("MT ballot issues page for %d was not parseable HTML", year)
        return None
    if issues is None:
        logger.warning("MT ballot issues page for %d has no 'Issues Qualified' heading", year)
    return issues


def _statement(pdf_bytes: bytes, issue_id: str) -> dict | None:
    first_page = _first_page_text(pdf_bytes)
    if first_page is None:
        logger.warning("MT %s: ballot-language PDF unreadable — failing the state", issue_id)
        return None
    parsed = parse_ballot_language(first_page, issue_id)
    if parsed is None:
        logger.warning("MT %s: not a 'BALLOT LANGUAGE FOR' statement — failing the state", issue_id)
    return parsed


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    url = URL_PATTERN.format(year=year)
    page_html = await get_text_unless_walled(client, url, f"MT ballot issues {year}")
    if page_html is None:
        return None
    issues = _issues(page_html, year)
    if issues is None:
        return None

    results: list[tuple[dict, str]] = []
    for issue_id, href in issues:
        resolved = await _resolve_pdf(client, href, url)
        if resolved is None:
            logger.warning("MT %s: ballot-language PDF not reachable — failing the state", issue_id)
            return None
        pdf_bytes, pdf_url = resolved
        parsed = _statement(pdf_bytes, issue_id)
        if parsed is None:
            return None
        results.append((parsed, pdf_url))
    return results


async def _archived_statement(client: httpx.AsyncClient, issue_id: str, link: str) -> tuple[dict, str] | None:
    """One issue's statement from the Archive: the newest capture of the
    link the archived page gives (the preview wrapper, read only to find
    its PDF), then the newest capture of that PDF, byte for byte."""
    if not link.startswith(_STATE_HOST):
        return None
    wrapper = await web_archive.newest_capture(client, link)
    body = await web_archive.read_page(client, wrapper) if wrapper else None
    if body is None:
        return None
    if body[:5] == b"%PDF-":
        pdf_url = link
    else:
        src = _pdf_src(body)
        pdf_url = web_archive.resolve(wrapper, src) if src else None
    if pdf_url is None or not pdf_url.startswith(_STATE_HOST):
        return None
    capture = await web_archive.newest_capture(client, pdf_url)
    pdf = await web_archive.read_capture(client, capture) if capture else None
    if pdf is None or pdf[:5] != b"%PDF-":
        return None
    parsed = _statement(pdf, issue_id)
    return (parsed, capture.page_url) if parsed else None


async def fetch_archived(
    client: httpx.AsyncClient, year: int, known_urls: list[str],
) -> list[tuple[dict, str]] | None:
    """The Secretary's ballot-issues page and each qualified issue's
    statement as the Internet Archive captured them, for when sosmt.gov is
    walled (ballot_measures_pdf calls this only on SourceBlocked). The
    newest capture of the page gives the qualified list, with every check
    the state's own copy gets; each statement PDF is read from its newest
    capture, matched to the capture's digest, and parsed the same way. The
    page and the preview wrappers are read only to find links
    (web_archive.read_page: the wrappers were captured brotli-encoded). Any issue
    without a verified statement fails the state (None), as on the state's
    own host. `known_urls` is unused: the archived page names every
    statement, and a statement's address alone doesn't say which issue it
    is."""
    url = URL_PATTERN.format(year=year)
    page = await web_archive.newest_capture(client, url)
    raw = await web_archive.read_page(client, page) if page else None
    if raw is None:
        logger.warning("MT %d: no Internet Archive capture of the ballot-issues page could be read", year)
        return None
    issues = _issues(raw.decode("utf-8", "replace"), year)
    if issues is None:
        return None
    results: list[tuple[dict, str]] = []
    for issue_id, href in issues:
        link = web_archive.resolve(page, href)
        pair = await _archived_statement(client, issue_id, link)
        if pair is None:
            logger.warning("MT %s: no Internet Archive capture of its statement verified — failing the state", issue_id)
            return None
        results.append(pair)
    return results
