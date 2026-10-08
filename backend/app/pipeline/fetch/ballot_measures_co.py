"""Colorado's ballot-measure PDF strategy — parses the Legislative
Council's "Blue Book", "Quick Ballot Reference Guide" section (one of
potentially many per-state strategies in ballot_measures_pdf.py; see
that module and ballot_measure_pdf_geometry.py for the shared contract
and geometry helpers this reuses).

Each measure: a large decorative letter/number badge (29pt+, e.g. "G",
"KK", "127" — filtered out by font size, not content, since the badge
alphabet isn't enumerable), a 22pt title, "Placed on the ballot by
<origin> • Passes with <threshold>", "Ballot Title" (label) + the
question text as official_summary, "What Your Vote Means" (label) +
YES/NO framing. No fiscal-impact field appears in this quick-reference
section (verified: even a real $39M tax measure has none here — Colorado
publishes that separately, elsewhere in the Blue Book) — left null,
same as any source that simply doesn't publish one.

Only the Quick Ballot Reference Guide pages are read: those whose
header line says so. The rest of the Blue Book repeats each measure in
its analysis section, which also opens with "Placed on the ballot by"
but follows it with "Amendment 81 proposes amending the Colorado
Constitution to:" instead of a Ballot Title. Reading every page made
every whole Blue Book fail — 2022, 2024 and 2026 alike, measured
2026-10-01 — because those analysis sections read as malformed measures.
(The "2022 sub-format" this module used to describe was that: analysis
pages, not a second guide format.) The same analysis sections are the
completeness check: one per measure, so the guide must list as many
measures as the document has analyses (14 = 14 in 2024 and 2026, 11 = 11
in 2022), or the document is refused rather than published short.

A measure on a guide page whose Ballot Title / What Your Vote Means
sections can't be found still raises rather than being dropped. 2022's
guide still fails (a measure whose number badge isn't found); only the
current cycle is ever ingested.

Drafters: the 22pt title is the Blue Book's own headline (the
Legislative Council's), not a ballot title, so no official_title is
claimed. What the Blue Book quotes under "Ballot Title" (stored as
official_summary) was written by the General Assembly for a referred
measure and by the Title Board for a citizen initiative, read from the
measure's own "Placed on the ballot by ..." line.
"""

import re
import statistics

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

_GUIDE_HEADER = "Quick Ballot Reference Guide"

_PLACED_RE = re.compile(r"^Placed on the ballot by (.+?)\s*•", re.IGNORECASE)
# The whole sentence, "A “yes” vote on Amendment 81 requires ..." — only
# the column's decorative YES/NO badge is dropped. It used to keep just
# what followed the measure number, so every Colorado yes/no line was
# stored as a lowercase fragment of the Blue Book's sentence (§7).
_YES_RE = re.compile(r'^(?:YES\s+)?(A\s+[“"]?yes[”"]?\s+vote on (?:Amendment|Proposition)\s+\S+\s+.*)$', re.IGNORECASE)
_NO_RE = re.compile(r'^(?:NO\s+)?(A\s+[“"]?no[”"]?\s+vote on (?:Amendment|Proposition)\s+\S+\s+.*)$', re.IGNORECASE)


def _row_words(page_rows: dict, rid: int) -> list[dict]:
    """This row's words, dropping a lone page-footer digit — real
    artifact, verified: a zone that runs to the bottom of the page picks
    up the printed page number otherwise, and its trailing digit then
    fails looks_corrupted's terminal-punctuation check."""
    row = page_rows[rid]
    if len(row) == 1 and row[0]["text"].isdigit():
        return []
    return row


# A table inside a ballot title (Amendment 87, 2026: the estimated change
# in tax owed by income category) puts its cells 11pt+ apart; words within
# a cell, like body text, sit ~2.7pt apart.
_CELL_GAP = 6.0
_EDGE_TOLERANCE = 1.0
# Header lines are closer together than table rows: 13pt against 16pt in
# Amendment 87's table, so under 90% of the row pitch is a wrapped line.
_HEADER_LEADING = 0.9


def _cells(row: list[dict]) -> list[list[dict]]:
    """One row's words split into cells at gaps wider than _CELL_GAP."""
    cells: list[list[dict]] = []
    for w in sorted(row, key=lambda w: w["x0"]):
        if cells and w["x0"] - cells[-1][-1]["x1"] <= _CELL_GAP:
            cells[-1].append(w)
        else:
            cells.append([w])
    return cells


def _text(words: list[dict]) -> str:
    return " ".join(w["text"] for w in words)


def _lines(words: list[dict]) -> list[str]:
    """The zone's lines top to bottom, with a table's wrapped column
    headers read column by column.

    Read row by row, a header whose cells wrap over three lines comes out
    interleaved — "Current Average Proposed Change in Average Income
    Income Tax Proposed Average ..." for the Blue Book's "Current Average
    Income Tax Owed" | "Proposed Average Income Tax Owed" | ... The table's
    data rows (two or more consecutive rows of cells ending at the same
    right edges, as numbers right-aligned in columns do) give the
    columns; the closely spaced rows above them whose every cell starts at
    the first column or ends on a column's edge are its header, and each column's
    header words are joined top to bottom. Only the order of those words
    changes; none is added or dropped."""
    bands = rows(words)
    ids = sorted(bands)
    cells = {i: _cells(bands[i]) for i in ids}
    lines = {i: _text(sorted(bands[i], key=lambda w: w["x0"])) for i in ids}

    def edges(i: int) -> list[float]:
        return [c[-1]["x1"] for c in cells[i][1:]]

    def same(a: list[float], b: list[float]) -> bool:
        return len(a) == len(b) and all(abs(x - y) <= _EDGE_TOLERANCE for x, y in zip(a, b))

    first = next(
        (k for k in range(len(ids) - 1)
         if len(cells[ids[k]]) >= 3 and same(edges(ids[k]), edges(ids[k + 1]))),
        None,
    )
    if first is None:
        return [lines[i] for i in ids]
    col_edges = edges(ids[first])
    left = cells[ids[first]][0][0]["x0"]

    def column(cell: list[dict]) -> int | None:
        if abs(cell[0]["x0"] - left) <= _EDGE_TOLERANCE:
            return 0
        return next((n + 1 for n, e in enumerate(col_edges) if abs(cell[-1]["x1"] - e) <= _EDGE_TOLERANCE), None)

    def top(k: int) -> float:
        return min(w["top"] for w in bands[ids[k]])

    # The aligned run: the data rows and, above them, the header rows. A
    # header's last line can fill every column like a data row (Amendment
    # 87's "Income Categories | Owed | Income Tax Owed | + or ‑"), so what
    # separates them is spacing: header lines sit at the font's leading
    # (13pt there), table rows at a wider pitch (16pt).
    start = first
    while start > 0 and all(column(c) is not None for c in cells[ids[start - 1]]):
        start -= 1
    end = first
    while end + 1 < len(ids) and same(edges(ids[end + 1]), col_edges):
        end += 1
    pitch = statistics.median(top(k + 1) - top(k) for k in range(first, end))
    last = start
    while last < end and top(last + 1) - top(last) < _HEADER_LEADING * pitch:
        last += 1
    header = ids[start:last + 1]
    if len(header) < 2:  # a one-line header already reads in order
        return [lines[i] for i in ids]
    columns: list[list[str]] = [[] for _ in range(len(col_edges) + 1)]
    for i in header:
        for c in cells[i]:
            columns[column(c)].append(_text(c))
    merged = " ".join(" ".join(col) for col in columns if col)
    return [lines[i] for i in ids if i < header[0]] + [merged] + [lines[i] for i in ids if i > header[-1]]


def _zone_text(page_rows: dict, row_ids: list[int], start: int, end: int | None) -> str | None:
    words = [
        w for rid in row_ids if start < rid < (end if end is not None else row_ids[-1] + 1)
        for w in _row_words(page_rows, rid)
    ]
    return clean_text(" ".join(_lines(words)))


def _placed_rows(page_rows: dict, row_ids: list[int]) -> list[int]:
    """Rows that open a measure: "Placed on the ballot by ..." as the row's
    first word (a sentence that merely mentions "Placed" doesn't count)."""
    return [
        rid for rid in row_ids
        if page_rows[rid] and min(page_rows[rid], key=lambda w: w["x0"])["text"] == "Placed"
    ]


def _is_guide_page(page_rows: dict, row_ids: list[int]) -> bool:
    if not row_ids:
        return False
    header = " ".join(w["text"] for w in sorted(page_rows[row_ids[0]], key=lambda w: w["x0"]))
    return _GUIDE_HEADER in header


def _vote_boundary(vote_words: list[dict]) -> float | None:
    """Where the NO column starts: the large "NO" badge the guide prints at
    the head of it. Colorado's gutter can be narrower than the generic
    gap test's 15pt minimum where the YES text runs long (Amendment 87,
    2026: 11-14pt), but the badge is the layout's own marker. The gap
    test is the fallback for a guide without one."""
    badge = next((w for w in vote_words if w["text"] == "NO" and w.get("size", 0) >= _TITLE_SIZE_MIN), None)
    if badge is not None:
        return badge["x0"] - 0.5
    return find_column_boundary(vote_words)


def parse_page(page) -> list[dict]:
    """Every measure on one Quick Ballot Reference Guide page, or [] if
    this page isn't in that format."""
    words = page.extract_words(extra_attrs=["size"])
    page_rows = rows(words)
    row_ids = sorted(page_rows)

    placed_rows = _placed_rows(page_rows, row_ids)
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
            # A measure ("Placed on the ballot by ...") whose Ballot Title or
            # What Your Vote Means section this reader can't find — the
            # 2022 sub-format for referred amendments is one. Dropping it
            # would publish the Blue Book one measure short, as "covered".
            raise ValueError("CO: a measure without its Ballot Title / What Your Vote Means sections")

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
        boundary = _vote_boundary(vote_words)
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
            raise ValueError(f"CO measure {number!r}: no ballot title text or no number")

        # `title` is the Blue Book's own headline for the measure (the
        # Legislative Council's), not the ballot's; the ballot title is
        # what the Blue Book quotes under "Ballot Title", stored as
        # official_summary — so its drafter is who wrote THAT text.
        results.append({
            "number": number, "title": title, "origin": origin,
            "official_summary": official_summary, "fiscal_impact": None,
            "yes_means": yes_means, "no_means": no_means,
            "title_authority": _ballot_title_drafter(origin), "fiscal_authority": None,
        })
    return results


def _ballot_title_drafter(origin: str | None) -> str | None:
    """Who wrote the quoted ballot title, from the Blue Book's own
    "Placed on the ballot by ..." line: the General Assembly writes a
    referred measure's ballot title into the referring bill or resolution;
    the Title Board (C.R.S. 1-40-106) sets a citizen initiative's. Neither
    is the Legislative Council, which only publishes the Blue Book."""
    lowered = (origin or "").lower()
    if "legislature" in lowered or "general assembly" in lowered:
        return "Colorado General Assembly"
    if "initiative" in lowered or "citizen" in lowered:
        return "Colorado Title Board"
    return None


def parse_document(pages) -> list[dict]:
    results = []
    analyses = 0
    for page in pages:
        page_rows = rows(page.extract_words())
        row_ids = sorted(page_rows)
        if _is_guide_page(page_rows, row_ids):
            results.extend(parse_page(page))
        else:
            analyses += len(_placed_rows(page_rows, row_ids))
    if not results:
        # Every general-election Blue Book carries measures; one this
        # reader finds none in is a document it can't read, never "none".
        raise ValueError("CO Blue Book: no measure found")
    if analyses != len(results):
        # Each measure has one analysis section; a guide that lists fewer
        # (or more) is one this reader misread, never a complete ballot.
        raise ValueError(f"CO Blue Book: {len(results)} guide measures but {analyses} analysis sections")
    return results
