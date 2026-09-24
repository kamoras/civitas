"""Regenerate app/data/les_reference.json — Legislative Effectiveness's
bundled pre-first-run population reference.

The pipeline recomputes this reference for each chamber on every run
(score_calculator.compute_les_reference) and writes /data/les_reference.json,
which takes precedence. The bundled file only matters before a deployment's
first pipeline run (or if /data is lost), so it just needs to be a real,
recent measurement — this script takes one from the public API using the
SAME compute_les_reference the pipeline uses, so the fallback and the live
reference can never be computed differently.

Per AGENTS.md §3a this writes the JSON file directly; nothing is pasted
into source code.

Run inside the backend container (imports the real scoring module):
    docker compose -f docker-compose.yml -f docker-compose.dev.yml \\
        run --rm --no-deps -v "$(pwd)/backend:/app" \\
        -e PYTHONPATH=/app backend python scripts/calibrate_les_credit_scale.py
Then commit the regenerated backend/app/data/les_reference.json.
"""

import datetime
import json
import pathlib
import urllib.request

from app.pipeline.analyze.score_calculator import (
    compute_les_reference,
    derive_chamber_majority,
)

API_BASE = "https://civitas-research.org/api"
UA = {"User-Agent": "CivitasCivicPlatform/1.0 (LES calibration; contact: mack.ryanm@gmail.com)"}
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "les_reference.json"


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def _sitting_president_party() -> str | None:
    # The directory's president branch lists only the sitting president.
    listing = _fetch_json(f"{API_BASE}/politicians?branch=president")
    return listing[0].get("party") if listing else None


def main() -> None:
    out: dict = {
        "_source": (
            "Pre-first-run fallback only: the pipeline recomputes this per chamber every "
            "run (score_calculator.compute_les_reference) and writes /data/les_reference.json, "
            f"which takes precedence. Measured from {API_BASE} by "
            "backend/scripts/calibrate_les_credit_scale.py."
        ),
        "_as_of": datetime.date.today().isoformat(),
    }
    tie_breaker = _sitting_president_party()

    for chamber in ("senate", "house"):
        members: list[tuple[list[dict], str | None]] = []
        congresses: set[int] = set()
        for entry in _fetch_json(f"{API_BASE}/politicians?branch={chamber}"):
            if not entry.get("hasScorecard"):
                continue
            sc = (_fetch_json(f"{API_BASE}/politicians/{entry['id']}").get("scorecard") or {})
            # The public scorecard doesn't expose caucus inference, so an
            # Independent counts toward neither side here (the pipeline
            # itself uses effectiveParty). Fine for a fallback file; revisit
            # if a chamber is ever within the Independents' margin.
            party = sc.get("party")
            bills = sc.get("sponsoredBills") or []
            members.append((bills, party))
            congresses.update(b["congress"] for b in bills if b.get("congress"))
        if not congresses:
            print(f"{chamber}: no sponsored-bill data; left unchanged")
            continue
        congress = max(congresses)
        majority = derive_chamber_majority([p for _, p in members], chamber, tie_breaker)
        ref = compute_les_reference(members, congress, majority)
        if ref is None:
            print(f"{chamber}: too few members with substantive bills; left unchanged")
            continue
        out[chamber] = ref
        print(f"{chamber}: {ref}")

    if "senate" not in out or "house" not in out:
        existing = json.loads(OUT.read_text()) if OUT.exists() else {}
        for chamber in ("senate", "house"):
            out.setdefault(chamber, existing.get(chamber))
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
