"""The bill-title word table's generator, run the way app/data_refresh.py
runs it (--skip-calibration --output <candidate>). On 2026-10-18 it wrote
its table and then crashed on a cache_clear() left over from when the
reader was lru_cached, so every monthly refresh kept the old table."""

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "calibrate_lda_bill_match.py"


def test_refresh_invocation_writes_the_table_and_exits_cleanly(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("calibrate_lda_bill_match", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    titles = {"hr1-119": ["Clean Water Act of 2025", "Clean Water Act"], "s2-119": ["Farm Credit Act"]}
    monkeypatch.setattr(script, "bill_titles", lambda congress: (titles, {}))
    out = tmp_path / "candidate.json"
    monkeypatch.setattr(sys, "argv", ["x", "--congress", "119", "--skip-calibration", "--output", str(out)])
    script.main()
    table = json.loads(out.read_text())
    assert table["documents"] == 6 and table["df"]
