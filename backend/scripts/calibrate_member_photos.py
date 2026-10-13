"""Calibrate the member-photo choice (pipeline/fetch/member_photos.py).

Writes app/data/member_photo_calibration.json: the two numbers
`prefers_bioguide` decides with, and how the rule scores on the labels.

Ground truth. Every current member's two official photos, the Congress.gov
record's credited depiction and bioguide's portrait, compared side by side by
a person and labelled by which is the better avatar (a square crop):

  - "depiction": bioguide's isn't head-and-shoulders (a full-length or
    painted portrait, a wide or waist-down shot) or doesn't exist, and the
    depiction is;
  - "bioguide": the other way round;
  - "neither": both are the same non-headshot.

Every other member is "either": the same photo, or two good headshots. The
labels live in the calibration file (`labels`), each with the sha256 of both
images it judged; a member whose photo has changed since is left out of the
fit and listed, to be looked at again.

Measured per image: the largest face's height over the image's shorter side
(member_photos.face_share). The rule chooses bioguide when the depiction's
share is below `depiction_ceiling` and bioguide's is at least `margin` times
it. Fitted by grid search: every setting that gets every "depiction" and
"bioguide" label right, and switches the fewest "either"/"neither" members,
is feasible; the chosen setting is the middle of that set, so a small drift in
either number keeps it feasible.

Usage (network: fetches every pair, politely; reads member records from the
database):
    python backend/scripts/calibrate_member_photos.py [--cache DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.http_client import make_async_client  # noqa: E402
from app.models import ApiCache, Representative, Senator  # noqa: E402
from app.photos import bioguide_portrait_url, credited_depiction  # noqa: E402
from app.pipeline.fetch.congress import member_detail_cache_key  # noqa: E402
from app.pipeline.fetch.face_detection import face_share  # noqa: E402
from app.pipeline.fetch.member_photos import CALIBRATION_PATH, PAUSE_S, _image  # noqa: E402

MARGINS = [round(1.0 + i / 20, 2) for i in range(2, 31)]       # 1.10 .. 2.50
CEILINGS = [round(0.2 + i / 200, 3) for i in range(0, 61)]     # 0.200 .. 0.500


def _members() -> dict[str, str | None]:
    """Current members' bioguide ids -> credited depiction URL."""
    with SessionLocal() as db:
        ids = {b for (b,) in db.execute(select(Senator.bioguide_id).where(Senator.is_current))}
        ids |= {b for (b,) in db.execute(select(Representative.bioguide_id).where(Representative.is_current))}
        out = {}
        for b in sorted(i for i in ids if i):
            row = db.get(ApiCache, ("congress", member_detail_cache_key(b)))
            out[b] = credited_depiction(json.loads(row.data_json)) if row else None
        return out


async def _fetch(cache: pathlib.Path, members: dict[str, str | None]) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    async with make_async_client() as client:
        for b, depiction in members.items():
            for kind, url in (("bio", bioguide_portrait_url(b)), ("cg", depiction)):
                path = cache / f"{b}_{kind}.jpg"
                if not url or path.exists() or path.with_suffix(".404").exists():
                    continue
                body = await _image(client, url)
                if body is None:
                    path.with_suffix(".404").touch()
                else:
                    path.write_bytes(body)
                await asyncio.sleep(PAUSE_S)


def _pick(sb: float | None, sc: float | None, margin: float, ceiling: float) -> str:
    if sc is None:
        return "bioguide" if sb is not None else "none"
    if sb is None:
        return "depiction"
    return "bioguide" if sc < ceiling and sb >= margin * sc else "depiction"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=pathlib.Path, default=pathlib.Path("/tmp/civitas-member-photos"))
    args = ap.parse_args()

    current = json.loads(CALIBRATION_PATH.read_text())
    members = _members()
    asyncio.run(_fetch(args.cache, members))

    def read(b: str, kind: str) -> bytes | None:
        path = args.cache / f"{b}_{kind}.jpg"
        return path.read_bytes() if path.exists() else None

    shares, label, stale = {}, {}, []
    for b in members:
        bio, cg = read(b, "bio"), read(b, "cg")
        shares[b] = (face_share(bio) if bio else None, face_share(cg) if cg else None)
        judged = current["labels"].get(b)
        if judged is None:
            label[b] = "either"
            continue
        digest = {k: hashlib.sha256(v).hexdigest() if v else None for k, v in (("bioguide", bio), ("depiction", cg))}
        if digest != judged["sha256"]:
            stale.append(b)
            continue
        label[b] = judged["label"]

    decisive = [b for b in label if label[b] in ("depiction", "bioguide")]
    others = [b for b in label if label[b] in ("either", "neither")]
    feasible: list[tuple[int, float, float]] = []
    for m in MARGINS:
        for t in CEILINGS:
            if all(_pick(*shares[b], m, t) == label[b] for b in decisive):
                switched = sum(_pick(*shares[b], m, t) == "bioguide" for b in others)
                feasible.append((switched, m, t))
    if not feasible:
        sys.exit("No setting gets every decisive label right; the file is left as it was.")
    fewest = min(s for s, _, _ in feasible)
    best = [(m, t) for s, m, t in feasible if s == fewest]
    margin = statistics.median_low(sorted({m for m, _ in best}))
    ceiling = statistics.median_low(sorted({t for mm, t in best if mm == margin}))

    confusion: dict[str, dict[str, int]] = {}
    for b, lab in label.items():
        chosen = _pick(*shares[b], margin, ceiling)
        confusion.setdefault(lab, {}).setdefault(chosen, 0)
        confusion[lab][chosen] += 1

    current.update({
        "_as_of": datetime.date.today().isoformat(),
        "margin": margin,
        "depiction_ceiling": ceiling,
        "feasible": {
            "margin": [min(m for m, _ in best), max(m for m, _ in best)],
            "depiction_ceiling": [min(t for _, t in best), max(t for _, t in best)],
            "either_switched": fewest,
        },
        "confusion": {"members": len(label), "label -> chosen": confusion},
        "labels_stale": stale,
    })
    CALIBRATION_PATH.write_text(json.dumps(current, indent=1) + "\n")
    print(json.dumps({k: current[k] for k in ("margin", "depiction_ceiling", "feasible", "confusion", "labels_stale")}, indent=1))


if __name__ == "__main__":
    main()
