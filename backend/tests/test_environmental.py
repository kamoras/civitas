"""/environmental's figures: every one sourced and dated, the worked-out ones
computed from them, and every box line free of the site's banned dash."""

import re

from fastapi.testclient import TestClient

from app import environmental
from app.main import app


def _leaves(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _leaves(v, f"{path}[{i}]")
    else:
        yield path, node


def test_every_figure_has_a_source_and_a_date():
    f = environmental.figures()
    sets = [v for k, v in f.items() if not k.startswith("_") and isinstance(v, dict) and k != "models"]
    sets += [f["models"]["llm"], *f["models"]["embedding"]]
    for s in sets:
        assert s.get("source"), s
        assert re.fullmatch(r"\d{4}(-\d{2}){0,2}", s.get("date", "")), s


def test_worked_out_figures():
    d = environmental.derived(environmental.figures())
    assert d["whPerDay"] == 168
    assert d["kWhPerYear"] == 61.3
    assert d["kgCo2ePerKWh"] == 0.35  # 770.9 lb/MWh
    assert d["kgCo2ePerYear"] == 21.4
    assert d["carMiles"] == 55
    assert d["phoneCharges"] == 3200
    assert d["llmMinutesPerDay"] == 29
    assert d["requestWh"] == 0.02
    assert d["memoryGiB"] == 16


def test_boxes_are_sourced_templates():
    boxes = environmental.boxes()
    assert len({b["id"] for b in boxes}) == len(boxes)
    for b in boxes:
        assert b["source"] and b["dek"] and b["figure"]["type"] == "stat"
        for _, v in _leaves(b):
            assert "—" not in str(v), b["id"]


def test_endpoint_serves_the_boxes():
    r = TestClient(app).get("/api/environmental")
    assert r.status_code == 200
    assert "max-age" in r.headers["cache-control"]
    body = r.json()
    assert [b["id"] for b in body["boxes"]] == [b["id"] for b in environmental.boxes()]
    assert body["reviewed"] == environmental.figures()["reviewed"]
