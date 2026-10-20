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
    assert small["sentence"] == ("Donors who gave $200 or less gave 5% of the money. 31 of the other 31 "
                                 "Senate Democrats and independents had a larger share.")
    assert by["pacMoney"]["expectedBasis"] == "peer median"
    assert by["billsMoved"]["shown"] == "9 of 83"
    assert by["breaks"]["shown"] == "0 of 100"
    assert small["distribution"] == sorted(small["distribution"])
    assert len(small["distributionIds"]) == len(small["distributionNames"]) == small["peerGroup"]["n"]
    assert out["standsOut"].startswith(("Money from small donors: ", "Money from PACs: "))
    assert out["standsOutWhy"].startswith(pc.STANDS_OUT_WHY)
    assert out["standsOutWhy"].endswith(small["why"]) or out["standsOutWhy"].endswith(by["pacMoney"]["why"])
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
    small = {m["key"]: m for m in pc.peer_comparison(None, "house", "d3")["measures"]}["smallGifts"]
    assert small["expectedBasis"] == "peer median"
    assert small["peerGroup"]["label"] == "House Democrats"


def test_not_serving_is_none(monkeypatch):
    _rows(monkeypatch, [_member("a")])
    assert pc.peer_comparison(None, "senate", "nobody") is None


def test_chamber_measures_lists_everyone_with_no_rank(monkeypatch):
    rows = [_member(f"d{i}", small=i / 40, stages=(3, 1, i % 4, 1, 0)) for i in range(25)]
    rows += [_member(f"r{i}", caucus="R", small=0.5 + i / 100) for i in range(5)]
    _rows(monkeypatch, rows)
    monkeypatch.setattr(pc, "seat_lean", lambda state, district: 7)
    out = pc.chamber_measures(None, "house")
    small = {m["key"]: m for m in out["measures"]}["smallGifts"]
    assert small["n"] == 30 and [p["value"] for p in small["points"]] == sorted(p["value"] for p in small["points"])
    # Furthest from each member's own expected figure (0.25 here), each way.
    assert [e["id"] for e in small["mostAbove"]] == ["d24", "d23", "d22"]
    assert [e["id"] for e in small["mostBelow"]] == ["d0", "d1", "d2"]
    assert [m["id"] for m in out["members"]] == sorted(m["id"] for m in out["members"])
    first = out["members"][0]
    assert first["lean"] == 7 and first["bills"] == 5 and first["passed"] == 1 and first["law"] == 0
    assert "rank" not in first


def test_every_strip_says_what_it_answers_and_why(monkeypatch):
    """Order is votes, money (PACs, then small donors), bills; each row
    carries its question, a neutral why line and the method text, and the
    stands-out line says why it was picked."""
    _rows(monkeypatch, [_member(f"d{i}") for i in range(30)])
    out = pc.peer_comparison(None, "senate", "d0")
    assert [m["key"] for m in out["measures"]] == ["breaks", "pacMoney", "smallGifts", "billsMoved"]
    for m in out["measures"]:
        assert m["question"].endswith("?") and m["why"] and m["measured"]
        # Funding covers the last completed election; votes and bills, this Congress.
        assert m["window"] == ("election" if m["key"] in ("pacMoney", "smallGifts") else "congress")
        assert ("most recent completed election" if m["window"] == "election" else "this Congress") in m["measured"]
        assert "\u2014" not in m["why"] + m["measured"]
        assert not ({"better", "worse", "good", "bad"} & set(m["why"].lower().replace(";", " ").split()))
    assert out["standsOut"] == "Close to peers on every measure."
    assert out["standsOutWhy"] == pc.NONE_STANDS_OUT_WHY
