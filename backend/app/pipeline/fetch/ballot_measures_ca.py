"""California's ballot-measure PDF strategy — parses the state's own
official Voter Information Guide PDF (vig.cdn.sos.ca.gov). One of
potentially many per-state strategies registered in
ballot_measures_pdf.py's generic fetch/cache/upsert pipeline; this module
owns only the page-parsing logic specific to California's document.

WHY THIS EXISTS: the user asked to stop depending on Vote Smart's
approval-gated signup and get the same data independently, state by
state. Checked directly whether California publishes something better
than a per-county composite ballot: it does — the Secretary of State's
own "Quick Reference Guide" section is a purpose-built, state-level
ballot-measure-only summary (title, origin, official summary, fiscal
impact, and — critically — explicit "WHAT YOUR VOTE MEANS: YES.../NO..."
framing in the state's own words, not derived). Two propositions per
page, consistent format across election cycles (verified against real
PDFs from two different elections: 2026 primary, 36.7MB / 64 pages, and
2024 general, 5MB / 144 pages).

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
   side) to separate YES from NO, and separately PRO from CON (see
   ballot_measure_pdf_geometry.split_by_row_gap, shared with any other
   state whose layout turns out to need the same technique).

Verified end to end against Proposition 2 in the real 2024 guide: the
reconstructed YES/NO text matches the source exactly, word for word.
"""

import logging
import re

from app.pipeline.fetch.ballot_measure_pdf_geometry import (
    clean_text,
    lines_from_words,
    looks_corrupted,
    rows,
    split_by_row_gap,
)

logger = logging.getLogger(__name__)

TITLE_AUTHORITY = "California Attorney General"
FISCAL_AUTHORITY = "California Legislative Analyst's Office"


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
    for top, row in rows(summary_words).items():
        row = sorted(row, key=lambda w: w["x0"])
        if len(row) < 2:
            continue
        i = max(range(len(row) - 1), key=lambda i: row[i + 1]["x0"] - row[i]["x1"])
        gaps.append((row[i]["x1"] + row[i + 1]["x0"]) / 2)
    if not gaps:
        return None
    gaps.sort()
    return gaps[len(gaps) // 2]  # median — robust to one or two odd rows


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
    summary_text = " ".join(lines_from_words(summary_words))
    origin_match = _ORIGIN_RE.search(summary_text)
    origin = clean_text(origin_match.group(1)) if origin_match else None

    # "SUMMARY <origin line> <official summary...> Fiscal Impact: <...>
    # Supporters: <...> Opponents: <...>" — split on the fixed markers
    # rather than guessing where prose ends, so a shift in the state's own
    # wording costs us a field, not a garbled blend of two fields.
    body = summary_text
    if origin_match:
        body = body[origin_match.end():]
    fiscal_split = _FISCAL_SPLIT_RE.split(body, maxsplit=1)
    official_summary = clean_text(fiscal_split[0])
    fiscal_impact = None
    if len(fiscal_split) > 1:
        supporters_split = _SUPPORTERS_SPLIT_RE.split(fiscal_split[1], maxsplit=1)
        fiscal_impact = clean_text(supporters_split[0])

    yes_words, no_words = split_by_row_gap(vote_means_words)
    yes_text = " ".join(lines_from_words(yes_words))
    no_text = " ".join(lines_from_words(no_words))
    yes_match = _YES_MEANS_RE.search(yes_text)
    no_match = _NO_MEANS_RE.search(no_text)
    yes_means = clean_text(yes_match.group(1)) if yes_match else None
    no_means = clean_text(no_match.group(1)) if no_match else None
    if (yes_means and looks_corrupted(yes_means)) or (no_means and looks_corrupted(no_means)):
        # See ballot_measure_pdf_geometry.looks_corrupted's docstring —
        # both checks there are calibrated against this exact real
        # failure (Prop 3's yes_means/no_means on a wrapped line where
        # the row-gap split had nothing to find). Drop both rather than
        # ship one that might be scrambled.
        logger.warning(
            "CA Prop %s: yes_means/no_means text looks corrupted — "
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
        "title": clean_text(title),
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
    this is a per-page result, not a whole-document verdict). Registered
    under strategy key "ca_quick_reference" in
    ballot_measure_pdf_sources.json — see ballot_measures_pdf.py."""
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
        for line in lines_from_words(title_words):
            if line.strip().isdigit():
                number = line.strip()
                break
        title_text = " ".join(
            line for line in lines_from_words(title_words) if not line.strip().isdigit()
        )
        vote_means_words = [
            w for w in side_words if what_tops[0] <= w["top"] < vote_means_end
        ]
        parsed = _parse_side(number or "", title_text, summary_words, vote_means_words)
        if parsed:
            results.append(parsed)
    return results


def parse_document(pages) -> list[dict]:
    """Every proposition across the whole PDF — registered under strategy
    key "ca_quick_reference" in ballot_measure_pdf_sources.json (see
    ballot_measures_pdf.py). A thin per-page loop suffices here: unlike
    Massachusetts, California's format never splits one proposition's
    fields across pages (verified against both real documents checked)."""
    results = []
    for page in pages:
        results.extend(parse_quick_reference_page(page))
    return results
