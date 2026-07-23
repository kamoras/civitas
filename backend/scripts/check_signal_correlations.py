"""Standing post-run check for v6.11's disclosed signal-overlap risks.

score_calculator.py's v6.11 changelog names two component pairs that are
measured from related data and must be re-checked against the live
population after every meaningful pipeline run — the same discipline that
caught the v6.8 double-count (ideology_score extremity vs.
bipartisanship_score, r=-0.76) and the v6.5 funding pair (r=0.72), both
of which shipped as "distinct signals" and weren't:

  1. Constituent Alignment: seat-relative vote alignment vs. position
     congruence — different constructs (a crossing RATE vs. a spatial
     POSITION), but both roll-call-derived. A high |r| here means the
     30% congruence weight is partially re-measuring the 70% vote
     component instead of adding information.
  2. Legislative Effectiveness: legislative leadership (cosponsorship
     PageRank) vs. bipartisan coalition attraction (receive-only
     cross-party cosponsor share) — different measures of the SAME
     cosponsorship network, capped at a combined 40% weight for exactly
     that reason.

Also reports the Constituent Alignment population stdev against
ground_truth's floor (breadth's spread contribution left the dimension in
v6.11; congruence's arrives only once member_ideal_points.json exists —
the transition window is where a collapse-to-neutral would show up).

Pulls component-level scores from the public score-breakdown API — same
reasoning audit_pac_ratio.py / fetch_district_pvi.py give for reading the
live deployment rather than a local DB.

Reporting thresholds (diagnostic labels only, not scoring constants):
|r| >= 0.60 is flagged ACTION — the magnitude class of the two audits
above, both of which led to structural fixes; 0.40-0.60 is WATCH.

Run from the repo (network required):
    python3 backend/scripts/check_signal_correlations.py
"""

import json
import statistics
import sys
import urllib.request

API_BASE = "https://civitas-research.org/api"
UA = {"User-Agent": "CivitasCivicPlatform/1.0 (signal-correlation audit; contact: mack.ryanm@gmail.com)"}

ACTION_R = 0.60
WATCH_R = 0.40


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Plain Pearson r; None when either side is degenerate (no spread)."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / (sxx * syy) ** 0.5


def component_scores(breakdown: dict, dimension: str) -> dict[str, float]:
    """{component label: score} for one dimension of a score-breakdown
    response; tolerates missing dimensions/components (skipped members
    simply don't contribute a pair)."""
    dim = breakdown.get(dimension) or {}
    return {
        c.get("label", ""): c.get("score")
        for c in (dim.get("components") or [])
        if c.get("score") is not None
    }


def collect_pairs(entries: list[dict]) -> dict:
    """Reduce per-member breakdowns to the two flagged pairs plus the CA
    dimension scores. Pure function of already-fetched data, so tests can
    drive it with fixtures."""
    ca_pairs: list[tuple[float, float]] = []
    le_pairs: list[tuple[float, float]] = []
    iv_scores: list[float] = []
    for b in entries:
        ca = component_scores(b, "independentVoting")
        le = component_scores(b, "legislativeEffectiveness")
        iv = (b.get("independentVoting") or {}).get("score")
        if iv is not None:
            iv_scores.append(float(iv))
        if "Seat-relative vote alignment" in ca and "Position congruence" in ca:
            ca_pairs.append((ca["Seat-relative vote alignment"], ca["Position congruence"]))
        if "Legislative leadership" in le and "Bipartisan coalition attraction" in le:
            le_pairs.append((le["Legislative leadership"], le["Bipartisan coalition attraction"]))
    return {"ca_pairs": ca_pairs, "le_pairs": le_pairs, "iv_scores": iv_scores}


def verdict(r: float | None, n: int, label: str) -> str:
    if r is None:
        return f"{label}: no signal (n={n} pairs — component inactive or degenerate)"
    band = "ACTION" if abs(r) >= ACTION_R else "WATCH" if abs(r) >= WATCH_R else "ok"
    return f"{label}: r={r:+.3f} (n={n}) [{band}]"


def fetch_breakdowns(branch: str) -> list[dict]:
    kind = "senators" if branch == "senate" else "representatives"
    listing = _fetch_json(f"{API_BASE}/politicians?branch={branch}")
    out = []
    for d in listing:
        if not d.get("hasScorecard"):
            continue
        try:
            out.append(_fetch_json(f"{API_BASE}/{kind}/{d['id']}/score-breakdown"))
        except Exception as e:  # a single missing member shouldn't kill the audit
            print(f"  skip {d.get('id')}: {e}")
    return out


def main() -> int:
    any_action = False
    for branch in ("senate", "house"):
        entries = fetch_breakdowns(branch)
        got = collect_pairs(entries)
        r_ca = pearson([a for a, _ in got["ca_pairs"]], [b for _, b in got["ca_pairs"]])
        r_le = pearson([a for a, _ in got["le_pairs"]], [b for _, b in got["le_pairs"]])
        print(f"\n{branch} ({len(entries)} members with breakdowns):")
        print(" ", verdict(r_ca, len(got["ca_pairs"]), "CA vote-alignment vs position-congruence"))
        print(" ", verdict(r_le, len(got["le_pairs"]), "LE leadership vs coalition-attraction"))
        if got["iv_scores"]:
            stdev = statistics.pstdev(got["iv_scores"])
            floor_note = ""
            try:  # single source of truth when run from the repo; plain report otherwise
                sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
                from app.pipeline.analyze.ground_truth import MIN_STDEV
                floor = MIN_STDEV.get("score_independent_voting")
                if floor is not None:
                    floor_note = f" vs floor {floor} [{'ok' if stdev >= floor else 'ACTION'}]"
                    any_action |= stdev < floor
            except Exception:
                floor_note = " (compare against ground_truth.MIN_STDEV['score_independent_voting'])"
            print(f"  CA population stdev: {stdev:.2f}{floor_note}")
        for r, n in ((r_ca, len(got["ca_pairs"])), (r_le, len(got["le_pairs"]))):
            if r is not None and abs(r) >= ACTION_R:
                any_action = True
    if any_action:
        print("\nACTION items above: see score_calculator.py's v6.8/v6.11 notes "
              "for the established fix pattern (reduce the redundant weight or "
              "restructure, don't recalibrate around it).")
    return 1 if any_action else 0


if __name__ == "__main__":
    sys.exit(main())
