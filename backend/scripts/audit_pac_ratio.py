"""Regenerate app/data/funding_reference.json — Funding Independence's
bundled pre-first-run PAC-share reference.

The PAC-dependency component scores a member's PAC share against their
chamber's median (the median member scores 50; multiplier = 0.5 / median).
The pipeline measures that median from the members it is about to score on
every run (score_calculator.compute_funding_reference) and writes
/data/funding_reference.json, which takes precedence; this bundled file
only matters before a deployment's first run. It uses the SAME
compute_funding_reference the pipeline does, so the two can't differ. Per
AGENTS.md §3a it writes the JSON directly — nothing is pasted into code.

Pulls live funding from the public API — same reasoning
fetch_district_pvi.py and fetch_state_small_donor_baseline.py give for
scraping over a local data source.

Run from the repo (network required), then commit the regenerated file:
    cd backend && PYTHONPATH=. python3 scripts/audit_pac_ratio.py
"""

import datetime
import json
import pathlib
import urllib.request

from app.pipeline.analyze.score_calculator import compute_funding_reference

API_BASE = "https://civitas-research.org/api"
UA = {"User-Agent": "CivitasCivicPlatform/1.0 (PAC-ratio audit; contact: mack.ryanm@gmail.com)"}
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "funding_reference.json"


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def fetch_fundings(branch: str) -> list[dict]:
    listing = _fetch_json(f"{API_BASE}/politicians?branch={branch}")
    ids = [d["id"] for d in listing if d.get("hasScorecard")]
    return [
        (_fetch_json(f"{API_BASE}/politicians/{pid}").get("scorecard") or {}).get("funding") or {}
        for pid in ids
    ]


def main() -> None:
    existing = json.loads(OUT.read_text()) if OUT.exists() else {}
    out = {
        "_as_of": datetime.date.today().isoformat(),
        "_source": (
            "Pre-first-run fallback only: the pipeline recomputes this per chamber every run "
            "(score_calculator.compute_funding_reference) and writes /data/funding_reference.json, "
            f"which takes precedence. Measured from {API_BASE} by backend/scripts/audit_pac_ratio.py."
        ),
    }
    for branch in ("senate", "house"):
        ref = compute_funding_reference(fetch_fundings(branch))
        if ref is None:
            print(f"{branch}: too few funded members; left unchanged")
            out[branch] = existing.get(branch)
            continue
        out[branch] = ref
        print(f"{branch}: {ref} (implied multiplier x{0.5 / ref['pac_ratio_median']:.2f})")
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
