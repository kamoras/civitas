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
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

URL_PATTERN = "https://sosmt.gov/elections/ballot_issues/proposed-{year}-ballot-issues/"
TITLE_AUTHORITY = "Montana Attorney General (approved ballot statement), as filed with the Montana Secretary of State"

_rate_limiter = RateLimiter(rps=1.0)

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


async def _resolve_pdf(client: httpx.AsyncClient, href: str, base: str) -> tuple[bytes, str] | None:
    """The site links a preview wrapper page, not the PDF; its <iframe>
    names the PDF. A direct PDF link is used as-is."""
    url = urljoin(base, href)
    body = await fetch_bytes_with_retry(client, _rate_limiter, url, "MT ballot issue link")
    if body is None:
        return None
    if body[:5] == b"%PDF-":
        return body, url
    try:
        tree = lxml_html.fromstring(body)
    except Exception:
        return None
    src = next((s for s in tree.xpath("//iframe/@src") if s.lower().split("?")[0].endswith(".pdf")), None)
    if src is None:
        return None
    pdf_url = urljoin(url, src)
    pdf = await fetch_bytes_with_retry(client, _rate_limiter, pdf_url, "MT ballot language PDF")
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


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    url = URL_PATTERN.format(year=year)
    page_html = await fetch_text_with_retry(client, _rate_limiter, url, f"MT ballot issues {year}")
    if page_html is None:
        return None
    try:
        issues = qualified_issues(page_html, year)
    except Exception:
        logger.exception("MT ballot issues page for %d was not parseable HTML", year)
        return None
    if issues is None:
        logger.warning("MT ballot issues page for %d has no 'Issues Qualified' heading", year)
        return None

    results: list[tuple[dict, str]] = []
    for issue_id, href in issues:
        resolved = await _resolve_pdf(client, href, url)
        if resolved is None:
            logger.warning("MT %s: ballot-language PDF not reachable — failing the state", issue_id)
            return None
        pdf_bytes, pdf_url = resolved
        first_page = _first_page_text(pdf_bytes)
        if first_page is None:
            logger.warning("MT %s: ballot-language PDF unreadable — failing the state", issue_id)
            return None
        parsed = parse_ballot_language(first_page, issue_id)
        if parsed is None:
            logger.warning("MT %s: not a 'BALLOT LANGUAGE FOR' statement — failing the state", issue_id)
            return None
        results.append((parsed, pdf_url))
    return results
