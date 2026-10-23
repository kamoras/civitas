"""Committee routing (app/committee_routing.py): its data, its arithmetic and
its place outside the analysis-code fingerprint."""

import ast
import pathlib
from types import SimpleNamespace

import numpy as np
import pytest

from app import committee_routing as cr


def test_calibration_matches_the_labelled_issues_and_prototypes():
    """Rerun scripts/calibrate_committee_routing.py when either file changes."""
    cal = cr.calibration()
    assert cal["data_hash"] == cr.data_hash()
    assert cal["examples"] == len(cr.examples())
    assert 1 <= cal["k"] <= len(cr.examples())
    # The prototypes always take part (alpha > 0).
    assert 0.0 < cal["alpha"] <= 1.0
    assert 0.0 < cal["threshold"] < 1.0
    assert cal["held_out"]["issues"] > 0
    assert cal["held_out_recent"]["issues"] > 0
    assert any(row["threshold"] == cal["threshold"] for row in cal["held_out_curve"])
    assert isinstance(cal["precision_rule_met_on_training"], bool)


def test_labels_name_only_committees_of_their_own_chamber():
    committees = cr.jurisdictions()
    for code, c in committees.items():
        assert c["chamber"] in cr.CHAMBERS, code
        assert c["jurisdiction"] and all(isinstance(s, str) and s for s in c["jurisdiction"]), code
        assert c["url"], code
    examples = cr.examples()
    assert len(examples) >= 150
    for e in examples:
        for ch in cr.CHAMBERS:
            assert all(committees[code]["chamber"] == ch for code in e[ch]), e
    # Both kinds of label are present: stories a committee handles and
    # stories none does (routing must be able to abstain).
    assert any(e["house"] or e["senate"] for e in examples)
    assert any(not e["house"] and not e["senate"] for e in examples)


def _toy_reference():
    """Two House committees, unit vectors on separate axes, and three
    labelled issues: one per committee and one no committee handles."""
    eye = np.eye(4)
    return SimpleNamespace(
        codes={"house": ["HA", "HB"], "senate": ["SA"]},
        items=np.stack([eye[0], eye[1], eye[0]]),
        item_chamber=np.array(["house", "house", "senate"]),
        item_col=np.array([0, 1, 0]),
        examples=np.stack([eye[0], eye[1], eye[2]]),
        votes={
            "house": np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]),
            "senate": np.array([[1.0, 0], [0, 1.0], [0, 1.0]]),
        },
    )


def test_an_issue_like_a_labelled_one_routes_to_its_committee():
    ref = _toy_reference()
    x = np.array([[1.0, 0, 0, 0]])
    combined = cr.shares(x, ref, k=1, alpha=0.5, temperature=10.0)
    assert cr.decide(combined["house"], threshold=0.3)[0] == 0
    assert cr.decide(combined["senate"], threshold=0.3)[0] == 0


def test_an_issue_like_one_no_committee_handles_abstains():
    ref = _toy_reference()
    x = np.array([[0, 0, 1.0, 0]])
    combined = cr.shares(x, ref, k=1, alpha=0.25, temperature=10.0)
    # The "none" vote outweighs any one committee's share.
    assert cr.decide(combined["house"], threshold=0.0)[0] == -1


def test_below_the_threshold_routing_abstains_rather_than_guesses():
    s = cr.Shares(np.array([[0.30, 0.25, 0.45], [0.60, 0.30, 0.10]]), np.array([True, True]))
    assert list(cr.decide(s, threshold=0.5)) == [-1, 0]


def test_a_committee_only_one_signal_picks_is_not_named():
    s = cr.Shares(np.array([[0.90, 0.05, 0.05]]), np.array([False]))
    assert list(cr.decide(s, threshold=0.1)) == [-1]


def test_the_two_signals_disagreeing_is_detected():
    ref = _toy_reference()
    # Closest labelled issue votes HB, but the prototypes point at HA.
    ref.votes["house"] = np.array([[0, 1.0, 0], [1.0, 0, 0], [0, 0, 1.0]])
    s = cr.shares(np.array([[1.0, 0, 0, 0]]), ref, k=1, alpha=0.5, temperature=10.0)["house"]
    assert not s.agree[0]
    assert cr.decide(s, threshold=0.0)[0] == -1


def test_a_mask_keeps_an_issue_from_voting_for_itself():
    ref = _toy_reference()
    x = np.array([[1.0, 0, 0, 0]])
    mask = np.array([[False, True, True]])
    knn_only = cr.shares(x, ref, k=1, alpha=1e-9, temperature=10.0, mask=mask)["house"].combined
    assert knn_only[0, 0] < 0.5


def test_routing_lives_outside_the_fingerprinted_code():
    """app/committee_routing.py is not under app/pipeline/, so the
    analysis-code hash never reads it — and no hashed module may import it,
    or an edit to it would change analysis results the hash can't see."""
    from app.pipeline import senate_pipeline

    app_dir = pathlib.Path(senate_pipeline.__file__).resolve().parent.parent
    assert pathlib.Path(cr.__file__).resolve().parent == app_dir
    hashed = [
        py for py in [*(app_dir / "pipeline").rglob("*.py"), app_dir / "config_definitions.py"]
        if "/fetch/" not in str(py)
        and py.relative_to(app_dir).as_posix() not in senate_pipeline._NOT_ANALYSIS_PATHS
    ]
    for py in hashed:
        for node in ast.walk(ast.parse(py.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "app.committee_routing", py
                assert not (node.module == "app" and any(a.name == "committee_routing" for a in node.names)), py
            elif isinstance(node, ast.Import):
                assert not any(a.name == "app.committee_routing" for a in node.names), py


@pytest.mark.slow
def test_a_stopgap_funding_story_names_no_unrelated_committee():
    # It may abstain (the two signals can split between Appropriations and
    # Budget); it must never name anything else.
    routed = cr.route_committees(
        "Senate passes stopgap funding bill to avert shutdown",
        "The Senate voted to pass a continuing resolution to keep the government funded.",
    )
    assert {c["code"] for c in routed} <= {"HSAP", "SSAP", "HSBU", "SSBU"}


def test_labels_come_from_bills_referrals_and_texts_no_committee_handles(db_session):
    """A bill is labelled with the committees its actions record it was
    referred to (a chamber it was never referred in says nothing); a feed
    article filed under another desk naming no official, and election
    coverage, are labelled no committee's; an article the publisher filed
    under politics is not labelled at all."""
    import json
    from datetime import datetime

    from app.models import ApiCache, FeedArticle, RaceCoverageItem, RepSponsoredBill

    referral = {"type": "IntroReferral", "actionDate": "2027-02-01", "text": "Referred to the Committee on Ways",
                "committees": [{"systemCode": "hswm00", "name": "Ways and Means Committee"}]}
    floor = {"type": "Floor", "actionDate": "2027-03-01", "text": "Passed", "committees": [
        {"systemCode": "hsag00"}]}
    db_session.add_all([
        ApiCache(tier="congress", cache_key="bill-actions-120-hr-7", data_json=json.dumps([referral, floor])),
        ApiCache(tier="congress", cache_key="bill-record-summaries-120-hr-7",
                 data_json=json.dumps({"value": [{"text": "<p>This bill changes the tax code.</p>",
                                                  "updateDate": "2027-02-03"}]})),
        ApiCache(tier="congress", cache_key="bill-actions-120-hr-8", data_json=json.dumps([floor])),
        RepSponsoredBill(representative_id="r", bill_id="HR.7", title="Tax Act", congress=120),
        FeedArticle(url="u1", source_name="BBC", scope="world", text="t", title="Storm hits coast",
                    description="Rain", label=0, embedding=b"", model="m", published=datetime(2027, 2, 2)),
        FeedArticle(url="u2", source_name="NPR", scope="politics", text="t", title="Senate votes",
                    description="", label=1, embedding=b"", model="m", published=datetime(2027, 2, 2)),
        RaceCoverageItem(race_id="r1", source_type="rss", source_name="Outlet", title="Poll shows tight race",
                         url="u3", summary="A new poll", published_at=datetime(2027, 2, 4)),
    ])
    db_session.flush()
    texts = {t.text: t for t in cr.labelled_texts(db_session)}
    assert set(texts) == {"Tax Act. This bill changes the tax code.", "Storm hits coast. Rain",
                          "Poll shows tight race. A new poll"}
    bill = texts["Tax Act. This bill changes the tax code."]
    assert bill.labels == {"house": ("HSWM",), "senate": None} and bill.kind == "bill"
    assert texts["Storm hits coast. Rain"].labels == {"house": (), "senate": ()}
    assert texts["Poll shows tight race. A new poll"].kind == "none"


def test_a_text_votes_only_in_a_chamber_it_has_a_label_for():
    texts = [cr.Labelled("a", {"house": ("HA",), "senate": None}, 1, "bill"),
             cr.Labelled("b", {"house": (), "senate": ()}, 1, "none")]
    v = cr.votes(texts, {"house": ["HA", "HB"], "senate": ["SA"]})
    assert v["house"].tolist() == [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    assert v["senate"].tolist() == [[0.0, 0.0], [0.0, 1.0]]


def test_the_nearest_texts_are_those_voting_in_the_chamber():
    ref = _toy_reference()
    ref.votes["senate"] = np.array([[0, 0.0], [0, 0.0], [0, 1.0]])  # only the third votes in the Senate
    nb = cr.neighbours(np.array([[1.0, 0, 0, 0]]), ref, 1)
    assert nb.index["house"][0, 0] == 0 and nb.index["senate"][0, 0] == 2


def test_a_refit_whose_reference_is_missing_falls_back_to_the_bundled_fit(monkeypatch):
    """A refit's parameters belong to its reference: without the reference
    file the bundled fit and its reference decide, worded as likely."""
    refit = {"reference": "data", "k": 15, "alpha": 0.0, "temperature": 5.0, "threshold": 0.5,
             "precision_rule_met": True}
    monkeypatch.setattr(cr, "calibration", lambda: refit)
    monkeypatch.setattr(cr.data_refresh, "read", lambda name: {} if name == cr.REFERENCE_NAME
                        else cr._load(cr.JURISDICTIONS_PATH))
    monkeypatch.setattr(cr, "_reference", None)
    used = []

    def fake_reference(embed_fn=None, texts=None, vectors=None):
        used.append(texts)
        return _toy_reference()

    monkeypatch.setattr(cr, "Reference", fake_reference)
    monkeypatch.setattr(cr, "embed", lambda texts: np.array([[1.0, 0, 0, 0]]))
    monkeypatch.setattr(cr, "jurisdictions", lambda: {c: {"name": c, "url": f"https://x/{c}"} for c in ("HA", "HB", "SA")})
    bundled = cr._load(cr.CALIBRATION_PATH)
    routed = cr.route_committees("t", "s")
    assert used == [None]  # the hand-labelled reference
    assert all(r["likely"] is (not bundled.get("precision_rule_met", False)) for r in routed)


def test_a_fit_meeting_the_precision_rule_is_not_worded_likely():
    assert cr.precision_rule_met({"precision_rule_met": True})
    assert not cr.precision_rule_met({})
    # The bundled hand-labelled fit never met it.
    assert not cr.precision_rule_met(cr._load(cr.CALIBRATION_PATH))
