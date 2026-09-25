"""The classification tasks of scripts/evaluate_embedding_models.py
(docs/research/embedding-models.md): label derivation from FEC committee
codes, the leave-one-out kNN, and the confidence AUROC."""

import importlib.util
import json
import pathlib
import sqlite3

import numpy as np

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "evaluate_embedding_models.py"
_spec = importlib.util.spec_from_file_location("evaluate_embedding_models", _PATH)
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)


def _cm_line(cid, name, dsgn, ctype, freq, org_type):
    # CMTE_ID|CMTE_NM|TRES_NM|ST1|ST2|CITY|ST|ZIP|DSGN|TP|PTY|FREQ|ORG_TP|CONNECTED_ORG|CAND_ID
    return f"{cid}|{name}|T|1 MAIN||X|VA|0|{dsgn}|{ctype}||{freq}|{org_type}||"


def test_donor_labels_come_from_filed_committee_type(tmp_path):
    lines = [
        _cm_line("C1", "SMITH FOR SENATE", "P", "S", "Q", ""),
        _cm_line("C2", "STATE DEMOCRATIC PARTY", "U", "Y", "M", ""),
        _cm_line("C3", "ACME CORP EMPLOYEES PAC", "B", "Q", "M", "C"),
        _cm_line("C4", "PEOPLE FOR GOOD GOVERNMENT", "U", "N", "M", ""),  # unconnected: ambiguous, excluded
        _cm_line("C5", "JONES FOR CONGRESS", "P", "H", "T", ""),  # terminated: excluded
    ]
    cm = tmp_path / "cm.txt"
    cm.write_text("\n".join(lines))
    names, labels = harness.load_donor_task(str(cm))
    assert dict(zip(names, labels)) == {
        "SMITH FOR SENATE": "CandidateAffiliated",
        "STATE DEMOCRATIC PARTY": "Party/Ideological",
        "ACME CORP EMPLOYEES PAC": "Org/Employees",
    }


def test_bill_task_dedupes_and_drops_sparse_areas(tmp_path):
    db = tmp_path / "c.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE api_cache (tier TEXT, cache_key TEXT, data_json TEXT, cached_at TEXT)")
    health = [{"congress": 119, "type": "S", "number": str(i), "title": f"Health bill {i}",
               "policyArea": {"name": "Health"}} for i in range(12)]
    rare = [{"congress": 119, "type": "S", "number": "99", "title": "Rare", "policyArea": {"name": "Rare"}}]
    for key, data in (("member-sponsored-v2-A", health + rare), ("member-sponsored-v2-B", health)):
        conn.execute("INSERT INTO api_cache VALUES ('congress', ?, ?, '')", (key, json.dumps(data)))
    conn.commit()
    conn.close()
    titles, labels = harness.load_bill_task(str(db))
    assert len(titles) == 12 and set(labels) == {"Health"}


def test_loo_knn_never_votes_for_itself_and_reports_share():
    emb = np.array([[1, 0], [0.99, 0.14], [0, 1], [0.14, 0.99]], dtype=np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    pred, share = harness._loo_knn(emb, ["a", "a", "b", "b"], 1)
    assert pred == ["a", "a", "b", "b"]
    assert np.allclose(share, 1.0)


def test_auroc_is_undefined_without_both_outcomes():
    assert np.isnan(harness._auroc(np.array([0.1, 0.2]), np.array([True, True])))
    assert harness._auroc(np.array([0.1, 0.9]), np.array([False, True])) == 1.0
