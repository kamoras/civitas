"""Properties every member's scores must hold, over seeded random inputs.

Example-based tests pin known members; these pin the invariants between
them: each sub-score is an int in 0-100 whatever the record looks like
(empty, partial, null fields), the "show the math" breakdown reproduces the
stored score, and the obvious directions hold — more PAC money never raises
Funding Independence, a larger small-donor share never lowers it, and one
more enacted bill never lowers Legislative Effectiveness.
"""
import copy
import random

import pytest

from app.config import settings
from app.pipeline.analyze import score_calculator as sc

# A member sponsors only their own chamber's measures.
_TYPES = {"house": ["hr", "hres", "hjres", "hconres"], "senate": ["s", "sres", "sjres", "sconres"]}
_STAGES = [
    None, "", "INTRODUCED", "REFERRED", "IN_COMMITTEE", "REPORTED", "ON_FLOOR",
    "PASSED_CHAMBER", "IN_OTHER_CHAMBER", "TO_PRESIDENT", "VETOED", "ENACTED",
]
_DONOR_TYPES = ["PAC", "Individual", "Self-Funded", "CandidateAffiliated", "Party", None]


def _maybe(rng: random.Random, value):
    r = rng.random()
    return 0 if r < 0.05 else None if r < 0.08 else value


def _member(rng: random.Random) -> dict:
    district = rng.choice([None, None, rng.randint(0, 53)])
    base = rng.choice([0, rng.uniform(1e3, 1e5), rng.uniform(1e5, 1e8)])
    n_votes = rng.randint(0, 400)
    return {
        "state": rng.choice(list(sc.get_state_pvi_map()) + ["", "XX"]),
        "district": district,
        "party": rng.choice(["D", "R", "I", "L", None]),
        "bioguideId": "X000001",
        "funding": {
            "totalRaised": base * rng.uniform(1, 1.3),
            "totalContributions": rng.choice([base, None]),
            "totalFromPACs": rng.uniform(0, base * 1.2) if base else 0,
            "smallDonorPercentage": rng.choice([0, rng.uniform(0, 100), None]),
            "topDonors": [
                {"name": f"d{i}", "total": rng.uniform(0, 1e5), "type": rng.choice(_DONOR_TYPES)}
                for i in range(rng.randint(0, 40))
            ],
            "industryBreakdown": [
                {"industry": rng.choice(["Defense", "Health", "Finance", "Energy"]), "total": rng.uniform(0, 1e6)}
                for _ in range(rng.randint(0, 12))
            ],
        },
        "votingRecord": {
            "keyVotes": [
                {"billId": f"b{i}", "votedWithParty": rng.choice([True, False, None]), "vote": rng.choice(["Yea", "Nay"])}
                for i in range(rng.randint(0, 60))
            ],
            "recentVotes": [],
            "partyLineRecord": rng.choice([
                None, {"votes": n_votes, "breaks": [{}] * rng.randint(0, n_votes)},
            ]),
            "effectiveParty": rng.choice([None, "D", "R"]),
        },
        "sponsoredBills": [
            {
                # Current congress only: scoring is windowed to it (AGENTS.md §6).
                "billType": rng.choice(_TYPES["senate" if district is None else "house"]), "congress": settings.CURRENT_CONGRESS,
                "stage": rng.choice(_STAGES), "isLaw": rng.random() < 0.05,
                "latestAction": rng.choice(["", "Passed Senate", "Referred"]),
                "commemorative": rng.random() < 0.1,
            }
            for _ in range(rng.randint(0, 50))
        ],
        "leadershipScore": _maybe(rng, rng.random()),
        # At least the half year below which no bills at all is neutral by
        # design (_MIN_TENURE_FOR_ZERO_SIGNAL_YEARS): against the bundled
        # full-Congress reference, a first bill there reads below neutral.
        "yearsInOffice": rng.uniform(sc._MIN_TENURE_FOR_ZERO_SIGNAL_YEARS, 40),
        "swornDate": rng.choice([None, "2025-01-03", "2025-09-10", "2026-06-10"]),
        "attractedBipartisanshipScore": _maybe(rng, rng.random()),
        "campaignPromises": [],
        "lobbyingMatches": [
            {"donationToSenator": rng.uniform(0, 1e5), "isConsensusVote": rng.random() < 0.5}
            for _ in range(rng.randint(0, 5))
        ],
    }


@pytest.mark.parametrize("seed", range(4))
def test_score_invariants_hold_for_any_record(seed):
    rng = random.Random(seed)
    for _ in range(60):
        member = _member(rng)
        scores = sc.calculate_scores(member)
        for name, value in scores.items():
            assert isinstance(value, int) and 0 <= value <= 100, (name, value)

        for name, breakdown in sc.explain_scores(member).items():
            assert breakdown["score"] == scores[name], name

        fi = scores["fundingIndependence"]
        base = sc.funding_share_base(member["funding"])
        if base > 0:
            more_pac = copy.deepcopy(member)
            more_pac["funding"]["totalFromPACs"] += 0.1 * base
            assert sc.calculate_scores(more_pac)["fundingIndependence"] <= fi

        # None is "not measured" (every gift itemized): the part is left
        # out, so a measured share is a different record, not more of one.
        if member["funding"]["smallDonorPercentage"] is not None:
            more_small = copy.deepcopy(member)
            more_small["funding"]["smallDonorPercentage"] = min(100, more_small["funding"]["smallDonorPercentage"] + 10)
            assert sc.calculate_scores(more_small)["fundingIndependence"] >= fi

        one_more_law = copy.deepcopy(member)
        one_more_law["sponsoredBills"].append({
            "billType": "hr" if member["district"] is not None else "s", "congress": settings.CURRENT_CONGRESS,
            "stage": "ENACTED", "isLaw": True, "latestAction": "", "commemorative": False,
        })
        assert (
            sc.calculate_scores(one_more_law)["legislativeEffectiveness"]
            >= scores["legislativeEffectiveness"]
        )
