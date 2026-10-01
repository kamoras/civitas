"""The industry a donor's stated occupation works in.

Itemized FEC contributions carry the donor's occupation, and the FEC totals
them per committee (fetch/fec.fetch_occupation_totals). Read against the
generated app/data/occupation_industry.json.gz (scripts/
fetch_occupation_industry.py): an occupation is assigned an industry only
when a majority of the people in it work there (Census ACS microdata), and an
occupation string is recognized only by an exact match with one of O*NET's
titles for it. Anything else (a generic role like "CEO", a non-job like
"RETIRED", a field name like "FINANCE") is left unattributed, never guessed;
the generator's docstring has the measurements behind both rules.
"""

import gzip
import json
import re
from pathlib import Path

_DATA = Path(__file__).resolve().parents[2] / "data" / "occupation_industry.json.gz"
_data_cache: dict | None = None


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("IES"):
        return word[:-3] + "Y"
    if len(word) > 3 and word.endswith("S") and not word.endswith("SS"):
        return word[:-1]
    return word


def normalize_title(text: str) -> str:
    """The form both O*NET titles and FEC occupation strings are matched in:
    upper case, runs of anything but letters and digits as one space, each
    word singular. O*NET names occupations in the plural ("Registered
    Nurses") and donors write the singular; the same folding on both sides
    can only make equal strings meet, never change what a match means."""
    words = re.sub(r"[^A-Z0-9]+", " ", (text or "").upper()).split()
    return " ".join(_singular(w) for w in words)


def _data() -> dict:
    global _data_cache
    if _data_cache is None:
        with gzip.open(_DATA, "rt", encoding="utf-8") as f:
            _data_cache = json.load(f)
    return _data_cache


def industry_of_occupation(occupation: str | None) -> str | None:
    """The industry of an FEC occupation string, or None when it names no
    occupation the data assigns one."""
    data = _data()
    socp = data["titles"].get(normalize_title(occupation or ""))
    return data["occupations"][socp]["industry"] if socp else None
