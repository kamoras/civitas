"""A person's recorded name read into its parts: surname, given names and
quoted nicknames, from FEC's "LAST, FIRST MIDDLE" or a printed "First
Middle Last". Shared by the Action Center (naming a race's candidates in a
story) and the story-to-vote link (masking names, app/roll_call_links.py),
outside both so neither imports the other."""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

from app.pipeline.candidate_dedup import normalized_surname
from app.pipeline.fetch.state_candidates import _NOT_A_NAME, _without_trailing_suffix
from app.pipeline.fetch.state_candidates_common import surname

APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "‘": "'"})


def strip_accents(text: str) -> str:
    """Diacritics removed and apostrophes made straight, case kept."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).translate(APOSTROPHES)


# A nickname a record quotes: FEC's 'DELGADO, MARTIN ALLEN "ROB"', a
# parenthesised "(BETO)", or a single-quoted 'ROB' (not an apostrophe: it
# stands after a space).
QUOTED_NICKNAME = re.compile(r"[\"“”(]\s*([^\"“”()]+?)\s*[\"“”)]|(?<!\S)'([^'\s]+)'(?!\S)")


class RecordName(NamedTuple):
    surname: str
    given: list[str]  # in order; an initial is one letter
    nicknames: list[str]  # quoted; an initials nickname ("A.J.") as one "a.j"
    filed: bool  # FEC's "LAST, FIRST": a filing, not a printing


def record_name(name: str | None) -> RecordName:
    """The surname, given names in order and quoted nicknames of one
    recorded name, FEC's "LAST, FIRST MIDDLE" or a printed "First Middle
    Last", all folded. Honorifics and suffixes are dropped; initials are
    kept as one letter. A quoted nickname of initials ("A.J.", "T.J.") is
    kept whole, as "a.j"; a single quoted letter is a ballot annotation
    ("(I)" for incumbent), not a name, and is dropped."""
    text = strip_accents(name or "").lower()
    quoted = [a or b for a, b in QUOTED_NICKNAME.findall(text)]
    text = _without_trailing_suffix(QUOTED_NICKNAME.sub(" ", text))
    filed = "," in text
    if filed:
        last = normalized_surname(text)
        given_part = text.split(",", 1)[1]
    else:
        last = (surname(text) or "").lower()
        words = text.split()
        at = max((i for i, w in enumerate(words) if w.strip(".,") == last), default=len(words))
        given_part = " ".join(words[:at])

    def names(part: str) -> list[str]:
        tokens = ("".join(ch for ch in t if ch.isalpha() or ch in "'-").strip("'-")
                  for t in part.replace(".", " ").split())
        return [t for t in tokens if t and t not in _NOT_A_NAME]

    nicknames: list[str] = []
    for nick in quoted:
        tokens = names(nick)
        if len(tokens) > 1 and all(len(t) == 1 for t in tokens):
            nicknames.append(".".join(tokens))  # "a.j": initials, one name
        else:
            nicknames += [t for t in tokens if len(t) > 1]
    return RecordName(last.strip(), names(given_part), nicknames, filed)
