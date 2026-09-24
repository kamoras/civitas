"""Reproduce the evidence behind Funding Independence's v6.13 components.

Every number in docs/research/funding-independence.md comes from this
script. It uses FEC's own bulk data for incumbents in the 2020, 2022 and
2024 elections who raised more than $100K: the candidate summary files
(contribution sources), the independent-expenditure files (Schedule E
spending supporting each candidate) and the committee master files (who
each spender is). Seat lean comes from this repo's district_pvi.json /
state_pvi.json. Shares are taken over contributions plus candidate loans,
the base the score uses (normalize_finance.summarize_election_totals).

Questions it answers:
  1. Does outside spending measure dependency, or race competitiveness?
  2. How much of "source breadth" is the small-donor share again?
  3. How many incumbents does breadth's treatment of self-funding touch?

Research-only dependencies, not in requirements.txt:
    pip install pandas scipy
Run:
    python backend/scripts/audit_funding_components.py [--cache DIR]
"""

import argparse
import json
import pathlib
import urllib.request
import zipfile

import numpy as np
import pandas as pd
from scipy import stats

BULK = "https://cg-519a459a-0ea3-42c2-b7bc-fa1143481f74.s3-us-gov-west-1.amazonaws.com/bulk-downloads"
DATA = pathlib.Path(__file__).resolve().parent.parent / "app" / "data"
YEARS = (2020, 2022, 2024)
CM_COLS = ("CMTE_ID CMTE_NM TRES_NM ST1 ST2 CITY ST ZIP CMTE_DSGN CMTE_TP "
           "CMTE_PTY_AFFILIATION FREQ ORG_TP CONNECTED_ORG_NM CAND_ID").split()
SPENDER_KIND = {"X": "party committee", "Y": "party committee", "Z": "party committee",
                "O": "super PAC", "U": "super PAC", "V": "hybrid PAC", "W": "hybrid PAC",
                "Q": "PAC", "N": "PAC"}


def fetch(cache: pathlib.Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    for y in YEARS:
        for name in (f"candidate_summary_{y}.csv", f"independent_expenditure_{y}.csv", f"cm{y % 100:02d}.zip"):
            path = cache / name
            if not path.exists():
                print(f"fetching {name}")
                urllib.request.urlretrieve(f"{BULK}/{y}/{name}", path)
        txt = cache / f"cm{y % 100:02d}.txt"
        if not txt.exists():
            with zipfile.ZipFile(cache / f"cm{y % 100:02d}.zip") as z:
                txt.write_bytes(z.read("cm.txt"))


def load(cache: pathlib.Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    districts = json.loads((DATA / "district_pvi.json").read_text())["districts"]
    states = json.loads((DATA / "state_pvi.json").read_text())["states"]
    panels, spending = [], []
    for y in YEARS:
        cs = pd.read_csv(cache / f"candidate_summary_{y}.csv", low_memory=False)
        cs = cs[cs.Cand_Incumbent_Challenger_Open_Seat.isin(["INCUMBENT", "I"]) & cs.Cand_Office.isin(["H", "S"])]

        def num(col):
            return pd.to_numeric(cs[col].astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce").fillna(0)
        base = num("Total_Contribution") + num("Cand_Loan")
        df = pd.DataFrame(dict(
            year=y, cand=cs.Cand_Id, office=cs.Cand_Office, st=cs.Cand_Office_St,
            dist=pd.to_numeric(cs.Cand_Office_Dist, errors="coerce").fillna(0).astype(int),
            party=cs.Cand_Party_Affiliation.str[:3], base=base,
            small=num("Individual_Unitemized_Contribution") / base,
            item=num("Individual_Itemized_Contribution") / base,
            pac=num("Other_Committee_Contribution") / base,
            pty=num("Party_Committee_Contribution") / base,
            self_=(num("Cand_Contribution") + num("Cand_Loan")) / base,
        ))
        panels.append(df[(df.base > 100_000) & df.party.isin(["DEM", "REP"])])

        ie = pd.read_csv(cache / f"independent_expenditure_{y}.csv", low_memory=False, dtype=str)
        ie["amt"] = pd.to_numeric(ie.exp_amo, errors="coerce").fillna(0)
        ie = ie.drop_duplicates(["spe_id", "tran_id"], keep="last")  # amended filings repeat a transaction
        cm = pd.read_csv(cache / f"cm{y % 100:02d}.txt", sep="|", header=None, names=CM_COLS,
                         dtype=str, usecols=range(15))
        ie = ie.merge(cm[["CMTE_ID", "CMTE_TP"]].rename(columns={"CMTE_ID": "spe_id"}), on="spe_id", how="left")
        ie["year"] = y
        spending.append(ie[ie.sup_opp == "S"][["year", "cand_id", "spe_nam", "CMTE_TP", "amt"]])
    panel, ie = pd.concat(panels), pd.concat(spending)
    ie["kind"] = ie.CMTE_TP.map(SPENDER_KIND).fillna("other (non-committee groups, individuals)")
    ie = ie[ie.cand_id.isin(set(panel.cand))]
    support = ie.groupby(["year", "cand_id"]).amt.sum().rename("ie_for").reset_index()
    panel = panel.merge(support.rename(columns={"cand_id": "cand"}), on=["year", "cand"], how="left")
    panel["ie_for"] = panel.ie_for.fillna(0)
    # Exactly the term score_calculator added to the PAC share before v6.13.
    panel["os_term"] = panel.ie_for / (panel.base + panel.ie_for) * 0.5
    panel["pvi"] = [
        states.get(r.st) if r.office == "S" else districts.get(f"{r.st}-{r.dist}", districts.get(f"{r.st}-0"))
        for r in panel.itertuples()
    ]
    panel["pvi"] = pd.to_numeric(panel.pvi, errors="coerce")
    panel["abs_pvi"] = panel.pvi.abs()
    return panel, ie


def outside_spending(panel, ie):
    t = ie.groupby("kind").amt.sum().sort_values(ascending=False)
    print(f"== Independent expenditures supporting incumbents, by spender (${t.sum() / 1e6:,.0f}M) ==")
    print((t / t.sum()).round(3).to_string())
    top = ie.groupby("spe_nam").amt.sum().sort_values(ascending=False).head(8)
    print("largest spenders (share of total):\n" + (top / t.sum()).round(3).to_string())
    # House districts are current lines, so only the 2024 House is matched to them.
    for label, d in (("House 2024", panel[(panel.office == "H") & (panel.year == 2024)]),
                     ("Senate 2020-24", panel[panel.office == "S"])):
        d = d[d.pvi.notna()]
        median_pac = d.pac.median()
        print(f"\n== {label}: n={len(d)} incumbents, median PAC share {median_pac:.3f} ==")
        print(f"  any supporting IE: {(d.ie_for > 0).mean():.0%}; outside-spending term "
              f"median {d.os_term.median():.3f}, p90 {d.os_term.quantile(.9):.3f}")
        for other, name in ((d.abs_pvi, "|PVI|"), (d.pac, "PAC share")):
            rho, p = stats.spearmanr(other, d.os_term)
            print(f"  Spearman({name}, outside-spending term) = {rho:.2f} (p={p:.1e})")
        d = d.assign(seat=pd.cut(d.abs_pvi, [-1, 3, 8, 15, 100], labels=["|PVI|<=3", "4-8", "9-15", ">15"]))
        g = d.groupby("seat", observed=True).agg(n=("os_term", "size"), mean_os_term=("os_term", "mean"),
                                                  mean_pac_share=("pac", "mean"))
        # Points the term took off the PAC component (median member scores 50).
        g["pac_component_points"] = g.mean_os_term * (0.5 / median_pac) * 100
        print(g.round(3).to_string())


def breadth(panel):
    print("\n== Source breadth vs small-donor share ==")
    print("(industryBreakdown covers itemized individual + PAC money; c = the share of it")
    print(" the classifier assigns an industry, unknown here, so three values are shown)")
    for c in (0.5, 0.8, 1.0):
        classified = c * (panel.item + panel.pac)
        unclassified = (1 - c) * (panel.item + panel.pac)
        other = (1 - panel.small - classified - unclassified).clip(lower=0)
        b = (panel.small + 0.6 * classified + 0.5 * unclassified + 0.2 * other).clip(upper=1)
        r = np.corrcoef(panel.small, b)[0, 1]
        print(f"  c={c:.1f}: corr = {r:.3f}, R^2 = {r * r:.3f}")
    print(f"\n  incumbents >=5% self-funded (scored 0.2 'opaque' by breadth): "
          f"{(panel.self_ >= .05).sum()} of {len(panel)}; >=25%: {(panel.self_ >= .25).sum()}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", default=".research-cache/funding", type=pathlib.Path)
    cache = ap.parse_args().cache
    fetch(cache)
    panel, ie = load(cache)
    outside_spending(panel, ie)
    breadth(panel)


if __name__ == "__main__":
    main()
