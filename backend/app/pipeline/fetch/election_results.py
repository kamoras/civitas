"""Live general-election counts, read from each state's own election-night
reporting system — the same vendor feeds the primary adapters already
read (state_candidates_*.py), asked a different question.

A primary adapter reduces a contest to its nominee and throws the counts
away, and it waits `settle_days` after the election before saying
anything, because a nominee confirmed from an unsettled count could be
wrong. Election night is the opposite: the count as it stands IS the
answer, published as it moves and labelled for what it is. So each vendor
module carries a second reader, `fetch_general_results`, that keeps every
candidate's votes and the contest's reporting status, and applies no
settle gate. Nothing here decides a winner: a count is only ever shown as
leading, or as official where the source itself says so (see RaceResult).

The election is found by its DATE — the statutory general election day —
rather than by name, so no per-state wording has to be configured: every
vendor's index carries a date per election. Where more than one election
shares that date (a local special on the same ballot), the one named a
general election is taken; a demo copy, recount or runoff is never it; and
where that still leaves more than one, the read is refused with an alert
rather than guessed (pick_general).

Coverage is by vendor, as everywhere in this system: a state whose
registered source (state_candidate_sources.json) runs on a vendor listed
in READERS is covered with no per-state code. A state on any other source
has no live count here, and the API says so rather than showing an empty
map as if nothing were happening.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import httpx

from app.pipeline.fetch.state_candidate_sources import configured_states, source_for_state

logger = logging.getLogger(__name__)

_GENERAL_RE = re.compile(r"general", re.IGNORECASE)


@dataclass
class ContestCount:
    """One federal contest's count as the source shows it."""
    office: str  # "S" | "H"
    district: int | None
    # (printed name, party letter from normalize_party or None, votes).
    # A candidate whose party the shared vocabulary doesn't know keeps
    # their votes: dropping them would overstate everyone else's share.
    candidates: list[tuple[str, str | None, int]]
    # Every vote cast in the contest where the source states it (write-ins
    # included), else the candidates' sum.
    total_votes: int | None = None
    reporting_units: int | None = None
    total_units: int | None = None
    # A contest name that marks it as the special election for a seat, so
    # a state electing both of its Senate seats at once is not merged.
    is_special: bool = False

    @property
    def votes_counted(self) -> int:
        return self.total_votes if self.total_votes is not None else sum(v for _, _, v in self.candidates)


@dataclass
class StateCount:
    source_name: str
    page_url: str | None
    official: bool
    unit_label: str = "precincts"
    contests: list[ContestCount] = field(default_factory=list)
    # When the source says it last updated (naive UTC), and its own version
    # id where it keeps one — what the sync compares to refuse a feed that
    # has gone BACKWARDS (a cache or a mirror serving an older copy). A
    # version is compared only when it ends in a plain number, and only
    # with versions of the same election: Clarity's is stored as
    # "<election id>:<version>" (sync._scoped_version). Tally's "v1-1" form
    # has no order and is never compared, so only its time guards it.
    source_updated: datetime | None = None
    source_version: str | None = None


class UntrustedCount(Exception):
    """The source answered, but not with this election's production count:
    a test or preview feed, or a response describing a different election
    than the one asked for. Every vendor here publishes test data on its
    real endpoints before election night (verified 2026-09-28: Enhanced
    Voting's isProduction, Clarity's istestmode, Tally's previewElections),
    and TV stations have aired such feeds as real results. Nothing from it
    is stored."""


def parse_utc(raw) -> datetime | None:
    """An ISO timestamp ("2025-01-07T16:55:00.63Z", with or without an
    offset) as naive UTC; None for anything else, including the
    "0001-01-01T00:00:00" placeholder Tally answers an unknown id with."""
    if not isinstance(raw, str) or not raw or raw.startswith("0001-"):
        return None
    text = raw.strip().replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)  # .NET's 7-digit fractions
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


_SPECIAL_RE = re.compile(r"\b(special|unexpired)\b", re.IGNORECASE)


def is_special_contest(name: str) -> bool:
    return bool(_SPECIAL_RE.search(name or ""))


# An election held on the general's date that is not the general's count
# itself: a vendor's demo/test/preview copy, a recount, a runoff. A
# recount can carry the very date of the election it recounts (Utah's
# "2024 Primary Election - US House 2 Recount" is dated 2024-06-25, the
# primary's own day, verified 2026-09-28), so a date match alone does not
# rule one out.
_NOT_THE_COUNT_RE = re.compile(r"\b(demo|test|preview)\b", re.IGNORECASE)
# A recount or a runoff is not the general either. But a state holding its
# nonpartisan runoffs on the general's ballot names that one election
# "General Election and Nonpartisan Runoff": a runoff name that also says
# "general" is the general when nothing plainer is held that day, and
# dropping it outright left the state with no count all night.
_RECOUNT_RE = re.compile(r"\brecount\b", re.IGNORECASE)
_RUNOFF_RE = re.compile(r"\brunoff\b", re.IGNORECASE)


def pick_general(elections: list[tuple[str, dict]], state: str = "") -> dict | None:
    """The one election, among those held on the general's date, that is
    the general itself. `elections` is [(name, entry)].

    Demo/test/preview copies and recounts are never it, and a runoff only
    when it is also named "general" ("General Election and Nonpartisan
    Runoff") and no other election that day is. Of what remains: the only
    one (unless it is a special not also named "general", which raises);
    else the only one named "general" that is not
    also a special; else the only one named "general" at all (Georgia
    named its 2022 ballot "November 8, 2022 - General/Special Election").
    None when nothing is held that day (a vendor's test copy of the day,
    published before the real one, is "not yet"). When SEVERAL remain and
    no rule singles one out, or the only elections that day are recounts
    or runoffs, this raises UntrustedCount rather than returning None: None
    reads downstream as "not published yet", and a state that silently
    shows no count all election night because its index grew a second
    same-day entry, or named its general in a way this can't read, is
    exactly the failure that must page someone."""
    not_test = [(name or "", entry) for name, entry in elections if not _NOT_THE_COUNT_RE.search(name or "")]
    counts = [(name, entry) for name, entry in not_test if not _RECOUNT_RE.search(name)]
    candidates = [(name, entry) for name, entry in counts if not _RUNOFF_RE.search(name)]
    if not any(_GENERAL_RE.search(name) for name, _ in candidates):
        # No plainer election named "general" that day: a runoff named
        # "general" is a candidate too, before narrowing — not only when
        # nothing else is held. Otherwise a same-day special beside a
        # "General Election and Nonpartisan Runoff" was the only candidate
        # left, and was returned as the general.
        candidates += [(name, entry) for name, entry in counts if _RUNOFF_RE.search(name) and _GENERAL_RE.search(name)]
    if not candidates:
        if not_test:
            raise UntrustedCount(
                f"{state or 'state'}: the only elections on the general's date are "
                f"{', '.join(repr(n) for n, _ in not_test)}, none of them the general; refusing to guess",
            )
        return None
    if len(candidates) == 1:
        name, entry = candidates[0]
        if _GENERAL_RE.search(name) or not _SPECIAL_RE.search(name):
            return entry
        # A lone "Special Election" is not the general: every general's date
        # is a federal general in every state (even years only), so a day
        # whose only entry is a special has the general missing, not held
        # under that name. Returned, a special's contests were read as the
        # regular races' count.
        raise UntrustedCount(
            f"{state or 'state'}: the only election on the general's date is {name!r}, a special, not the "
            "general; refusing to guess",
        )
    named = [(name, entry) for name, entry in candidates if _GENERAL_RE.search(name)]
    regular = [entry for name, entry in named if not _SPECIAL_RE.search(name)]
    if len(regular) == 1:
        return regular[0]
    if len(named) == 1:
        return named[0][1]
    raise UntrustedCount(
        f"{state or 'state'}: {len(candidates)} elections share the general's date and none is singly the "
        f"general ({', '.join(repr(n) for n, _ in candidates)}); refusing to guess",
    )


def _readers() -> dict:
    # Imported lazily: each vendor module imports this one's dataclasses.
    from app.pipeline.fetch import (
        state_candidates_clarity,
        state_candidates_enhanced_voting,
        state_candidates_tally_enr,
        state_candidates_totalvote,
    )

    return {
        "clarity": state_candidates_clarity.fetch_general_results,
        "tally_enr": state_candidates_tally_enr.fetch_general_results,
        "totalvote_enr": state_candidates_totalvote.fetch_general_results,
        "enhanced_voting": state_candidates_enhanced_voting.fetch_general_results,
    }


def live_source(source: dict) -> dict:
    """The source entry the live reader uses. A state's `live_results`
    block, where it has one, names an election-night feed its primary
    adapter doesn't read (South Carolina's primary source is a candidate
    list; its count is on Clarity) — the same "a whole second source
    entry" shape as `general_list` and `fallback`."""
    live = source.get("live_results")
    if isinstance(live, dict):
        return {"source_name": source.get("source_name"), **live}
    return source


def _reader_for(source: dict):
    source = live_source(source)
    strategy = source.get("strategy")
    # Georgia, Washington, Virginia, Utah and Idaho read their primaries
    # from this vendor's spreadsheet exports (the tabular strategy), but the
    # same portal serves its results API, which is what counts live.
    if strategy == "tabular" and (source.get("discovery") or {}).get("mode") == "sos_api_report":
        strategy = "enhanced_voting"
    return _readers().get(strategy)


def live_results_states() -> set[str]:
    """Every state this module can read a live count for."""
    return {s for s in configured_states() if (src := source_for_state(s)) and _reader_for(src)}


async def fetch_state_count(
    client: httpx.AsyncClient, state: str, election_day: date,
) -> StateCount | None:
    """The state's federal count for `election_day`, or None when its
    source can't be read or hasn't published that election. A published
    election with nothing counted yet is a StateCount with zero votes —
    the difference between "no results yet" and "couldn't check". Raises
    UntrustedCount for test or preview data, or a mismatched election."""
    source = source_for_state(state)
    reader = _reader_for(source) if source else None
    if reader is None:
        return None
    try:
        count = await reader(client, election_day, state.upper(), live_source(source))
    except UntrustedCount:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        logger.exception("%s live results read failed", state)
        return None
    if count is not None:
        count.contests = _without_unnumbered_house_collisions(state, count.contests)
    return count


def _without_unnumbered_house_collisions(state: str, contests: list[ContestCount]) -> list[ContestCount]:
    """A House contest with no district is an at-large seat, and a state has
    at most one. Two of them means the reader could not read the district
    out of a label (Utah's "U.S. House 1", before its entry named the
    format) and every district would land on the same seat — so none of
    them is kept, and the gap is logged as the parsing failure it is,
    rather than showing one district's count on another's race."""
    groups: dict[bool, int] = {}
    for c in contests:
        if c.office == "H" and c.district is None:
            groups[c.is_special] = groups.get(c.is_special, 0) + 1
    clashing = {special for special, n in groups.items() if n > 1}
    if not clashing:
        return contests
    logger.error(
        "%s live results: %d House contests carry no district number; dropped rather than merged "
        "(add the state's label format as house_label_regex)", state, sum(groups[s] for s in clashing),
    )
    return [c for c in contests if not (c.office == "H" and c.district is None and c.is_special in clashing)]
