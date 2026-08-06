"""Direct parsing of California's own official Voter Information Guide PDF
(vig.cdn.sos.ca.gov) — a real, no-API-key replacement for Vote Smart's role
for California statewide propositions specifically.

WHY THIS EXISTS: the user asked to stop depending on Vote Smart's approval-
gated signup and get the same data independently. Checked directly whether
California publishes something better than a per-county composite ballot:
it does — the Secretary of State's own "Quick Reference Guide" section is a
purpose-built, state-level ballot-measure-only summary (title, origin,
official summary, fiscal impact, and — critically — explicit "WHAT YOUR
VOTE MEANS: YES.../NO..." framing in the state's own words, not derived).
Two propositions per page, consistent format across election cycles
(verified against real PDFs from two different elections: 2026 primary,
36.7MB / 64 pages, and 2024 general, 5MB / 144 pages).

REAL DOCUMENT, NOT ASSUMED: the November 2026 general election guide is not
published yet at the standard CDN path (confirmed: HTTP 403 as of
2026-08-06 — same timing constraint every other source in this codebase
has for the upcoming general). Built and verified against the 2024 general
guide instead, which has real propositions (the 2026 primary guide that IS
already published has none — CA propositions are a general-election-cycle
thing, not guaranteed on primaries). The parser targets the DOCUMENT
FORMAT, which is consistent guide-to-guide, not this specific election's
content.

THE HARD PART: pdfplumber's plain extract_text() badly mangles this page.
It's not one 2-column layout — the "Quick Reference Guide" page has TWO
levels of column splitting: the two propositions side by side, AND, within
each proposition's own half, a further YES/NO (and separately PRO/CON) sub-
split for the "WHAT YOUR VOTE MEANS" and "ARGUMENTS" sections. A naive
single geometric crop() at a fixed x-coordinate literally cuts words in
half at the boundary (verified: "this" split into "th"/"his" across two
crops) because the sub-column gutter is narrow and text isn't rigidly
justified to it on every line.

Fixed with a two-phase, GAP-based (not fixed-coordinate) reconstruction,
built and verified against this exact real page's word coordinates:

1. Determine the true OUTER column boundary once, from unambiguous rows in
   the SUMMARY zone (which never has more than 2 text fragments per visual
   row) — the x-gap there reliably marks the Prop-A/Prop-B gutter. Using a
   single largest-gap-per-row rule across the WHOLE page failed: rows in
   the sub-split zones have 4 fragments (PropA-YES, PropA-NO, PropB-YES,
   PropB-NO), and the largest gap on those specific rows is not reliably
   the outer one.
2. Bucket every word on the page by that fixed threshold into PropA/PropB.
3. WITHIN each already-isolated side, apply a per-row largest-x-gap split
   AGAIN (now genuinely unambiguous — only 2 fragments per row within one
   side) to separate YES from NO, and separately PRO from CON.

Verified end to end against Proposition 2 in the real 2024 guide: the
reconstructed YES/NO text matches the source exactly, word for word.
"""

import io
import logging
import re
from collections import defaultdict

import httpx
import pdfplumber

from app.pipeline.cache import api_cache_get, api_cache_set

logger = logging.getLogger(__name__)

TITLE_AUTHORITY = "California Attorney General"
FISCAL_AUTHORITY = "California Legislative Analyst's Office"
SOURCE_NAME = "California Secretary of State"

# Longer than Vote Smart's 12h (MEASURE_CACHE_TTL_HOURS in
# ballot_measures.py) — that shorter window exists because Vote Smart's
# own feed can change under us mid-cycle; this module re-derives
# everything from one static PDF the state republishes wholesale on the
# rare occasion it changes, so there is nothing to catch by polling more
# often. Matches the platform's general 72h API-cache default instead.
CACHE_TTL_HOURS = 72


def _rows(words: list[dict]) -> dict[int, list[dict]]:
    bands: dict[int, list[dict]] = defaultdict(list)
    for w in words:
        bands[round(w["top"])].append(w)
    return bands


def _split_by_row_gap(words: list[dict]) -> tuple[list[dict], list[dict]]:
    """Per visual row, cut at the single largest x-gap between adjacent
    words. Correct ONLY when each row has exactly two fragments — verified
    this holds for the YES/NO and PRO/CON sub-zones once already isolated
    to one outer side (see module docstring, phase 3)."""
    left: list[dict] = []
    right: list[dict] = []
    for top in sorted(_rows(words)):
        row = sorted(_rows(words)[top], key=lambda w: w["x0"])
        if len(row) < 2:
            left.extend(row)
            continue
        best_gap, best_idx = -1.0, len(row)
        for i in range(len(row) - 1):
            gap = row[i + 1]["x0"] - row[i]["x1"]
            if gap > best_gap:
                best_gap, best_idx = gap, i + 1
        left.extend(row[:best_idx])
        right.extend(row[best_idx:])
    return left, right


def _lines_from_words(words: list[dict]) -> list[str]:
    bands = _rows(words)
    return [
        " ".join(w["text"] for w in sorted(bands[top], key=lambda w: w["x0"]))
        for top in sorted(bands)
    ]


def _outer_boundary(page) -> float | None:
    """The Prop-A/Prop-B gutter x-coordinate, calibrated from the SUMMARY
    zone's own rows (top < the first 'WHAT' heading), where every row has
    exactly two fragments and the single gap found IS the outer gutter —
    unlike the sub-split zones lower on the page, where per-row gap-finding
    is ambiguous (see module docstring). None if no 'WHAT' heading is
    found at all — this page isn't in the expected Quick Reference format,
    and the caller should skip it rather than guess a boundary."""
    words = page.extract_words()
    what_tops = [w["top"] for w in words if w["text"] == "WHAT"]
    if not what_tops:
        return None
    summary_zone_end = min(what_tops)
    summary_words = [w for w in words if w["top"] < summary_zone_end]
    gaps = []
    for top, row in _rows(summary_words).items():
        row = sorted(row, key=lambda w: w["x0"])
        if len(row) < 2:
            continue
        i = max(range(len(row) - 1), key=lambda i: row[i + 1]["x0"] - row[i]["x1"])
        gaps.append((row[i]["x1"] + row[i + 1]["x0"]) / 2)
    if not gaps:
        return None
    gaps.sort()
    return gaps[len(gaps) // 2]  # median — robust to one or two odd rows


def _text(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = " ".join(raw.split())
    return cleaned or None


_CONTAMINATION_NGRAM = 3  # words


def _looks_corrupted(text: str) -> bool:
    """Two independent checks, both calibrated against real text (Prop 2,
    correctly extracted; Prop 3, a real row-gap misclassification on a
    wrapped line where the YES and NO sentences render with essentially
    zero visual gap between them — confirmed on the actual PDF, not
    hypothetical):

    1. Doesn't end in terminal punctuation. A real official sentence from
       this document always does; Prop 3's misclassified yes_means
       trailed off mid-thought ("...There would be can") with nothing
       after it to attribute — the row-gap split had run out of rows
       assigned to that side before the sentence's own words did.
    2. A 3+ word phrase repeats within the SAME string. Catches content
       that got attributed to a side once correctly and then again
       (Prop 3's no_means: "no change in who can marry" / "no change in
       who marry" — not byte-identical, since a word dropped between the
       two occurrences, so this is deliberately word-existence-based
       rather than requiring an exact repeat).

    Deliberately NOT "do yes_means and no_means share a phrase with each
    other" — tried that first and it false-positived on Prop 2's already
    verified-correct text, where YES and NO legitimately share a long
    tail ("...build new or renovate existing public school and community
    college facilities") differing only in "could"/"could not". Sharing
    vocabulary across two independent, complete sentences is normal;
    either signal above, within ONE sentence, is not.
    """
    if not text.rstrip().endswith((".", "!", "?")):
        return True
    words = [w.lower() for w in text.split()]
    seen: set[tuple[str, ...]] = set()
    for i in range(len(words) - _CONTAMINATION_NGRAM + 1):
        ngram = tuple(words[i:i + _CONTAMINATION_NGRAM])
        if ngram in seen:
            return True
        seen.add(ngram)
    return False


_TITLE_RE = re.compile(r"^PROP\b(.*)$")
_YES_MEANS_RE = re.compile(r"A YES vote on this measure means:\s*(.*)$", re.IGNORECASE)
_NO_MEANS_RE = re.compile(r"A NO vote on this measure means:\s*(.*)$", re.IGNORECASE)
# A non-greedy capture with no reliable stop point (verified as a real
# bug: with nothing to anchor the end on, ".+?" expanded to swallow the
# entire rest of the summary paragraph as "origin"). California propos-
# itions only ever reach the ballot one of two ways under state election
# law, so match those two fixed phrases explicitly rather than an open-
# ended capture — a phrase this module has never seen costs us the
# `origin` field alone (falls through to None), not a corrupted summary.
_ORIGIN_RE = re.compile(r"Put on the Ballot by (the Legislature|Petition Signatures)")
_FISCAL_SPLIT_RE = re.compile(r"\bFiscal Impact:\s*", re.IGNORECASE)
_SUPPORTERS_SPLIT_RE = re.compile(r"\bSupporters:\s*", re.IGNORECASE)


def _parse_side(
    number: str, title: str, summary_words: list[dict], vote_means_words: list[dict],
) -> dict | None:
    """One proposition's fields, from its own already-isolated word set
    (see parse_quick_reference_page for how `summary_words`/
    `vote_means_words` get split to just this side)."""
    summary_text = " ".join(_lines_from_words(summary_words))
    origin_match = _ORIGIN_RE.search(summary_text)
    origin = _text(origin_match.group(1)) if origin_match else None

    # "SUMMARY <origin line> <official summary...> Fiscal Impact: <...>
    # Supporters: <...> Opponents: <...>" — split on the fixed markers
    # rather than guessing where prose ends, so a shift in the state's own
    # wording costs us a field, not a garbled blend of two fields.
    body = summary_text
    if origin_match:
        body = body[origin_match.end():]
    fiscal_split = _FISCAL_SPLIT_RE.split(body, maxsplit=1)
    official_summary = _text(fiscal_split[0])
    fiscal_impact = None
    if len(fiscal_split) > 1:
        supporters_split = _SUPPORTERS_SPLIT_RE.split(fiscal_split[1], maxsplit=1)
        fiscal_impact = _text(supporters_split[0])

    yes_words, no_words = _split_by_row_gap(vote_means_words)
    yes_text = " ".join(_lines_from_words(yes_words))
    no_text = " ".join(_lines_from_words(no_words))
    yes_match = _YES_MEANS_RE.search(yes_text)
    no_match = _NO_MEANS_RE.search(no_text)
    yes_means = _text(yes_match.group(1)) if yes_match else None
    no_means = _text(no_match.group(1)) if no_match else None
    if (yes_means and _looks_corrupted(yes_means)) or (no_means and _looks_corrupted(no_means)):
        # See _looks_corrupted's docstring — both checks there are
        # calibrated against this exact real failure (Prop 3's yes_means/
        # no_means on a wrapped line where the row-gap split had nothing
        # to find). Drop both rather than ship one that might be
        # scrambled.
        logger.warning(
            "Prop %s: yes_means/no_means text looks corrupted — "
            "dropping both rather than risk shipping scrambled text", number,
        )
        yes_means = no_means = None

    if not official_summary:
        # No official summary means this side of the split didn't land on
        # real proposition content (e.g. a page-edge artifact) — nothing
        # trustworthy to return rather than a mostly-empty record.
        return None

    return {
        "number": number,
        "title": _text(title),
        "origin": origin,
        "official_summary": official_summary,
        "fiscal_impact": fiscal_impact,
        "yes_means": yes_means,
        "no_means": no_means,
        "title_authority": TITLE_AUTHORITY,
        "fiscal_authority": FISCAL_AUTHORITY,
    }


def parse_quick_reference_page(page) -> list[dict]:
    """Both propositions on one Quick Reference Guide page, or [] if this
    page isn't in that format (caller should still check other pages —
    this is a per-page result, not a whole-document verdict)."""
    boundary = _outer_boundary(page)
    if boundary is None:
        return []

    words = page.extract_words()
    left_words = [w for w in words if w["x0"] < boundary]
    right_words = [w for w in words if w["x0"] >= boundary]

    results = []
    for side_words in (left_words, right_words):
        # PROP <number>\n<TITLE...> precedes SUMMARY; "WHAT" marks the
        # start of the yes/no zone. Isolate each by top position within
        # this side's own words — same "read the real boundary from the
        # words, don't assume a fixed one" discipline as the outer split.
        prop_tops = sorted({w["top"] for w in side_words if w["text"] == "PROP"})
        what_tops = sorted({w["top"] for w in side_words if w["text"] == "WHAT"})
        if not prop_tops or not what_tops:
            continue
        # "ARGUMENTS" (PRO/CON) follows "WHAT YOUR VOTE MEANS" on the same
        # page, in the same 2-fragment-per-row shape — if left unbounded,
        # vote_means_words would include it, and the YES/NO regexes'
        # `.search()`-to-end-of-string would sweep PRO/CON text into
        # yes_means/no_means. Bound the zone to end at "ARGUMENTS" (or the
        # side's last word, if this page has no arguments section).
        arguments_tops = sorted({w["top"] for w in side_words if w["text"] == "ARGUMENTS"})
        vote_means_end = arguments_tops[0] if arguments_tops else float("inf")
        # "SUMMARY" itself marks where the title block ends and the
        # summary block begins — without it, title_words and
        # summary_words both span the same PROP-to-WHAT range and end up
        # as duplicate copies of the whole zone (title text polluted with
        # the entire summary paragraph, verified as a real bug here).
        summary_tops = sorted({w["top"] for w in side_words if w["text"] == "SUMMARY"})
        summary_start = summary_tops[0] if summary_tops else what_tops[0]
        title_words = [w for w in side_words if prop_tops[0] < w["top"] < summary_start]
        summary_words = [w for w in side_words if summary_start <= w["top"] < what_tops[0]]
        # Number sits alone on its own row directly under "PROP" in this
        # layout (verified: "PROP" then "2" on the next row, both left-
        # aligned at the same x as the title that follows) — the first
        # short numeric-only line in the title zone.
        number = None
        for line in _lines_from_words(title_words):
            if line.strip().isdigit():
                number = line.strip()
                break
        title_text = " ".join(
            line for line in _lines_from_words(title_words) if not line.strip().isdigit()
        )
        vote_means_words = [
            w for w in side_words if what_tops[0] <= w["top"] < vote_means_end
        ]
        parsed = _parse_side(number or "", title_text, summary_words, vote_means_words)
        if parsed:
            results.append(parsed)
    return results


def _pdf_url(year: int) -> str:
    return f"https://vig.cdn.sos.ca.gov/{year}/general/pdf/complete-vig.pdf"


def _to_measure(parsed: dict, election_date: str, source_url: str) -> dict:
    """One parsed proposition -> the combined raw+detail shape
    election_pipeline._upsert_measure expects. Unlike Vote Smart (a list
    call, then a per-item detail call), this PDF already carries every
    field in one pass, so the same dict is passed to _upsert_measure as
    both `raw` and `detail` — there's nothing a second fetch would add.
    """
    return {
        "id": f"CA-{election_date}-{parsed['number']}",
        "state": "CA",
        "election_date": election_date,
        "number": parsed["number"],
        "title": parsed["title"] or f"Proposition {parsed['number']}",
        "official_title": parsed["title"],
        "official_summary": parsed["official_summary"],
        "fiscal_impact": parsed["fiscal_impact"],
        "yes_means": parsed["yes_means"],
        "no_means": parsed["no_means"],
        "measure_type": None,
        "origin": parsed["origin"],
        "source_url": source_url,
    }


async def fetch_ca_measures(
    client: httpx.AsyncClient, db, year: int, election_date: str,
) -> list[dict] | None:
    """Every California statewide proposition for `year`'s November
    general, parsed directly from the state's own Voter Information Guide
    PDF — no API key, no Vote Smart.

    None on a fetch/parse failure (including the guide simply not being
    published yet — confirmed HTTP 403 for the 2026 general as of
    2026-08-06, same as every other not-yet-published source in this
    codebase); [] if the guide is real and parses but genuinely contains
    no Quick Reference Guide pages (verified real case: the 2026 primary
    guide, which has none — CA propositions are general-election-only).
    Same None-vs-[] discipline as ballot_measures.fetch_state_measures.
    """
    cache_key = f"ca-vig-{year}"
    cached = api_cache_get(db, "ca_vig", cache_key, max_age_hours=CACHE_TTL_HOURS)
    if cached is not None:
        return cached.get("measures")

    url = _pdf_url(year)
    try:
        response = await client.get(url, timeout=60.0)
        response.raise_for_status()
        pdf_bytes = response.content
    except httpx.HTTPStatusError as exc:
        logger.warning("CA VIG fetch failed for %d: HTTP %d", year, exc.response.status_code)
        return None
    except Exception:
        logger.exception("CA VIG fetch failed for %d", year)
        return None

    try:
        measures = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                for parsed in parse_quick_reference_page(page):
                    measures.append(_to_measure(parsed, election_date, url))
    except Exception:
        logger.exception("CA VIG parse failed for %d", year)
        return None

    api_cache_set(db, "ca_vig", cache_key, {"measures": measures}, normal_ttl_hours=CACHE_TTL_HOURS)
    return measures
