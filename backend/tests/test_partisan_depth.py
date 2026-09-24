"""Partisan depth is relative to the member's own party, and the ideology
prior is blended on the vote-lean scale (fix 13; party_platform.
finalize_partisan_depth)."""

from app.pipeline.analyze.party_platform import finalize_partisan_depth


def profile(party, vote_lean, votes=20, ideology=None, cross=0.0):
    return {"evalParty": party, "voteLean": vote_lean, "partisanVoteCount": votes,
            "ideologyLean": ideology, "crossRatio": cross, "totalPositions": 5}


def labels(profiles):
    return [p["depth"] for p in finalize_partisan_depth(profiles)]


def test_depth_is_tercile_within_each_party():
    d = [profile("D", -0.05 * i) for i in range(1, 7)]
    r = [profile("R", 0.10 + 0.05 * i) for i in range(1, 7)]
    out = labels(d + r)
    assert out[:6].count("deep") == out[6:].count("deep") == 2
    assert out[:6].count("centrist") == out[6:].count("centrist") == 2


def test_mirrored_parties_get_mirrored_labels():
    # One fixed threshold treated the parties differently when their lean
    # ranges differ; within-party terciles don't care where the range sits.
    d = [profile("D", -0.02 - 0.04 * i) for i in range(8)]
    r = [profile("R", 0.20 + 0.04 * i) for i in range(8)]
    out = labels(d + r)
    assert out[:8] == out[8:]


def test_cross_cutting_is_kept():
    ps = [profile("D", -0.1 * i) for i in range(1, 8)] + [profile("D", -0.3, cross=0.5)]
    assert labels(ps)[-1] == "cross-cutting"


def test_prior_is_mapped_onto_the_vote_scale():
    # Full-data members: vote lean = 0.3 x ideology lean. A member with no
    # votes and ideology lean 1.0 gets 0.3, not 1.0.
    full = [profile("R" if i % 2 else "D", 0.3 * x, ideology=x)
            for i, x in enumerate([-1, -0.6, -0.2, 0.2, 0.6, 1, -0.8, 0.8, -0.4, 0.4])]
    sparse = profile("R", 0.0, votes=0, ideology=1.0)
    finalize_partisan_depth([*full, sparse])
    assert abs(sparse["overallLean"] - 0.3) < 1e-6


def test_profiles_stored_before_the_change_keep_their_lean():
    old = {"evalParty": "R", "overallLean": 0.25, "depth": "deep", "totalPositions": 3}
    finalize_partisan_depth([old])
    assert old["overallLean"] == 0.25
