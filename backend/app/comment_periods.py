"""How an open comment period is ordered and shown: the Today tab's list,
an agency page's, and Explore's open-comments view all read this.

Proposed and final rules come first, then notices; a Paperwork Reduction
Act information-collection notice ("paperwork notice") comes after every
other notice and is folded on the agency and Explore lists. Which notices
those are is the Federal Register's own table-of-contents subject, the
heading the Office of the Federal Register files every such notice under
(ExploreDocument.fr_toc_subject), never a word in the title. Measured on
the 615 periods open on 2026-10-17: 303 notices carry the heading, every
one an information collection by its own title or abstract (precision
303/303), and one information collection was filed under another heading
("Hearings, Meetings, Proceedings, etc."), so recall is 303/304. Proposed
rules drew a median of 238 Register page views each, those notices 110.

Read path only: no model, no fetch.
"""
from datetime import date

from app.pipeline.fetch.fr_rulemaking import FR_TYPE_LABELS, RULE_DOC_TYPES
from app.readout import period_marks
from app.sentences import SENTENCE_END, ends_sentence

# The Register's table-of-contents subject for Paperwork Reduction Act
# collections (44 U.S.C. 3506(c)(2)(A) and 3507(a)(1)(D) notices). A
# documented data-format value, compared whole (AGENTS.md principle 1).
PAPERWORK_TOC_SUBJECT = "Agency Information Collection Activities; Proposals, Submissions, and Approvals"

# ExploreDocument.summary keeps the abstract's first 500 characters
# (fetch/fr_rulemaking.py): a text that long with no sentence end was cut.
_SUMMARY_CAP = 500


def is_paperwork(doc_type: str | None, toc_subject: str | None) -> bool:
    """A notice the Register files under the information-collection heading.
    The Register writes it with a trailing colon on some notices."""
    return doc_type == FR_TYPE_LABELS["Notice"] and (toc_subject or "").rstrip(": ") == PAPERWORK_TOC_SUBJECT


def salience(doc, linked: frozenset[int] | set[int] = frozenset()) -> tuple:
    """Sort key, most salient first: documents an issue links; then rules
    (the Register's own type: proposed and final rules) before notices, and
    paperwork notices last; then by readers, as the Register counts them
    (fr_page_views); then deadline.

    Sorting by deadline alone led with whatever closed soonest: on
    2026-10-11, a waiver petition and five Paperwork Reduction Act
    collections in the Today tab's first ten. Citations were measured and
    not used: the most-cited open documents were airworthiness directives
    (306 citations each, all of them the boilerplate cross-references every
    directive carries)."""
    tier = 0 if doc.doc_type in RULE_DOC_TYPES else 2 if is_paperwork(doc.doc_type, doc.fr_toc_subject) else 1
    return (doc.id not in linked, tier, -(doc.fr_page_views or 0), doc.comments_close_on)


def first_sentence(summary: str | None) -> str | None:
    """The abstract's first sentence, verbatim; None when the stored text
    has none whole (empty, or cut at the cap mid-sentence)."""
    text = " ".join((summary or "").split())
    for m in SENTENCE_END.finditer(text):
        if ends_sentence(text, m.start()):
            return text[:m.start() + 1]
    if text.endswith((".", "?", "!")) and len(text) < _SUMMARY_CAP:
        return text
    return None


def comment_window(opened: str | None, closes: str | None, today: str) -> dict | None:
    """The period from publication to its deadline, as of `today` (the
    deadline's zone, time_utils.comment_period_today): {opened, closes,
    totalDays, daysLeft, elapsedPercent, marks}, `marks` being what the
    bar's readout says. None without both dates."""
    try:
        start, end, now = date.fromisoformat(opened or ""), date.fromisoformat(closes or ""), date.fromisoformat(today)
    except ValueError:
        return None
    total = max((end - start).days, 1)
    left = min(max((end - now).days, 0), total)
    return {"opened": opened, "closes": closes, "totalDays": total, "daysLeft": left,
            "elapsedPercent": round(100 * (total - left) / total),
            "marks": period_marks(opened, closes, total, left)}
