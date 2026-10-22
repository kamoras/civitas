"""A member profile's boxes: every title, line and figure the page shows
for a senator or representative, served on the profile API (`boxes`) so the
page draws them and counts nothing (AGENTS.md "Boxes, not blocks").

Every sentence is a fixed template filled from stored fields or from the
figures another service already computed: the score breakdown's facts and
findings (score_findings), the votes-with-the-president summary
(presidential_support_service), the holdings breakdown, the trade counts,
the survey reading (constituent_survey). Nothing here measures anything
new, no model writes any of it, and no sentence about the member uses a
pronoun: the page is about one member, so lines are subjectless or name
the group ("88% of Oregon Democrats approve").

Read path only: ORM reads and string templates.
"""

from sqlalchemy.orm import Session

from app.election_calendar import class_up, senator_class
from app.election_phase import active_election
from app.live_results.sync import elected_congress_sits
from app.models import PresidentialSupport
from app.readout import compact_money, seat_name
from app.score_display import shown_share
from app.services.constituent_survey import survey_year
from app.services.presidential_support_service import summary as support_summary

_PLURAL = {"D": "Democrats", "R": "Republicans", "I": "independents"}
_ADJECTIVE = {"D": "Democratic", "R": "Republican"}
_CHAMBER = {"senate": "the Senate", "house": "the House"}
_OFFICE = {"S": "the Senate", "H": "the House", "P": "president"}
# How a box names the president's stated position against the seat's
# expected rate (presidential_support_service.summary's comparedWithExpected).
VERDICT = {
    "within": "about as expected",
    "more": "more often than expected",
    "less": "less often than expected",
}
# A Senate seat comes up every six years: three cycles of two.
_SENATE_TERM_CYCLES = 3


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _stat(value: float, text: str, caption: str) -> dict:
    return {"type": "stat", "value": value, "text": text, "caption": caption}


def short_committee(name: str) -> str:
    """A committee as a chip names it: the chamber and "Committee on"
    dropped from Congress.gov's name ("Senate Committee on the Budget" is
    "Budget"); a select or joint committee keeps its kind ("Select
    Committee on Intelligence", "Joint Committee on Taxation")."""
    for prefix in ("Senate ", "House "):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    if name.startswith("Committee on "):
        name = name[len("Committee on "):]
        if name.startswith("the "):
            name = name[len("the "):]
    return name


def committees_box(committees: list[dict]) -> dict:
    """The header's one committee line ("5 committees · Ranking Member,
    Finance") and the chips behind its More."""
    items = [{"name": short_committee(c.get("committeeName") or ""), "fullName": c.get("committeeName") or "",
              "title": c.get("title"), "chamber": c.get("chamber")} for c in committees]
    posts = [f"{c['title']}, {c['name']}" for c in items if c["title"]]
    line = _plural(len(items), "committee") if items else "No committee seats on file"
    return {"count": len(items), "line": " · ".join([line, *posts]), "posts": posts, "items": items}


def ballot_line(db: Session, branch: str, entity, on_ballot: dict | None, seat_race: dict | None) -> dict | None:
    """The header's one-line ballot status, or None when nothing on file
    says (a House seat with no race stored yet): on the ballot, the seat
    on the ballot without the member as a nominee, or (a senator) the
    cycle the seat is next up."""
    cycle = active_election(db).cycle
    own = "S" if branch == "senate" else "H"
    if on_ballot:
        same_seat = on_ballot.get("office") == own and (
            own == "S" or on_ballot.get("district") == getattr(entity, "district", None))
        what = "for re-election" if same_seat else f"for {_OFFICE.get(on_ballot.get('office'), 'another office')}"
        return {"line": f"On the {on_ballot['cycleYear']} ballot {what}", "raceId": on_ballot["raceId"]}
    if seat_race:
        status = seat_race.get("status")
        if status == "notNominee":
            line = f"Seat on the {seat_race['cycleYear']} ballot; not a nominee for it"
        elif status == "unconfirmed":
            line = f"Seat on the {seat_race['cycleYear']} ballot; nominees not confirmed yet"
        else:
            line = f"On the {seat_race['cycleYear']} ballot for re-election"
        return {"line": line, "raceId": seat_race["raceId"]}
    if branch != "senate":
        return None
    cls = senator_class(entity.bioguide_id)
    if cls is None:
        return None
    first = cycle + 2 if elected_congress_sits(cycle) else cycle
    year = next((y for y in range(first, first + 2 * _SENATE_TERM_CYCLES, 2) if class_up(y) == cls), None)
    if year is None:
        return None
    return {"line": f"Seat on the {year} ballot" if year == cycle else f"Seat next on the ballot in {year}",
            "raceId": None}


def money_box(funding: dict | None, election_year: int | None, dimension: dict | None) -> dict:
    when = f"{election_year} election" if election_year else "most recent completed election"
    total = (funding or {}).get("totalContributions") or (funding or {}).get("totalRaised") or 0
    box = {"kicker": f"Money · {when}", "dek": (dimension or {}).get("finding")}
    if total <= 0:
        return {**box, "headline": "No contributions on file", "figure": None}
    shown = compact_money(total)
    return {**box, "headline": f"{shown} in contributions", "figure": _stat(total, shown, "in contributions")}


def votes_box(dimension: dict | None) -> dict:
    facts = (dimension or {}).get("facts") or {}
    box = {"kicker": "Votes · this Congress", "dek": (dimension or {}).get("finding")}
    votes, breaks = facts.get("partyVotes") or 0, facts.get("breaks")
    if not votes:
        return {**box, "headline": "No party-line votes on record yet", "figure": None}
    if breaks is None:
        return {**box, "headline": f"{_plural(votes, 'party-line vote')} on record", "figure": None}
    text = f"{breaks:,} of {votes:,}"
    caption = "party-line votes against the party" if votes != 1 else "party-line vote against the party"
    return {**box, "headline": f"{text} {caption}", "figure": _stat(breaks, text, caption)}


def bills_box(dimension: dict | None, branch: str) -> dict:
    """How far the member's bills got, by the same stages and the same
    precedence as the finding beneath it (score_findings.effectiveness_finding)."""
    facts = (dimension or {}).get("facts") or {}
    box = {"kicker": "Bills · this Congress", "dek": (dimension or {}).get("finding")}
    bills = facts.get("bills")
    stages = facts.get("billsByStage")
    if not bills or not stages:
        return {**box, "headline": "No bills sponsored this Congress", "figure": None}
    _, committee, beyond, passed, law = stages
    if law:
        count, caption = law, "became law"
    elif passed:
        count, caption = passed, f"passed {_CHAMBER[branch]}"
    elif committee + beyond:
        count, caption = committee + beyond, "got committee action"
    else:
        count, caption = 0, "moved past introduction"
    text = f"{count:,} of {bills:,}"
    return {**box, "headline": f"{text} {caption}", "figure": _stat(count, text, caption)}


def president_box(db: Session, member_id: str) -> dict | None:
    """Votes on the president's stated side, against the seat's expected
    rate; None for a member with no counted vote."""
    row = db.query(PresidentialSupport).filter_by(member_id=member_id).first()
    if row is None or not row.votes:
        return None
    s = support_summary(row)
    text = f"{s['withPresident']:,} of {s['votes']:,}"
    caption = "votes on the president's stated side" if s["votes"] != 1 else "vote on the president's stated side"
    verdict = VERDICT.get(s["comparedWithExpected"] or "")
    rate = shown_share(s["rate"])
    if verdict and s["expected"] is not None:
        expected = shown_share(s["expected"])
        dek = f"{rate}, against {expected} for the same party in seats that lean the same way: {verdict}."
        finding = (f"Voted on the president's stated side on {text} counted votes ({rate}). Members of the same "
                   f"party in seats that lean the same way do so on {expected}: {verdict}.")
    else:
        dek = f"{rate} of the counted votes."
        finding = f"Voted on the president's stated side on {text} counted votes ({rate})."
    return {"kicker": "With the president", "headline": f"{text} {caption}", "dek": dek, "finding": finding,
            "verdict": verdict, "figure": _stat(s["withPresident"], text, caption)}


def approval_box(approval: dict | None, party: str, state_name: str, district: int | None) -> dict | None:
    """The member's own constituents' approval, led by the member's own
    party's figure, from the one reading the scorecard serves
    (constituent_survey.constituent_approval)."""
    groups = [g for g in (approval or {}).get("byParty") or [] if g.get("approve") is not None]
    if not groups:
        return None
    lead = next((g for g in groups if g["party"] == party), groups[0])
    who = _PLURAL.get(lead["party"], "respondents")
    place = (f"{state_name} {who}" if district is None
             else f"{who} in {seat_name(state_name, district, False)}")
    year = survey_year()
    return {
        "kicker": f"Approval at home · CES {year}" if year else "Approval at home",
        "headline": f"{shown_share(lead['approve'])} of {place} approve",
        "dek": ", ".join(f"{_PLURAL.get(g['party'], g['party']).capitalize()} {shown_share(g['approve'])}"
                         for g in groups) + ".",
        "figure": _stat(lead["approve"], shown_share(lead["approve"]), f"of {place} approve"),
    }


def holdings_box(holdings) -> dict | None:
    """The newest annual report's holdings (holdings_service), or None
    when no report has been read for the member."""
    if holdings is None or not holdings.available:
        return None
    kicker = f"Holdings · {holdings.report_label}" if holdings.report_label else "Holdings"
    filed = f"Filed {holdings.filed_date}." if holdings.filed_date else None
    if not holdings.parsed:
        return {"kicker": kicker, "headline": "Report on file could not be read", "dek": filed, "figure": None}
    n = holdings.holdings_count
    unvalued = (f"{_plural(holdings.unvalued_count, 'asset')} with no stated value, not charted."
                if holdings.unvalued_count else None)
    return {"kicker": kicker,
            "headline": f"{holdings.total_shown} disclosed across {_plural(n, 'asset')}",
            "dek": " ".join(x for x in (filed, unvalued) if x) or None,
            "figure": _stat(holdings.total_low, holdings.total_shown, f"disclosed across {_plural(n, 'asset')}")}


def trades_box(total: int, late: int, branch: str) -> dict:
    chamber = "Senate" if branch == "senate" else "House"
    dek = f"Periodic transaction reports filed with the {chamber}."
    if not total:
        return {"kicker": "Stock trades · STOCK Act reports", "headline": "No trades disclosed", "dek": dek,
                "figure": None}
    text = f"{total:,}"
    caption = f"{'trade' if total == 1 else 'trades'} disclosed, {late:,} late"
    return {"kicker": "Stock trades · STOCK Act reports", "headline": f"{text} {caption}", "dek": dek,
            "figure": _stat(total, text, caption)}


def positions_box(depth: dict | None, party: str) -> dict | None:
    """Policy areas whose party-labelled votes lean with the member's own
    party (partisan depth's per-area alignment)."""
    areas = (depth or {}).get("policyBreakdown") or []
    if not areas or party not in _ADJECTIVE:
        return None
    same = sum(1 for a in areas if a.get("alignment") == party)
    text = f"{same:,} of {len(areas):,}"
    caption = "area on the party line" if len(areas) == 1 else "areas on the party line"
    return {"kicker": "Positions by policy area", "headline": f"{text} {caption}",
            "dek": depth.get("leanSentence"), "figure": _stat(same, text, caption)}


def record_box(record: dict) -> dict:
    n = record.get("totalDocs") or 0
    return {"kicker": "Government record · verbatim sources",
            "headline": f"{_plural(n, 'document')} on public record" if n else "No documents on record",
            "dek": "Floor speeches and documents, newest first." if n else None,
            "figure": _stat(n, f"{n:,}", "documents on public record") if n else None}


def boxes(db: Session, branch: str, entity, identity: dict, scorecard: dict | None, breakdown: dict | None,
          holdings, trades: tuple[int, int], record: dict, on_ballot: dict | None,
          seat_race: dict | None) -> dict:
    """Every box on a senator's or representative's profile. `scorecard` is
    the profile's scorecard as served (camelCase), `breakdown` the score
    breakdown (with findings), `holdings` holdings_service's schema,
    `trades` (disclosed, late)."""
    scorecard = scorecard or {}
    breakdown = breakdown or {}
    party = getattr(entity, "caucus_party", None) or entity.party
    district = identity.get("district") if branch == "house" else None
    return {
        "committees": committees_box(identity.get("committees") or []),
        "ballot": ballot_line(db, branch, entity, on_ballot, seat_race),
        "fundingElectionYear": getattr(entity, "funding_election_year", None),
        "money": money_box(scorecard.get("funding"), getattr(entity, "funding_election_year", None),
                           breakdown.get("fundingIndependence")),
        "votes": votes_box(breakdown.get("constituentAlignment")),
        "bills": bills_box(breakdown.get("legislativeEffectiveness"), branch),
        "president": president_box(db, entity.id),
        "approval": approval_box(scorecard.get("constituentApproval"), party, identity.get("stateName") or "",
                                 district),
        "holdings": holdings_box(holdings),
        "trades": trades_box(*trades, branch),
        "positions": positions_box(scorecard.get("partisanDepth"), party),
        "record": record_box(record),
    }
