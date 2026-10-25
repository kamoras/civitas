"""Michigan's ballot-measure strategy — the Bureau of Elections' own
"<year> November ballot questions" PDF, linked from the Secretary of
State's elections landing page (one of MULTI_DOCUMENT_STRATEGIES in
ballot_measures_pdf.py; see below for why it can't use the shared
discover_pdf_url + STRATEGIES path).

The PDF's URL is not predictable — michigan.gov serves it from a
content-managed media path with a `?rev=<hash>` suffix that changes on
every republish — so it is discovered each run from
michigan.gov/sos/elections, by the link whose text names `year`,
"November" and "ballot questions" (verified live 2026-09-28: "2026
November ballot questions"). The shared discover_pdf_url can't do this:
its link pattern requires an href ending in ".pdf", and it sends no
request headers.

Headers: michigan.gov's CDN (Akamai) answers Civitas's User-Agent with a
challenge (403), keyed on the contact in its comment (measured 2026-09-28
and again 2026-10-25: a bare "Civitas/1.0" is served, as is a browser's).
Until 2026-10-25 this module dropped the comment; it now sends Civitas's own
User-Agent, and while the refusal stands the read fails and is alerted
(AGENTS.md §7), never sent under another name.

The document is one page per proposal, each certified on its own date
(verified on both 2026 proposals):

    August 17, 2026
    Proposal 2026-2                                  <- bold
    A proposed initiated law to prohibit ...         <- bold, may wrap
    The proposal would:                              <- regular
    • Prohibit ...
    Should this proposal be adopted?
    [ ] Yes
    [ ] No

The bold lines after the "Proposal" line are the ballot's own title;
the regular lines up to "[ ] Yes" are the ballot's own question text
(Proposal 2026-1 has no bullet list — just its "Shall a convention
...?" question). Both are stored verbatim. The Bureau of Elections
publishes no yes/no framing and no fiscal statement in it, so those
stay None. Bold is read from the PDF's own font names, not guessed from
wording: the boundary between title and question has no textual marker
on Proposal 2026-1.

No document linked for `year` raises NotYetPublished (not yet covered):
the Bureau posts it only once a proposal is certified, so its absence is
"nothing to read yet", not a failure and never "none".

A document that WAS read and is no longer linked on the landing page is
looked for again by rule (RECORDING keeps every successful read):

- On the Bureau's other election pages linked from the landing page (one
  level, under /sos/elections, at most _RELINK_PAGES), any PDF link whose
  text names `year`, November and ballot question(s) or proposal(s) is a
  candidate. A candidate counts only when the same parser reads it whole
  and it shares a proposal (number and title) with the recorded document.
  Found, it is read as tonight's document and recorded: a proposal it no
  longer carries is the Bureau's own document saying it is gone, and the
  pipeline marks it removed. A proposal is never marked removed on a
  missing link alone.
- Not found, the reader runs its self-check (the recorded document through
  the same extraction and parser). Passing, it raises DocumentNotLinked:
  the measures on file stay current, credited to the recorded document
  and its read, and the pipeline alerts only after RELINK_WINDOW_DAYS.
  Failing, it is a failure (None, alerted).

With no recording for `year` (never read), it is still not yet published.
"""

import base64
import io
import json
import logging
import os
import re
from urllib.parse import urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.atomic_write import runtime_data_path, write_text_atomic
from app.pipeline.fetch.ballot_measure_text import DocumentNotLinked, NotYetPublished, join_lines
from app.pipeline.fetch.http_utils import FETCH_HEADERS, fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

LANDING_URL = "https://www.michigan.gov/sos/elections"
TITLE_AUTHORITY = "Michigan Bureau of Elections"

# Civitas's own (see the module docstring).
HEADERS = FETCH_HEADERS

_rate_limiter = RateLimiter(rps=1.0)

RECORDING = "mi_ballot_questions_{year}.json"
# The Bureau's pages a missing document is looked for on, and the candidate
# PDFs opened: the landing page links some forty election pages, and the
# document has only ever been linked from one or two of them.
_RELINK_PAGES = 40
_RELINK_CANDIDATES = 4

_PROPOSAL_RE = re.compile(r"^Proposal\s+(\d{4}-\d+)$")
_YES_BOX_RE = re.compile(r"^\[\s*\]\s*Yes$")


def _pdf_links(landing_html: str, year: int) -> set[str]:
    tree = lxml_html.fromstring(landing_html)
    matches = set()
    for a in tree.xpath("//a[@href]"):
        text = " ".join(a.text_content().split()).lower()
        href = a.get("href")
        if str(year) in text and "november" in text and "ballot question" in text and ".pdf" in href.lower():
            matches.add(urljoin(LANDING_URL, href))
    return matches


def _link_count(landing_html: str, year: int) -> int:
    return len(_pdf_links(landing_html, year))


def find_pdf_url(landing_html: str, year: int) -> str | None:
    matches = _pdf_links(landing_html, year)
    if len(matches) != 1:
        return None
    return matches.pop()


def pdf_lines(raw: bytes) -> list[list[tuple[str, bool]]]:
    """Per page, each text line with whether all of its letters are set
    in a bold face — the only layout fact parse_lines needs, kept as
    plain data so tests run on a real extraction without a binary PDF."""
    pages = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            lines = []
            for line in page.extract_text_lines():
                letters = [c for c in line["chars"] if c["text"].strip()]
                bold = bool(letters) and all("bold" in c["fontname"].lower() for c in letters)
                lines.append((line["text"], bold))
            pages.append(lines)
    return pages


def _origin(title: str) -> str | None:
    lowered = title.lower()
    if "initiated" in lowered or "initiative" in lowered:
        return "Michigan voters (initiative petition)"
    if "legislat" in lowered:
        return "Michigan Legislature"
    return None


def _proposal_start(line: tuple[str, bool]) -> str | None:
    """The proposal number a line opens, or None. A bold line that starts
    "Proposal" but isn't in the verified "Proposal <year>-<n>" form raises:
    it is a proposal heading this reader doesn't know, and skipping it
    would publish the document one proposal short."""
    text, bold = line[0].strip(), line[1]
    m = _PROPOSAL_RE.match(text)
    if m:
        return m.group(1)
    if bold and text.lower().startswith("proposal"):
        raise ValueError(f"MI: unrecognised proposal heading {text!r}")
    return None


def parse_lines(pages: list[list[tuple[str, bool]]], year: int) -> list[dict] | None:
    """Every `year` proposal in the document, or None when a proposal
    doesn't have the verified shape or none is found at all.

    Every "Proposal" heading on a page is read, each up to the next one —
    not just the first per page: the document is one page per proposal
    today, but two short proposals sharing a page would otherwise lose
    the second without a trace."""
    results = []
    for lines in pages:
        try:
            starts = [(i, n) for i, line in enumerate(lines) if (n := _proposal_start(line))]
        except ValueError:
            logger.warning("MI: a proposal heading didn't match the verified shape — refusing the document")
            return None
        for k, (idx, number) in enumerate(starts):
            if not number.startswith(f"{year}-"):
                continue
            end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
            rest = lines[idx + 1:end]
            title_lines = []
            while rest and rest[0][1]:
                title_lines.append(rest.pop(0)[0])
            yes_idx = next((i for i, (t, _) in enumerate(rest) if _YES_BOX_RE.match(t.strip())), None)
            title = join_lines(title_lines)
            question = join_lines([t for t, _ in rest[:yes_idx]]) if yes_idx else None
            if question:
                # Keep the document's own bullet list a list (the card renders
                # official_summary with pre-line whitespace); no word changes.
                question = re.sub(r"\s*•\s*", "\n• ", question)
                if "•" in question:
                    # The closing question ("Should this proposal be
                    # adopted?") is its own sentence after the last bullet,
                    # not part of it: the sentence after the last bullet's
                    # full stop that ends the text with "?".
                    question = re.sub(r"(?<=\.)\s+([A-Z][^.•]*\?)$", r"\n\n\1", question)
            if not title or not question:
                logger.warning("MI Proposal %s didn't match the verified shape — refusing the document", number)
                return None
            results.append({
                "number": number,
                "title": title,
                # The bold lines are the ballot's own title, as printed.
                "official_title": title,
                "origin": _origin(title),
                "official_summary": question,
                "fiscal_impact": None,
                "yes_means": None,
                "no_means": None,
                "title_authority": TITLE_AUTHORITY,
                "fiscal_authority": None,
            })
    # The Bureau publishes this document only once something is certified
    # to the ballot; one with no readable proposal is a shape change, not
    # a statement that there are none.
    return results or None


def _record(year: int, url: str, raw: bytes) -> None:
    path = runtime_data_path(RECORDING.format(year=year))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_text_atomic(path, json.dumps({"url": url, "pdf": base64.b64encode(raw).decode()}))
    except OSError:
        logger.exception("Could not record Michigan's %d ballot questions document", year)


def _recorded(year: int) -> tuple[str, bytes] | None:
    try:
        with open(runtime_data_path(RECORDING.format(year=year))) as f:
            data = json.load(f)
        return data["url"], base64.b64decode(data["pdf"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _unlinked_since(year: int) -> str:
    """The date the document was first missed, kept with its recording."""
    path = runtime_data_path(RECORDING.format(year=year))
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return utcnow().date().isoformat()
    if not data.get("unlinked_since"):
        data["unlinked_since"] = utcnow().date().isoformat()
        try:
            write_text_atomic(path, json.dumps(data))
        except OSError:
            logger.exception("Could not note when Michigan's %d document went unlinked", year)
    return data["unlinked_since"]


def _same_document(found: list[dict], recorded: list[dict]) -> bool:
    """Whether a candidate is this election's document: it shares a
    proposal, by number and title, with the recorded one."""
    seen = {(p["number"], p["title"]) for p in recorded}
    return any((p["number"], p["title"]) in seen for p in found)


def _relink_pages(landing_html: str) -> list[str]:
    tree = lxml_html.fromstring(landing_html)
    pages = []
    for a in tree.xpath("//a[@href]"):
        url = urljoin(LANDING_URL, a.get("href")).split("#")[0]
        if url.startswith(LANDING_URL + "/") and ".pdf" not in url.lower() and url not in pages:
            pages.append(url)
    return pages[:_RELINK_PAGES]


def _candidate_links(page_html: str, page_url: str, year: int) -> list[str]:
    tree = lxml_html.fromstring(page_html)
    found = []
    for a in tree.xpath("//a[@href]"):
        text = " ".join(a.text_content().split()).lower()
        href = a.get("href")
        if (".pdf" in href.lower() and str(year) in text and "november" in text
                and ("ballot question" in text or "proposal" in text)):
            url = urljoin(page_url, href)
            if url not in found:
                found.append(url)
    return found


async def _refind(client: httpx.AsyncClient, landing_html: str, year: int, recorded: list[dict]):
    """(url, raw, parsed) of the document found again on the Bureau's
    other election pages, or None."""
    for page in _relink_pages(landing_html):
        html = await fetch_text_with_retry(client, _rate_limiter, page, "MI elections page", headers=HEADERS)
        if html is None:
            continue
        for url in _candidate_links(html, page, year)[:_RELINK_CANDIDATES]:
            raw = await fetch_bytes_with_retry(client, _rate_limiter, url, "MI ballot questions candidate",
                                               headers=HEADERS)
            if raw is None:
                continue
            try:
                parsed = parse_lines(pdf_lines(raw), year)
            except Exception:
                continue
            if parsed and _same_document(parsed, recorded):
                return url, raw, parsed
    return None


async def _not_linked(client: httpx.AsyncClient, landing_html: str, year: int) -> list[tuple[dict, str]] | None:
    """The landing page no longer links `year`'s document: see the module
    docstring for the rule."""
    recorded = _recorded(year)
    if recorded is None:
        raise NotYetPublished(
            f"Michigan's '{year} November ballot questions' document", deadline_applies=False,
        )
    url, raw = recorded
    try:
        recorded_parsed = parse_lines(pdf_lines(raw), year)
    except Exception:
        logger.exception("MI self-check: the recorded %d document no longer parses", year)
        recorded_parsed = None
    if not recorded_parsed:
        logger.warning("MI %d: document no longer linked, and the reader fails on its recording", year)
        return None
    found = await _refind(client, landing_html, year, recorded_parsed)
    if found is not None:
        new_url, new_raw, parsed = found
        logger.info("MI %d: ballot questions document found again at %s", year, new_url)
        _record(year, new_url, new_raw)
        return [(p, new_url) for p in parsed]
    raise DocumentNotLinked(f"Michigan's '{year} November ballot questions' document ({url})",
                            since=_unlinked_since(year))


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    landing_html = await fetch_text_with_retry(
        client, _rate_limiter, LANDING_URL, "MI elections landing", headers=HEADERS,
    )
    if landing_html is None:
        return None
    pdf_url = find_pdf_url(landing_html, year)
    if pdf_url is None:
        if _link_count(landing_html, year) == 0:
            # Never read: the Bureau posts this document only once
            # something is certified, so not yet covered. Read before and
            # taken down: decided by rule (_taken_down).
            return await _not_linked(client, landing_html, year)
        logger.warning("MI: more than one '%d November ballot questions' PDF link on the landing page", year)
        return None
    raw = await fetch_bytes_with_retry(client, _rate_limiter, pdf_url, "MI ballot questions PDF", headers=HEADERS)
    if raw is None:
        return None
    try:
        parsed = parse_lines(pdf_lines(raw), year)
    except Exception:
        logger.exception("MI ballot questions PDF parse failed")
        return None
    if parsed is None:
        return None
    _record(year, pdf_url, raw)
    return [(m, pdf_url) for m in parsed]
