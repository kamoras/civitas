"""/environmental's figures: what the site can read it reads (the machine,
the models, the pipelines' hours); what it can't is sourced and dated; the
worked-out figures are computed; a figure not read says so; and a newer
eGRID edition raises an alert."""

import json
import re
import struct
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import data_refresh as dr
from app import environmental, environmental_sampler
from app.database import get_db
from app.main import app
from app.models import HousePipelineRun, PipelineRun, PipelineStatus


def _leaves(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _leaves(v)
    elif isinstance(node, list):
        for v in node:
            yield from _leaves(v)
    else:
        yield node


def test_every_stored_figure_has_a_source_and_a_date():
    f = environmental.figures()
    for k, s in f.items():
        if k.startswith("_") or not isinstance(s, dict):
            continue
        assert s.get("source"), k
        assert re.fullmatch(r"\d{4}(-\d{2}){0,2}", s.get("date", "")), k
    # Read by the site, never typed into the file.
    assert not {"hardware", "models", "pipelines"} & set(f)


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


def test_the_machine_is_read_from_proc(tmp_path):
    (tmp_path / "meminfo").write_text("MemTotal:       16607536 kB\nMemFree: 1 kB\n")
    (tmp_path / "cpuinfo").write_text(
        "".join(f"processor\t: {i}\nCPU part\t: 0xd0b\n\n" for i in range(4))
        + "Model\t\t: Raspberry Pi 5 Model B Rev 1.1\n")
    hw = environmental.read_machine(str(tmp_path))
    assert (hw["model"], hw["cores"], hw["memoryGiB"]) == ("Raspberry Pi 5 Model B Rev 1.1", 4, 16)
    assert environmental.read_machine(str(tmp_path / "missing")) is None


def test_pipeline_hours_merge_overlapping_runs_across_pipelines(db_session):
    now = datetime(2026, 10, 21, 12, 0)
    day = datetime(2026, 10, 20)  # the last whole day
    db_session.add_all([
        PipelineRun(started_at=day + timedelta(hours=3), completed_at=day + timedelta(hours=5),
                    status=PipelineStatus.COMPLETED),
        # Overlaps the Senate run by an hour: counted once.
        HousePipelineRun(started_at=day + timedelta(hours=4), completed_at=day + timedelta(hours=7),
                         status=PipelineStatus.COMPLETED),
        # Killed without a completion time: its recorded elapsed time.
        HousePipelineRun(started_at=day - timedelta(days=1, hours=-2), completed_at=None,
                         elapsed_seconds=3600, status=PipelineStatus.FAILED),
    ])
    db_session.commit()
    pl = environmental.pipeline_hours(db_session, now)
    assert pl["maxHours"] == 4.0 and pl["minHours"] == 0.0
    assert pl["meanHours"] == round(5 / 7, 1)
    assert pl["through"] == "2026-10-20"
    assert environmental.pipeline_hours(db_session, now + timedelta(days=30)) is None


def test_a_figure_not_read_says_so_and_is_never_zero():
    boxes = {b["id"]: b for b in environmental.boxes(environmental.figures(), None, None, None)}
    for key in ("machine", "models", "pipelines"):
        assert boxes[key]["figure"] is None and boxes[key]["dek"].startswith("Not read yet")


def test_boxes_are_sourced_templates():
    hw = {"model": "Raspberry Pi 5", "cores": 4, "memoryGiB": 16, "source": "x", "date": "2026-10-21"}
    models = {"llm": {"name": "m.gguf", "parameters": 1170340608, "bytes": 728509440, "readAt": "2026-10-21T06:20:00Z"},
              "embedding": {"a": {"parameters": 22565376, "use": "classification", "readAt": "2026-10-21T06:20:00Z"}}}
    pl = {"days": 7, "minHours": 4, "maxHours": 20, "meanHours": 10.5, "through": "2026-10-20",
          "source": "runs", "date": "2026-10-21"}
    boxes = environmental.boxes(environmental.figures(), hw, models, pl)
    assert len({b["id"] for b in boxes}) == len(boxes)
    by = {b["id"]: b for b in boxes}
    assert by["models"]["figure"]["text"] == "1.2B" and "729 MB" in by["models"]["dek"]
    assert by["machine"]["figure"]["text"] == "16 GB"
    for b in boxes:
        assert b["source"] and b["dek"] and b["figure"]["type"] == "stat"
        assert "—" not in " ".join(map(str, _leaves(b))), b["id"]


def test_the_sampler_reads_safetensors_headers(tmp_path):
    header = json.dumps({"__metadata__": {}, "a": {"dtype": "F32", "shape": [384, 512], "data_offsets": [0, 0]},
                         "b": {"dtype": "F32", "shape": [512], "data_offsets": [0, 0]}}).encode()
    p = tmp_path / "model.safetensors"
    p.write_bytes(struct.pack("<Q", len(header)) + header)
    assert environmental_sampler.safetensors_parameters(str(p)) == 384 * 512 + 512


def test_the_sampler_reads_the_llm_server_and_keeps_the_last_reading(monkeypatch, tmp_path):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "/models/m.gguf", "meta": {"n_params": 10, "size": 20, "ftype": "Q4"}}]}

    out = tmp_path / "environmental_runtime.json"
    monkeypatch.setattr(environmental_sampler, "runtime_data_path", lambda name: str(out))
    monkeypatch.setattr(environmental_sampler, "embedding_parameters", lambda repo: None)
    monkeypatch.setattr(environmental_sampler.httpx, "get", lambda *a, **k: Resp())
    doc = environmental_sampler.sample()
    assert doc["llm"]["parameters"] == 10 and doc["llm"]["name"] == "m.gguf"

    def down(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(environmental_sampler.httpx, "get", down)
    assert environmental_sampler.sample()["llm"]["parameters"] == 10  # kept, with its date


def test_a_newer_egrid_edition_is_found(monkeypatch):
    monkeypatch.setattr(dr, "_record_try", lambda n, o: None)
    ctx = dr.Context("environmental.json", {"grid": {"edition": "eGRID2023"}}, date(2026, 10, 21), None)
    monkeypatch.setattr(dr, "_get_text", lambda url: "<h2>eGRID 2023</h2> eGRID2022")
    assert dr._egrid_stale(ctx) is None
    monkeypatch.setattr(dr, "_get_text", lambda url: "<h2>eGRID 2024</h2> eGRID2023")
    assert "eGRID2024" in dr._egrid_stale(ctx)
    monkeypatch.setattr(dr, "_get_text", lambda url: None)
    assert dr._egrid_stale(ctx) is None
    assert any(s.name == "environmental.json" and s.stale is dr._egrid_stale for s in dr.DATA_FILES)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_endpoint_serves_the_boxes(client):
    r = client.get("/api/environmental")
    assert r.status_code == 200
    assert "max-age" in r.headers["cache-control"]
    body = r.json()
    assert [b["id"] for b in body["boxes"]][:3] == ["machine", "board", "assumed"]
    assert body["reviewed"] == environmental.figures()["reviewed"]
