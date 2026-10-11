"""The one plain sentence at the top of each scored dimension on a member's
scorecard: what the score found, before how it was computed.

Each sentence is a fixed template filled with figures the scorer already
computed and serves in the dimension's `facts` (score_calculator's *_core
functions); nothing here measures anything new, and no model writes any of
it. The words around the figures are chosen to read at about an
eighth-grade level (Flesch-Kincaid), and name no one: the subject is the
member the page is about, so the sentences are subjectless ("Breaks with
Democrats ..."), never "he", "she" or "they".

Lives in services/, not pipeline/: the wording is display, and a string
constant under pipeline/ would move the analysis-code fingerprint and wipe
the learning store (senate_pipeline._compute_analysis_code_hash).
"""

_PARTY_MEMBERS = {"D": "Democrats", "R": "Republicans"}

# A rate within a quarter of the seat's norm reads as "about as often":
# the norm is itself an estimate (a fitted rate for same-party members of
# seats with this lean), and a sentence claiming "1.1 times as often"
# would state more precision than the comparison has.
_ABOUT_THE_SAME = 1.25


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _pct(share: float) -> str:
    """A 0-1 share as a whole percent; "under 1%" for a share that rounds
    to zero but isn't (the scorecard's percent())."""
    if 0 < share < 0.005:
        return "under 1%"
    return f"{round(share * 100)}%"


def _times(ratio: float) -> str:
    """How many times as often, in words a reader says aloud: "about 1.5
    times", "about twice", "about 3 times"."""
    if ratio < 1.75:
        return "about 1.5 times"
    n = round(ratio)
    return "about twice" if n == 2 else f"about {n} times"


def _fraction(ratio: float) -> str:
    """How much less often, for a ratio under 1: "about half as often as"."""
    n = round(1 / ratio)
    if n <= 1:
        return "less often than"
    if n == 2:
        return "about half as often as"
    if n == 3:
        return "about a third as often as"
    return "far less often than"


def alignment_finding(facts: dict | None, chamber: str) -> str | None:
    """Constituent Alignment: how often the member breaks with the party,
    against same-party members of seats that lean the same way."""
    if not facts:
        return None
    members = _PARTY_MEMBERS.get(facts.get("party") or "")
    if members is None:
        return None
    # "Breaks with the party", not "with Democrats": naming the party twice
    # in one sentence put it past a ninth-grade reading level (measured).
    seats = f"{'districts' if chamber == 'house' else 'states'} that lean the same way"
    rate, expected = facts.get("breakRate"), facts.get("expectedBreakRate")
    breaks, votes = facts.get("breaks"), facts.get("partyVotes") or 0
    if rate is None or breaks is None:
        return f"Too few party-line votes so far to compare with {members} in {seats}."
    tally = _plural(votes, "party-line vote", "party-line votes")
    if not expected:
        if breaks == 0:
            return f"Has not broken with the party on any of {tally}."
        return f"Broke with the party on {breaks:,} of {tally}."
    if breaks == 0:
        return f"Never broke with the party on a party-line vote, though {members} in {seats} break on {_pct(expected)}."
    ratio = rate / expected
    if ratio >= _ABOUT_THE_SAME:
        return f"Breaks with the party {_times(ratio)} as often as {members} in {seats}."
    if ratio <= 1 / _ABOUT_THE_SAME:
        return f"Breaks with the party {_fraction(ratio)} {members} in {seats}."
    return f"Breaks with the party about as often as {members} in {seats}."


def funding_finding(facts: dict | None) -> str | None:
    """Funding Independence: the share of the money from small gifts,
    against what is typical; the PAC share where the filings can't say."""
    if not facts or not facts.get("contributions"):
        return None
    small, expected = facts.get("smallDonorShare"), facts.get("smallDonorExpectedShare")
    if small is None:
        return (f"PACs gave {_pct(facts.get('pacShare') or 0)} of the money; the campaign "
                "lists every gift, so the filings can't show how much came in small gifts.")
    lead = f"Gifts of $200 or less made up {_pct(small)} of the money"
    if not expected:
        return f"{lead}."
    if small >= expected * _ABOUT_THE_SAME:
        word = "more than"
    elif small <= expected / _ABOUT_THE_SAME:
        word = "less than"
    else:
        word = "close to"
    if facts.get("smallDonorComparison") == "house-median":
        return f"{lead}, {word} the House median of {_pct(expected)}."
    return f"{lead}, {word} the {_pct(expected)} typical for a state this size."


def effectiveness_finding(facts: dict | None, chamber: str) -> str | None:
    """Legislative Effectiveness: how far the member's bills got."""
    if not facts or facts.get("bills") is None:
        return None
    bills = facts["bills"]
    if bills == 0:
        return "Sponsored no bills this Congress."
    _, committee, beyond, passed, law = facts["billsByStage"]
    house = "the House" if chamber == "house" else "the Senate"
    of = f"Of {_plural(bills, 'bill', 'bills')} sponsored this Congress"
    if law:
        more = f", and {passed:,} more passed {house}" if passed else ""
        return f"{of}, {law:,} became law{more}."
    if passed:
        return f"{of}, {passed:,} passed {house}; none has become law."
    if committee + beyond:
        return f"{of}, {committee + beyond:,} got committee action; none has passed {house}."
    if bills == 1:
        return "The one bill sponsored this Congress has not moved past introduction."
    return f"None of the {bills:,} bills sponsored this Congress has moved past introduction."


def add_findings(breakdown: dict, chamber: str) -> dict:
    """Set each dimension's `finding` (None when its facts can't support
    one) on a member's score breakdown, in place, and return it."""
    builders = {
        "fundingIndependence": funding_finding,
        "constituentAlignment": lambda f: alignment_finding(f, chamber),
        "legislativeEffectiveness": lambda f: effectiveness_finding(f, chamber),
    }
    for key, build in builders.items():
        dimension = breakdown.get(key)
        if isinstance(dimension, dict):
            dimension["finding"] = build(dimension.get("facts"))
    return breakdown
