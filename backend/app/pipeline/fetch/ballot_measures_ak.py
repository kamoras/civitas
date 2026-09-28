"""Alaska's ballot-measure PDF strategy — the Division of Elections'
own official sample ballot (one of potentially many per-state
strategies; see ballot_measures_pdf.py for the shared contract).

Why the sample ballot and not the Official Election Pamphlet: as of
2026-09-28 the pamphlet for the November 3, 2026 general was not yet
posted (the petitions page's "Ballot Measures on the Ballot" section
still showed 2024), while the Division's sample ballots — posted ~50
days before each election, one PDF per House district — were. The
sample ballot IS the ballot: every word this module keeps is what a
voter will read in the booth.

Statewide measures appear identically on every district's ballot, so
one district's PDF (House District 1, which lies wholly in Judicial
District 1 — hence the "HD1-JD1" filename) carries all of them. The
url_pattern was verified live against two real generals: 2026
(Ballot Measures No. 2 and No. 3) and 2024 (Ballot Measures No. 1
and No. 2). Measure numbers continue across a year's elections — the
August 2026 primary carried Ballot Measure No. 1 — so numbering is read
from each heading, never assumed to start at 1.

Layout (both real years): a three-column ballot page. A measure sits in
one column as a bold 12pt "Ballot Measure No.N" heading, a bold 12pt
ballot title ("24ESEG - An Act Restoring Political Party Primaries,
..."), a 9pt summary, and a closing question ("Should this initiative
become law?") above a YES/NO oval row. Neighbouring columns carry
judicial-retention questions whose lines share the same y-positions, so
each measure's words are cropped to its own column first. Column edges
are measured from the words themselves, not fixed x-positions: an edge
is an x where several words start flush and no word straddles it
(_column_starts — the real gutter is only ~5pt, about one space in the
12pt headings, so a minimum-gap rule can't find it). A measure
that doesn't close with a question followed by YES/NO inside its column
fails the whole document (read as ingest_failed), never guessed at and
never silently left out of an otherwise-published list.

No "A YES vote means / A NO vote means" framing is printed on the
ballot, so yes_means/no_means stay null; no fiscal statement is printed
either. Origin is taken only from the ballot's own closing question
("Should this initiative become law?"), never assumed — the legislature
can also refer amendments and bond propositions.
"""

import re

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text, rows

TITLE_AUTHORITY = "Alaska Lieutenant Governor (ballot title and summary, AS 15.45.180)"

_HEADING_RE = re.compile(r"^Ballot Measure No\.?\s*(\d+)$", re.IGNORECASE)
_QUESTION_RE = re.compile(r"^(Should|Shall)\b.*\?$")
_MIN_COLUMN_LINES = 2  # words sharing one left edge (with nothing straddling it) mark a column start


def _is_bold(word: dict) -> bool:
    return "bold" in (word.get("fontname") or "").lower()


def _column_starts(words: list[dict], top: float, bottom: float) -> list[float]:
    """x-positions where a column of text begins within [top, bottom]:
    at least _MIN_COLUMN_LINES words start there (a left-aligned text
    edge) and no word in the band straddles it (so it is a real gap, not
    a coincidental alignment inside running text). Measured from the
    words themselves — the real gutter here is only ~5pt wide, about the
    width of a space in the 12pt headings, so a fixed minimum gap width
    cannot tell the two apart but an aligned edge can."""
    band = [w for w in words if w["bottom"] >= top and w["top"] <= bottom]
    starts = sorted(w["x0"] for w in band)
    edges: list[float] = []
    for x in starts:
        if edges and x - edges[-1] < 1.0:
            continue
        aligned = sum(1 for w in band if abs(w["x0"] - x) < 1.0)
        if aligned < _MIN_COLUMN_LINES:
            continue
        if any(w["x0"] < x - 1.0 and w["x1"] > x + 0.5 for w in band):
            continue
        edges.append(x)
    return edges


def _lines(words: list[dict]) -> list[list[dict]]:
    bands = rows(words)
    return [sorted(bands[k], key=lambda w: w["x0"]) for k in sorted(bands)]


def _parse_block(number: str, lines: list[list[dict]]) -> dict | None:
    """One measure's lines (heading already removed): bold title lines,
    then body lines up to the closing question, then the YES/NO row."""
    title_parts: list[str] = []
    i = 0
    while i < len(lines) and all(_is_bold(w) for w in lines[i]):
        title_parts.append(" ".join(w["text"] for w in lines[i]))
        i += 1

    body: list[str] = []
    question = None
    for j in range(i, len(lines)):
        text = " ".join(w["text"] for w in lines[j])
        if text.replace(" ", "").upper() == "YESNO":
            break
        if all(_is_bold(w) for w in lines[j]):
            # A bold line after the summary started is the NEXT contest's
            # heading ("Supreme Court") — this measure never closed.
            return None
        body.append(text)
    else:
        return None  # never reached a YES/NO row inside this column

    # The closing question may wrap; it is the trailing run of body lines
    # from the last line starting "Should"/"Shall" to the line ending "?".
    for k in range(len(body) - 1, -1, -1):
        candidate = clean_text(" ".join(body[k:]))
        if candidate and _QUESTION_RE.match(candidate):
            question = candidate
            body = body[:k]
            break
    if question is None:
        return None

    title = clean_text(" ".join(title_parts))
    summary = clean_text(" ".join(body))
    if not title or not summary:
        return None
    origin = "Alaska voters (initiative petition)" if "initiative" in question.lower() else None
    return {
        "number": number,
        "title": title,
        # The bold title is the Lieutenant Governor's ballot title, printed
        # on the ballot itself.
        "official_title": title,
        "origin": origin,
        "official_summary": f"{summary} {question}",
        "fiscal_impact": None,
        "yes_means": None,
        "no_means": None,
        "title_authority": TITLE_AUTHORITY,
        "fiscal_authority": None,
    }


def parse_page_words(words: list[dict], page_width: float) -> list[dict]:
    """Every measure on one sample-ballot page, from pdfplumber
    extract_words(extra_attrs=["fontname", "size"]) output."""
    headings = []
    for line in _lines(words):
        # A heading shares its row with other columns' text, so match the
        # contiguous run starting at a bold "Ballot".
        for idx, w in enumerate(line):
            if w["text"] != "Ballot" or not _is_bold(w):
                continue
            for n in (3, 4):
                m = _HEADING_RE.match(" ".join(x["text"] for x in line[idx:idx + n]))
                if m:
                    headings.append((m.group(1), line[idx], line[idx + n - 1]))
                    break
    if not headings:
        # "Ballot Measure" printed somewhere but no heading recognised is a
        # layout we don't know, not a ballot without measures.
        page_text = " ".join(w["text"] for w in words)
        if re.search(r"Ballot\s+Measure", page_text, re.IGNORECASE):
            raise ValueError("AK sample ballot mentions a Ballot Measure but no heading was recognised")
        return []
    page_bottom = max(w["bottom"] for w in words)
    results = []
    for number, first, last in headings:
        edges = _column_starts(words, first["top"], page_bottom)
        left = max((e for e in edges if e <= first["x0"]), default=0.0) - 1.0
        right = min((e for e in edges if e > last["x1"]), default=page_width) - 0.5
        column = [
            w for w in words
            if w["x0"] >= left and w["x1"] <= right and w["top"] > first["bottom"] - 1
        ]
        # Stop at the next measure heading in this same column.
        next_tops = [f["top"] for _, f, _ in headings if f["top"] > first["top"] and left <= f["x0"] < right]
        if next_tops:
            column = [w for w in column if w["top"] < min(next_tops) - 1]
        parsed = _parse_block(number, _lines(column))
        if parsed is None:
            # A heading we recognised but whose column doesn't close the
            # expected way: fail the whole document (ingest_failed) rather
            # than publish a list that silently omits a measure.
            raise ValueError(f"AK Ballot Measure No. {number}: unexpected layout")
        results.append(parsed)
    return results


def parse_document(pages) -> list[dict]:
    """Every measure on the sample ballot, [] for a ballot that carries
    none — but only a ballot this reader can actually see: the document's
    own text must say it is the "Official Ballot" for a "General
    Election". A scan with no text layer, or any other document at the
    address, has no words to find a measure in, and [] from it would read
    as a checked "none"; it raises instead (ingest_failed)."""
    results: list[dict] = []
    seen: set[str] = set()
    header_seen = False
    for page in pages:
        words = page.extract_words(extra_attrs=["fontname", "size"])
        text = " ".join(w["text"] for w in words)
        if re.search(r"Official\s+Ballot", text) and re.search(r"General\s+Election", text):
            header_seen = True
        for parsed in parse_page_words(words, float(page.width)):
            if parsed["number"] not in seen:
                seen.add(parsed["number"])
                results.append(parsed)
    if not header_seen:
        raise ValueError("AK: document text doesn't identify a general-election official ballot")
    return results
