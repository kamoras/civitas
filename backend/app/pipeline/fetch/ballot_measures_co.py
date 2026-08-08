"""Colorado's ballot-measure PDF strategy — parses the Legislative
Council's "Blue Book", "Quick Ballot Reference Guide" section (one of
potentially many per-state strategies in ballot_measures_pdf.py; see
that module and ballot_measures_ca.py for the shared contract and
geometry helpers this reuses).

Each measure: a large decorative letter/number badge (29pt+, e.g. "G",
"KK", "127" — filtered out by font size, not content, since the badge
alphabet isn't enumerable), a 22pt title, "Placed on the ballot by
<origin> • Passes with <threshold>", "Ballot Title" (label) + the
question text as official_summary, "What Your Vote Means" (label) +
YES/NO framing. No fiscal-impact field appears in this quick-reference
section (verified: even a real $39M tax measure has none here — Colorado
publishes that separately, elsewhere in the Blue Book) — left null,
same as any source that simply doesn't publish one.

KNOWN GAP, not guessed around: 2024's format (14/14 measures found, 12/14
with clean yes/no) is internally consistent, but 2022's is NOT — its
legislature-referred constitutional amendments use a materially
different sub-format with no "Ballot Title" label at all ("Amendment D
proposes amending the Colorado Constitution to: ..."), while citizen-
initiative measures that same year DO match 2024's shape. A future
year that resembles 2022 will have its odd-format measures safely
dropped (no official_summary found) rather than parsed wrong — this
module never ships a guess — but won't be complete. Extending to that
second sub-format needs a real 2022-shaped document to build against,
not a guess at what "probably" changed.
"""

import re

from app.pipeline.fetch.ballot_measure_pdf_geometry import (
    clean_text,
    find_column_boundary,
    lines_from_words,
    looks_corrupted,
    rows,
    split_by_fixed_boundary,
)

TITLE_AUTHORITY = "Colorado Legislative Council"

_TITLE_SIZE_MIN = 18.0
_BADGE_X0_MAX = 90.0  # excludes the decorative badge, which sits left of the title

_PLACED_RE = re.compile(r"^Placed on the ballot by (.+?)\s*•", re.IGNORECASE)
_YES_RE = re.compile(r'^(?:YES\s+)?A\s+[“"]?yes[”"]?\s+vote on (?:Amendment|Proposition)\s+\S+\s+(.*)$', re.IGNORECASE)
_NO_RE = re.compile(r'^(?:NO\s+)?A\s+[“"]?no[”"]?\s+vote on (?:Amendment|Proposition)\s+\S+\s+(.*)$', re.IGNORECASE)


def _row_words(page_rows: dict, rid: int) -> list[dict]:
    """This row's words, dropping a lone page-footer digit — real
    artifact, verified: a zone that runs to the bottom of the page picks
    up the printed page number otherwise, and its trailing digit then
    fails looks_corrupted's terminal-punctuation check."""
    row = page_rows[rid]
    if len(row) == 1 and row[0]["text"].isdigit():
        return []
    return row


def _zone_text(page_rows: dict, row_ids: list[int], start: int, end: int | None) -> str | None:
    words = [
        w for rid in row_ids if start < rid < (end if end is not None else row_ids[-1] + 1)
        for w in _row_words(page_rows, rid)
    ]
    return clean_text(" ".join(lines_from_words(words)))


def parse_page(page) -> list[dict]:
    """Every measure on one Quick Ballot Reference Guide page, or [] if
    this page isn't in that format."""
    words = page.extract_words(extra_attrs=["size"])
    page_rows = rows(words)
    row_ids = sorted(page_rows)

    placed_rows = [rid for rid in row_ids if any(w["text"] == "Placed" for w in page_rows[rid])]
    if not placed_rows:
        return []
    title_rows = [rid for rid in row_ids if any(w["text"] == "Title" for w in page_rows[rid])]
    what_rows = [rid for rid in row_ids if any(w["text"] == "Means" for w in page_rows[rid])]

    results = []
    for i, placed_row in enumerate(placed_rows):
        block_end = placed_rows[i + 1] if i + 1 < len(placed_rows) else None

        origin_line = " ".join(
            w["text"] for w in sorted(page_rows[placed_row], key=lambda w: w["x0"])
        )
        m = _PLACED_RE.match(origin_line)
        origin = clean_text(m.group(1)) if m else None

        block_start_rows = [
            rid for rid in row_ids
            if rid < placed_row and (i == 0 or rid > placed_rows[i - 1])
        ]
        # Large-font (title-sized+) words in this window that aren't the
        # title itself: only the previous measure's "YES"/"NO" vote-means
        # badges are big enough to land here too (verified: nothing else
        # in the previous measure's tail clears _TITLE_SIZE_MIN) — the
        # window otherwise correctly bounds just this measure's own
        # badge+title, so excluding those two literal tokens is enough.
        header_words = [
            w for rid in block_start_rows for w in page_rows[rid]
            if w["text"] not in ("YES", "NO")
        ]
        title_words = [
            w for w in header_words
            if w.get("size", 0) >= _TITLE_SIZE_MIN and w["x0"] >= _BADGE_X0_MAX
        ]
        badge_words = [
            w for w in header_words
            if w.get("size", 0) >= _TITLE_SIZE_MIN and w["x0"] < _BADGE_X0_MAX
        ]
        number = badge_words[0]["text"] if badge_words else None
        title = clean_text(" ".join(lines_from_words(title_words)))

        ballot_title_row = next((rid for rid in title_rows if placed_row < rid < (block_end or float("inf"))), None)
        what_row = next((rid for rid in what_rows if placed_row < rid < (block_end or float("inf"))), None)
        if ballot_title_row is None or what_row is None:
            continue

        official_summary = _zone_text(page_rows, row_ids, ballot_title_row, what_row)

        # The vote-means paragraph has no marker of its own end — it just
        # runs until the next measure's badge+title (or the page ends).
        # Same contamination shape as the title-detection window above:
        # the first row carrying a real title-sized word (excluding this
        # zone's own "YES"/"NO" badges) is where the NEXT measure starts.
        candidate_rows = [
            rid for rid in row_ids
            if what_row < rid < (block_end if block_end is not None else row_ids[-1] + 1)
        ]
        vote_end = next(
            (rid for rid in candidate_rows if any(
                w.get("size", 0) >= _TITLE_SIZE_MIN and w["text"] not in ("YES", "NO")
                for w in page_rows[rid]
            )),
            None,
        )
        vote_words = [
            w for rid in candidate_rows if vote_end is None or rid < vote_end
            for w in _row_words(page_rows, rid)
        ]
        boundary = find_column_boundary(vote_words)
        yes_means = no_means = None
        if boundary is not None:
            left, right = split_by_fixed_boundary(vote_words, boundary)
            yes_text = " ".join(lines_from_words(left)).strip()
            no_text = " ".join(lines_from_words(right)).strip()
            ym = _YES_RE.match(yes_text)
            nm = _NO_RE.match(no_text)
            yes_means = clean_text(ym.group(1)) if ym else None
            no_means = clean_text(nm.group(1)) if nm else None
            if (yes_means and looks_corrupted(yes_means)) or (no_means and looks_corrupted(no_means)):
                yes_means = no_means = None

        if not official_summary or not number:
            continue

        results.append({
            "number": number, "title": title, "origin": origin,
            "official_summary": official_summary, "fiscal_impact": None,
            "yes_means": yes_means, "no_means": no_means,
            "title_authority": TITLE_AUTHORITY, "fiscal_authority": None,
        })
    return results


def parse_document(pages) -> list[dict]:
    results = []
    for page in pages:
        results.extend(parse_page(page))
    return results
