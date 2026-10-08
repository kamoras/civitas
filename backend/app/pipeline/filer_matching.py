"""Match a disclosure filer's name to a sitting senator or representative.

Shared by the STOCK Act trade ingest (stock_pipeline.py) and the annual
holdings ingest (holdings_pipeline.py): both read filings indexed by the
filer's printed name, and both must skip a filing rather than guess when the
name is ambiguous.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from app.models import Representative, Senator
from app.pipeline.transform.normalize_members import strip_accents


def _fold(text: str | None) -> str:
    """Lowercased, accents stripped, punctuation to spaces — the folding
    AGENTS.md 4a applies to roll-call names (the same strip_accents), because
    the disclosure systems print "NUNEZ" and "Pena" where the roster
    says "Núñez" and "Peña", and SQL ILIKE doesn't fold accents."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", strip_accents(text or "").lower()).split())


def _surname(last: str | None) -> str:
    """The filing's last-name field up to its first comma: both systems put
    what follows the surname after one — eFD a generational suffix
    ("McConnell, Jr.", "Hagerty, IV": four sitting senators in 2026-10, none
    of whom matched), the House index a credential ("Dunn, MD, FACS")."""
    return _fold((last or "").split(",", 1)[0])


def _has_surname(name: str, last: str) -> bool:
    surname = _surname(last)
    return bool(surname) and f" {surname} " in f" {_fold(name)} "


def _first_names(*sources: str | None) -> set[str]:
    """Every first-name token a filing offers: its first-name field and, for
    eFD rows, the office column's display name ("Scott, Rick (Senator)")."""
    names: set[str] = set()
    for source in sources:
        folded = _fold(source)
        if folded:
            names.add(folded.split()[0])
    return names


def _office_first_name(office: str | None) -> str | None:
    """"Scott, Rick (Senator)" -> "Rick"."""
    if not office or "," not in office:
        return None
    return office.split(",", 1)[1].split("(", 1)[0].strip() or None


def _pick(candidates: list, last: str, firsts: set[str]):
    candidates = [c for c in candidates if _has_surname(c.name, last)]
    if len(candidates) == 1:
        return candidates[0]
    by_first = [c for c in candidates if firsts & set(_fold(c.name).split())]
    if len(by_first) == 1:
        return by_first[0]
    # Ambiguous (several same-surname members, none singled out by a first
    # name) — skip rather than guess whose filing it is.
    return None


@dataclass(frozen=True)
class Member:
    """A roster entry as plain values. The ingest phases commit after every
    fetch, and a commit expires every ORM object in the session — a roster
    of Senator/Representative rows held across those commits would reload
    each row, one SELECT apiece, on the next filer's match."""

    id: str
    name: str
    state: str | None = None
    district: int | None = None


def current_senators(db: Session) -> list[Member]:
    """The roster match_senator compares against — load it once per ingest
    phase and pass it in, rather than once per filer."""
    rows = db.query(Senator.id, Senator.name).filter(Senator.is_current == True).all()  # noqa: E712
    return [Member(id, name) for id, name in rows]


def match_senator(roster: list[Member], last: str, first: str, office: str | None = None) -> Member | None:
    """`roster` from current_senators, loaded once per phase."""
    if not _surname(last):
        return None
    return _pick(roster, last, _first_names(first, _office_first_name(office)))


def current_representatives(db: Session) -> list[Member]:
    """The roster match_representative compares against — load it once per
    ingest phase and pass it in, rather than once per filer."""
    rows = (
        db.query(Representative.id, Representative.name, Representative.state, Representative.district)
        .filter(Representative.is_current == True)  # noqa: E712
        .all()
    )
    return [Member(*row) for row in rows]


def match_representative(roster: list[Member], last: str, first: str, state_district: str) -> Member | None:
    """`roster` from current_representatives, loaded once per phase."""
    if not _surname(last):
        return None
    state = state_district[:2] if state_district else None
    # The House FD index supplies the FULL district ("CA27"), and
    # Representative.district exists — so filter on it. Previously only the
    # state was used, leaving same-state same-surname pairs to a fragile
    # first-name substring match that silently skipped the filing every run
    # whenever the formal filing name differed from the display name
    # ("Michael" vs "Mike"). District makes the match exact for all 435
    # voting seats.
    district: int | None = None
    if state_district and len(state_district) > 2 and state_district[2:].isdigit():
        district = int(state_district[2:])

    # A single member with the surname in the district is taken without
    # comparing first names. Against the 2025–26 indexes and the real
    # roster (433 members, 2026-09), 45 of 445 annual-report matches and 12
    # distinct PTR filers name the member differently — Richard/Rick,
    # Rohit/Ro, Michael/Mike — and all of them are the same person; no
    # departed member sharing a successor's surname and district turned up.
    # Requiring a first name to agree would drop those real matches.
    in_state = [r for r in roster if not state or r.state == state]
    candidates = [r for r in in_state if district is None or r.district == district]
    if district is None or any(_has_surname(r.name, last) for r in candidates):
        return _pick(candidates, last, _first_names(first))
    return _moved_district(in_state, last, first)


# A filer the index lists under a district no member of that surname holds
# can be a member whose district was renumbered: the Clerk keeps a filer's
# registered district, so after a redistricting a member files under the old
# number for years (2025-26 indexes: two members' annual reports and seven
# periodic transaction reports, every one unmatched). Then the state decides,
# but only with a first name that agrees — a former member, or a candidate,
# can share a sitting member's surname in the same state. Agreement is a
# shared first-name token or a Ratcliff/Obershelp ratio of the first tokens
# of at least this: measured over the 2024-26 indexes, the off-district
# same-surname pairs that were different people scored 0.15-0.36, and 31 of
# the 39 district-matched pairs whose first names differ (Lucia/Lucy,
# Michael/Mike, Randall/Randy) score 0.5 or more.
_MOVED_FIRST_NAME_RATIO = 0.5


def _moved_district(in_state: list[Member], last: str, first: str) -> Member | None:
    same = [r for r in in_state if _has_surname(r.name, last)]
    if len(same) != 1:
        return None
    # Initials don't count: a middle "L." is shared by unrelated people.
    filed = [t for t in _fold(first).split() if len(t) > 1]
    roster = [t for t in _fold(same[0].name).split() if len(t) > 1]
    if not filed or not roster:
        return None
    if set(filed) & set(roster) or SequenceMatcher(None, filed[0], roster[0]).ratio() >= _MOVED_FIRST_NAME_RATIO:
        return same[0]
    return None


class FilerMatcher:
    """A matcher that looks each distinct filer up once: an index or search
    lists the same filer on many filings. Called with the filer's key — the
    arguments after the roster to match_senator/match_representative — it
    returns the matched member's id or None."""

    def __init__(self, roster: list[Member], match) -> None:
        self._roster = roster
        self._match = match
        self._ids: dict[tuple, str | None] = {}

    def __call__(self, *filer) -> str | None:
        if filer not in self._ids:
            found = self._match(self._roster, *filer)
            self._ids[filer] = found.id if found is not None else None
        return self._ids[filer]
