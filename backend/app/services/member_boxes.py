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
from app.config_definitions import BILL_STAGES
from app.models import PresidentialSupport
from app.peer_comparison import SUPPORT_RULE
from app.pipeline.analyze.score_calculator import SUBSTANTIVE_BILL_TYPES
from app.readout import compact_money, seat_name
from app.score_display import shown_share
from app.services.congress_service import bill_label
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
# Top donors that aren't donors to the member: the member's own committee
# and self-funding (the Money box's donor list leaves them out too).
_NOT_DONORS = {"CandidateAffiliated", "Self-Funded"}
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


def money_box(funding: dict | None, election_year: int | None) -> dict:
    """The money's total and its largest donor. The PAC and small-gift
    shares are "Where <name> stands"'s strips, so the box states neither
    (each fact once on the page); its line names who gave, the record
    behind those shares."""
    when = f"{election_year} election" if election_year else "most recent completed election"
    total = (funding or {}).get("totalContributions") or (funding or {}).get("totalRaised") or 0
    donor = next((d for d in (funding or {}).get("topDonors") or []
                  if d.get("type") not in _NOT_DONORS and (d.get("total") or 0) > 0), None)
    # An employer's line is its employees' gifts, as the donor list says.
    who = f"{donor['name']} employees" if donor and donor.get("type") == "Org/Employees" else (donor or {}).get("name")
    dek = f"Largest donor: {who}, ${donor['total']:,.0f}." if donor else None
    box = {"kicker": f"Money · {when}", "dek": dek}
    if total <= 0:
        return {**box, "headline": "No contributions on file", "figure": None}
    shown = compact_money(total)
    return {**box, "headline": f"{shown} in contributions", "figure": _stat(total, shown, "in contributions")}


# What a break was a vote on, one and many, in the order a tie is told.
_BREAK_KINDS = {
    "nominations": ("a nomination", "nominations"),
    "amendments": ("an amendment", "amendments"),
    "bills": ("a bill or resolution", "bills and resolutions"),
    "other": ("another question", "other questions"),
}


def _break_kind(vote: dict) -> str:
    """What a break was a vote on, from the roll call's own fields."""
    rc = vote.get("rollCall") or {}
    if rc.get("nominationVote"):
        return "nominations"
    if rc.get("amendment"):
        return "amendments"
    return "bills" if rc.get("billId") else "other"


def votes_box(dimension: dict | None) -> dict:
    """What the member's breaks with the party were votes on. How many
    there were, against the seat and the peers, is "Where <name> stands"'s
    strip, so the box leads with the breaks themselves: the kind most of
    them were (nominations, bills), the rest, and the newest one's date."""
    facts = (dimension or {}).get("facts") or {}
    box = {"kicker": "Votes · this Congress", "dek": None, "figure": None}
    listed = facts.get("breakVotes")
    if not facts.get("partyVotes"):
        return {**box, "headline": "No party-line votes on record yet"}
    if listed is None:
        # Before the whole-Congress record is measured, the list behind
        # More is the stored votes; the count is the strip's.
        return {**box, "headline": "Votes against the party, as recorded"}
    if not listed:
        flank = len(facts.get("flankBreakVotes") or [])
        if not flank:
            return {**box, "headline": "No votes against the party to list"}
        text = f"{flank:,}"
        caption = ("vote against the party from its flank, not counted" if flank == 1
                   else "votes against the party from its flank, not counted")
        return {**box, "headline": f"{text} {caption}", "figure": _stat(flank, text, caption)}
    counts = {k: 0 for k in _BREAK_KINDS}
    for v in listed:
        counts[_break_kind(v)] += 1
    kinds = sorted((k for k in counts if counts[k]), key=lambda k: -counts[k])
    lead, n = kinds[0], counts[kinds[0]]
    dates = [d for d in ((v.get("rollCall") or {}).get("date") for v in listed) if d]
    newest = f"Newest {max(dates)}." if dates else ""
    if len(kinds) == 1:
        # One kind: its count would be the strip's count again, so no figure.
        headline = (f"The break was on {_BREAK_KINDS[lead][0]}" if n == 1
                    else f"Every break was on {_BREAK_KINDS[lead][1]}")
        return {**box, "headline": headline, "dek": newest or None}
    text = f"{n:,}"
    caption = f"break was on {_BREAK_KINDS[lead][0]}" if n == 1 else f"breaks were on {_BREAK_KINDS[lead][1]}"
    rest = ", ".join(f"{counts[k]:,} on {_BREAK_KINDS[k][0 if counts[k] == 1 else 1]}" for k in kinds[1:])
    dek = " ".join(p for p in (f"{rest[0].upper()}{rest[1:]}.", newest) if p)
    return {**box, "headline": f"{text} {caption}", "dek": dek, "figure": _stat(n, text, caption)}


# A sponsored bill's furthest stage as the box says it, by BILL_STAGES code.
_STAGE_PHRASE = {
    "ENACTED": "became law",
    "VETOED": "was vetoed",
    "TO_PRESIDENT": "went to the president",
    "PASSED_BOTH": "passed both chambers",
    "IN_OTHER_CHAMBER": "passed {chamber}",
    "PASSED_CHAMBER": "passed {chamber}",
    "ON_FLOOR": "reached the floor",
    "REPORTED": "was reported by committee",
    "IN_COMMITTEE": "got committee action",
}


def bills_box(bills: list[dict] | None, branch: str) -> dict:
    """The member's bill furthest along, named, with its title and latest
    action. How many bills got how far is "Where <name> stands"'s strip and
    the box's stage chart, so the box names the bill. With none past
    referral, the newest bill. Bills and joint resolutions only, as the
    counts take them."""
    box = {"kicker": "Bills · this Congress", "figure": None}
    own = [b for b in bills or []
           if (b.get("billType") or "").lower() in SUBSTANTIVE_BILL_TYPES and bill_label(b.get("billId"))]
    if not own:
        return {**box, "headline": "No bills sponsored this Congress", "dek": None}
    top = max(own, key=lambda b: ((BILL_STAGES.get(b.get("stage") or "") or {}).get("order", 0),
                                  b.get("latestActionDate") or "", b.get("introducedDate") or ""))
    phrase = _STAGE_PHRASE.get(top.get("stage") or "")
    if phrase:
        headline = f"{bill_label(top['billId'])} {phrase.format(chamber=_CHAMBER[branch])}"
        when = f"Latest action {top['latestActionDate']}." if top.get("latestActionDate") else ""
    else:
        top = max(own, key=lambda b: b.get("introducedDate") or "")
        headline = f"Newest bill: {bill_label(top['billId'])}"
        when = f"Introduced {top['introducedDate']}." if top.get("introducedDate") else ""
    title = (top.get("title") or "").rstrip(".")
    return {**box, "headline": headline, "dek": " ".join(p for p in (f"{title}." if title else "", when) if p)
            or None}


def president_box(db: Session, member_id: str) -> dict | None:
    """Votes on the president's stated side, against the seat's expected
    rate; None for a member with no counted vote."""
    row = db.query(PresidentialSupport).filter_by(member_id=member_id).first()
    if row is None or not row.votes:
        return None
    s = support_summary(row)
    text = f"{s['withPresident']:,} of {s['votes']:,}"
    caption = "votes on the president's stated side" if s["votes"] != 1 else "vote on the president's stated side"
    # A record under the measures page's minimum draws no comparison there
    # (peer_comparison.SUPPORT_RULE), so none here either.
    thin = s["votes"] < SUPPORT_RULE["minRecord"]
    verdict = None if thin else VERDICT.get(s["comparedWithExpected"] or "")
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
        "money": money_box(scorecard.get("funding"), getattr(entity, "funding_election_year", None)),
        "votes": votes_box(breakdown.get("constituentAlignment")),
        "bills": bills_box(scorecard.get("sponsoredBills"), branch),
        "president": president_box(db, entity.id),
        "approval": approval_box(scorecard.get("constituentApproval"), party, identity.get("stateName") or "",
                                 district),
        "holdings": holdings_box(holdings),
        "trades": trades_box(*trades, branch),
        "positions": positions_box(scorecard.get("partisanDepth"), party),
        "record": record_box(record),
    }
