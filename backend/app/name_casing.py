"""Names the FEC files in capitals, shown as they are written.

The casing comes from usage: app/data/name_casing.json
(scripts/build_name_casing.py, from Federal Register prose) holds the words
usage writes in capitals or with an interior capital ("UCLA", "McDonnell").
Any other word takes a first capital. Used for donor names
(normalize_finance) and for a candidate's name where no state has printed
one (`person_display_name`). Outside app/pipeline/ and free of the
pipeline's imports, so the read-only API can use it without loading the
classifiers.
"""

import logging
import re

from app import data_refresh

logger = logging.getLogger(__name__)

NAME_CASING_FILE = "name_casing.json"
# Set only to stand in for the table (tests).
_cache: dict[str, str] | None = None


def name_casing() -> dict[str, str]:
    """{lower-case word: its written form} for the words usage writes in
    capitals or with an interior capital ("ucla": "UCLA")."""
    if _cache is not None:
        return _cache
    # Rebuilt yearly from the last year's issues (data_refresh), else the
    # bundled file.
    forms = data_refresh.read(NAME_CASING_FILE).get("forms")
    if forms is None:
        logger.warning("name_casing.json unreadable: names keep first-letter capitals only")
    return forms or {}


# A run of letters, and what precedes it: a run after a digit or an
# apostrophe is the rest of a word ("21ST", "AMERICA'S"), not a word.
_LETTER_RUN_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]+")
_APOSTROPHES = ("'", "’")


def clean_name(name: str, forms: dict[str, str] | None = None) -> str:
    """The FEC prints names in capitals; show each word as it is written.
    A word usage writes in capitals or with an interior capital takes that
    form ("UCLA", "AFL-CIO", "McDonnell"), any other its first letter
    capitalized. Words were all first-letter capitalized until 2026-10,
    which printed "Ucla" and "Cuny", a guess where usage is on record. A
    name not in capitals is left as filed."""
    if name != name.upper():
        return name
    if forms is None:
        forms = name_casing()

    def word(m: re.Match) -> str:
        run = m.group(0)
        prev = name[m.start() - 1] if m.start() else " "
        if prev.isdigit() or prev in _APOSTROPHES:
            return run.lower()
        if name[m.end():m.end() + 1] in _APOSTROPHES:
            # Part of an elided or possessive word ("INT'L", "AMERICA'S"):
            # not the word the table describes.
            return run.capitalize()
        return forms.get(run.lower()) or run.capitalize()

    return " ".join(_LETTER_RUN_RE.sub(word, name).split())


# FEC candidate names follow the form's "LAST, FIRST MIDDLE SUFFIX" with an
# optional title: a documented data-format convention, not a guess. A lone
# "V" is left alone: as a middle initial it is far commoner than "the fifth".
_SUFFIXES = {"jr": "Jr.", "sr": "Sr.", "ii": "II", "iii": "III", "iv": "IV"}
_TITLES = frozenset({"mr", "mrs", "ms", "miss", "dr", "hon", "honorable"})
# A person's "O'BRIEN", "D'ANGELO": one letter, an apostrophe, then the
# name, which keeps its capital (a word's "INT'L" has more than one letter
# before it). And "MC" before a name the table doesn't hold: "McBath".
# The table's forms that a surname can take: a prefix and the name, each
# capitalized ("McDonnell", "MacArthur", "DeSantis", "LaHood"). Its
# acronyms are a person's surname only by accident (Doe is not the DOE,
# Sam is not SAM), and its other mixed forms are a brand's ("ToM").
_PERSON_FORM_RE = re.compile(r"^[A-Z][a-z]{1,2}[A-Z][a-z]+$")
# (the table it was filtered from, the filtered forms): refiltered when
# data_refresh hands back a refreshed table.
_person_forms: tuple[dict | None, dict[str, str]] = (None, {})


def _surname_forms() -> dict[str, str]:
    global _person_forms
    table = name_casing()
    if _person_forms[0] is not table:
        _person_forms = (table, {k: v for k, v in table.items() if _PERSON_FORM_RE.match(v)})
    return _person_forms[1]


_ELIDED_PREFIX_RE = re.compile(r"\b([A-Z])(['’])([a-z])")
_MC_RE = re.compile(r"\bMc([a-z])")


def person_display_name(name: str | None) -> str:
    """An FEC candidate name as a reader writes it: "DOE, JANE" ->
    "Jane Doe", "COLLINS, MICHAEL A JR" -> "Michael A Collins Jr.",
    "O'BRIEN, MARY" -> "Mary O'Brien". Titles are dropped and a suffix goes
    last; letters are not added or removed otherwise. A name not in
    capitals is a printing, not a filing, and is returned as it is. For
    display only: the stored name stays the FEC's."""
    text = " ".join((name or "").split())
    if not text or text != text.upper():
        return text
    last, comma, given = text.partition(",")

    def split(part: str) -> tuple[list[str], list[str]]:
        words, suffixes = [], []
        for tok in part.split():
            key = tok.strip(".,").lower()
            if key in _SUFFIXES:
                suffixes.append(_SUFFIXES[key])
            elif key not in _TITLES:
                words.append(tok)
        return words, suffixes

    last_words, last_suffixes = split(last)
    given_words, given_suffixes = split(given) if comma else ([], [])
    if not last_words:
        return clean_name(text, _surname_forms())
    cased = clean_name(" ".join(given_words + last_words), _surname_forms())
    cased = _ELIDED_PREFIX_RE.sub(lambda m: m.group(1) + m.group(2) + m.group(3).upper(), cased)
    cased = _MC_RE.sub(lambda m: "Mc" + m.group(1).upper(), cased)
    return " ".join([cased, *last_suffixes, *given_suffixes])


def candidate_display_name(cand, member_name: str | None = None) -> str:
    """The name to show for a candidate: the state's printed ballot name,
    verbatim, when a state has printed one (§7); else, for a sitting member
    the crosswalk ties this FEC id to (`member_bioguide`), the member's
    Congress.gov name (`member_name`), which keeps its own casing and
    survives an FEC row filed with the names swapped ("NICK, LALOTA");
    else the FEC filing's name as a reader writes it."""
    return cand.ballot_name or member_name or person_display_name(cand.name)
