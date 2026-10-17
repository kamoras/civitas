"""Where a sentence ends in an official text (a Federal Register abstract):
not at an initial, a dotted abbreviation or a title. Read by the Action
Center's rule issues (pipeline/analyze/early_signal.py) and the comment
period cards (app/comment_periods.py)."""
import re

# A period that ends a word like these doesn't end the sentence. The dotted
# forms ("U.S.", "U.S.C.") and initials are caught by shape in ends_sentence.
_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "inc", "co", "corp", "ltd",
    "no", "nos", "sec", "secs", "pub", "vol", "fig", "dept", "gov", "gen",
})
SENTENCE_END = re.compile(r"[.?!](?=\s+[\"“(]?[A-Z])")


def ends_sentence(text: str, at: int) -> bool:
    """Whether the mark at `at` (followed by a capitalised word) ends a
    sentence: not the period of an initial ("John Q. Public"), a dotted
    abbreviation ("the U.S. Fish and Wildlife Service") or a title."""
    if text[at] != ".":
        return True
    word = text[:at].rsplit(" ", 1)[-1].lstrip("(\"“")
    return not ("." in word or len(word) == 1 or word.lower() in _ABBREVIATIONS)
