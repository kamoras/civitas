"""Refit the member-photo choice (pipeline/fetch/member_photos.py) from the
face shares themselves.

Writes member_photo_calibration.json: the two numbers `prefers_bioguide`
decides with, how they were fitted, and how they score on the hand labels.
Run daily by app/data_refresh.py, which installs the refit only under the
acceptance rule (precision_rule.accept, data_refresh.precision_rule_accepts).
No network: it reads the shares the nightly run stored (MemberPhoto), one per
measured image, the largest face's height over the image's shorter side.

The two numbers, from the data alone:

  - depiction_ceiling: what counts as below a head-and-shoulders share. Most
    official photos are headshots, so their log face shares form the
    dominant component of a Gaussian mixture over every measured image
    (one per distinct image; the number of components by BIC, 1 to
    MAX_COMPONENTS). The ceiling is that component's lower CEILING_Z point
    (one-sided 97.5%, a convention): a depiction whose face is smaller than
    almost every headshot's is not one.
  - margin: how much larger bioguide's face must be. Most members' two
    photos are the same picture, resized, so the log ratio of a member's two
    shares has a dominant component at about zero: the detector measuring
    one picture twice. The margin is that component's upper MARGIN_Z point
    (99.9%, a convention: a switch changes what every reader sees, so it
    needs a difference well past re-measurement), as a ratio.

Hand labels (`labels`, carried over from the copy in use) are the held-out
check, never the fit: each current member's two photos were compared side by
side by a person and judged "depiction" (bioguide's isn't head-and-shoulders),
"bioguide" (the other way round), "neither" (the same non-headshot) or, for
everyone else, "either" (the same photo, or two good headshots; a switch
there is no error). A label counts only while both images are the ones it
judged (their sha256, stored by the nightly run). The comparison
(precision_rule.compare) is on the decisive labels: a switch to bioguide is
right for "bioguide" and wrong for "depiction".

Measured 2026-10-23 on production's stored shares: see `fit` and `check` in
the calibration file; the figures are in the PR that introduced this refit.

Usage:
    python backend/scripts/calibrate_member_photos.py --output PATH [--in-use PATH] [--database URL]
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scipy.stats import norm  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import precision_rule  # noqa: E402
from app.models import MemberPhoto  # noqa: E402
from app.pipeline.fetch.member_photos import CALIBRATION_PATH  # noqa: E402

MAX_COMPONENTS = 4
CEILING_Z = norm.ppf(0.975)
MARGIN_Z = norm.ppf(0.999)
# Fewer measured images than this is a store that failed to fill, not a
# population to fit.
MIN_IMAGES = 100
EM_ITERATIONS = 500


def mixture(x: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """A k-component 1-D Gaussian mixture by EM, started from the data's
    quantiles (deterministic): (weights, means, sds, BIC)."""
    means = np.quantile(x, (np.arange(k) + 0.5) / k)
    sds = np.full(k, x.std() / k + 1e-6)
    weights = np.full(k, 1.0 / k)
    for _ in range(EM_ITERATIONS):
        dens = weights * np.exp(-0.5 * ((x[:, None] - means) / sds) ** 2) / (sds * math.sqrt(2 * math.pi))
        total = dens.sum(axis=1, keepdims=True) + 1e-300
        resp = dens / total
        n = resp.sum(axis=0) + 1e-12
        new_means = (resp * x[:, None]).sum(axis=0) / n
        sds = np.sqrt((resp * (x[:, None] - new_means) ** 2).sum(axis=0) / n) + 1e-6
        weights = n / len(x)
        if np.allclose(new_means, means, atol=1e-9):
            means = new_means
            break
        means = new_means
    dens = weights * np.exp(-0.5 * ((x[:, None] - means) / sds) ** 2) / (sds * math.sqrt(2 * math.pi))
    loglik = float(np.log(dens.sum(axis=1) + 1e-300).sum())
    return weights, means, sds, (3 * k - 1) * math.log(len(x)) - 2 * loglik


def dominant(x: np.ndarray) -> dict:
    """The dominant component of the mixture BIC chooses."""
    fits = [mixture(x, k) for k in range(1, MAX_COMPONENTS + 1)]
    weights, means, sds, _ = min(fits, key=lambda f: f[3])
    j = int(np.argmax(weights))
    return {"components": len(weights), "weight": round(float(weights[j]), 3), "mean": float(means[j]),
            "sd": float(sds[j]), "bic": [round(f[3], 1) for f in fits]}


def fit(rows: list[MemberPhoto]) -> dict:
    by_image: dict[str, float] = {}
    for r in rows:
        for share, digest, kind in ((r.bioguide_face_share, r.bioguide_sha256, "b"),
                                    (r.depiction_face_share, r.depiction_sha256, "d")):
            if share:  # 0.0 is no face found, None no image
                by_image[digest or f"{r.bioguide_id}-{kind}"] = share
    if len(by_image) < MIN_IMAGES:
        raise SystemExit(f"{len(by_image)} measured images: too few to fit")
    shares = dominant(np.log(np.array(list(by_image.values()))))
    ratios = np.log(np.array([r.bioguide_face_share / r.depiction_face_share for r in rows
                              if r.bioguide_face_share and r.depiction_face_share]))
    noise = dominant(ratios)
    return {
        "depiction_ceiling": round(math.exp(shares["mean"] - CEILING_Z * shares["sd"]), 3),
        "margin": round(math.exp(noise["mean"] + MARGIN_Z * noise["sd"]), 3),
        "fit": {"images": len(by_image), "pairs": int(len(ratios)), "headshot_component": shares,
                "same_picture_component": noise, "ceiling_z": round(float(CEILING_Z), 3),
                "margin_z": round(float(MARGIN_Z), 3)},
    }


def switches(rows: list[MemberPhoto], margin: float, ceiling: float) -> dict[str, bool]:
    return {r.bioguide_id: bool(r.bioguide_face_share and r.depiction_face_share
                                and r.depiction_face_share < ceiling
                                and r.bioguide_face_share >= margin * r.depiction_face_share)
            for r in rows}


def current_labels(rows: list[MemberPhoto], labels: dict) -> tuple[dict[str, str], list[str]]:
    """{bioguide id: label} for the labels whose images are still the ones
    judged, and the ids whose images have changed or are not hashed yet."""
    stored = {r.bioguide_id: r for r in rows}
    out, stale = {}, []
    for b, judged in labels.items():
        r = stored.get(b)
        digest = {"bioguide": r.bioguide_sha256, "depiction": r.depiction_sha256} if r else None
        if digest != judged["sha256"]:
            stale.append(b)
        else:
            out[b] = judged["label"]
    return out, stale


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--in-use", default=None)
    ap.add_argument("--database", default=None)
    args = ap.parse_args()
    in_use = json.loads(pathlib.Path(args.in_use or CALIBRATION_PATH).read_text())
    if args.database:
        db = Session(create_engine(args.database))
    else:
        from app.database import SessionLocal
        db = SessionLocal()
    try:
        rows = list(db.scalars(select(MemberPhoto)))
        for r in rows:
            db.expunge(r)
    finally:
        db.close()

    refit = fit(rows)
    labels, stale = current_labels(rows, in_use.get("labels") or {})
    cand = switches(rows, refit["margin"], refit["depiction_ceiling"])
    used = switches(rows, float(in_use["margin"]), float(in_use["depiction_ceiling"]))
    decisive = sorted(b for b, lab in labels.items() if lab in ("bioguide", "depiction") and b in cand)
    correct = np.array([labels[b] == "bioguide" for b in decisive], bool)
    comparison = precision_rule.compare(correct, np.array([cand[b] for b in decisive], bool),
                                        np.array([used[b] for b in decisive], bool))
    comparison["unchanged"] = (refit["margin"] == in_use.get("margin")
                               and refit["depiction_ceiling"] == in_use.get("depiction_ceiling"))

    def record(chosen: dict[str, bool]) -> dict:
        out: dict[str, dict[str, int]] = {}
        for b, lab in labels.items():
            if b in chosen:
                pick = "bioguide" if chosen[b] else "depiction"
                out.setdefault(lab, {}).setdefault(pick, 0)
                out[lab][pick] += 1
        either = [b for b in chosen if b not in labels and b not in stale]
        out.setdefault("either", {})
        for b in either:
            pick = "bioguide" if chosen[b] else "depiction"
            out["either"][pick] = out["either"].get(pick, 0) + 1
        return out

    out = {k: v for k, v in in_use.items() if k in ("detector", "labels")}
    out.update({
        "_source": (
            "Generated by backend/scripts/calibrate_member_photos.py from the face shares the nightly run "
            "stored (MemberPhoto): depiction_ceiling is the lower 97.5% point of the dominant component of a "
            "Gaussian mixture over every measured image's log face share (components by BIC), margin the upper "
            "99.9% point of the dominant component of the log ratio of each member's two shares (the same "
            "picture measured twice). The hand labels (labels, each with the sha256 of the two images judged; "
            "every other member was judged 'either') are the held-out check, never the fit. Face shares "
            "measured with OpenCV's YuNet face detector (app/data/face_detection_yunet_2023mar.onnx)."),
        "_as_of": datetime.date.today().isoformat(),
        "margin": refit["margin"],
        "depiction_ceiling": refit["depiction_ceiling"],
        "fit": refit["fit"],
        "check": {"members": len(rows), "labels_current": len(labels), "label -> chosen": record(cand),
                  "in_use_label -> chosen": record(used), "switched": sum(cand.values()),
                  "in_use_switched": sum(used.values())},
        "labels_stale": sorted(stale),
        "_comparison": comparison,
    })
    pathlib.Path(args.output).write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k not in ("labels", "detector")}, indent=1))


if __name__ == "__main__":
    main()
