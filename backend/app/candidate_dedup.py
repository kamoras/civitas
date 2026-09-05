"""Collapse two FEC candidate_ids that are the same real person under one
race. A real, observed FEC artifact -- a candidate refiles (a name
correction, a party-declaration change) and is assigned a NEW
candidate_id, but FEC's own bulk data links the same committee's
financial totals to both. Verified live across 22 real 2026 races: 21 of
22 pairs share a name (exact or an obvious variant, e.g. "ONDER JR,
ROBERT FRANK" / "ONDER, ROBERT FOR JR."); the one exception (CA-4's
"BROWN, SHARON" / "GHUSAR, MANDY", both $7,000 raised / $0 cash) proves
identical financials ALONE is not safe evidence -- it takes a matching
surname AND identical financials together, never either alone.

Deliberately conservative: contributions and cash_on_hand must both be
non-null and at least one non-zero (a shared "never synced"/$0 pair is
common and proves nothing). Unlike state_candidates.py's _match_candidate,
this never falls back to a looser rule on a miss -- the cost of NOT
merging is one duplicate row; the cost of a wrong merge is misattributing
a real candidate's identity, which this system treats as the worse
failure everywhere else. Never touches the DB: both FEC ids are real,
independently-filed records worth keeping for anyone who clicks through
to fec.gov on either one -- this only shapes which rows a caller's
response/decision includes, the same "never delete source data"
precedent confirmed_general/on_primary_ballot already set.

Lives at the app root, not under app/api/, so both the API layer
(app/api/elections.py, the original caller) and pipeline code
(app/pipeline/analyze/election_bluesky.py's _roster_fact) can import it
without the pipeline depending on the API layer. A pipeline consumer that
looks up a Candidate by a STORED id (a coverage item's
matched_candidate_id, fetched in a prior run) needs dedupe_merge_map, not
just dedupe_candidates: if that stored id was the one this rule would
drop from a fresh race list, the caller must resolve it to the surviving
id rather than silently looking up a row the site's own race page no
longer shows.
"""

from app.models import Candidate

_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalized_surname(name: str) -> str:
    """FEC's Candidate.name is "LAST, FIRST MIDDLE ..." -- the surname is
    everything before the comma, with a trailing generational suffix
    stripped, since FEC inconsistently attaches JR/SR/II/III to either
    half of the name (see dedupe_merge_map)."""
    tokens = name.split(",")[0].strip().lower().split()
    while tokens and tokens[-1].strip(".") in _NAME_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def dedupe_merge_map(candidates: list[Candidate]) -> dict[str, str]:
    """{dropped_id: surviving_id} for every duplicate pair found in
    `candidates` — empty when none are found. Exposed as a mapping (not
    just a drop set) so a caller holding a single, possibly-stale
    candidate id can resolve it to the row the deduped list would
    actually show, rather than only being able to detect that its id was
    dropped."""
    by_fingerprint: dict[tuple[float, float], list[Candidate]] = {}
    for c in candidates:
        if c.contributions is None or c.cash_on_hand is None:
            continue
        if c.contributions == 0 and c.cash_on_hand == 0:
            continue
        by_fingerprint.setdefault((c.contributions, c.cash_on_hand), []).append(c)

    merge_map: dict[str, str] = {}
    for group in by_fingerprint.values():
        if len(group) < 2:
            continue
        by_surname: dict[str, list[Candidate]] = {}
        for c in group:
            by_surname.setdefault(normalized_surname(c.name), []).append(c)
        for dupes in by_surname.values():
            if len(dupes) < 2:
                continue
            # Rank confirmed_general over on_primary_ballot over neither, so
            # whichever flag made the group real survives the merge -- not
            # whichever id happens to sort first. A prior version treated
            # "exactly one dupe carries confirmed_general OR on_primary_ballot"
            # as the only safe case and fell back to an arbitrary id-sort
            # otherwise, which could drop the one confirmed_general row when a
            # second dupe separately had on_primary_ballot set.
            def _rank(c: Candidate) -> tuple[int, str]:
                if c.confirmed_general:
                    return (0, c.id)
                if c.on_primary_ballot:
                    return (1, c.id)
                return (2, c.id)

            keep = min(dupes, key=_rank)
            merge_map.update({c.id: keep.id for c in dupes if c.id != keep.id})

    return merge_map


def dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Collapse duplicates, keeping one row per real person. See
    dedupe_merge_map's docstring for the matching rule."""
    merge_map = dedupe_merge_map(candidates)
    return [c for c in candidates if c.id not in merge_map]


def resolve_candidate_id(candidate_id: str, candidates: list[Candidate]) -> str:
    """`candidate_id` if it survives dedupe_candidates(candidates), or the
    id of the candidate it was merged into otherwise. For a consumer that
    holds one stored id and needs to look up "the" row the current race
    list would show for that person — see election_bluesky.py's
    _roster_fact."""
    return dedupe_merge_map(candidates).get(candidate_id, candidate_id)
