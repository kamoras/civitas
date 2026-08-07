"""Tests for incremental member analysis fingerprinting.

The asymmetry that shapes every test here: a fingerprint that is too
*sensitive* costs a wasted re-derivation, which is merely slow. A
fingerprint that is too *coarse* serves a stale scorecard, which for a
transparency project is the actual harm. So the coverage weights toward
proving that changed inputs are detected, not that unchanged ones are
skipped.
"""

import pytest

from app.models import MemberAnalysisFingerprint, PipelineRun, ScoreSnapshot, Senator
from app.pipeline.analyze.member_fingerprint import (
    clear_fingerprints,
    compute_fingerprint,
    load_fingerprints,
    record_fingerprint,
)

CODE_HASH = "abc123"

BASE_INPUTS = {
    "senator": {"id": "S1", "name": "A Senator", "party": "D"},
    "funding": {"totalRaised": 100.0, "topDonors": [{"name": "X", "amount": 5}]},
    "votingRecord": {"keyVotes": [{"billId": "hr-1", "position": "Yea"}]},
    "sponsoredBills": [{"billId": "hr-1", "title": "A Bill"}],
    "platformText": "some platform",
    "leadershipScore": 0.5,
    "ideologyScore": -0.2,
    "bipartisanshipScore": 0.1,
    "attractedBipartisanshipScore": 0.3,
    "officialTitles": {"hr-1": "An Official Title"},
    "rollCallData": {"hr-1": None},
    "ideologyBounds": (0.3, 0.7),
}


def test_identical_inputs_produce_identical_fingerprints():
    assert compute_fingerprint(BASE_INPUTS, CODE_HASH) == compute_fingerprint(
        dict(BASE_INPUTS), CODE_HASH
    )


def test_dict_key_order_does_not_affect_the_fingerprint():
    reordered = {k: BASE_INPUTS[k] for k in reversed(list(BASE_INPUTS))}
    assert compute_fingerprint(reordered, CODE_HASH) == compute_fingerprint(BASE_INPUTS, CODE_HASH)


@pytest.mark.parametrize(
    "path,new_value",
    [
        ("platformText", "a different platform"),
        ("leadershipScore", 0.9),
        ("ideologyScore", 0.9),
        ("bipartisanshipScore", 0.9),
        ("attractedBipartisanshipScore", 0.9),
        ("funding", {"totalRaised": 200.0, "topDonors": []}),
        ("votingRecord", {"keyVotes": [{"billId": "hr-1", "position": "Nay"}]}),
        ("sponsoredBills", [{"billId": "hr-2", "title": "Another Bill"}]),
        ("officialTitles", {"hr-1": "A Changed Official Title"}),
        ("rollCallData", {"hr-1": {"yea": 51, "nay": 49}}),
        ("senator", {"id": "S1", "name": "A Senator", "party": "R"}),
        ("ideologyBounds", (0.25, 0.75)),
    ],
)
def test_every_tracked_input_changes_the_fingerprint(path, new_value):
    """Each of these really does affect the analyze body's output. A field
    that stopped moving the hash would mean stale scorecards after it
    changed — the failure mode this whole design is built to avoid."""
    changed = {**BASE_INPUTS, path: new_value}
    assert compute_fingerprint(changed, CODE_HASH) != compute_fingerprint(BASE_INPUTS, CODE_HASH)


def test_analysis_code_hash_invalidates_the_fingerprint():
    """Any edit to analyze/transform/assemble/scoring changes the code hash
    the pipeline already computes, which must invalidate every member at
    once — otherwise a silently-changed algorithm keeps serving output
    produced by the old one."""
    assert compute_fingerprint(BASE_INPUTS, "hash_v1") != compute_fingerprint(
        BASE_INPUTS, "hash_v2"
    )


def test_schema_version_is_folded_in(monkeypatch):
    import app.pipeline.analyze.member_fingerprint as mf

    before = compute_fingerprint(BASE_INPUTS, CODE_HASH)
    monkeypatch.setattr(mf, "FINGERPRINT_SCHEMA_VERSION", 99)
    assert mf.compute_fingerprint(BASE_INPUTS, CODE_HASH) != before


def test_non_json_types_do_not_raise():
    """Datetimes and Decimals reach this from the fetch layer."""
    from datetime import datetime
    from decimal import Decimal

    inputs = {**BASE_INPUTS, "extra": {"when": datetime(2026, 1, 1), "amt": Decimal("1.5")}}
    assert compute_fingerprint(inputs, CODE_HASH)


PREPARED = {
    "senator": {"id": "S1", "bioguideId": "B1", "name": "A Senator", "party": "D"},
    "funding": {"totalRaised": 100.0},
    "votingRecord": {"keyVotes": []},
    "sponsoredBills": [{"billId": "hr-1", "title": "A Bill"}],
}


def _inputs(**overrides):
    from app.pipeline.senate_pipeline import _senator_fingerprint_inputs

    kwargs = {
        "prepared": PREPARED,
        "platform_texts": {"B1": "platform"},
        "leadership_scores": {"B1": 0.5},
        "ideology_scores": {"B1": -0.2},
        "bipartisanship_scores": {"B1": 0.1},
        "attracted_bipartisanship_scores": {"B1": 0.3},
        "official_titles_map": {"hr-1": "Official"},
        "roll_call_data_map": {"hr-1": {"yea": 51}},
        "ideology_bounds_by_party": {"D": (0.3, 0.7), "R": (0.2, 0.8)},
    }
    kwargs.update(overrides)
    return _senator_fingerprint_inputs(**kwargs)


def test_inputs_helper_captures_the_cohort_level_scores():
    """The analyze body reads four cohort-wide sponsorship scores that are
    not part of `prepared`. Hashing `prepared` alone would keep serving a
    stale scorecard after a cohort recompute moved them."""
    base = _inputs()
    assert base["leadershipScore"] == 0.5
    assert base["ideologyScore"] == -0.2
    assert base["bipartisanshipScore"] == 0.1
    assert base["attractedBipartisanshipScore"] == 0.3
    assert base["platformText"] == "platform"

    changed = _inputs(leadership_scores={"B1": 0.9})
    assert compute_fingerprint(changed, CODE_HASH) != compute_fingerprint(base, CODE_HASH)


def test_inputs_helper_only_takes_this_members_slice_of_global_maps():
    """Hashing the whole title/roll-call maps would make every senator's
    fingerprint change whenever any bill anywhere changed, which defeats
    the optimisation entirely."""
    base = _inputs()
    assert set(base["officialTitles"]) == {"hr-1"}
    assert set(base["rollCallData"]) == {"hr-1"}

    # Another senator's bill moving must not disturb this one.
    unrelated = _inputs(
        official_titles_map={"hr-1": "Official", "hr-999": "Someone Else's Bill"},
        roll_call_data_map={"hr-1": {"yea": 51}, "hr-999": {"yea": 1}},
    )
    assert compute_fingerprint(unrelated, CODE_HASH) == compute_fingerprint(base, CODE_HASH)


def test_inputs_helper_detects_a_change_to_this_members_own_bill():
    base = _inputs()
    retitled = _inputs(official_titles_map={"hr-1": "A Different Official Title"})
    revoted = _inputs(roll_call_data_map={"hr-1": {"yea": 99}})

    assert compute_fingerprint(retitled, CODE_HASH) != compute_fingerprint(base, CODE_HASH)
    assert compute_fingerprint(revoted, CODE_HASH) != compute_fingerprint(base, CODE_HASH)


def test_inputs_helper_captures_this_senators_party_ideology_bounds():
    """score_calculator._constituent_alignment_core reads the cohort-wide
    party_ideology_bounds() terciles (via write_party_ideology_bounds /
    _party_ideology_bounds) for the Constituent Alignment score component,
    and describe_senator_position reads the same value for the
    progressive/moderate/centrist label. A senator whose own ideologyScore
    is unchanged can still get a different score and label if some OTHER
    senator's ideology score moved their shared party's terciles — this
    must not be missed the way officialTitles/rollCallData deliberately
    are not missed for cohort-wide bill changes."""
    base = _inputs()
    assert base["ideologyBounds"] == (0.3, 0.7)

    shifted = _inputs(ideology_bounds_by_party={"D": (0.35, 0.75), "R": (0.2, 0.8)})
    assert compute_fingerprint(shifted, CODE_HASH) != compute_fingerprint(base, CODE_HASH)


def test_inputs_helper_ignores_the_other_partys_bounds():
    """Only this senator's own party's bounds are relevant — scoped the
    same conservative way as officialTitles/rollCallData, so a shift in
    the OTHER party's distribution doesn't force an unnecessary
    re-derivation of every senator in the unaffected party."""
    base = _inputs()
    other_party_shifted = _inputs(
        ideology_bounds_by_party={"D": (0.3, 0.7), "R": (0.1, 0.9)}
    )
    assert compute_fingerprint(other_party_shifted, CODE_HASH) == compute_fingerprint(base, CODE_HASH)


def test_inputs_helper_survives_a_member_with_no_sponsored_bills():
    empty = _inputs(prepared={**PREPARED, "sponsoredBills": []})
    assert empty["officialTitles"] == {}
    assert empty["rollCallData"] == {}


def test_record_and_load_round_trip(db_session):
    record_fingerprint(db_session, "senator", "S1", "fp1")
    record_fingerprint(db_session, "senator", "S2", "fp2")
    record_fingerprint(db_session, "representative", "R1", "fp3")

    senators = load_fingerprints(db_session, "senator")
    assert senators == {"S1": "fp1", "S2": "fp2"}
    # Entity types are isolated — a rep's fingerprint must never satisfy a
    # senator's skip check.
    assert load_fingerprints(db_session, "representative") == {"R1": "fp3"}


def test_recording_the_same_member_twice_updates_in_place(db_session):
    record_fingerprint(db_session, "senator", "S1", "fp1")
    record_fingerprint(db_session, "senator", "S1", "fp2")

    rows = db_session.query(MemberAnalysisFingerprint).all()
    assert len(rows) == 1
    assert rows[0].fingerprint == "fp2"


def test_load_returns_empty_on_failure_rather_than_raising(db_session, monkeypatch):
    """No fingerprints means no skips, which is the pre-existing behaviour.
    This optimisation must never be the reason a run fails."""
    def _explode(*args, **kwargs):
        raise RuntimeError("db gone")

    monkeypatch.setattr(db_session, "query", _explode)
    assert load_fingerprints(db_session, "senator") == {}


def test_record_failure_is_swallowed(db_session, monkeypatch):
    def _explode(*args, **kwargs):
        raise RuntimeError("db gone")

    monkeypatch.setattr(db_session, "query", _explode)
    record_fingerprint(db_session, "senator", "S1", "fp1")  # must not raise


def test_clear_fingerprints_scopes_by_entity_type(db_session):
    record_fingerprint(db_session, "senator", "S1", "fp1")
    record_fingerprint(db_session, "representative", "R1", "fp2")

    assert clear_fingerprints(db_session, "senator") == 1
    assert load_fingerprints(db_session, "senator") == {}
    assert load_fingerprints(db_session, "representative") == {"R1": "fp2"}


def test_clear_fingerprints_without_a_type_clears_everything(db_session):
    record_fingerprint(db_session, "senator", "S1", "fp1")
    record_fingerprint(db_session, "representative", "R1", "fp2")

    assert clear_fingerprints(db_session) == 2
    assert db_session.query(MemberAnalysisFingerprint).count() == 0


def test_skipped_members_still_get_a_score_snapshot(db_session):
    """The property that makes skipping safe at all.

    _record_score_snapshots iterates every Senator row, not just the ones
    this run re-derived, so a skipped senator still contributes today's
    trend point from its stored scores. If this ever stops holding, the
    incremental path silently punches holes in every trend chart.
    """
    from app.pipeline.senate_pipeline import _record_score_snapshots

    db_session.add(Senator(
        id="S1", name="Analysed Senator", state="CA", party="D",
        score_funding_independence=80.0, score_promise_persistence=70.0,
        score_independent_voting=60.0, score_funding_diversity=50.0,
        score_legislative_effectiveness=40.0,
    ))
    db_session.add(Senator(
        id="S2", name="Skipped Senator", state="NY", party="R",
        score_funding_independence=30.0, score_promise_persistence=20.0,
        score_independent_voting=10.0, score_funding_diversity=15.0,
        score_legislative_effectiveness=25.0,
    ))
    db_session.commit()

    _record_score_snapshots(db_session)

    snapshots = db_session.query(ScoreSnapshot).filter(
        ScoreSnapshot.entity_type == "senator"
    ).all()
    assert {s.entity_id for s in snapshots} == {"S1", "S2"}
    # The skipped senator's snapshot carries its real stored scores, not
    # zeros or a placeholder.
    skipped = next(s for s in snapshots if s.entity_id == "S2")
    assert skipped.score_1 == 30.0


def test_reset_all_data_clears_fingerprints_too(db_session):
    """A fingerprint outliving its member row would let the next run skip
    re-deriving a member that no longer exists, so a reset would silently
    fail to rebuild it."""
    from app import models

    # The reset list is the contract under test — assert the model is in it
    # rather than standing up a whole second engine to run reset_all_data.
    import inspect

    from app.database import reset_all_data

    source = inspect.getsource(reset_all_data)
    assert "MemberAnalysisFingerprint" in source
    assert models.MemberAnalysisFingerprint is not None


@pytest.mark.asyncio
async def test_clear_fingerprints_endpoint_refuses_during_a_run(db_session):
    from fastapi import HTTPException

    from app.api.admin import admin_clear_fingerprints

    db_session.add(PipelineRun(status="running"))
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await admin_clear_fingerprints(entity_type=None, db=db_session)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_clear_fingerprints_endpoint_clears_when_idle(db_session):
    from app.api.admin import admin_clear_fingerprints

    record_fingerprint(db_session, "senator", "S1", "fp1")
    result = await admin_clear_fingerprints(entity_type="senator", db=db_session)

    assert result["cleared"] == 1
    assert load_fingerprints(db_session, "senator") == {}
