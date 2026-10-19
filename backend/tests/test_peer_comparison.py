"""Where a member stands among peers (app/peer_comparison.py): the
percentile, the word's rule, the sentence, and the peer group."""

from app import peer_comparison as pc


def _member(mid, caucus="D", party=None, small=0.2, pac=0.1, breaks=0, votes=100, stages=(5, 1, 1, 0, 0),
            thin=()):
    return {
        "id": mid, "name": mid, "party": party or caucus, "caucus": caucus, "state": "OR", "district": None,
        "thin": list(thin),
        "facts": {
            "fundingIndependence": {"contributions": 1000, "smallDonorShare": small, "pacShare": pac,
                                    "smallDonorExpectedShare": 0.25, "smallDonorComparison": "state-size"},
            "constituentAlignment": {"partyVotes": votes, "breaks": breaks, "breakRate": breaks / votes,
                                     "expectedBreakRate": 0.02},
            "legislativeEffectiveness": {"bills": sum(stages), "billsByStage": list(stages)},
        },
    }


def _rows(monkeypatch, rows):
    monkeypatch.setattr(pc, "chamber_rows", lambda db, chamber: rows)


def test_percentile_is_mid_rank():
    assert pc.percentile(5, [1, 5, 9]) == 50
    assert pc.percentile(1, [1, 1, 2, 3]) == 25


def test_word_rule():
    assert pc.peer_word(95, 1.5) == "well above peers"
    assert pc.peer_word(95, 0.5) == "above peers"  # outside the deciles, but close to expected
    assert pc.peer_word(20, None) == "below peers"
    assert pc.peer_word(50, 3) == "near peers"


def test_rows_against_the_caucus_with_template_sentences(monkeypatch):
    peers = [_member(f"d{i}", small=0.2 + i / 100, breaks=i % 4, stages=(5, 1, i % 3, 0, 0)) for i in range(30)]
    me = _member("me", small=0.05, breaks=0, pac=0.5, stages=(70, 4, 7, 2, 0))
    others = [_member(f"r{i}", caucus="R", small=0.9) for i in range(30)]
    independent = _member("ind", caucus="D", party="I")
    _rows(monkeypatch, peers + [me, independent] + others)
    out = pc.peer_comparison(None, "senate", "me")
    by = {m["key"]: m for m in out["measures"]}
    small = by["smallGifts"]
    assert small["peerGroup"] == {"key": "caucus", "label": "Senate Democrats and independents", "n": 32}
    assert small["expected"] == 0.25 and small["expectedBasis"] == "seat"
    assert small["word"] == "well below peers"
    assert small["sentence"] == ("Gifts of $200 or less were 5% of the money. 31 of the other 31 "
                                 "Senate Democrats and independents had a larger share.")
    assert by["pacMoney"]["expectedBasis"] == "peer median"
    assert by["billsMoved"]["shown"] == "9 of 83"
    assert by["breaks"]["shown"] == "0 of 100"
    assert small["distribution"] == sorted(small["distribution"])
    assert len(small["distributionIds"]) == small["peerGroup"]["n"]
    assert out["standsOut"].startswith("Small gifts: ") or out["standsOut"].startswith("PAC money: ")
    assert pc.peer_comparison(None, "senate", "me", "chamber")["measures"][0]["peerGroup"]["n"] == 62


def test_thin_record_draws_no_word(monkeypatch):
    peers = [_member(f"d{i}") for i in range(30)]
    me = _member("me", votes=6, thin=("legislativeEffectiveness",))
    _rows(monkeypatch, peers + [me])
    by = {m["key"]: m for m in pc.peer_comparison(None, "senate", "me")["measures"]}
    assert by["breaks"]["thin"] and by["breaks"]["word"] is None and by["breaks"]["record"] == 6
    assert by["billsMoved"]["word"] is None
    assert by["smallGifts"]["word"] is not None


def test_house_median_comparison_has_no_seat_tick(monkeypatch):
    rows = [_member(f"d{i}", small=i / 50) for i in range(30)]
    for r in rows:
        r["facts"]["fundingIndependence"]["smallDonorComparison"] = "house-median"
    _rows(monkeypatch, rows)
    small = pc.peer_comparison(None, "house", "d3")["measures"][0]
    assert small["expectedBasis"] == "peer median"
    assert small["peerGroup"]["label"] == "House Democrats"


def test_not_serving_is_none(monkeypatch):
    _rows(monkeypatch, [_member("a")])
    assert pc.peer_comparison(None, "senate", "nobody") is None
