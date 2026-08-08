"""Massachusetts's ballot-measure PDF strategy — parses the Secretary of
the Commonwealth's own "Information For Voters" guide PDF
(sec.state.ma.us). One of potentially many per-state strategies
registered in ballot_measures_pdf.py's generic fetch/cache/upsert
pipeline; this module owns only the page-parsing logic specific to
Massachusetts's document.

WHY THIS EXISTS: same reason as ballot_measures_ca.py — replacing Vote
Smart with the state's own real document, state by state. Massachusetts
publishes a "Question N: <origin>" section per ballot question with
official SUMMARY, "WHAT YOUR VOTE WILL DO" (A YES VOTE.../A NO VOTE...
framing in the state's own words), and "STATEMENT OF FISCAL
CONSEQUENCES" — the same fields CA's strategy extracts, in a genuinely
different document layout. Verified against two real elections' guides
(2024 general, 56 pages, 5 real questions; 2022 general, 5 questions
across the guide) at a STABLE URL pattern
(.../IFV_{year}.pdf) — the same two-election bar
ballot_measures_ca.py's URL pattern was held to.

THE HARD PART, different from California: this page format is NOT one
fixed two-level nested column layout. It has THREE separate challenges,
each found and fixed against the real document, not assumed:

1. QUESTIONS SPAN A VARIABLE NUMBER OF PAGES. A short question's summary,
   vote-means, and fiscal-impact sections fit on one page (2024's
   Question 1). A long question's summary alone can fill an entire page,
   pushing "WHAT YOUR VOTE WILL DO" and fiscal impact onto the NEXT page
   (2024's Question 3, verified: summary on page 12, everything else on
   page 13, with the "QUESTION 3: ..." header repeated at the top of
   page 13 too). A strategy that only looked at one page at a time could
   never reunite those — this one scans the whole document, groups pages
   by the (repeated) question-number header, and gathers whichever zone
   markers appear on whichever page in that group.

2. SAME-VISUAL-LINE WORDS CAN HAVE MEASURABLY DIFFERENT "top" VALUES.
   Two content columns' text baselines drift apart over a long paragraph
   — verified directly: one genuinely-same-printed-line pair of words
   differed by 4.5pt of pdfplumber's reported `top`, while some
   genuinely DIFFERENT lines elsewhere in the same document are only
   ~4-6pt apart. This ruled out a fixed-tolerance row-clustering
   approach at any single tolerance value (see
   ballot_measure_pdf_geometry.find_column_boundary's docstring for the
   two rejected approaches and why). The fix that held up: pair
   same-row fragments using a LARGER tolerance than the module's default
   (verified safe because genuine distinct lines were measured at >=11pt
   apart within one column on this real document), then require the
   resulting gap to be large AND to recur across more than one row
   before trusting it as a real column boundary.

3. A FIELD CAN BE SINGLE-COLUMN OR GENUINELY TWO-COLUMN DEPENDING ON
   LENGTH. A short summary (2024 Q1, 98 characters) never needs a second
   column; a long one (2024 Q3, 4046 characters) does, with the FIRST
   half of the sentence entirely in the left column and the SECOND half
   entirely in the right (read left-column-fully, then right-column-
   fully — reconstructs the sentence correctly, unlike the YES/NO zone
   where left and right are two SEPARATE fields). Each zone is checked
   independently (find_column_boundary returns None for a single-column
   block) rather than assuming either shape.

Verified end to end against all 5 real 2024 questions and all 3 real
2022 questions: every summary, fiscal-impact, yes_means, and no_means
matches the source document exactly, word for word (spot-checked by hand
against the raw extracted text before this module existed) — notably
better yes/no completion (8/8) than California's (4/10), because this
format's baseline-drift issue, once fixed, doesn't have CA's remaining
zero-visual-gap wrapped-line ambiguity.
"""

import logging
import re

from app.pipeline.fetch.ballot_measure_pdf_geometry import (
    clean_text,
    find_column_boundary,
    lines_from_words,
    looks_corrupted,
    rows,
    split_by_fixed_boundary,
)

logger = logging.getLogger(__name__)

TITLE_AUTHORITY = "Massachusetts Attorney General"
FISCAL_AUTHORITY = "Massachusetts Executive Office of Administration and Finance"

# Body text starts here; anything to the left is the page's marginal
# annotation column ("As required by law, summaries are written by the
# State Attorney General.") — real content on this document, but not
# ballot content, so it's dropped rather than swept into a field.
_BODY_X0_MIN = 105.0
# Title text renders at 24pt bold-italic on the real document; everything
# else in this zone (the "Do you approve..." intro sentence, at 7pt) is
# smaller. 18 clears the gap with margin either direction.
_TITLE_SIZE_MIN = 18.0

_ZONE_MARKERS = ("SUMMARY", "WHAT", "STATEMENT", "ARGUMENTS")

_QUESTION_RE = re.compile(r"^QUESTION\s+(\d+):\s*(.*)$")
_YES_VOTE_RE = re.compile(r"^A YES VOTE\s+(.*)$", re.IGNORECASE)
_NO_VOTE_RE = re.compile(r"^A NO VOTE\s+(.*)$", re.IGNORECASE)


def _page_markers(page_rows: dict, row_ids_sorted: list[int]) -> dict:
    """{marker: row_id} for the FIRST occurrence of each zone marker on
    this page, plus "question": (row_id, number, origin) if the repeated
    "QUESTION N: <origin>" header appears here."""
    found: dict = {}
    for rid in row_ids_sorted:
        row_words = page_rows[rid]
        texts = {w["text"] for w in row_words}
        for marker in _ZONE_MARKERS:
            if marker in texts and marker not in found:
                found[marker] = rid
        if "QUESTION" in texts and "question" not in found:
            line = " ".join(w["text"] for w in sorted(row_words, key=lambda w: w["x0"]))
            m = _QUESTION_RE.match(line)
            if m:
                found["question"] = (rid, m.group(1), clean_text(m.group(2)))
    return found


def _zone_words(page_rows: dict, row_ids_sorted: list[int], start_row: int, end_row: int | None) -> list[dict]:
    """Body words (x0 >= _BODY_X0_MIN) in rows [start_row, end_row) —
    dropping any row that's a single standalone digit, a page-footer
    number caught by a zone that runs to the bottom of the page (real
    artifact, verified: 2024 Q3/Q4's summaries each end their own page)."""
    end = end_row if end_row is not None else (row_ids_sorted[-1] + 1)
    words = []
    for rid in row_ids_sorted:
        if not (start_row <= rid < end):
            continue
        row = [w for w in page_rows[rid] if w["x0"] >= _BODY_X0_MIN]
        if len(row) == 1 and row[0]["text"].isdigit():
            continue
        words.extend(row)
    return words


def _prose(words: list[dict]) -> str | None:
    """One field's worth of continuous prose: left-column-fully then
    right-column-fully if this block is genuinely two columns (a long
    summary/fiscal paragraph), natural row order if it's one (a short
    one) — find_column_boundary itself decides which, per block, since
    the same field is single-column when short and two-column when long
    (verified both shapes for real on this document)."""
    boundary = find_column_boundary(words)
    if boundary is None:
        return clean_text(" ".join(lines_from_words(words)))
    left, right = split_by_fixed_boundary(words, boundary)
    return clean_text(" ".join(lines_from_words(left)) + " " + " ".join(lines_from_words(right)))


def _yes_no(words: list[dict]) -> tuple[str | None, str | None]:
    """The YES and NO columns as two SEPARATE strings (not concatenated
    prose, unlike _prose) — None, None if this block isn't really two
    columns (shouldn't happen for a real WHAT YOUR VOTE WILL DO zone,
    but never guess which side is which without one)."""
    boundary = find_column_boundary(words)
    if boundary is None:
        return None, None
    left, right = split_by_fixed_boundary(words, boundary)
    return " ".join(lines_from_words(left)), " ".join(lines_from_words(right))


def parse_information_for_voters(pages) -> list[dict]:
    """Every ballot question across the whole Information For Voters PDF.
    Registered under strategy key "ma_information_for_voters" in
    ballot_measure_pdf_sources.json — see ballot_measures_pdf.py."""
    page_info = []
    for page in pages:
        words = page.extract_words(extra_attrs=["size"])
        page_rows = rows(words)
        row_ids_sorted = sorted(page_rows)
        markers = _page_markers(page_rows, row_ids_sorted) if row_ids_sorted else {}
        page_info.append((page_rows, row_ids_sorted, markers))

    # Group page indices by question number, from the repeated
    # "QUESTION N:" header — a question's fields can span more than one
    # page (see module docstring, challenge 1), so gather every page
    # that carries this question's header before extracting anything.
    groups: dict[str, tuple[str | None, list[int]]] = {}
    for i, (_, _, markers) in enumerate(page_info):
        q = markers.get("question")
        if q is None:
            continue
        _, number, origin = q
        if number not in groups:
            groups[number] = (origin, [])
        groups[number][1].append(i)

    results = []
    for number, (origin, page_indices) in groups.items():
        title = official_summary = fiscal_impact = yes_means = no_means = None
        for i in page_indices:
            page_rows, row_ids_sorted, markers = page_info[i]

            if title is None and "question" in markers:
                qrow = markers["question"][0]
                summary_row = markers.get("SUMMARY")
                title_words = [
                    w for rid in row_ids_sorted
                    if rid > qrow and (summary_row is None or rid < summary_row)
                    for w in page_rows[rid]
                    if w.get("size", 0) >= _TITLE_SIZE_MIN and w["x0"] >= _BODY_X0_MIN
                ]
                if title_words:
                    title = clean_text(" ".join(lines_from_words(title_words)))

            if "SUMMARY" in markers:
                zwords = _zone_words(page_rows, row_ids_sorted, markers["SUMMARY"], markers.get("WHAT"))
                official_summary = _prose(zwords)

            if "STATEMENT" in markers:
                zwords = _zone_words(page_rows, row_ids_sorted, markers["STATEMENT"], markers.get("ARGUMENTS"))
                fiscal_impact = _prose(zwords)

            if "WHAT" in markers:
                zwords = _zone_words(page_rows, row_ids_sorted, markers["WHAT"], markers.get("STATEMENT"))
                yes_text, no_text = _yes_no(zwords)
                if yes_text:
                    ym = _YES_VOTE_RE.match(yes_text.strip())
                    yes_means = clean_text(ym.group(1)) if ym else None
                if no_text:
                    nm = _NO_VOTE_RE.match(no_text.strip())
                    no_means = clean_text(nm.group(1)) if nm else None

        if (yes_means and looks_corrupted(yes_means)) or (no_means and looks_corrupted(no_means)):
            logger.warning(
                "MA Question %s: yes_means/no_means text looks corrupted — "
                "dropping both rather than risk shipping scrambled text", number,
            )
            yes_means = no_means = None
        # looks_corrupted is NOT applied to official_summary/fiscal_impact:
        # tried it, and it false-positived on real correct text (2024 Q2's
        # summary legitimately says "...to receive a high school diploma."
        # twice, once per sentence describing the current and proposed
        # rule) — a 5+ word repeat that's normal in longer prose, unlike
        # the short yes/no sentences this check was calibrated against.
        if not official_summary:
            continue

        results.append({
            "number": number, "title": title, "origin": origin,
            "official_summary": official_summary, "fiscal_impact": fiscal_impact,
            "yes_means": yes_means, "no_means": no_means,
            "title_authority": TITLE_AUTHORITY,
            "fiscal_authority": FISCAL_AUTHORITY,
        })
    return results
