"""Vermont's ballot-measure PDF strategy: the General Assembly's own
"Vermont Constitutional Amendment Notice" (one of potentially many
per-state strategies; see ballot_measures_pdf.py for the registry and
the shared fetch/cache pipeline this plugs into).

Vermont statewide ballot measures are constitutional amendments only —
the state has no citizen initiative — and every one of them is a
proposal the General Assembly passed in two successive bienniums
(Vt. Const. ch. II, § 72). 17 V.S.A. chapter 32 requires notice of
each proposal to be published "on the websites of the General Assembly
and the Office of the Secretary of State" before the vote. The
General Assembly's copy is the one this module reads: a short PDF
linked from the Legislature's "Announcements" page
(legislature.vermont.gov/home/noteworthy/announcements) whose filename
carries the election year ("2026-Vt.-Const.-amendment-publication-
notice-...pdf" — verified live 2026-09-28). The shared
discover_pdf_url finds it by year + "const" + "amendment", so a new
cycle's notice needs no config change. Cross-checked against the
Governor's two proclamations of 2026-07-27 (governor.vermont.gov), which
notice the same two proposals — Proposal 3 and Proposal 4 — for the
November 3, 2026 general election.

Shape (both pages real, see tests/fixtures_vt_2026_notice.json): a
preamble, then per proposal one plain-language sentence written by the
General Assembly ("Proposal 3 would amend the Vermont Constitution to
provide that ...") followed by the amendment's full text. That sentence
is stored verbatim as official_summary; the amendment text itself is
not (same "narrative summary only" scope as every other state here). A
running "(ID 421676)" document-ID footer on each page is stripped
before splitting so a proposal spanning the page break can't absorb it.

No "a yes vote means" framing and no fiscal statement are published for
Vermont amendments, so yes_means/no_means/fiscal_impact stay null.
Origin and title_authority are fixed to the General Assembly, the only
body that can propose an amendment. "Proposal N" is a label, not a
ballot title, so no official_title is claimed.

A year with no notice linked from the Announcements page (Vermont votes
on amendments only in a year the Assembly has passed one) reads as not
yet published ("absent_until_published" in the registry), not as a
failed ingest — and never as "none".
"""

import logging
import re

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text

logger = logging.getLogger(__name__)

ORIGIN = "Vermont General Assembly"
TITLE_AUTHORITY = ORIGIN

_FOOTER_RE = re.compile(r"^\(ID \d+\)$")
# The plain-language sentence runs from "Proposal N would ..." to the
# first period that ends a line — verified on both real 2026 proposals,
# each of which wraps over two or three lines before that period.
_PROPOSAL_RE = re.compile(r"^Proposal\s+(\d+)\s+(would\b.*?\.)$", re.MULTILINE | re.DOTALL)


def parse_document(pages) -> list[dict]:
    """Every proposal in the notice, in document order. A proposal whose
    number repeats raises (the whole notice is refused) rather than one of
    the two sentences being picked.

    Raises ValueError when the notice yields no proposal at all: this
    notice is only ever published BECAUSE proposals are going to the
    voters, so an empty parse is a changed layout, not "none this
    cycle" — and [] would be recorded as MeasureCoverage.CONFIRMED_NONE
    (ballot_measures_pdf.fetch_state_measures_pdf turns the exception
    into None, i.e. ingest_failed)."""
    lines = []
    for page in pages:
        for ln in (page.extract_text() or "").splitlines():
            ln = ln.strip()
            if ln and not _FOOTER_RE.match(ln):
                lines.append(ln)
    text = "\n".join(lines)

    results: list[dict] = []
    seen: set[str] = set()
    for m in _PROPOSAL_RE.finditer(text):
        number = m.group(1)
        if number in seen:
            # Two sentences for one proposal: which is the notice's? Not
            # guessed — keeping the first used to drop the second silently.
            raise ValueError(f"Vermont amendment notice names Proposal {number} twice")
        seen.add(number)
        summary = clean_text(f"Proposal {number} {m.group(2)}")
        results.append({
            "number": number,
            "title": f"Proposal {number}",
            "origin": ORIGIN,
            "official_summary": summary,
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": TITLE_AUTHORITY,
            "fiscal_authority": None,
        })
    if not results:
        raise ValueError("Vermont amendment notice contained no recognisable 'Proposal N would ...' sentence")
    return results
