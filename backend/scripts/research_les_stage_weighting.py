"""Does Legislative Effectiveness's stage credit track Volden & Wiseman's LES?

Legislative Effectiveness's main component is described as following Volden &
Wiseman (2014). V&W score each member as the sum, over five stages (bill
introduced, action in committee, action beyond committee, passed the chamber,
became law), of that member's significance-weighted bills at the stage divided
by the chamber's total at the stage, times N/5. Dividing by each stage's total
is what makes a law count for far more than an introduction: few bills reach
the later stages.

Civitas credits significance weight x stages reached instead, so an enacted
bill is worth four introductions. It also sets significance by bill TYPE
(every H.R./S. bill 5x), where V&W set it by CONTENT: commemorative bills
(post-office namings, commemorations) 1x, substantive 5x, substantive and
significant 10x.

This measures how much those departures cost, using V&W's own published
per-member counts from the Center for Effective Lawmaking (thelawmakers.org):

  1. Rebuild LES 1.0 from the counts and confirm it reproduces the published
     column. If it does not, the comparisons below mean nothing.
  2. Score every member with Civitas's current credit, and with alternatives
     Civitas could compute, and report the Spearman rank correlation with
     the published LES within each congress and majority/minority status
     (Civitas compares each member only with their own status).
  3. Report what each variant rewards: its correlation with sheer bill
     introductions versus with laws.

Stage mapping. Civitas has five stages since v6.17, V&W's own:
introduced/referred (1), a hearing or markup (2), reported out, discharged
or taken up on the floor (3, V&W's "action beyond committee"), passed the
chamber (4), enacted (5). Before v6.17 action beyond committee folded into
stage 2; the "stage_normalized" variants below are that four-stage scheme.

Usage:
    python backend/scripts/research_les_stage_weighting.py [--first 110] [--last 118] [--cache DIR]

Downloads the two CEL spreadsheets (House .xlsx, Senate .xls; dated URLs, so
effectively pinned) into --cache on first run. Needs pandas, openpyxl and xlrd, which are not runtime
dependencies of the app.
"""

from __future__ import annotations

import argparse
import pathlib
import statistics
import sys
import urllib.request

import pandas as pd

HOUSE_URL = (
    "https://thelawmakers.org/wp-content/uploads/2025/06/"
    "CELHouse93to118-REVISED-06.26.2025.xlsx"
)
SENATE_URL = "https://thelawmakers.org/wp-content/uploads/2025/03/CELSenate93to118.xls"

STAGES = ("BILL", "AIC", "ABC", "PASS", "LAW")
TIERS = ("C", "S", "SS")
VW_TIER_WEIGHT = {"C": 1.0, "S": 5.0, "SS": 10.0}
# Civitas stage for each V&W stage a bill reaches. ABC folds into stage 2
# (it is committee action that went further) so it adds nothing on its own.
CIVITAS_STAGES = ("BILL", "AIC", "PASS", "LAW")


def _fetch(url: str, cache: pathlib.Path) -> pathlib.Path:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / url.rsplit("/", 1)[1]
    if not path.exists():
        print(f"downloading {url}", file=sys.stderr)
        urllib.request.urlretrieve(url, path)
    return path


def _load(chamber: str, cache: pathlib.Path) -> pd.DataFrame:
    df = pd.read_excel(_fetch(HOUSE_URL if chamber == "house" else SENATE_URL, cache))
    cols = list(df.columns)
    # The 15 standalone-bill counts (commemorative, substantive, S&S; five
    # stages each) are the first run of "Number of ..." columns, followed by
    # LES 1.0. Located by position from the first commemorative column, since
    # the two files word their headers differently.
    start = next(i for i, c in enumerate(cols) if str(c).startswith("Number of commemorative bills sponsored"))
    counts = cols[start:start + 15]
    les = cols[start + 15]
    assert "LES" in str(les), f"{chamber}: expected LES 1.0 after the counts, got {les!r}"
    congress = next(c for c in cols if str(c).lower().startswith("congress number"))
    majority = next(c for c in cols if "majority party member" in str(c).lower()
                    or "in majority party" in str(c).lower())
    out = pd.DataFrame({
        "congress": df[congress],
        "majority": df[majority],
        "les": df[les],
    })
    for k, (tier, stage) in enumerate((t, s) for t in TIERS for s in STAGES):
        out[f"{tier}_{stage}"] = df[counts[k]].fillna(0).astype(float)
    return out.dropna(subset=["congress", "les", "majority"])


def vw_les(g: pd.DataFrame, tier_weight: dict[str, float], stages=STAGES) -> pd.Series:
    """V&W's formula over the given stages: per stage, the member's weighted
    count over the chamber's weighted total, summed, times N/len(stages)."""
    total = pd.Series(0.0, index=g.index)
    for stage in stages:
        member = sum(tier_weight[t] * g[f"{t}_{stage}"] for t in TIERS)
        denom = member.sum()
        if denom > 0:
            total += member / denom
    return total * len(g) / len(stages)


def civitas_credit(g: pd.DataFrame) -> pd.Series:
    """Current Civitas credit: 5x every H.R./S. bill (type-based significance)
    times stages reached. Summing a stage's count across stages is the same as
    summing each bill's stage number."""
    return sum(5.0 * g[f"{t}_{s}"] for t in TIERS for s in CIVITAS_STAGES)


def introductions(g: pd.DataFrame) -> pd.Series:
    return sum(g[f"{t}_BILL"] for t in TIERS)


def laws(g: pd.DataFrame) -> pd.Series:
    return sum(g[f"{t}_LAW"] for t in TIERS)


def spearman(a: pd.Series, b: pd.Series) -> float:
    return float(a.rank().corr(b.rank()))


VARIANTS = {
    # What Civitas computes today.
    "civitas_current": lambda g: civitas_credit(g),
    # V&W's stage normalization over Civitas's four observable stages, one
    # significance tier (what Civitas can compute from bill type alone).
    "stage_normalized": lambda g: vw_les(g, {"C": 1.0, "S": 1.0, "SS": 1.0}, CIVITAS_STAGES),
    # As above, plus commemorative bills at 1/5 — the upper bound of what a
    # commemorative detector would add. S&S stays at S weight: Civitas has
    # no source for CQ Almanac coverage.
    "stage_normalized_commem": lambda g: vw_les(g, {"C": 1.0, "S": 5.0, "SS": 5.0}, CIVITAS_STAGES),
    # V&W's fifth stage, "action beyond committee" (reported out,
    # discharged, or taken up on the floor), credited on its own — what
    # Civitas can observe once a reported or floor-debated bill is its own
    # stage instead of folding into committee action.
    "five_stages": lambda g: vw_les(g, {"C": 1.0, "S": 1.0, "SS": 1.0}, STAGES),
    "five_stages_commem": lambda g: vw_les(g, {"C": 1.0, "S": 5.0, "SS": 5.0}, STAGES),
}


def members_from_counts(g: pd.DataFrame, bill_type: str) -> list[tuple[list[dict], str]]:
    """Each member's bills rebuilt from their stage counts, as the pipeline
    sees them (stage names from bill_stage.py), with majority members given
    party "R" and minority "D" so the scorer's status logic applies. Counts
    are cumulative, so a bill reaching a stage is also counted at every
    earlier one; clipping keeps them nested where the data is not."""
    members = []
    for _, row in g.iterrows():
        bill = sum(row[f"{t}_BILL"] for t in TIERS)
        aic = min(sum(row[f"{t}_AIC"] for t in TIERS), bill)
        abc = min(sum(row[f"{t}_ABC"] for t in TIERS), aic)
        pas = min(sum(row[f"{t}_PASS"] for t in TIERS), abc)
        law = min(sum(row[f"{t}_LAW"] for t in TIERS), pas)
        bills = []
        # REPORTED stands for V&W's action beyond committee (v6.17).
        for stage, n in (("ENACTED", law), ("PASSED_CHAMBER", pas - law), ("REPORTED", abc - pas),
                         ("IN_COMMITTEE", aic - abc), ("INTRODUCED", bill - aic)):
            bills += [{"billType": bill_type, "congress": int(row["congress"]), "stage": stage}] * int(n)
        members.append((bills, "R" if row["majority"] == 1 else "D"))
    return members


def shipped_scorer(df: pd.DataFrame, chamber: str) -> None:
    """Run the pipeline's own compute_les_reference and _les_component_score
    over each congress and report rank agreement with the published LES and
    the gap between majority and minority members' median scores."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from app.pipeline.analyze.score_calculator import _les_component_score, compute_les_reference

    bill_type = "hr" if chamber == "house" else "s"
    # v6.13's four stages: action beyond committee folded into committee.
    stage_of = {"INTRODUCED": 1, "IN_COMMITTEE": 2, "REPORTED": 2, "PASSED_CHAMBER": 3, "ENACTED": 4}

    def v613_scores(members) -> list[float]:
        # v6.13, reproduced: credit = 5 x stages reached per bill; each
        # status's median is the bar; saturation 1.5 population stdevs; the
        # result shrunk toward 50 by min(bills / 10, 1).
        credit = [sum(5.0 * stage_of[b["stage"]] for b in bills) for bills, _ in members]
        scored = [c for c, (bills, _) in zip(credit, members) if bills]
        sat = 1.5 * statistics.pstdev(scored)
        med = {p: statistics.median([c for c, (bills, q) in zip(credit, members) if bills and q == p])
               for p in ("R", "D")}
        out = []
        for c, (bills, p) in zip(credit, members):
            if not bills:
                out.append(50.0)
                continue
            conf = min(len(bills) / 10, 1.0)
            raw = 50.0 + 50.0 * max(-1.0, min((c - med[p]) / sat, 1.0))
            out.append(raw * conf + 50.0 * (1 - conf))
        return out

    def v614_scores(members, congress) -> list[float]:
        ref = compute_les_reference(members, int(congress), "R")
        return [_les_component_score(b, p, 4.0, {chamber: ref})[0] for b, p in members]

    for label, fn in (("v6.13", lambda m, c: v613_scores(m)), ("v6.17", v614_scores)):
        rhos, gaps = [], []
        for congress, g in df.groupby("congress"):
            members = members_from_counts(g, bill_type)
            scores = pd.Series(fn(members, congress), index=g.index)
            for _, sub in g.groupby("majority"):
                if len(sub) >= 15:
                    rhos.append(spearman(scores[sub.index], sub["les"]))
            gaps.append(scores[g["majority"] == 1].median() - scores[g["majority"] == 0].median())
        print(f"shipped scorer {label}: Spearman vs LES median {statistics.median(rhos):.3f} "
              f"(min {min(rhos):.3f}); majority-minus-minority median score gap "
              f"median {statistics.median(gaps):+.1f} (range {min(gaps):+.1f} to {max(gaps):+.1f})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=110)
    ap.add_argument("--last", type=int, default=118)
    ap.add_argument("--cache", default=".research-cache/les-stage-weighting", type=pathlib.Path)
    args = ap.parse_args()

    for chamber in ("house", "senate"):
        df = _load(chamber, args.cache)
        df = df[(df.congress >= args.first) & (df.congress <= args.last)]

        # 1. Reproduce LES 1.0.
        repro = []
        for _, g in df.groupby("congress"):
            repro.append(float(vw_les(g, VW_TIER_WEIGHT).corr(g["les"])))
        print(f"\n== {chamber}: congresses {args.first}-{args.last}, {len(df)} member-congresses")
        print(f"rebuilt LES 1.0 vs published: min Pearson r = {min(repro):.4f} "
              f"(mean {statistics.mean(repro):.4f})")

        # 2. Rank agreement with published LES, within congress x status.
        print(f"{'variant':<26}{'Spearman vs LES (median)':>26}{'min':>8}"
              f"{'rho w/ bills introduced':>26}{'rho w/ laws':>13}")
        for name, fn in VARIANTS.items():
            rhos, intro_r, law_r = [], [], []
            for _, g in df.groupby(["congress", "majority"]):
                if len(g) < 15:
                    continue
                v = fn(g)
                rhos.append(spearman(v, g["les"]))
                intro_r.append(spearman(v, introductions(g)))
                law_r.append(spearman(v, laws(g)))
            print(f"{name:<26}{statistics.median(rhos):>26.3f}{min(rhos):>8.3f}"
                  f"{statistics.median(intro_r):>26.3f}{statistics.median(law_r):>13.3f}")
        ref_intro = [spearman(g["les"], introductions(g)) for _, g in df.groupby(["congress", "majority"]) if len(g) >= 15]
        ref_law = [spearman(g["les"], laws(g)) for _, g in df.groupby(["congress", "majority"]) if len(g) >= 15]
        print(f"{'(published LES itself)':<26}{'':>26}{'':>8}"
              f"{statistics.median(ref_intro):>26.3f}{statistics.median(ref_law):>13.3f}")

        # 3. What one enacted bill is worth, in introductions, under each.
        worth = []
        for _, g in df.groupby("congress"):
            n_bill = introductions(g).sum()
            n_law = laws(g).sum()
            aic = sum(g[f"{t}_AIC"] for t in TIERS).sum()
            pas = sum(g[f"{t}_PASS"] for t in TIERS).sum()
            # A law reaches every stage; an introduction only the first.
            law_value = 1 / n_bill + 1 / aic + 1 / pas + 1 / n_law
            worth.append(law_value / (1 / n_bill))
        print(f"one enacted bill = 4 introductions (Civitas) vs a median "
              f"{statistics.median(worth):.0f} introductions (stage-normalized)")
        shipped_scorer(df, chamber)


if __name__ == "__main__":
    main()
