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


# A box's quoted line is one sentence a phone shows in a few lines. Past
# this many words (a convention: about four lines at 375px) the quote is
# the sentence's opening words and an ellipsis, still the source's own
# words in order, with the whole text behind the box's "More".
LEDE_MAX_WORDS = 40


def lede(text: str | None, max_words: int = LEDE_MAX_WORDS) -> str | None:
    """The text's first sentence, verbatim, for a box to quote: whitespace
    folded, cut at the first mark that ends a sentence (ends_sentence), and
    past `max_words` cut at a word with an ellipsis. None for no text."""
    flat = " ".join((text or "").split())
    if not flat:
        return None
    sentence = flat
    for m in SENTENCE_END.finditer(flat):
        if ends_sentence(flat, m.start()):
            sentence = flat[:m.start() + 1]
            break
    words = sentence.split(" ")
    if len(words) <= max_words:
        return sentence
    return " ".join(words[:max_words]).rstrip(",;:") + "…"
