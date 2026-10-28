"""A state's statewide ballot measures from the Voting Information
Project's ballot data, read through Google Civic's voterInfoQuery (the
same answers state_candidates_civic.py reads), for a state whose own sites
are not read: Michigan's, whose terms of use forbid automated access
(app/data/host_terms.json, 2026-10-28; the Bureau of Elections pointed
Civitas to VIP).

The words are the state's, as VIP republishes them (AGENTS.md §7): the
referendum's title and text verbatim, the card crediting the republisher
(`republished_by`) beside the state office that drafted them (the
registry's `title_authority`). Nothing VIP doesn't carry is filled in:
no fiscal statement, no yes/no meaning.

A referendum is statewide when it is on every answering ballot from points
in different congressional districts (STATEWIDE_ANSWERS of them), which
leaves a city's millage or a county's charter question out by rule. Its
number is the title's first line's last token ("Proposal 2026-1" ->
"2026-1"), its official title the rest of the title. Google holding no
ballot for the state yet raises NotYetPublished, never a confirmed none: an
empty answer from a republisher is not the state saying there is nothing.
"""

import logging
import re

import httpx

from app.pipeline.fetch.ballot_measure_text import NotYetPublished
from app.pipeline.fetch.state_candidates_civic import (
    _points, contests_at, general_election_id, is_configured, _FETCH_FAILED,
)

logger = logging.getLogger(__name__)

PUBLISHER = "the Voting Information Project"
# Answers from this many congressional districts must all carry a
# referendum for it to count as statewide.
STATEWIDE_ANSWERS = 3
_NUMBER_RE = re.compile(r"(\d{4}-\d+|[A-Z]{0,3}-?\d+[A-Z]?)\s*$")


def _split_title(title: str) -> tuple[str, str | None]:
    """(first line, the rest or None): "Proposal 2026-1\\n\\nA proposal to
    ..." -> ("Proposal 2026-1", "A proposal to ...")."""
    lines = [line.strip() for line in title.splitlines() if line.strip()]
    return lines[0], (" ".join(lines[1:]) or None)


def _origin(text: str, rules: dict) -> str | None:
    """The registry's `origin_when_title_says` ({"initiated": "..."}): the
    state's own ballot wording names who proposed a measure."""
    lowered = text.lower()
    return next((origin for word, origin in rules.items() if word in lowered), None)


def parse_referenda(answers: list[list[dict]], source: dict) -> list[dict]:
    """The statewide measures among `answers` (each one point's parsed
    contests), as the reader dicts ballot_measures_pdf._to_measure takes."""
    answering = [a for a in answers if a]
    if len(answering) < STATEWIDE_ANSWERS:
        return []
    def titles(answer):
        return {m["title"]: m for m in answer if m.get("kind") == "measure"}
    common = set(titles(answering[0]))
    for answer in answering[1:]:
        common &= set(titles(answer))
    out = []
    for title in (t for t in titles(answering[0]) if t in common):
        measure = titles(answering[0])[title]
        first, rest = _split_title(title)
        # A heading VIP carries as the subtitle (Minnesota's "Increasing
        # funding to school districts") names a one-line title; a subtitle
        # that is only the title's own word (Michigan's "PROPOSAL") doesn't.
        subtitle = (measure.get("subtitle") or "").strip()
        heading = subtitle if subtitle and subtitle.lower() not in first.lower() else None
        number = _NUMBER_RE.search(first)
        out.append({
            "number": number.group(1) if number else first,
            "id_key": None if number else first,
            "title": rest or heading or first,
            "official_title": rest,
            "official_summary": measure.get("text"),
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "origin": _origin(rest or first, source.get("origin_when_title_says") or {}),
            "title_authority": source.get("title_authority"),
            "republished_by": PUBLISHER,
        })
    return out


async def fetch_measures(client: httpx.AsyncClient, year: int, state: str, source: dict):
    """(parsed, source url) pairs for `state`'s statewide measures, None on
    a failed fetch; NotYetPublished while Google holds no ballot for it."""
    if not is_configured():
        return None
    election_id = await general_election_id(client, state, year)
    if election_id is _FETCH_FAILED:
        return None
    if election_id is None:
        raise NotYetPublished(f"{PUBLISHER}: no {year} general listed for {state} yet", deadline_applies=False)
    points = _points(state)
    def districts(kind: str) -> list[str]:
        keys = [k for k in points if k.split("-")[1] == kind]
        return sorted(keys, key=lambda k: int(re.sub(r"\D", "", k.rsplit("-", 1)[1]) or 0))
    # Congressional districts far apart in the state's numbering (an
    # at-large state's state Senate districts), a point at a time until
    # STATEWIDE_ANSWERS of them have answered.
    pool = districts("H") if len(districts("H")) >= STATEWIDE_ANSWERS else districts("upper")
    spread = pool[:: max(1, len(pool) // (STATEWIDE_ANSWERS + 1))] or pool
    answers: list[list[dict]] = []
    for district in spread:
        for lat, lon in points[district]:
            contests = await contests_at(client, election_id, f"{lat},{lon}", f"Civic voterinfo ({district})")
            if contests is None:
                return None
            if contests:
                answers.append(contests)
                break
        if len(answers) >= STATEWIDE_ANSWERS:
            break
    parsed = parse_referenda(answers, source)
    if len(answers) < STATEWIDE_ANSWERS:
        raise NotYetPublished(f"{PUBLISHER}: {len(answers)} of {STATEWIDE_ANSWERS} districts answered for {state}",
                              deadline_applies=False)
    link = source.get("link_url")
    return [(p, link) for p in parsed]
