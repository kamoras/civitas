"""Line-joining for ballot text read out of a PDF's text layer.

pdfplumber hands back a paragraph as its printed lines. Joining them
with a space is right everywhere except after a line that ends in a
hyphen: "low-" + "income" is the word "low-income" wrapped at the
hyphen (verified in South Dakota's 2026 pamphlet), and a space there
would put "low- income" into text this codebase presents as quoted
verbatim. None of the documents these readers were verified against
uses a soft (discretionary) hyphen, so the hyphen is kept, not dropped.
"""

import re

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text

_WRAPPED_HYPHEN_RE = re.compile(r"(?<=\w-)\s*\n\s*(?=\w)")


def join_lines(text: str | list[str]) -> str | None:
    """Printed lines -> one whitespace-normalised paragraph."""
    if isinstance(text, list):
        text = "\n".join(text)
    return clean_text(_WRAPPED_HYPHEN_RE.sub("", text))


class NotYetPublished(Exception):
    """Raised by a state's measure reader when the document it reads for
    this election has not been published YET — Maine's Citizen's Guide
    appears weeks before November, and West Virginia only posts a notice
    in a year with an amendment. That is neither a failure (nothing is
    broken, so it must not page anyone) nor an answer (the state may still
    have measures), so it is recorded as not yet covered and checked again
    next run. The message says what is awaited, for the log."""

    def __init__(self, awaited: str, *, deadline_applies: bool = True):
        """`deadline_applies`: whether the awaited document is one the
        state publishes for EVERY such election (a sample ballot, a voter
        guide with a statutory mail-by date, a legislation summary). Then
        its absence past the state's expected-by cutoff is a failure, not
        a wait (election_pipeline._sync_pdf_measures). False where the
        document exists only in a year that has a measure — Maine's guide,
        West Virginia's notice, Michigan's November document — so its
        absence right up to election day can be the state's real answer."""
        super().__init__(awaited)
        self.deadline_applies = deadline_applies
