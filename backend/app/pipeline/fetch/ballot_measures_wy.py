"""Wyoming's ballot-measure PDF strategy — the Secretary of State's
"<year> Statewide Ballot Propositions" document (one of potentially many
per-state strategies; see ballot_measures_pdf.py for the shared
contract).

Discovered from the Elections Division's landing page
(sos.wyo.gov/Elections/Default.aspx links it directly as "<year>
Statewide Ballot Propositions"), not a fixed URL: the 2026 file sits at
/Elections/Docs/2026/2026_Statewide_Ballot_Propositions.pdf but the same
path for 2024 and 2022 404s, so the filename is not a proven convention.
Verified live 2026-09-28 against the real November 3, 2026 document:
one measure, Proposed Initiative Proposition Number One (a homeowner's
property-tax exemption), with its ballot language and estimated fiscal
impact.

Plain single-column text. Each proposition opens with an all-caps
heading ("PROPOSED INITIATIVE PROPOSITION NUMBER ONE"; a legislative
amendment would read "... CONSTITUTIONAL AMENDMENT ..."), then
"Following is the ballot language of ... as it will appear on the <year>
General Election ballot:", then the ballot language itself, an optional
"Estimated Fiscal Impact:" section, and the FOR / AGAINST choice. Only
text between those markers is kept. A heading whose block lacks the
FOR/AGAINST close raises, so the whole document reads as a failed
ingest rather than a partial list — and so does a document with more
FOR/AGAINST choices than recognised headings (a proposition under a
heading this module doesn't know). Pages after the propositions carry
the full text of each law, which is not parsed.

No "A YES vote means / A NO vote means" framing is published, so
yes_means/no_means stay null. The document doesn't name the fiscal
estimate's drafter, so it is attributed to its publisher.
"""

import re

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text

AUTHORITY = "Wyoming Secretary of State"

_HEADING_RE = re.compile(
    r"^PROPOSED\s+(?:INITIATIVE|REFERENDUM|CONSTITUTIONAL AMENDMENT|AMENDMENT)\b[A-Z0-9 .\-]*$",
)
# "Following is the ballot language of ... as it will appear on the" /
# "2026 General Election ballot:" — wraps across two lines in the real
# document, so the lead-in is matched on the joined pair.
_LEAD_IN_RE = re.compile(r"ballot language of .* as it will appear on the .*ballot:\s*$", re.IGNORECASE)
_FISCAL_RE = re.compile(r"^Estimated Fiscal Impact:?\s*$", re.IGNORECASE)


def _origin(heading: str) -> str | None:
    if "INITIATIVE" in heading:
        return "Wyoming voters (initiative petition)"
    if "REFERENDUM" in heading:
        return "Wyoming voters (referendum petition)"
    if "AMENDMENT" in heading:
        return "Wyoming Legislature"
    return None


def _parse_block(heading: str, lines: list[str]) -> dict:
    try:
        start = next(
            i for i, ln in enumerate(lines)
            if _LEAD_IN_RE.search(ln) or (i and _LEAD_IN_RE.search(f"{lines[i - 1]} {ln}"))
        ) + 1
        close = next(i for i in range(start, len(lines) - 1)
                     if lines[i].strip() == "FOR" and lines[i + 1].strip() == "AGAINST")
    except StopIteration:
        raise ValueError(f"WY {heading!r}: no ballot-language lead-in or FOR/AGAINST close") from None
    body = lines[start:close]
    fiscal_idx = next((i for i, ln in enumerate(body) if _FISCAL_RE.match(ln.strip())), None)
    summary_lines = body if fiscal_idx is None else body[:fiscal_idx]
    fiscal_lines = [] if fiscal_idx is None else body[fiscal_idx + 1:]
    summary = clean_text(" ".join(summary_lines))
    if not summary:
        raise ValueError(f"WY {heading!r}: empty ballot language")
    fiscal = clean_text(" ".join(fiscal_lines))
    return {
        "number": heading,
        "title": heading,
        "origin": _origin(heading),
        "official_summary": summary,
        "fiscal_impact": fiscal,
        "yes_means": None,
        "no_means": None,
        "title_authority": AUTHORITY,
        "fiscal_authority": AUTHORITY if fiscal else None,
    }


def parse_document(pages) -> list[dict]:
    lines: list[str] = []
    for page in pages:
        lines.extend((page.extract_text() or "").splitlines())
    headings = [i for i, ln in enumerate(lines) if _HEADING_RE.match(ln.strip())]
    results = []
    for n, idx in enumerate(headings):
        end = headings[n + 1] if n + 1 < len(headings) else len(lines)
        results.append(_parse_block(lines[idx].strip(), lines[idx + 1:end]))
    # Every question on the ballot closes with a FOR / AGAINST pair. More
    # pairs than parsed headings means a proposition under a heading this
    # module doesn't recognise — fail rather than leave it out.
    closes = sum(1 for i in range(len(lines) - 1)
                 if lines[i].strip() == "FOR" and lines[i + 1].strip() == "AGAINST")
    if closes != len(results):
        raise ValueError(f"WY: {closes} FOR/AGAINST choices but {len(results)} recognised propositions")
    return results
