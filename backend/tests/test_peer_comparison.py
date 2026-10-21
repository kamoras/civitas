"""Where a member stands among peers (app/peer_comparison.py): the
percentile, the word's rule, the sentence, and the peer group."""

from app import peer_comparison as pc
from app.ordinals import RIGHTMOST, rank_shown


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


def test_a_count_short_of_the_group_says_with_a_figure(monkeypatch):
    """Two rows count different peers when some have no figure for one of
    them (no FEC contributions); the sentence says so."""
    peers = [_member(f"r{i}", caucus="R", pac=0.1 + i / 100) for i in range(25)]
    peers[0]["facts"]["fundingIndependence"]["contributions"] = 0
    me = _member("me", caucus="R", pac=0.5)
    _rows(monkeypatch, peers + [me])
    by = {m["key"]: m for m in pc.peer_comparison(None, "house", "me")["measures"]}
    assert by["pacMoney"]["sentence"].endswith("of the other 24 House Republicans with a figure took a smaller share.")
    assert "with a figure" not in by["breaks"]["sentence"]
    assert by["breaks"]["peerGroup"]["labelOne"] == "House Republican"


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
    assert small["peerGroup"] == {"key": "caucus", "label": "Senate Democrats and independents",
                                  "labelOne": "Senate Democrat or independent", "n": 32}
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


def test_rank_words_share_a_place_on_ties():
    v = [1, 2, 2, 3, 5]
    assert [rank_shown(x, v) for x in v] == ["Lowest of 5", "Tied for 3rd highest of 5",
                                             "Tied for 3rd highest of 5", "2nd highest of 5", "Highest of 5"]
    assert rank_shown(4, [4, 4]) == "Tied for highest of 2"
    assert rank_shown(0.3, [-1, 0.3, 2], RIGHTMOST) == "2nd from the right of 3"


def test_every_dot_carries_its_seat_and_place(monkeypatch):
    """The strips' hover labels: each peer's party and seat and place on the
    row, computed here so the page only prints them."""
    rows = [_member(f"d{i}", small=i / 40) for i in range(5)]
    rows[4]["district"] = 5
    rows[3]["district"] = 0
    _rows(monkeypatch, rows)
    small = {m["key"]: m for m in pc.peer_comparison(None, "house", "d0")["measures"]}["smallGifts"]
    assert small["distributionSeats"][-2:] == ["D-OR-AL", "D-OR-5"]
    assert small["distributionShown"][-1] == "10%"
    breaks = {m["key"]: m for m in pc.peer_comparison(None, "house", "d0")["measures"]}["breaks"]
    assert breaks["distributionShown"][0] == "0 of 100"
    assert small["distributionRanks"] == ["Lowest of 5", "4th highest of 5", "3rd highest of 5",
                                          "2nd highest of 5", "Highest of 5"]
    assert pc.peer_comparison(None, "senate", "d0")["measures"][0]["distributionSeats"][0] == "D-OR"
    points = {m["key"]: m for m in pc.chamber_measures(None, "senate")["measures"]}["smallGifts"]["points"]
    assert points[-1]["rank"] == "Highest of 5" and points[0]["rank"] == "Lowest of 5"


def test_a_strip_is_drawn_from_the_api():
    # The axis and table print a figure as the strip does; the right end
    # rounds the largest figure up to a step (frontend draws nothing itself).
    assert [pc.axis_figure("share", 0.134), pc.axis_figure("rate", 0.034), pc.axis_figure("count", 7.4)] == [
        "13%", "3.4%", "7"]
    assert (pc.axis_top("share", [0.07]), pc.axis_top("share", [0.33]), pc.axis_top("count", [12])) == (
        0.08, 0.4, 15)
    assert pc.axis_top("count", []) == 5


def test_a_measure_over_time_reads_night_by_night_in_one_congress():
    m = pc.BY_KEY["breaks"]
    snaps = [("2024-12-01", {"breaks": 0.9}),  # the 118th Congress: left out
             ("2026-03-01", {"breaks": 0.02}), ("2026-04-01", {"breaks": 0.04}), ("2026-05-01", {"breaks": 0.03})]
    out = pc.over_time(m, snaps)
    assert out["points"] == [{"x": 0.0, "y": 100.0}, {"x": 50.0, "y": 0.0}, {"x": 100.0, "y": 50.0}]
    assert out["marks"][1] == {"id": "2026-04-01", "label": "Apr 1, 2026", "detail": None, "shown": "4.0%",
                               "context": None, "href": None}
    assert out["sentence"] == "Over time: 2.0% on Mar 1, 2026, 3.0% on May 1, 2026."
    # Unchanged, or a measure of the last election: nothing drawn.
    assert pc.over_time(m, snaps[1:2] * 3) is None
    assert pc.over_time(pc.BY_KEY["pacMoney"], snaps) is None
