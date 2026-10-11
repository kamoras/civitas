"""The finding sentence atop each scored dimension: a template around the
scorer's own figures, naming no one and using no pronoun."""

import re

import pytest

from app.services import score_findings as sf


def _alignment(rate, expected, breaks=10, votes=400, party="D"):
    return {"party": party, "partyVotes": votes, "breaks": breaks, "breakRate": rate, "expectedBreakRate": expected}


class TestAlignment:
    @pytest.mark.parametrize("rate, expected, phrase", [
        (0.15, 0.05, "about 3 times as often as"),
        (0.10, 0.05, "about twice as often as"),
        (0.08, 0.05, "about 1.5 times as often as"),
        (0.05, 0.05, "about as often as"),
        (0.025, 0.05, "about half as often as"),
        (0.017, 0.05, "about a third as often as"),
        (0.005, 0.05, "far less often than"),
        (0.035, 0.05, "less often than"),
    ])
    def test_the_comparison(self, rate, expected, phrase):
        s = sf.alignment_finding(_alignment(rate, expected), "senate")
        assert s == f"Breaks with the party {phrase} Democrats in states that lean the same way."

    def test_a_house_seat(self):
        s = sf.alignment_finding(_alignment(0.05, 0.05, party="R"), "house")
        assert s.endswith("Republicans in districts that lean the same way.")

    def test_never_broke(self):
        s = sf.alignment_finding(_alignment(0.0, 0.05, breaks=0), "senate")
        assert s == ("Never broke with the party on a party-line vote, though Democrats in states that lean "
                     "the same way break on 5%.")

    def test_too_few_votes(self):
        assert sf.alignment_finding(_alignment(None, None, breaks=None, votes=3), "house").startswith("Too few")

    def test_no_norm(self):
        assert sf.alignment_finding(_alignment(0.1, None, breaks=4, votes=40), "senate") == (
            "Broke with the party on 4 of 40 party-line votes.")

    def test_no_party_line_to_break(self):
        assert sf.alignment_finding(_alignment(0.1, 0.05, party="I"), "senate") is None
        assert sf.alignment_finding(None, "senate") is None


class TestFunding:
    def _facts(self, small, expected, comparison="state-size", pac=0.1):
        return {"contributions": 1_000_000, "pacShare": pac, "smallDonorShare": small,
                "smallDonorExpectedShare": expected, "smallDonorComparison": comparison}

    def test_against_a_state_this_size(self):
        assert sf.funding_finding(self._facts(0.44, 0.26)) == (
            "Gifts of $200 or less made up 44% of the money, more than the 26% typical for a state this size.")

    def test_against_the_house_median(self):
        assert sf.funding_finding(self._facts(0.03, 0.06, "house-median")) == (
            "Gifts of $200 or less made up 3% of the money, less than the House median of 6%.")
        assert "close to" in sf.funding_finding(self._facts(0.06, 0.055, "house-median"))

    def test_itemized_every_gift(self):
        s = sf.funding_finding(self._facts(None, 0.26, pac=0.004))
        assert s.startswith("PACs gave under 1% of the money;")

    def test_no_money(self):
        assert sf.funding_finding({"contributions": 0}) is None


class TestEffectiveness:
    def _facts(self, stages):
        return {"billsByStage": stages, "bills": sum(stages), "resolutions": 0}

    def test_laws(self):
        assert sf.effectiveness_finding(self._facts([40, 5, 3, 3, 1]), "senate") == (
            "Of 52 bills sponsored this Congress, 1 became law, and 3 more passed the Senate.")

    def test_passed_a_chamber(self):
        assert sf.effectiveness_finding(self._facts([10, 0, 0, 2, 0]), "house") == (
            "Of 12 bills sponsored this Congress, 2 passed the House; none has become law.")

    def test_committee_action(self):
        assert sf.effectiveness_finding(self._facts([10, 1, 1, 0, 0]), "house") == (
            "Of 12 bills sponsored this Congress, 2 got committee action; none has passed the House.")

    def test_introduced_only(self):
        assert sf.effectiveness_finding(self._facts([1, 0, 0, 0, 0]), "house") == (
            "The one bill sponsored this Congress has not moved past introduction.")
        assert sf.effectiveness_finding(self._facts([0] * 5), "house") == "Sponsored no bills this Congress."


def test_add_findings_and_no_pronouns():
    breakdown = {
        "fundingIndependence": {"facts": {"contributions": 5, "pacShare": 0.2, "smallDonorShare": 0.3,
                                          "smallDonorExpectedShare": 0.3, "smallDonorComparison": "state-size"}},
        "constituentAlignment": {"facts": _alignment(0.2, 0.05)},
        "fundingDiversity": {"components": []},
        "legislativeEffectiveness": {"facts": {"billsByStage": [3, 0, 0, 0, 0], "bills": 3, "resolutions": 0}},
    }
    sf.add_findings(breakdown, "senate")
    found = [breakdown[k]["finding"] for k in ("fundingIndependence", "constituentAlignment", "legislativeEffectiveness")]
    assert all(found) and "finding" not in breakdown["fundingDiversity"]
    for s in found:
        assert not re.search(r"\b(he|she|they|his|her|hers|their|them)\b", s, re.IGNORECASE), s
