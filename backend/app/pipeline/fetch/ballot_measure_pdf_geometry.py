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


def rows(words: list[dict]) -> dict[int, list[dict]]:
    """Words bucketed by rounded y-position ("top") into visual rows."""
    bands: dict[int, list[dict]] = defaultdict(list)
    for w in words:
        bands[round(w["top"])].append(w)
    return bands


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


_CONTAMINATION_NGRAM = 3  # words


def looks_corrupted(text: str) -> bool:
    """Two independent checks, calibrated against a real failure (see
    ballot_measures_ca.py's Prop 3: a row-gap misclassification on a
    wrapped line where two sentences render with essentially zero visual
    gap between them):

    1. Doesn't end in terminal punctuation. A real official sentence
       always does; the misclassified text trailed off mid-thought with
       nothing after it to attribute.
    2. A 3+ word phrase repeats within the SAME string — content that got
       attributed to a side once correctly and then again (deliberately
       word-existence-based, not requiring an exact repeat, since a word
       can drop between the two occurrences).

    Deliberately NOT "do yes_means and no_means share a phrase with each
    other" — tried that first (California) and it false-positived on
    legitimately-correct text where YES and NO share a long common tail
    differing only by "could"/"could not". Sharing vocabulary across two
    independent, complete sentences is normal; either signal above,
    within ONE sentence, is not.
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
