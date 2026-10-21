"""The four measures a member is compared on, read from the facts the
scorer stores beside each member's scores (score_breakdown): breaks with
party, PAC money, small donors, bills past committee. One definition for the peer
comparison (app/peer_comparison.py), the measures page and the facts each
score snapshot keeps (models.ScoreSnapshot.facts).

Imports nothing from the app, so models.py can use it. Outside
app/pipeline/, so it never moves the analysis-code fingerprint.
"""

import json
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Measure:
    key: str
    label: str
    dimension: str
    direction: str
    unit: str
    source: dict
    value: Callable[[dict], float | None]
    seat_expected: Callable[[dict], float | None]
    record: Callable[[dict], int | None]
    question: str = ""
    why: str = ""
    measured: str = ""
    # The span the figure covers: "congress" (this Congress, so it moves
    # night to night and can be drawn over time) or "election" (the
    # member's most recent completed election, AGENTS.md section 6, so it
    # stays flat between elections and is never drawn as a nightly line).
    window: str = "congress"


def funding_facts(facts: dict) -> dict:
    return facts.get("fundingIndependence") or {}


def alignment_facts(facts: dict) -> dict:
    return facts.get("constituentAlignment") or {}


def bill_facts(facts: dict) -> dict:
    return facts.get("legislativeEffectiveness") or {}


def past_committee(facts: dict) -> int | None:
    stages = bill_facts(facts).get("billsByStage")
    # [introduced only, committee action, past committee, passed the chamber, law]
    return sum(stages[2:]) if isinstance(stages, list) and len(stages) == 5 else None


_FEC = {"name": "FEC filings", "url": "https://www.fec.gov/data/"}

# Each strip answers one question a voter has, and says why in one line.
# Order is how widely each matters: every roll-call vote binds the whole
# state or district; money is the main way outside interests reach a
# member (two strips, PACs then small donors, the two ends of who pays);
# a member's own bills are one part of lawmaking, and most bills stop in
# committee whoever sponsors them. The `why` lines are fixed text, about
# grade 8, neutral (what the figure is and why a voter might care, never
# what to conclude); `measured` is the researcher's version. Sources for
# both are cited on /about/scores#peers.
MEASURES = (
    Measure("breaks", "Votes against the party", "constituentAlignment",
            "higher means more breaks with the party on party-line votes", "rate",
            {"name": "Roll-call votes", "url": "https://voteview.com/"},
            lambda f: alignment_facts(f).get("breakRate"),
            lambda f: alignment_facts(f).get("expectedBreakRate"),
            lambda f: alignment_facts(f).get("partyVotes"),
            question="How does this member vote?",
            why=("Share of party-line votes where this member sided with most of the other party. "
                 "It shows how closely votes follow the party; members from seats that lean the "
                 "other way tend to break more."),
            measured=("A party-line vote is a roll call where most Democrats and most Republicans "
                      "voted on opposite sides. A break is a vote with the other party's majority, "
                      "counted over this Congress; a vote against the party from its outer "
                      "flank (judged by the member's Voteview position) is not counted, and each "
                      "measure counts once. The tick is the seat average: the mean break rate for the "
                      "same party in seats with the same Cook PVI lean, measured each run from every "
                      "member. A few members break often and most rarely, so most sit below the "
                      "average and the peers' middle is lower than it. "
                      "Fewer than 20 party-line votes draws no word.")),
    Measure("pacMoney", "Money from PACs", "fundingIndependence",
            "higher means more of the money came from PACs", "share", _FEC,
            lambda f: funding_facts(f).get("pacShare") if funding_facts(f).get("contributions") else None,
            lambda f: None,
            lambda f: 1 if funding_facts(f).get("contributions") else 0,
            question="Who funds the campaign?",
            why=("Share of contributions from PACs, which pool money for companies, unions, trade "
                 "groups and causes. A larger share means more of the money came from groups, less "
                 "from individual donors."),
            measured=("Contributions from other political committees (PACs, not party committees) "
                      "over all contributions, from the FEC's totals. Window: the member's "
                      "most recent completed election, not this Congress: six years of filings "
                      "for a senator, two for a representative. There is no seat-based "
                      "expectation here, so the tick is the peer median."),
            window="election"),
    Measure("smallGifts", "Money from small donors", "fundingIndependence",
            "higher means more of the money came from donors who gave $200 or less", "share", _FEC,
            lambda f: funding_facts(f).get("smallDonorShare"),
            # The House compares with the chamber median, not a seat fit: no
            # seat expectation then, so the row's tick is the peer median.
            lambda f: (funding_facts(f).get("smallDonorExpectedShare")
                       if funding_facts(f).get("smallDonorComparison") != "house-median" else None),
            lambda f: 1 if funding_facts(f).get("contributions") else 0,
            question="Who funds the campaign?",
            why=("Share of contributions from people who gave the campaign $200 or less in total. "
                 "More small donors can mean broader grassroots support; less can mean more "
                 "reliance on large donors and PACs."),
            measured=("The FEC's unitemized individual contributions over all contributions. "
                      "Window: the member's most recent completed election, as for the PAC "
                      "share. A campaign must name each donor whose gifts add up to more than "
                      "$200 in the election cycle (52 U.S.C. 30104(b)(3)(A), "
                      "11 CFR 104.3(a)(4)); the rest are filed as one unnamed total. ActBlue and "
                      "WinRed name every donor whatever the size, so gifts through them are left "
                      "out and the share undercounts small donors; a campaign that names every "
                      "donor shows no figure. The tick is the average share for a state of that "
                      "size for a senator, the peer median for a representative."),
            window="election"),
    Measure("billsMoved", "Bills past committee", "legislativeEffectiveness",
            "higher means more of the member's bills got past committee", "count",
            {"name": "Congress.gov", "url": "https://www.congress.gov/"},
            past_committee,
            lambda f: None,
            lambda f: bill_facts(f).get("bills"),
            question="Do this member's bills move?",
            why=("How many bills this member sponsored this Congress got past committee, where "
                 "most bills stop. It shows whether the member's own bills get acted on; members "
                 "of the majority party usually move more."),
            measured=("Bills the member sponsored in this Congress, from Congress.gov's "
                      "actions, counted once reported by a committee, passed by a chamber or "
                      "enacted. Bills a member shapes as a cosponsor or through another member's "
                      "bill are not counted. The tick is the peer median.")),
)
BY_KEY = {m.key: m for m in MEASURES}




def snapshot_facts(dimensions: dict | None) -> dict | None:
    """The four measures' figures, with the counts behind them, from a
    stored breakdown's dimensions: what a score snapshot keeps so history
    can show a measure over time. None without a breakdown."""
    if not isinstance(dimensions, dict):
        return None
    facts = {k: (v or {}).get("facts") or {} for k, v in dimensions.items() if isinstance(v, dict)}
    out = {m.key: m.value(facts) for m in MEASURES}
    out.update({
        "partyVotes": alignment_facts(facts).get("partyVotes"),
        "breakCount": alignment_facts(facts).get("breaks"),
        "bills": bill_facts(facts).get("bills"),
    })
    return out if any(v is not None for v in out.values()) else None


def snapshot_facts_json(stored_breakdown: str | None) -> str | None:
    """snapshot_facts as the column stores it, from a member row's
    score_breakdown column value."""
    try:
        kept = json.loads(stored_breakdown or "null")
    except (TypeError, ValueError):
        return None
    facts = snapshot_facts(kept.get("dimensions") if isinstance(kept, dict) else None)
    return json.dumps(facts) if facts else None
