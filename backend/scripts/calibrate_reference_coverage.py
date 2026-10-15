"""Regenerate app/data/reference_coverage.json — the share of a chamber a
run must have read before it re-measures a population reference.

Each member pipeline measures its references (Legislative Effectiveness,
Funding Independence, Constituent Alignment) from the members it is about
to score (AGENTS.md §3a). When a step fails part-way, only some members'
inputs are read, and a reference measured on them is measured on a
subset. That subset is not random: a read stops partway through the
roster, which is in Congress.gov's order (2026-10-15: 105 of 433 House
members' bills read, and 133 of the 149 members whose Legislative
Effectiveness rose were Republicans). Below the floor written here, the run
carries the last reference measured on (near-)full coverage instead
(live_references).

How the floor is measured, per chamber and reference, on the current
members' stored inputs (a full read):
  - each member's own noise in the overall score: the standard deviation
    when their sponsored bills are resampled and their party-line breaks
    redrawn (binomial at their own rate) — the sampling noise in the record
    itself — combined with the display's rounding;
  - the error of a reference measured on a share s of the chamber, in units
    of noise (Chamber.error): the 95th-percentile member's change in overall
    score over their own noise, or a party's mean change over the standard
    error of that mean, whichever is larger. For a random subset (the 90th
    percentile over DRAWS draws) and for the first s of the roster in
    Congress.gov's order — the shape a stopped read takes (2026-10-15's 105
    read were exactly the first 105) — the larger of the two;
  - the floor is the smallest share s on the grid from which every larger
    share's error stays under 1.
The party term is what a per-member test misses: a reference shift is
systematic. On 2026-10-15 the 95th-percentile member moved by about half
their own noise, yet the shift ran one way for most of a party, which no
amount of member-level noise averages away.

Run inside the backend container against a populated database (a full,
healthy night), then commit the regenerated file:
    docker compose run --rm --no-deps -v "$(pwd)/backend/scripts:/app/scripts" \\
        -e PYTHONPATH=/app backend python scripts/calibrate_reference_coverage.py
"""

import datetime
import json
import math
import pathlib
import random
import statistics

from app.config_definitions import SCORE_WEIGHTS
from app.database import SessionLocal
from app.models import ApiCache, Representative, Senator
from app.pipeline.analyze.score_calculator import (
    _constituent_alignment_core,
    _funding_independence_core,
    _legislative_effectiveness_core,
    compute_constituent_reference,
    compute_funding_reference,
    constituent_reference_inputs,
    derive_chamber_majority,
)
from app.pipeline.live_references import measure_les_reference, sitting_president_party
from app.services._scorecard_common import build_score_breakdown_entity

OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "reference_coverage.json"

# 10% .. 95% in steps of 5, then each point to 100%: a chamber's members
# rarely all prepare (one failing member is 0.2% of the House), so where a
# floor falls near the top matters.
GRID = [round(0.05 * k, 2) for k in range(2, 20)] + [0.96, 0.97, 0.98, 0.99, 1.0]
DRAWS = 20  # random subsets per share
NOISE_DRAWS = 30  # resamples per member for the noise yardstick
SEED = 7

_WEIGHT = {
    "les_reference": SCORE_WEIGHTS["legislativeEffectiveness"],
    "funding_reference": SCORE_WEIGHTS["fundingIndependence"],
    "constituent_reference": SCORE_WEIGHTS["constituentAlignment"],
}


def _members(db, model, attr: str, list_key: str) -> list[dict]:
    """Serving members as the breakdown API builds them, in the order
    Congress.gov lists them (the order a pipeline read walks)."""
    rows = db.query(model).filter(model.is_current.is_(True)).all()
    members = [build_score_breakdown_entity(r, lobbying_donation_attr=attr) for r in rows]
    cached = db.query(ApiCache.data_json).filter(ApiCache.cache_key == list_key).scalar()
    order = {m.get("bioguideId"): i for i, m in enumerate(json.loads(cached) if cached else [])}
    return sorted(members, key=lambda m: order.get(m["bioguideId"], len(order)))


def _party(m: dict) -> str | None:
    return m["votingRecord"].get("effectiveParty") or m.get("party")


class Chamber:
    def __init__(self, name: str, members: list[dict], db):
        self.name = name
        self.members = members
        self.majority = derive_chamber_majority([_party(m) for m in members], name, sitting_president_party(db))
        self.full = {key: self.reference(key, members) for key in _WEIGHT}

    def reference(self, key: str, members: list[dict]) -> dict | None:
        if key == "les_reference":
            return measure_les_reference(self.name, [(m["sponsoredBills"], _party(m)) for m in members], self.majority)
        if key == "funding_reference":
            ref = compute_funding_reference(
                [m["funding"] for m in members], [m["state"] for m in members], [m.get("party") for m in members],
            )
            # As live_funding_reference does: a stat this subset couldn't
            # measure keeps the full one.
            return None if ref is None else {**(getattr(self, "full", {}).get(key) or {}), **ref}
        return compute_constituent_reference(constituent_reference_inputs(members))

    def score(self, key: str, m: dict, ref: dict, **override) -> float:
        m = {**m, **override}
        if key == "les_reference":
            return _legislative_effectiveness_core(
                m["sponsoredBills"], m.get("leadershipScore"), party=_party(m),
                years_in_office=m.get("yearsInOffice"), attracted_bipartisanship=m.get("attractedBipartisanshipScore"),
                les_reference={self.name: ref}, chamber=self.name, sworn_date=m.get("swornDate"),
            )["score"]
        if key == "funding_reference":
            return _funding_independence_core(
                m["funding"], m["state"], m.get("district"), {self.name: ref}, m.get("party"),
            )["score"]
        return _constituent_alignment_core(
            m["votingRecord"], [], {}, state=m["state"], party=m.get("party", "I"), district=m.get("district"),
            bioguide_id=m.get("bioguideId"), reference={self.name: ref}, name=m.get("name") or "",
            years_in_office=m.get("yearsInOffice") or 0,
        )["score"]

    def error(self, key: str, subset: list[dict]) -> float | None:
        """How far measuring `key` on `subset` instead of the whole chamber
        moves scores, in units of noise — the larger of:
          - the 95th-percentile member's change in overall score over that
            member's own noise (self.sd);
          - each party's mean change over the standard error of that mean
            (its members' noise over the square root of their number): a
            shift shared by a whole party does not average out the way
            noise does, so it is held to the noise of the party's average.
        """
        ref = self.reference(key, subset)
        if ref is None:
            return None  # too few to measure at all: the pipeline keeps the last one anyway
        full = self.full[key]
        deltas = {
            m["bioguideId"]: (self.score(key, m, ref) - self.score(key, m, full)) * _WEIGHT[key]
            for m in self.members
        }
        ratios = sorted(abs(d) / self.sd[b] for b, d in deltas.items())
        member = ratios[min(len(ratios) - 1, math.ceil(0.95 * len(ratios)) - 1)]
        party = 0.0
        for p in ("D", "R"):
            ids = [m["bioguideId"] for m in self.members if _party(m) == p]
            if len(ids) < 2:
                continue
            se = math.sqrt(statistics.fmean(self.sd[b] ** 2 for b in ids) / len(ids))
            party = max(party, abs(statistics.fmean(deltas[b] for b in ids)) / se)
        return max(member, party)

    def noise(self, rng: random.Random) -> None:
        """Each member's own noise in the overall score: the SD from
        resampling their sponsored bills and redrawing their party-line
        breaks, combined with the display's rounding to a whole number
        (uniform, SD 1/sqrt(12)), so a member with no record to resample
        still has the resolution the page shows."""
        les_ref, ca_ref = self.full["les_reference"], self.full["constituent_reference"]
        self.sd = {}
        for m in self.members:
            record = m["votingRecord"].get("partyLineRecord") or {}
            n, k = record.get("votes") or 0, len(record.get("breaks") or [])
            bills = m["sponsoredBills"]
            overall = []
            for _ in range(NOISE_DRAWS):
                les = self.score("les_reference", m, les_ref, sponsoredBills=[rng.choice(bills) for _ in bills])
                if n:
                    p = rng.betavariate(k + 0.5, n - k + 0.5)
                    kb = sum(1 for _ in range(n) if rng.random() < p)
                    vr = {**m["votingRecord"], "partyLineRecord": {**record, "breaks": [{}] * kb}}
                    ca = self.score("constituent_reference", m, ca_ref, votingRecord=vr)
                else:
                    ca = self.score("constituent_reference", m, ca_ref)
                overall.append(_WEIGHT["les_reference"] * les + _WEIGHT["constituent_reference"] * ca)
            self.sd[m["bioguideId"]] = math.sqrt(statistics.pvariance(overall) + 1 / 12)


def calibrate(chamber: Chamber, rng: random.Random) -> dict:
    chamber.noise(rng)
    n = len(chamber.members)
    out = {
        "members": n, "median_member_noise": round(statistics.median(chamber.sd.values()), 3),
        "floors": {}, "curves": {},
    }
    for key in _WEIGHT:
        curve = {}
        for share in GRID:
            k = max(1, round(share * n))
            if k >= n:
                curve[share] = 0.0
                continue
            draws = [chamber.error(key, rng.sample(chamber.members, k)) for _ in range(DRAWS)]
            draws = sorted(d for d in draws if d is not None)
            prefix = chamber.error(key, chamber.members[:k])
            random_err = draws[min(len(draws) - 1, math.ceil(0.9 * len(draws)) - 1)] if draws else math.inf
            curve[share] = round(max(random_err, prefix if prefix is not None else math.inf), 3)
        floor = 1.0
        for share in sorted(GRID, reverse=True):
            if curve[share] >= 1.0:  # the 95th-percentile member moved by more than their own noise
                break
            floor = share
        out["floors"][key] = floor
        out["curves"][key] = {str(s): e for s, e in curve.items()}
        print(f"{chamber.name} {key}: floor {floor:.0%}; {curve}", flush=True)
    return out


def main() -> None:
    rng = random.Random(SEED)
    db = SessionLocal()
    try:
        chambers = {
            "senate": Chamber("senate", _members(db, Senator, "donation_to_senator", "senators-list"), db),
            "house": Chamber("house", _members(db, Representative, "donation_to_representative",
                                               "representatives-list"), db),
        }
        result = {name: calibrate(c, rng) for name, c in chambers.items()}
    finally:
        db.close()
    OUT.write_text(json.dumps({
        "_as_of": datetime.date.today().isoformat(),
        "_source": (
            "backend/scripts/calibrate_reference_coverage.py over the serving members' stored inputs. "
            "Per chamber and reference: the smallest share of the chamber whose reference, measured on "
            "a random subset (90th percentile of draws) or the roster's first members in Congress.gov's "
            "order, moves the 95th-percentile member's overall score by less than that member's own "
            "noise (bills resampled, party-line breaks redrawn, display rounding). Below it a run carries the last "
            "reference measured on full coverage (live_references). Regenerate after a scoring change."
        ),
        **result,
    }, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
