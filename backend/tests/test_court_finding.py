"""scripts/fetch_court_finding.py, the data refresh's numpy port of the
research script's Court-level finding, gives the research script's numbers
on the same panel. The comparison with the research script itself runs
where its pandas and statsmodels are installed (the Research deps CI job);
the fixed-effect fit is checked against plain dummies everywhere."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _panel(seed: int = 3) -> list[tuple]:
    """Justices whose appointer leaves office part-way through their careers."""
    rng, rows = np.random.default_rng(seed), []
    for j in range(8):
        start, under = 1950 + 3 * j, 2 + j % 4
        for term in range(start, start + 12):
            inside = int(term < start + under)
            for _ in range(int(rng.integers(3, 9))):
                pet = int(rng.integers(0, 2))
                y = int(rng.random() < 0.45 + 0.08 * inside + 0.1 * pet + 0.02 * j)
                rows.append((f"J{j:02d}", term, y, inside, pet))
    return rows


def test_the_fixed_effect_fit_is_the_dummy_regressions():
    port = _script("fetch_court_finding")
    rows = _panel()
    a = np.array([r[2:] for r in rows], dtype=float)
    groups = sorted({r[0] for r in rows})
    dummies = np.array([[float(r[0] == g) for g in groups] for r in rows])
    full, *_ = np.linalg.lstsq(np.column_stack([a[:, 1], a[:, 2], dummies]), a[:, 0], rcond=None)
    assert port._fe_coef([r[0] for r in rows], a[:, 0], a[:, 1], a[:, 2]) == pytest.approx(full[0], abs=1e-10)


def test_the_port_gives_the_research_scripts_finding():
    pd = pytest.importorskip("pandas")
    pytest.importorskip("statsmodels")
    research, port = _script("research_justice_loyalty"), _script("fetch_court_finding")
    rows = _panel()
    P = pd.DataFrame(rows, columns=["justiceName", "term", "y", "in_office", "pet"])
    assert port.court_finding(rows) == research.court_finding(P)
