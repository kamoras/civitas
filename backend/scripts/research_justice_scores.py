"""Reproduce the evidence behind the justice scorecard's v6.13 measures.

Every number in docs/research/justice-scores.md comes from this script.
It runs this repo's own analyze_justice_votes over:

  1. The Rehnquist Court vote matrix, 1994-2004 terms (485 formally decided,
     non-unanimous cases; Spaeth's Supreme Court Database, as packaged in the
     R package MCMCpack, fetched at a pinned commit). A one-dimensional ideal
     point per justice-term (first principal component of that term's votes)
     gives each justice's distance from the term's median justice.
  2. A simulated 6-3 Court (1-D spatial voting, appointing party assigned at
     random), with party carrying no information and with symmetric
     partisanship, to measure what each score does to the smaller bloc.

Research-only dependencies, not in requirements.txt:
    pip install pandas scipy rdata
Run from backend/:
    PYTHONPATH=. python scripts/research_justice_scores.py [--cache DIR]
"""

import argparse
import pathlib
import urllib.request
import warnings

import numpy as np
import pandas as pd
import rdata
from scipy import stats

from app.pipeline.analyze.justice_analyzer import analyze_justice_votes

warnings.filterwarnings("ignore")

MATRIX_URL = ("https://raw.githubusercontent.com/cran/MCMCpack/"
              "2b02c1d57d83f372fa6b3bc5522e27701690d0d9/data/Rehnquist.rda")
JUSTICES = ["Rehnquist", "Stevens", "O.Connor", "Scalia", "Kennedy", "Souter", "Thomas", "Ginsburg", "Breyer"]
APPOINTING_PARTY = {j: "R" for j in JUSTICES} | {"Ginsburg": "D", "Breyer": "D"}
# The pre-v6.13 measures, recomputed here since the analyzer no longer
# produces them: dissent rate (what "restraint" was a curve of) and
# agreement with opposing-bloc justices ("bipartisan agreement").


def case_records(case_id, row, justices):
    lib = int(np.nansum(row.values))
    con = int(row.notna().sum()) - lib
    maj = 1 if lib > con else 0
    return [dict(case_id=case_id, justice_id=j, vote="majority" if row[j] == maj else "minority",
                 is_unanimous=min(lib, con) == 0, majority_votes=max(lib, con),
                 minority_votes=min(lib, con), opinion_type="", is_close=min(lib, con) == 4)
            for j in justices if not pd.isna(row[j])]


def bipartisan_agreement(jid, per, all_case, party_map):
    opp = {o for o, p in party_map.items() if p != party_map[jid]}
    agree = total = 0
    for v in per[jid]:
        for o in all_case[v["case_id"]]:
            if o["justice_id"] in opp:
                total += 1
                agree += o["vote"] == v["vote"]
    return 100 * agree / total if total else np.nan


def rehnquist_court(cache: pathlib.Path):
    path = cache / "Rehnquist.rda"
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MATRIX_URL, path)
    data = rdata.conversion.convert(rdata.parser.parse_file(path))["Rehnquist"]
    rows = []
    for term, g in data.groupby("term"):
        votes = g[JUSTICES]
        X = votes.T.values.astype(float)
        X = np.where(np.isnan(X), np.nanmean(X, 0), X)
        u, s, _ = np.linalg.svd(X - X.mean(0), full_matrices=False)
        x = u[:, 0] * s[0]
        if np.corrcoef(x, [1 - np.nanmean(votes[j]) for j in JUSTICES])[0, 1] < 0:
            x = -x  # + = conservative
        all_case, per = {}, {j: [] for j in JUSTICES}
        for cid, row in votes.iterrows():
            recs = case_records(str(cid), row, JUSTICES)
            all_case[str(cid)] = recs
            for r in recs:
                per[r["justice_id"]].append(r)
        for i, j in enumerate(JUSTICES):
            a = analyze_justice_votes(j, APPOINTING_PARTY[j], per[j], all_case, APPOINTING_PARTY)
            rows.append(dict(term=term, justice=j, x=x[i], dist_median=abs(x[i] - np.median(x)),
                             dissent=a["dissent_pct"], consistency=a["score_consistency"],
                             independence=a["score_independence"],
                             bipartisan=bipartisan_agreement(j, per, all_case, APPOINTING_PARTY)))
    d = pd.DataFrame(rows)
    print(f"== Rehnquist Court, {len(data)} cases, {len(d)} justice-terms ==")
    means = d.groupby("justice")[["x", "dissent", "consistency", "independence", "bipartisan"]].mean().sort_values("x")
    means.insert(0, "appointed_by", [APPOINTING_PARTY[j] for j in means.index])
    print(means.round(1).to_string())
    print("\nSpearman correlations across justice-terms:")
    print(d[["consistency", "independence", "bipartisan", "dissent", "dist_median"]].corr("spearman").round(2).to_string())
    rho, p = stats.spearmanr(d.dist_median, d.dissent)
    print(f"\ndissent rate vs distance from the term's median justice: rho={rho:.2f} (p={p:.1e})")
    median = d.loc[d.groupby("term").dist_median.idxmin()]
    print(f"median justice's dissent rate: {median.dissent.mean():.1f}% vs {d.drop(median.index).dissent.mean():.1f}% for the rest")


def restraint_curve(dissent_rate):
    """The removed pre-v6.13 dissent curve (authored-dissent penalty omitted:
    the simulation has no opinion authorship)."""
    if dissent_rate < 0.02:
        return 80.0
    if dissent_rate <= 0.08:
        return 80.0 + (dissent_rate - 0.02) / 0.06 * 20.0
    if dissent_rate <= 0.15:
        return 100.0
    if dissent_rate <= 0.25:
        return 100.0 - (dissent_rate - 0.15) / 0.10 * 30.0
    return max(0.0, 70.0 - (dissent_rate - 0.25) / 0.35 * 70.0)


def simulated_court(n_sims=300, n_cases=60, seed=0):
    rng = np.random.default_rng(seed)
    print("\n== Simulated 6-3 Court: mean score, 6-member vs 3-member bloc ==")
    for shift, label in ((0.0, "party carries no information"), (0.75, "symmetric partisanship")):
        scores = {k: {"R": [], "D": []} for k in ("consistency", "independence", "bipartisan", "restraint")}
        for _ in range(n_sims):
            party = np.array(["R"] * 6 + ["D"] * 3)
            rng.shuffle(party)
            x = rng.normal(0, 1, 9) + np.where(party == "R", shift, -shift)
            ids = [f"j{i}" for i in range(9)]
            party_map = dict(zip(ids, party))
            votes = pd.DataFrame(
                [(rng.random(9) < 1 / (1 + np.exp(-2 * (x - rng.normal(0, 1))))).astype(float) for _ in range(n_cases)],
                columns=ids,
            )
            all_case, per = {}, {i: [] for i in ids}
            for cid, row in votes.iterrows():
                recs = case_records(str(cid), row, ids)
                all_case[str(cid)] = recs
                for r in recs:
                    per[r["justice_id"]].append(r)
            for i in ids:
                a = analyze_justice_votes(i, party_map[i], per[i], all_case, party_map)
                bloc = party_map[i]
                scores["consistency"][bloc].append(a["score_consistency"])
                scores["independence"][bloc].append(a["score_independence"])
                scores["bipartisan"][bloc].append(bipartisan_agreement(i, per, all_case, party_map))
                scores["restraint"][bloc].append(restraint_curve(a["dissent_pct"] / 100))
        print(f"{label}:")
        for k, v in scores.items():
            r, d = np.nanmean(v["R"]), np.nanmean(v["D"])
            print(f"  {k:13s} {r:6.1f} vs {d:6.1f}   gap {d - r:+6.1f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", default=".research-cache/justices", type=pathlib.Path)
    rehnquist_court(ap.parse_args().cache)
    simulated_court()


if __name__ == "__main__":
    main()
