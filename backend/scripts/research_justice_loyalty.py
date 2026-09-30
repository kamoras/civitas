"""Research: loyalty to the appointing president, the justice measure.

Reproduces the evidence in docs/research/justice-scores.md (sections from
"Loyalty to the appointing president" on). Reads three public datasets,
downloaded once into --cache:

- Epstein & Posner (2016), "Supreme Court Justices' Loyalty to the
  President", J. Legal Studies 45: every vote in a case of concern to the
  president, 1937-2014, hand-coded by the Solicitor General's position.
- The Supreme Court Database, 2025 Release 01 (justice-centered, by
  citation): every vote 1946-2024, whose lead parties extend the president
  cases past 2014.
- Martin-Quinn scores (2024 release): each justice's position per term.

Run: python scripts/research_justice_loyalty.py --cache .research-cache/justice-loyalty
(pandas, statsmodels, scipy). With --write-bundle it also writes
app/data/justice_president_votes_1937_2014.csv.gz, the hand-coded votes the
pipeline reads for the terms before the Database's lead parties take over
(pipeline/analyze/justice_loyalty.py): the one source here it can't fetch,
since Epstein & Posner publish it as a Stata file.

Research-only dependencies (scripts/requirements-research.txt):
    pip install -r requirements.txt -r scripts/requirements-research.txt
"""

import gzip

import argparse
import io
import pathlib
import sys
import urllib.request
import zipfile

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy.stats import spearmanr

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.contact import BOT_USER_AGENT  # noqa: E402

SOURCES = {
    "JusticePresident.zip": "https://epstein.wustl.edu/s/JusticePresident.zip",
    "SCDB_2025_01_justiceCentered_Citation.zip": "https://scdb.la.psu.edu/?jet_download=d9fd858d0211fe70abbe33bf7cd7ec832f3a2313",
    "mq_justices.csv": "https://mqscores.wustl.edu/media/2024/justices.csv",
}
# Presidents by the day they took office (a decision is credited to the
# president in office on its date, as in Epstein & Posner's main results).
PRESIDENTS = [
    ("FDR", "1933-03-04"), ("Truman", "1945-04-12"), ("Eisenhower", "1953-01-20"), ("Kennedy", "1961-01-20"),
    ("Johnson", "1963-11-22"), ("Nixon", "1969-01-20"), ("Ford", "1974-08-09"), ("Carter", "1977-01-20"),
    ("Reagan", "1981-01-20"), ("Bush41", "1989-01-20"), ("Clinton", "1993-01-20"), ("Bush43", "2001-01-20"),
    ("Obama", "2009-01-20"), ("Trump", "2017-01-20"), ("Biden", "2021-01-20"), ("Trump", "2025-01-20"),
]
# Who appointed each justice voting after 2014, the extension's span.
APPOINTER = {
    "JGRoberts": "Bush43", "SAAlito": "Bush43", "CThomas": "Bush41", "AMKennedy": "Reagan", "AScalia": "Reagan",
    "RBGinsburg": "Clinton", "SGBreyer": "Clinton", "SSotomayor": "Obama", "EKagan": "Obama",
    "NMGorsuch": "Trump", "BMKavanaugh": "Trump", "ACBarrett": "Trump", "KBJackson": "Biden",
}
CURRENT = ["JGRoberts", "CThomas", "SAAlito", "SSotomayor", "EKagan", "NMGorsuch", "BMKavanaugh", "ACBarrett", "KBJackson"]


def fetch(cache: pathlib.Path) -> dict[str, pathlib.Path]:
    cache.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, url in SOURCES.items():
        path = cache / name
        if not path.exists():
            print(f"fetching {url}")
            req = urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT})
            with urllib.request.urlopen(req) as resp:
                path.write_bytes(resp.read())
        paths[name] = path
    return paths


def read_zip(path: pathlib.Path, suffix: str, reader):
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.endswith(suffix) and not n.startswith("__MACOSX"))
        return reader(io.BytesIO(z.read(name)))


def president_on(date: str) -> str:
    return [name for name, start in PRESIDENTS if date >= start][-1]


def reproduce(ep: pd.DataFrame) -> None:
    print("== 1. Epstein & Posner's Table 4, from their data ==")
    t = ep.dropna(subset=["JVoteForPres"]).groupby(["justiceName", "pres_inOfficeApptJ"]).JVoteForPres.agg(["mean", "size"])
    t = t.unstack()
    for j in ("SAAlito", "JGRoberts", "CThomas", "AScalia", "DHSouter"):
        out, inn = t.loc[j, ("mean", 0.0)], t.loc[j, ("mean", 1.0)]
        print(f"  {j:10} other presidents {out:.2%} (N={int(t.loc[j, ('size', 0.0)])})  "
              f"appointing president {inn:.2%} (N={int(t.loc[j, ('size', 1.0)])})")


def government_codes(ep: pd.DataFrame, cases: pd.DataFrame) -> set:
    """The SCDB party codes that are the federal government, learned from
    the cases Epstein & Posner coded (1946-2001) and checked on 2002-2014."""
    print("\n== 2. Lead-party coding of president cases ==")
    epc = ep.drop_duplicates("caseId")[["caseId", "PresPet", "PresResp"]].fillna(0)
    m = epc.merge(cases, on="caseId")
    train, test = m[m.term <= 2001], m[m.term >= 2002]
    long = pd.concat([
        train.rename(columns={"petitioner": "code", "PresPet": "gov"})[["code", "gov"]],
        train.rename(columns={"respondent": "code", "PresResp": "gov"})[["code", "gov"]],
    ])
    stat = long.groupby("code").gov.agg(["mean", "size"])
    learned = set(stat[(stat["mean"] >= 0.5) & (stat["size"] >= 3)].index)
    print(f"  learned codes: {sorted(int(c) for c in learned)}")
    # Every learned code is 1, 27 or in 300-414: the SCDB's federal block.
    # Taking the block, not only the codes seen in training, catches an
    # agency first litigating after 2001.
    def gov(code):
        return code in (1, 27) or 300 <= code <= 420
    for name, df in (("1946-2001", train), ("2002-2014 (held out)", test)):
        for side, col, flag in (("petitioner", "petitioner", "PresPet"), ("respondent", "respondent", "PresResp")):
            pred, true = df[col].map(gov), df[flag] == 1
            tp = (pred & true).sum()
            print(f"  {name} {side}: precision {tp / pred.sum():.3f}, recall {tp / true.sum():.3f}")
    return gov


def scdb_votes(sc: pd.DataFrame, gov) -> pd.DataFrame:
    sc = sc[sc.decisionType.isin([1, 7])].copy()  # orally argued, signed opinions
    sc["pet_gov"], sc["resp_gov"] = sc.petitioner.map(gov), sc.respondent.map(gov)
    sc = sc[sc.pet_gov ^ sc.resp_gov]  # the government on exactly one side
    sc = sc[sc.partyWinning.isin([0, 1]) & sc.majority.isin([1, 2])]
    petitioner_won, in_majority = sc.partyWinning == 1, sc.majority == 2
    sc["vote_for_pres"] = np.where(sc.pet_gov, petitioner_won == in_majority, (~petitioner_won) == in_majority).astype(int)
    return sc


def panel(ep: pd.DataFrame, sc: pd.DataFrame) -> pd.DataFrame:
    m = ep.dropna(subset=["JVoteForPres"]).merge(sc[["voteId", "vote_for_pres", "pet_gov"]], on="voteId")
    print(f"  on the {len(m)} votes both hold: same vote-for-president {np.mean(m.JVoteForPres == m.vote_for_pres):.2%}, "
          f"same side {np.mean(m.PresPet == m.pet_gov.astype(int)):.2%}")
    new = sc[(sc.term >= 2015) & sc.justiceName.isin(APPOINTER)].copy()
    new["pres"] = pd.to_datetime(new.dateDecision).dt.strftime("%Y-%m-%d").map(president_on)
    new["in_office"] = (new.pres == new.justiceName.map(APPOINTER)).astype(int)
    new = new.assign(pet=new.pet_gov.astype(int)).rename(columns={"vote_for_pres": "y"})
    old = ep.dropna(subset=["JVoteForPres"]).assign(term=lambda d: d.caseId.str[:4].astype(int))
    old = old.rename(columns={"JVoteForPres": "y", "pres_inOfficeApptJ": "in_office", "PresPet": "pet"})
    cols = ["justiceName", "term", "y", "in_office", "pet"]
    return pd.concat([old[cols], new[cols]], ignore_index=True)


def loyalty(P: pd.DataFrame) -> pd.DataFrame:
    print("\n== 3. Loyalty, 1937-2024 ==")
    fit = smf.ols("y ~ in_office + pet + C(justiceName)", P).fit(
        cov_type="cluster", cov_kwds={"groups": pd.factorize(P.justiceName)[0]})
    print(f"  pooled: {fit.params['in_office']:+.3f} (t={fit.tvalues['in_office']:.1f}), N={len(P)} votes "
          "(justice fixed effects; petitioner or respondent controlled)")
    rows = []
    for j, g in P.groupby("justiceName"):
        if g.in_office.nunique() < 2 or min(g.in_office.sum(), (1 - g.in_office).sum()) < 10:
            continue
        f = smf.ols("y ~ in_office + pet", g).fit(cov_type="HC1")
        rows.append((j, f.params["in_office"], f.bse["in_office"], int(g.in_office.sum()), int((1 - g.in_office).sum())))
    R = pd.DataFrame(rows, columns=["justice", "b", "se", "n_in", "n_out"])
    # DerSimonian-Laird random effects: each justice's estimate shrunk
    # toward the mean by its own noise.
    w = 1 / R.se ** 2
    mu_fe = (w * R.b).sum() / w.sum()
    q = (w * (R.b - mu_fe) ** 2).sum()
    tau2 = max(0.0, (q - (len(R) - 1)) / (w.sum() - (w ** 2).sum() / w.sum()))
    mu = ((R.b / (R.se ** 2 + tau2)).sum()) / (1 / (R.se ** 2 + tau2)).sum()
    R["shrunk"] = (tau2 * R.b + R.se ** 2 * mu) / (tau2 + R.se ** 2)
    R["shrunk_se"] = np.sqrt(tau2 * R.se ** 2 / (tau2 + R.se ** 2))
    print(f"  {len(R)} justices: mean {mu:+.3f}, between-justice sd {np.sqrt(tau2):.3f}")
    print("  the current Court (points more often with the government under the appointing president):")
    for _, r in R[R.justice.isin(CURRENT)].sort_values("shrunk", ascending=False).iterrows():
        print(f"    {r.justice:12} {r.shrunk * 100:+5.1f} ± {r.shrunk_se * 100:.1f}  (raw {r.b * 100:+5.1f}, "
              f"{r.n_in} votes under the appointing president, {r.n_out} under others)")
    return R


def validity(P: pd.DataFrame, R: pd.DataFrame, mq: pd.DataFrame) -> None:
    print("\n== 4. What loyalty is not ==")
    med = mq.groupby("term").post_mn.median()
    mq = mq.assign(dist=(mq.post_mn - mq.term.map(med)).abs())
    R = R.assign(extremity=R.justice.map(mq.groupby("justiceName").dist.mean())).dropna(subset=["extremity"])
    rho = spearmanr(R.shrunk, R.extremity)
    print(f"  loyalty vs mean distance from the Court's median (Martin-Quinn): Spearman {rho.statistic:.2f} "
          f"(p {rho.pvalue:.2f}, {len(R)} justices)")
    rows = []
    for j, g in P.groupby("justiceName"):
        est = []
        for half in (0, 1):
            h = g[g.term % 2 == half]
            if h.in_office.nunique() < 2 or min(h.in_office.sum(), (1 - h.in_office).sum()) < 8:
                break
            est.append(smf.ols("y ~ in_office + pet", h).fit().params["in_office"])
        if len(est) == 2:
            rows.append(est)
    r = np.corrcoef(np.array(rows).T)[0, 1]
    print(f"  split-half (odd vs even terms, {len(rows)} justices): r {r:.2f}, Spearman-Brown {2 * r / (1 + r):.2f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", default=".research-cache/justice-loyalty", type=pathlib.Path)
    ap.add_argument("--write-bundle", action="store_true")
    args = ap.parse_args()
    paths = fetch(args.cache)
    ep = read_zip(paths["JusticePresident.zip"], ".dta", lambda b: pd.read_stata(b, convert_categoricals=False))
    sc = read_zip(paths["SCDB_2025_01_justiceCentered_Citation.zip"], ".csv",
                  lambda b: pd.read_csv(b, encoding="latin-1", low_memory=False))
    mq = pd.read_csv(paths["mq_justices.csv"])
    reproduce(ep)
    gov = government_codes(ep, sc.drop_duplicates("caseId")[["caseId", "term", "petitioner", "respondent"]])
    P = panel(ep, scdb_votes(sc, gov))
    R = loyalty(P)
    validity(P, R, mq)
    if args.write_bundle:
        write_bundle(ep)


def write_bundle(ep: pd.DataFrame) -> None:
    """Epstein & Posner's votes as the pipeline reads them: justice, term,
    for_government, appointer_in_office, government_petitioner."""
    out = pathlib.Path(__file__).resolve().parents[1] / "app/data/justice_president_votes_1937_2014.csv.gz"
    v = ep.dropna(subset=["JVoteForPres"]).assign(term=lambda d: d.caseId.str[:4].astype(int))
    v = v[["justiceName", "term", "JVoteForPres", "pres_inOfficeApptJ", "PresPet"]].astype(
        {"term": int, "JVoteForPres": int, "pres_inOfficeApptJ": int, "PresPet": int})
    v.columns = ["justice", "term", "for_government", "appointer_in_office", "government_petitioner"]
    with gzip.open(out, "wt", newline="") as f:
        v.sort_values(["justice", "term"]).to_csv(f, index=False)
    print(f"\nwrote {len(v)} votes to {out}")


if __name__ == "__main__":
    main()
