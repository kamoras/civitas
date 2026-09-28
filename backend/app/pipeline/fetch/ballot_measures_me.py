"""Maine's ballot-measure strategy — the Secretary of State's "Maine
Citizen's Guide to the Referendum Election" (one of potentially many
per-state strategies; see ballot_measures_pdf.py for the shared
single-PDF contract this module does NOT use, and why).

The guide is prepared by the Secretary of State with the Attorney
General, the Treasurer and the Office of Fiscal and Program Review
(21-A M.R.S. § 605-B), and for every question it prints, under named
drafters:

  "Intent and Content / Prepared by the Office of the Attorney General"
      -> official_summary, ending in the AG's own two sentences
         'A "YES" vote ...' / 'A "NO" vote ...' -> yes_means / no_means
         (lifted verbatim, whole sentence; never derived)
  "Debt Service / Prepared by the Office of the Treasurer" (bonds only)
  "Fiscal Impact Statement / Prepared by the Office of Fiscal and
   Program Review"
      -> fiscal_impact (debt service first when present, since the
         bond FIS reads "no significant fiscal impact other than the
         debt service costs identified above"), fiscal_authority named
         from those same "Prepared by" lines.

Verified against the real 2024 guide (5 questions: a citizen initiative,
three bonds, a legislative referendum) and the real 2025 guide (2
citizen initiatives) — see tests/fixtures_me_citizens_guide.json. The two
use different page footers ("11" vs "Page 15") and 2025 repeats each
"Question N: ..." heading inside its Intent section; both are handled.

Discovery. The guide's filename changes every year (2024:
"Citizens-20Guide-2011.5.2024-20FINAL.pdf", 2025:
"MaineCitizensGuide2025.pdf" — both real), so it is found, not
templated: from the Secretary's "Upcoming Elections" page and then the
news release announcing it (slug "citizens-guide-{year}-maine-
referendum-election-..." — real for 2024 and 2025), taking a PDF link
that names the year and "guide". The guide's own cover must name
November and the year, or it is refused.

2026 (checked live 2026-09-28): not yet published — the Secretary's news
list carries no guide release and no ballot-order announcement, so this
returns None (not yet covered) rather than anything else. Maine prints
a guide only when there are questions (2025's appeared 9/25), and no
official source yet says whether 2026 has any: the one citizen
initiative that qualified (school sports/facilities) had its petition
invalidated, affirmed by the Maine Supreme Judicial Court on 2026-07-10
with a federal challenge pending, and the people's veto of the
supplemental budget was abandoned before its signature deadline. A
missing guide is never read as "none".
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text
from app.pipeline.fetch.http_utils import fetch_bytes_with_retry, fetch_text_with_retry
from app.pipeline.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

UPCOMING_URL = "https://www.maine.gov/sos/elections-voting/upcoming-elections"
NEWS_URL = "https://www.maine.gov/sos/about-us/news"
NEWS_PAGES = 3

TITLE_AUTHORITY = "Maine Secretary of State"

_rate_limiter = RateLimiter(rps=1.0)

_ANCHOR_RE = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

_FOOTER_RE = re.compile(r"^(?:Page\s+)?\d+$")
_HEADING_RE = re.compile(r"^Question\s+(\d+):\s*(.+)$", re.MULTILINE)
_INTENT_RE = re.compile(r"^Intent and Content\s*\nPrepared by (the [^\n]+)$", re.MULTILINE)
# "List of Referendum Questions" (2024) / "Listing of Referendum
# Questions" (2025) — both real.
_LISTING_RE = re.compile(r"^List(?:ing)? of Referendum Questions$", re.MULTILINE)
_COMMENTS_RE = re.compile(r"^Public Comments?\b", re.MULTILINE)
_Q = "[\"“”]"
_YES_RE = re.compile(rf"^(A {_Q}YES{_Q} vote\b.*?)\n(?=A {_Q}NO{_Q} vote\b)", re.MULTILINE | re.DOTALL)
_NO_RE = re.compile(
    rf"^(A {_Q}NO{_Q} vote\b.*?)(?=\n(?:Debt Service|Fiscal Impact Statement)\s*\n|\Z)",
    re.MULTILINE | re.DOTALL,
)
_DEBT_RE = re.compile(
    r"^Debt Service\s*\nPrepared by (the [^\n]+)\n(.*?)(?=^Fiscal Impact Statement\s*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
_FIS_RE = re.compile(r"^Fiscal Impact Statement\s*\nPrepared by (the [^\n]+)\n(.*)\Z", re.MULTILINE | re.DOTALL)


def _origin(kind: str) -> str | None:
    """The guide's own "Question N: <kind>" label, translated — a
    form-vocabulary mapping, not a guess from the question's content."""
    k = kind.lower()
    if "initiative" in k:
        return "Maine voters (citizen initiative)"
    if "veto" in k:
        return "Maine voters (people's veto)"
    if "bond" in k or "referendum" in k or "amendment" in k:
        return "Maine Legislature"
    return None


def _authority(prepared_by: str) -> str:
    # "the Office of the Attorney General" -> "Maine Office of the Attorney General"
    return "Maine " + re.sub(r"^the\s+", "", prepared_by.strip())


def _clean_lines(full_text: str) -> str:
    lines = [ln.strip() for ln in full_text.splitlines()]
    return "\n".join(ln for ln in lines if ln and not _FOOTER_RE.match(ln))


def parse_guide(full_text: str) -> list[dict]:
    """Every question's record, in guide order. A question whose sections
    don't have the verified shape is skipped with a warning (and the
    caller treats a short list as a failure — see fetch_measures)."""
    text = _clean_lines(full_text)
    headings = list(_HEADING_RE.finditer(text))
    intents = list(_INTENT_RE.finditer(text))
    results: list[dict] = []
    for i, intent in enumerate(intents):
        prior = [h for h in headings if h.start() < intent.start()]
        if not prior:
            continue
        number, kind = prior[-1].group(1), prior[-1].group(2).strip()
        end = intents[i + 1].start() if i + 1 < len(intents) else len(text)
        block = text[intent.end():end]
        comments = _COMMENTS_RE.search(block)
        if comments:
            block = block[: comments.start()]
        block = re.sub(rf"^\s*Question\s+{number}:[^\n]*\n", "", block)

        yes_m, no_m = _YES_RE.search(block), _NO_RE.search(block)
        if yes_m is None or no_m is None:
            logger.warning("ME Question %s: no YES/NO sentences in its Intent section — skipping", number)
            continue
        summary = clean_text(block[: yes_m.start()])
        tail = block[no_m.end():]
        debt, fis = _DEBT_RE.search(tail), _FIS_RE.search(tail)
        parts = [clean_text(m.group(2)) for m in (debt, fis) if m]
        fiscal = "\n\n".join(p for p in parts if p) or None
        authorities = [_authority(m.group(1)) for m in (debt, fis) if m]
        if not summary:
            continue
        results.append({
            "number": number,
            "title": clean_text(f"Question {number}: {kind}"),
            "origin": _origin(kind),
            "official_summary": summary,
            "fiscal_impact": fiscal,
            "yes_means": clean_text(yes_m.group(1)),
            "no_means": clean_text(no_m.group(1)),
            "title_authority": TITLE_AUTHORITY,
            "fiscal_authority": " and ".join(authorities) if fiscal else None,
        })
    return results


def listed_numbers(full_text: str) -> list[str]:
    """Question numbers from the guide's "List(ing) of Referendum
    Questions" page — the completeness check for parse_guide."""
    text = _clean_lines(full_text)
    listing_heading = _LISTING_RE.search(text)
    if listing_heading is None:
        return []
    start = listing_heading.end()
    first_intent = _INTENT_RE.search(text)
    listing = text[start: first_intent.start() if first_intent else len(text)]
    numbers: list[str] = []
    for m in _HEADING_RE.finditer(listing):
        if m.group(1) not in numbers:
            numbers.append(m.group(1))
        elif numbers:
            break  # the listing is over; the first question's own section has begun
    return numbers


def guide_links(page_html: str, base_url: str, year: int) -> list[str]:
    found = []
    for href, text in _ANCHOR_RE.findall(page_html):
        hay = f"{href} {_TAG_RE.sub(' ', text)}".lower()
        if ".pdf" in href.lower() and str(year) in hay and "guide" in hay and "citizen" in hay.replace("%20", " "):
            url = urljoin(base_url, href)
            if url not in found:
                found.append(url)
    return found


def release_links(page_html: str, base_url: str, year: int) -> list[str]:
    found = []
    for href, _ in _ANCHOR_RE.findall(page_html):
        if f"citizens-guide-{year}" in href.lower():
            url = urljoin(base_url, href)
            if url not in found:
                found.append(url)
    return found


def _extract_text(raw: bytes) -> str:
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


async def _find_guide(client: httpx.AsyncClient, year: int) -> str | None:
    pages = [UPCOMING_URL] + [f"{NEWS_URL}?page={n}" for n in range(NEWS_PAGES)]
    releases: list[str] = []
    for url in pages:
        html = await fetch_text_with_retry(client, _rate_limiter, url, "ME SOS page")
        if html is None:
            continue
        links = guide_links(html, url, year)
        if links:
            return links[0]
        releases += [r for r in release_links(html, url, year) if r not in releases]
    for url in releases:
        html = await fetch_text_with_retry(client, _rate_limiter, url, "ME citizen's guide release")
        if html is None:
            continue
        links = guide_links(html, url, year)
        if links:
            return links[0]
    return None


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    """[(parsed, source_url), ...] for every question in `year`'s guide;
    None when the guide can't be found (including "not published yet"),
    fetched, or read completely. Never [] — see module docstring."""
    guide_url = await _find_guide(client, year)
    if guide_url is None:
        logger.info("ME %d Citizen's Guide not found (not yet published?)", year)
        return None
    raw = await fetch_bytes_with_retry(client, _rate_limiter, guide_url, f"ME {year} Citizen's Guide")
    if raw is None:
        return None
    try:
        text = _extract_text(raw)
    except Exception:
        logger.exception("ME %d Citizen's Guide did not parse as a PDF", year)
        return None
    cover = re.sub(r"\s+", " ", text[:600])
    if "November" not in cover or str(year) not in cover:
        logger.warning("ME guide at %s doesn't name November %d on its cover — refusing", guide_url, year)
        return None
    parsed = parse_guide(text)
    expected = listed_numbers(text)
    if not parsed or [p["number"] for p in parsed] != expected:
        logger.warning(
            "ME %d guide: parsed questions %s don't match the guide's own listing %s",
            year, [p["number"] for p in parsed], expected,
        )
        return None
    return [(p, guide_url) for p in parsed]
