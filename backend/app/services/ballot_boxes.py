"""The state ballot page's boxes: one short, templated line and one counted
figure per part of the ballot ("Boxes, not blocks", AGENTS.md).

Every sentence is a fixed template filled from the state ballot payload
(api/elections.state_ballot): counts of the lists the page shows, the
state's name, the lean's label and dates. Nothing here describes what a
measure does or what a vote means: measures, yes/no wording and deadlines
stay quoted verbatim where the page shows them (AGENTS.md section 7).
Counts are of the lists as served, so a box and the list behind it agree,
and the page never counts them itself (backend owns calculations).
"""

from datetime import date

from app.district_lean import lean_label

# Federal terms are fixed by the Constitution (art. I, sections 2 and 3);
# a special Senate election fills only what is left of the vacated term.
_HOUSE_TERM = "2-year term"
_SENATE_TERM = "6-year term"
_SPECIAL_TERM = "fills the rest of the term"
_INACTIVE = ("removed", "withdrawn")


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _stat(value: int, caption: str, text: str | None = None) -> dict:
    figure = {"type": "stat", "value": value, "caption": caption}
    if text is not None:
        figure["text"] = text
    return figure


def _long_date(iso: str | None) -> str | None:
    try:
        d = date.fromisoformat((iso or "")[:10])
    except ValueError:
        return None
    return f"{d:%B} {d.day}, {d.year}"


def is_active(c: dict) -> bool:
    """The page's own rule for a candidate it lists as running
    (frontend isActiveCandidate): confirmed by the state, a declared FEC
    candidate, one who has raised money, or the incumbent."""
    return bool(c.get("confirmed") or c.get("candidateStatus") == "C"
                or c.get("hasRaisedFunds") or c.get("incumbentChallenge") == "I")


def _major(c: dict) -> str | None:
    group = c.get("partyGroup") or c.get("party")
    return group if group in ("DEM", "REP") else None


def _statewide_groups(races: list[dict]) -> list[list[dict]]:
    """Statewide rows with every seat of one body together (the rows that
    share an officeCode and carry a seat), as the page groups them."""
    groups: list[list[dict]] = []
    for race in races:
        last = groups[-1] if groups else None
        if (race.get("seat") and race.get("officeCode") and last and last[0].get("seat")
                and last[0].get("officeCode") == race["officeCode"]):
            last.append(race)
        else:
            groups.append([race])
    return groups


def _statewide_marked(races: list[dict]) -> int:
    """Statewide contests one voter marks: each office, and for a body with
    seats, one seat where each voter votes in a single district's, else
    every seat (frontend countStatewideContests)."""
    return sum(1 if g[0].get("seat") and g[0].get("electedBy") == "district" else len(g)
               for g in _statewide_groups(races))


def _covered(coverage: dict | None) -> bool:
    return bool(coverage) and coverage.get("status") != "not_yet_covered"


def _basis_words(ballot_list: bool) -> str:
    return "" if ballot_list else " From primary results."


def _seat_line(race: dict) -> str:
    incumbent = next((c for c in race["candidates"] if c.get("incumbentChallenge") == "I"), None)
    seat = (f"{incumbent.get('displayName') or incumbent.get('name')} running again"
            if incumbent else "Open seat")
    return f"Special election · {seat} · {_SPECIAL_TERM}" if race.get("isSpecial") \
        else f"{seat} · {_SENATE_TERM}"


def _measures_box(ballot: dict) -> dict:
    measures = ballot.get("measures") or []
    coverage = ballot.get("measureCoverage") or {}
    active = sum(1 for m in measures if m.get("status") not in _INACTIVE)
    none = coverage.get("status") == "confirmed_none"
    if active:
        # A list the latest read didn't confirm is the last good read's,
        # and says so (the drawer's notice says the rest).
        confirmed = coverage.get("status") in ("covered", "confirmed_none") \
            and not coverage.get("unreadReason")
        return {"dek": f"{_plural(active, 'measure')}, each quoted as the state "
                       "publishes it." if confirmed else
                       f"{_plural(active, 'measure')} from our last successful read, which "
                       "may be out of date.",
                "figure": _stat(active, "measure" if active == 1 else "measures")}
    if none and (measures or coverage.get("basis") == "operator"):
        return {"dek": "None remaining, as far as Civitas can tell.", "figure": None}
    if none:
        return {"dek": "None on this ballot, per the state's own list.",
                "figure": _stat(0, "measures")}
    if measures:
        return {"dek": "None current: check the official lookup.", "figure": None}
    # Not read is not none: no zero figure for a list nobody has.
    return {"dek": "Not loaded yet: check the official lookup.", "figure": None}


def contest_boxes(ballot: dict) -> dict[str, dict]:
    """{contest key: {"dek", "figure"}}, keyed as the page keys its
    contests (lib/ballotContests.ts)."""
    out: dict[str, dict] = {}
    coverage = ballot.get("coverage") or []
    for race in ballot.get("senateRaces") or []:
        n = sum(1 for c in race["candidates"] if is_active(c))
        stories = sum(1 for c in coverage if (c.get("race") or {}).get("id") == race["id"])
        out[f"senate-{race['id']}"] = {
            "dek": f"{_seat_line(race)}. {_plural(stories, 'news story', 'news stories')}.",
            "figure": _stat(n, "candidate" if n == 1 else "candidates"),
        }

    house = ballot.get("houseRaces") or []
    if len(house) == 1:
        n = sum(1 for c in house[0]["candidates"] if is_active(c))
        out["house"] = {"dek": f"One statewide seat · {_HOUSE_TERM}.",
                        "figure": _stat(n, "candidate" if n == 1 else "candidates")}
    elif house:
        out["house"] = {"dek": f"{len(house)} districts, one per voter · {_HOUSE_TERM}.",
                        "figure": _stat(len(house), "districts")}

    if ballot.get("nextSenateElection") is not None:
        # Senators serve staggered six-year terms (art. I, section 3), so a
        # state votes on a seat about one cycle in three.
        out["no-senate"] = {
            "dek": f"Not up in {ballot['cycleYear']}; next regular election "
                   f"{ballot['nextSenateElection']}.",
            "figure": None,
        }

    statewide_cov = ballot.get("statewideCoverage") or {}
    if _covered(statewide_cov):
        n = len(_statewide_groups(ballot.get("statewideRaces") or []))
        out["statewide"] = (
            {"dek": "None on this ballot.", "figure": None} if n == 0 else
            {"dek": f"{_plural(n, 'office')} on the ballot.{_basis_words(bool(statewide_cov.get('ballotList')))}",
             "figure": _stat(n, "office" if n == 1 else "offices")})

    chambers = ballot.get("stateLegRaces") or []
    if chambers:
        seats = sum(len(c["districts"]) for c in chambers)
        out["stateleg"] = {
            "dek": f"{_plural(seats, 'seat')} contested."
                   f"{_basis_words(bool(statewide_cov.get('ballotList')))}",
            "figure": _stat(seats, "seat" if seats == 1 else "seats"),
        }

    judicial_cov = ballot.get("judicialCoverage") or {}
    if _covered(judicial_cov):
        courts = len(ballot.get("judicialRaces") or [])
        out["judicial"] = (
            {"dek": "None on this ballot: judges here are elected at the primary.", "figure": None}
            if courts == 0 else
            {"dek": f"{_plural(courts, 'court')} on the ballot.{_basis_words(bool(judicial_cov.get('ballotList')))}",
             "figure": _stat(courts, "court" if courts == 1 else "courts")})

    out["state-offices"] = {"dek": "Not loaded yet, which does not mean none: check the official "
                                   "lookup.", "figure": None}
    out["measures"] = _measures_box(ballot)
    n = len(coverage)
    out["news"] = ({"dek": f"{_plural(n, 'story', 'stories')} about these races.",
                    "figure": _stat(n, "story" if n == 1 else "stories")}
                   if n else {"dek": "No coverage of these races yet.", "figure": None})
    return out


def contest_count(ballot: dict) -> int:
    """Contests a voter marks (frontend countBallotContests): each Senate
    race, one House race, each statewide contest, one seat per legislative
    chamber, each court and each measure still on the ballot."""
    n = len(ballot.get("senateRaces") or []) + (1 if ballot.get("houseRaces") else 0)
    if _covered(ballot.get("statewideCoverage")):
        n += _statewide_marked(ballot.get("statewideRaces") or [])
    n += len(ballot.get("stateLegRaces") or [])
    if _covered(ballot.get("judicialCoverage")):
        n += len(ballot.get("judicialRaces") or [])
    n += sum(1 for m in ballot.get("measures") or [] if m.get("status") not in _INACTIVE)
    return n


def summary_boxes(ballot: dict) -> list[dict]:
    """The ballot's shape at a glance, one box per figure."""
    name = ballot.get("stateName") or ballot["state"]
    federal = [c for r in (ballot.get("senateRaces") or []) + (ballot.get("houseRaces") or [])
               for c in r["candidates"] if is_active(c)]
    basis = (ballot.get("ballotBasis") or {}).get("basis")
    boxes = []

    if federal:
        records = sum(1 for c in federal if c.get("incumbentRecord") or c.get("memberRecord"))
        others = sum(1 for c in federal if _major(c) is None)
        title = (f"{len(federal):,} registered with the FEC" if basis == "filers"
                 else f"{_plural(len(federal), 'federal candidate')}")
        if records:
            title += f", {records:,} with a voting record in Congress"
        primary = _long_date(ballot.get("primaryDate"))
        dek = {
            "confirmed": "From the state's certified list for this ballot.",
            "nominees": f"Nominees from the {primary} primary results." if primary
                        else "Nominees from the state's primary results.",
            "primary": "From the state's primary ballot.",
            "filers": "Registered with the FEC; the state certifies its own ballot after the "
                      "primary.",
        }.get(basis, "")
        if others:
            dek = f"{dek} {_plural(others, 'candidate')} outside the two major parties.".strip()
        boxes.append({
            "key": "candidates", "kicker": "Who is on it", "title": title, "dek": dek,
            "figure": _stat(len(federal), "FEC filers" if basis == "filers"
                            else "candidate" if len(federal) == 1 else "candidates"),
        })

    pvi = ballot.get("statePvi")
    if pvi is not None:
        label = lean_label(pvi)
        boxes.append({
            "key": "lean", "kicker": "Usual vote",
            "title": f"{name} votes about evenly" if label == "EVEN" else f"{name} leans {label}",
            "dek": "Against the national presidential vote, from Cook PVI. Not a forecast.",
            "figure": _stat(pvi, "statewide lean", text=label),
        })

    ev = ballot.get("electoralVotes")
    if ev is not None:
        boxes.append({
            "key": "electoral", "kicker": "President",
            "title": f"{_plural(ev, 'electoral vote')} for president",
            "dek": "From the House apportionment.",
            "figure": _stat(ev, "electoral vote" if ev == 1 else "electoral votes"),
        })
    return boxes


def ballot_boxes(ballot: dict) -> dict:
    return {"summary": summary_boxes(ballot), "contests": contest_boxes(ballot),
            "contestCount": contest_count(ballot)}
