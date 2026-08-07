"""Word-geometry and text-sanity helpers shared by every state's ballot-
measure PDF strategy (see ballot_measures_pdf.py for the registry these
strategies plug into).

State-agnostic by construction: everything here operates on pdfplumber's
raw `extract_words()` output (top/x0/x1/text dicts) or on plain strings,
never on a specific state's page layout. A state whose PDF needs
multi-column reconstruction (built and verified against California's
real Voter Information Guide — see ballot_measures_ca.py) uses
`split_by_row_gap`; a state with a plain single-column layout may not
need it at all. Either way, `looks_corrupted` and `clean_text` apply
unchanged, because they check properties of English prose, not of any
one page format.
"""

from collections import defaultdict


ROW_TOLERANCE = 3.0  # points


def rows(words: list[dict], tolerance: float = ROW_TOLERANCE) -> dict[int, list[dict]]:
    """Words clustered into visual rows by y-position PROXIMITY, not a
    fixed rounding grid. A naive round(top) (the original implementation)
    breaks on real documents where same-line words carry different exact
    `top` values — verified on Massachusetts's real Voter Information
    Guide, where a line mixing a label's font baseline with the body
    text's baseline differed by up to ~1.3pt, enough to round into two
    different integer buckets and scramble split_by_row_gap's YES/NO
    pairing (words that were genuinely on one row got assigned across
    two, so the largest-gap-per-row rule cut in the wrong place). 3pt
    tolerance clears that real jitter with room to spare while staying
    well under the ~13pt line-to-line spacing seen in both MA's and CA's
    documents, so distinct rows still don't merge.

    Returns a dict keyed by an arbitrary but ROW-ORDERED integer id (not
    a rounded top value) — callers already do `sorted(rows(words))` to
    get top-to-bottom order, which still works unchanged since keys are
    assigned in top-sorted order below.
    """
    ordered = sorted(words, key=lambda w: w["top"])
    bands: dict[int, list[dict]] = defaultdict(list)
    current_id = -1
    current_top = None
    for w in ordered:
        if current_top is None or w["top"] - current_top > tolerance:
            current_id += 1
        current_top = w["top"]
        bands[current_id].append(w)
    return bands


COLUMN_ROW_TOLERANCE = 7.0  # points


def find_column_boundary(
    words: list[dict], min_gap: float = 15.0, min_rows: int = 2,
    row_tolerance: float = COLUMN_ROW_TOLERANCE,
) -> float | None:
    """The x-coordinate of a genuine column gutter for `words`, or None
    if this block of words isn't really two columns at all.

    Two approaches were tried and rejected before this one, both on real
    failures against Massachusetts's Voter Information Guide:

    1. Pair same-row left/right fragments using rows()'s DEFAULT
       tolerance (3pt), then look for a large within-row gap. Broke
       because two columns' text baselines drift apart over a long
       paragraph — one genuinely-same-printed-line pair differed by
       4.5pt of `top`, wider than the default tolerance, so the two
       fragments landed in different "rows" and the gap was never seen.
    2. Skip row-pairing; look at the x0 distribution directly (single
       largest gap in sorted x0 values). Broke the other direction: a
       SHORT single-column paragraph (few words) has coincidental gaps
       in its x0 distribution just from sparse sampling, indistinguishable
       from a real gutter with too few data points.

    This uses row-pairing like (1), but with a LARGER tolerance
    (`row_tolerance`, separate from rows()'s own default) — safe because
    genuine distinct lines were verified to always be >=11pt apart
    within one column (measured directly on this document), comfortably
    above any same-row cross-column jitter seen (up to ~4.5pt). `min_gap`
    then rules out ordinary word-spacing as in approach (1); `min_rows`
    requires more than one row's agreement, robust to one stray pairing.
    Returns the MEDIAN x-midpoint of qualifying gaps.
    """
    candidates = []
    for row in rows(words, tolerance=row_tolerance).values():
        row = sorted(row, key=lambda w: w["x0"])
        if len(row) < 2:
            continue
        i = max(range(len(row) - 1), key=lambda i: row[i + 1]["x0"] - row[i]["x1"])
        gap = row[i + 1]["x0"] - row[i]["x1"]
        if gap >= min_gap:
            candidates.append((row[i]["x1"] + row[i + 1]["x0"]) / 2)
    if len(candidates) < min_rows:
        return None
    candidates.sort()
    return candidates[len(candidates) // 2]


def split_by_fixed_boundary(words: list[dict], boundary: float) -> tuple[list[dict], list[dict]]:
    """Bucket every word by a single x-threshold (from
    find_column_boundary), rather than re-deriving a split point per
    row — the right choice once a real gutter is established, since a
    per-row dynamic gap (split_by_row_gap) can still misfire on rows
    whose own largest gap happens to fall somewhere other than the true
    gutter (e.g. a wide space inside one column's own text)."""
    left = [w for w in words if w["x0"] < boundary]
    right = [w for w in words if w["x0"] >= boundary]
    return left, right


def split_by_row_gap(words: list[dict]) -> tuple[list[dict], list[dict]]:
    """Per visual row, cut at the single largest x-gap between adjacent
    words. Correct ONLY when each row has exactly two fragments — e.g.
    California's YES/NO and PRO/CON sub-zones once already isolated to
    one outer side (see ballot_measures_ca.py's module docstring)."""
    left: list[dict] = []
    right: list[dict] = []
    for top in sorted(rows(words)):
        row = sorted(rows(words)[top], key=lambda w: w["x0"])
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


def lines_from_words(words: list[dict]) -> list[str]:
    bands = rows(words)
    return [
        " ".join(w["text"] for w in sorted(bands[top], key=lambda w: w["x0"]))
        for top in sorted(bands)
    ]


def clean_text(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = " ".join(raw.split())
    return cleaned or None


_CONTAMINATION_NGRAM = 4  # words


def looks_corrupted(text: str) -> bool:
    """Two independent checks, calibrated against real failures on two
    different states' documents:

    1. Doesn't end in terminal punctuation. A real official sentence
       always does; a misclassified fragment trails off mid-thought with
       nothing after it to attribute (California's Prop 3).
    2. A 4+ word phrase repeats within the SAME string — content that got
       attributed to a side once correctly and then again (deliberately
       word-existence-based, not requiring an exact repeat, since a word
       can drop between the two occurrences — California's Prop 3 again:
       "no change in who can marry" / "no change in who marry").

    Deliberately NOT "do yes_means and no_means share a phrase with each
    other" — tried that first (California) and it false-positived on
    legitimately-correct text where YES and NO share a long common tail
    differing only by "could"/"could not". Sharing vocabulary across two
    independent, complete sentences is normal; either signal above,
    within ONE sentence, is not.

    The n-gram length is 4, not 3: a real false positive surfaced on
    Massachusetts's real Voter Information Guide, where a single
    legitimate sentence used "the number of" twice for two different
    license types ("increase the number of licenses... limit the number
    of...licenses...") — a common 3-word phrase repeating by ordinary
    coincidence, not corruption. 4 still catches California's real
    corruption case ("no change in who" repeats exactly) while clearing
    MA's false positive; verified against both real documents.
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
