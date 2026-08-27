"""Louisiana's ballot-measure PDF strategy — parses the Secretary of
State's "Proposed Constitutional Amendments" guide (one of potentially
many per-state strategies in ballot_measures_pdf.py; see that module for
the shared contract).

Plain single-column text — no multi-column geometry needed at all,
unlike CA/MA/CO. Each amendment: "Proposed Amendment No. N", then
"Act NNN (YYYY Regular Session) - Proposing to ..." (the legal
description), then "Do you support an amendment to ...?" (the actual
ballot question, verbatim). All Louisiana constitutional amendments are
legislature-referred — the state constitution has no citizen-initiative
process for amendments, so origin is fixed, not derived per-amendment.

No separate "A YES vote means... / A NO vote means..." framing is
published in this document (unlike CA/MA/CO) — the ballot question
itself IS the yes/no question ("Do you support...?"). Inferring "yes
means the question as stated, no means the opposite" would be deriving
polarity from the question's phrasing rather than reading it, which is
exactly what this codebase's yes/no fields exist to avoid guessing at
(a "no" vote on a repeal question retains the challenged law, not what
casual reading suggests) — so yes_means/no_means stay null here, same
as any source that simply doesn't publish this framing. No fiscal
impact statement appears in this document either — also left null.
"""

import re

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text

ORIGIN = "Louisiana Legislature"
TITLE_AUTHORITY = "Louisiana Legislature"

_AMENDMENT_SPLIT_RE = re.compile(r"Proposed Amendment No\.\s*(\d+)")
_TRAILING_PAGE_NUMBER_RE = re.compile(r"\s+\d+\s*$")
# Per-PAGE running footer/header, seen on some real years' documents and
# not others (verified: the footer in 2022, the header in both 2020 and
# 2022 — 2024/2026 happen not to hit either, purely because of where
# their content lines up with a page break, not because the pattern is
# absent from those years' template). Both repeat on every page, not
# just once, so either can land INSIDE an amendment's text if that
# amendment's content spans a page boundary — not just at the document's
# very end. Both stripped from the whole joined text before splitting,
# not trimmed off individual chunks after the fact, so a mid-document
# occurrence can't leak into a real amendment's official_summary.
_PAGE_FOOTER_RE = re.compile(r"Prepared by the Louisiana Secretary of State\s*\d*")
_PAGE_HEADER_RE = re.compile(
    r"(?:[A-Za-z]+ \d{1,2}, )?\d{4} PROPOSED CONSTITUTIONAL AMENDMENTS?",
    re.IGNORECASE,
)


def parse_document(pages) -> list[dict]:
    """Every amendment across the whole PDF. Plain text, not word
    geometry — this document has no column layout to reconstruct."""
    full_text = "\n".join(page.extract_text() or "" for page in pages)
    full_text = _PAGE_FOOTER_RE.sub("", full_text)
    full_text = _PAGE_HEADER_RE.sub("", full_text)
    parts = _AMENDMENT_SPLIT_RE.split(full_text)

    results = []
    # parts = [preamble, number_1, body_1, number_2, body_2, ...]
    for i in range(1, len(parts), 2):
        number = parts[i]
        body = _TRAILING_PAGE_NUMBER_RE.sub("", parts[i + 1])
        official_summary = clean_text(body)
        if not official_summary:
            continue
        results.append({
            "number": number,
            "title": f"Proposed Amendment No. {number}",
            "origin": ORIGIN,
            "official_summary": official_summary,
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": TITLE_AUTHORITY,
            "fiscal_authority": None,
        })
    return results
