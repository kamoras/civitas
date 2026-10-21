"""The partisan-depth chart's words and geometry come from the API, so the
page draws without arithmetic (frontend PlatformTracker)."""
from app.schemas import PartisanDepthSchema


def _depth(lean: float, party: str, strength: float = 0.63) -> PartisanDepthSchema:
    return PartisanDepthSchema(overall_lean=lean, overall_party=party, depth="moderate", cross_party_count=0,
                               total_positions=1,
                               policy_breakdown=[{"area": "foreign_policy", "alignment": "R", "strength": strength}])


def test_the_spectrum_bar_in_words_and_place():
    d = _depth(-0.213, "D").model_dump(by_alias=True)
    assert d["leanShown"] == "−0.21"
    assert d["leanSentence"].startswith("Votes lean Democratic: −0.21, on a scale from −1")
    assert (d["leanMarkerPercent"], d["leanBarLeft"], d["leanBarWidth"]) == (39.35, 39.35, 10.65)
    r = _depth(0.4, "R").model_dump(by_alias=True)
    assert (r["leanShown"], r["leanBarLeft"], r["leanBarWidth"]) == ("+0.40", 50.0, 20.0)
    # A lean past the measure's bounds (the cosponsorship prior) is drawn at the end.
    assert _depth(1.3, "R").lean_marker_percent == 100.0


def test_each_policy_area_reads_out_with_a_visible_floor():
    area = _depth(0.1, "R", strength=0.02).policy_breakdown[0].model_dump(by_alias=True)
    assert (area["strengthShown"], area["barPercent"]) == ("2%", 4)
    assert area["mark"] == {"id": "foreign_policy", "label": "Foreign Policy", "detail": None,
                            "shown": "leans Republican", "context": "strength 2%", "href": None}
