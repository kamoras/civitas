"""The fitted cosponsorship edge weights (scripts/calibrate_sponsorship_edge_weights.py,
app/data/sponsorship_edge_weights.json) and their refresh gate."""

import json
import pathlib
import sys

import numpy as np
import pytest

from app import data_refresh
from app.pipeline.analyze.sponsorship_analysis import EDGE_WEIGHTS_FILE, edge_weights

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import calibrate_sponsorship_edge_weights as cw  # noqa: E402

BUNDLED = json.loads((pathlib.Path(data_refresh.BUNDLED) / EDGE_WEIGHTS_FILE).read_text())


def test_each_chamber_reads_its_own_weights():
    for chamber in ("house", "senate"):
        enacted, advanced, stalled = edge_weights(chamber)
        assert enacted == 1.0 and 0.0 <= stalled <= advanced <= enacted
    with pytest.raises(KeyError):
        edge_weights("assembly")


def test_the_bundled_file_passes_its_own_gate_and_records_its_test():
    assert data_refresh._edge_weights_gate(BUNDLED, BUNDLED) == []
    for chamber, fit in BUNDLED["fit"].items():
        forward = fit["forward"]
        kept = BUNDLED["chambers"][chamber] != fit["best"]
        # A chamber takes its fit only past one standard error forward.
        assert kept == (not fit["adopted"])
        assert fit["adopted"] == (forward["delta"] > forward["se"])


def test_the_gate_refuses_disordered_weights_and_a_shorter_fit():
    bad = json.loads(json.dumps(BUNDLED))
    bad["chambers"]["senate"] = {"enacted": 1.0, "advanced": 0.2, "stalled": 0.5}
    assert any("senate weights" in f for f in data_refresh._edge_weights_gate(bad, BUNDLED))
    short = json.loads(json.dumps(BUNDLED))
    short["fit"]["house"]["transitions"] = short["fit"]["house"]["transitions"][:1]
    assert any("transitions" in f for f in data_refresh._edge_weights_gate(short, BUNDLED))


def test_within_party_ranks_each_party_on_its_own():
    # Each party perfectly ordered within itself, the two parties' outcomes
    # on different levels: a whole-chamber ranking would read the level.
    scores = np.array([1, 2, 3, 10, 11, 12], dtype=float)
    outcome = np.array([7, 8, 9, 0, 1, 2], dtype=float)
    groups = [np.array([0, 1, 2]), np.array([3, 4, 5])]
    assert cw.within_party(scores, outcome, groups) == pytest.approx(1.0)
    assert cw.spearman(scores, outcome) < 0


def test_the_forward_test_fits_only_on_earlier_congresses():
    # Weights a win on the first two Congresses and b on the rest: the
    # third is predicted by a (the best on the two before it).
    a, b, in_use = (1.0, 0.1, 0.0), (1.0, 0.9, 0.9), (1.0, 0.6, 0.3)
    transitions = [{"congress": c, "chamber": "senate"} for c in (113, 114, 115, 116)]
    scores = {a: [0.5, 0.5, 0.1, 0.1], b: [0.1, 0.1, 0.6, 0.6], in_use: [0.2, 0.2, 0.2, 0.2]}
    forward = cw.forward_test(transitions, scores, in_use, "senate")
    assert [s["weights"] for s in forward["steps"]] == [list(a), list(a)]
    assert forward["delta"] == pytest.approx(-0.1)


def test_the_leadership_matrix_is_the_pipelines():
    counts = np.zeros((3, 3, 3))
    counts[cw.ENACTED, 0, 1] = 2
    counts[cw.STALLED, 2, 1] = 4
    got = cw.leadership(counts, (1.0, 0.5, 0.25))
    assert got.shape == (3,) and got.sum() == pytest.approx(1.0)
