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
from app.pipeline.analyze.justice_loyalty import MIN_VOTES_EACH_SIDE, Estimate, shrink  # noqa: E402

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
# Each president's party, for the same-party check on the post-2014 votes
# (EP_PRESIDENT_DEMOCRAT before then).
PARTY = {
    "FDR": "D", "Truman": "D", "Eisenhower": "R", "Kennedy": "D", "Johnson": "D", "Nixon": "R", "Ford": "R",
    "Carter": "D", "Reagan": "R", "Bush41": "R", "Clinton": "D", "Bush43": "R", "Obama": "D", "Trump": "R",
    "Biden": "D",
}
# Epstein & Posner's pres_inOfficeN, FDR (1) to Obama (13): 1 Democratic.
EP_PRESIDENT_DEMOCRAT = {1: 1, 2: 1, 3: 0, 4: 1, 5: 1, 6: 0, 7: 0, 8: 1, 9: 0, 10: 0, 11: 1, 12: 0, 13: 1}
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
    # same_party: the sitting president is of the appointer's party (the
    # appointer's own terms included).
    new["same_party"] = (new.pres.map(PARTY) == new.justiceName.map(APPOINTER).map(PARTY)).astype(int)
    new = new.assign(pet=new.pet_gov.astype(int)).rename(columns={"vote_for_pres": "y"})
    old = ep.dropna(subset=["JVoteForPres"]).assign(term=lambda d: d.caseId.str[:4].astype(int))
    # Epstein & Posner's same_partyExcludeInOffice is not a same-party
    # indicator: it is 1 under the appointer, 0 under other presidents of
    # the appointer's party and missing under the other party's (the sample
    # of their Tables 8-10). The party match is derived from their president
    # index and j_party (the appointer's party, 1 Democratic) instead.
    old["same_party"] = (old.pres_inOfficeN.map(EP_PRESIDENT_DEMOCRAT) == old.j_party).astype(int)
    old = old.rename(columns={"JVoteForPres": "y", "pres_inOfficeApptJ": "in_office", "PresPet": "pet"})
    cols = ["justiceName", "caseId", "term", "y", "in_office", "same_party", "pet"]
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


def _per_justice(P: pd.DataFrame, formula: str, coef: str, side_in, side_out) -> pd.DataFrame:
    """Each justice's `coef` from `formula` (OLS, HC1), shrunk and scored
    with the pipeline's own code (justice_loyalty): measurable with
    MIN_VOTES_EACH_SIDE votes on each side, the sides being the masks
    side_in(g) and side_out(g)."""
    est, n = {}, {}
    for j, g in P.groupby("justiceName"):
        n[j] = (int(side_in(g).sum()), int(side_out(g).sum()))
        if min(n[j]) < MIN_VOTES_EACH_SIDE:
            continue
        f = smf.ols(formula, g).fit(cov_type="HC1")
        est[j] = Estimate(raw=float(f.params[coef]), se=float(f.bse[coef]), votes_in=n[j][0], votes_out=n[j][1],
                          rate_in=float(g.y[side_in(g)].mean()), rate_out=float(g.y[side_out(g)].mean()))
    shrunk, mu, tau = shrink(est)
    R = pd.DataFrame(
        [(j, e.raw, e.se, shrunk[j].loyalty, shrunk[j].se, shrunk[j].score, e.votes_in, e.votes_out) for j, e in est.items()],
        columns=["justice", "b", "se", "shrunk", "shrunk_se", "score", "n_in", "n_out"],
    ).set_index("justice")
    R.attrs.update(mu=mu, tau=tau, n=n)
    return R


def same_party(P: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Personal or partisan? "Under other presidents" mixes presidents of
    the appointer's party with the other party's, so a lean toward any
    same-party administration could read as loyalty. Four specifications,
    each justice fitted, shrunk and scored as the pipeline does:
      A  appointer vs every other president (the score)
      B  appointer vs other presidents of the appointer's party only
         (Epstein & Posner's Section 4.2)
      C  every vote, appointer and same-party indicators together: the
         appointer's effect net of the party's (identified, like B, only
         where another president of that party served)
      D  same-party vs other-party presidents, the appointer's terms left
         out: the party effect itself"""
    print("\n== 5. Personal or partisan: four specifications ==")
    P = P.assign(sp_other=((P.same_party == 1) & (P.in_office == 0)).astype(int))
    f = smf.ols("y ~ in_office + sp_other + pet + C(justiceName)", P).fit(
        cov_type="cluster", cov_kwds={"groups": pd.factorize(P.justiceName)[0]})
    cov = f.cov_params()
    diff = f.params["in_office"] - f.params["sp_other"]
    se = np.sqrt(cov.loc["in_office", "in_office"] + cov.loc["sp_other", "sp_other"] - 2 * cov.loc["in_office", "sp_other"])
    print(f"  pooled (justice fixed effects, clustered by justice), against the other party's presidents: appointer "
          f"{f.params['in_office']:+.3f} (t={f.tvalues['in_office']:.1f}), other same-party "
          f"{f.params['sp_other']:+.3f} (t={f.tvalues['sp_other']:.1f}); appointer against other same-party "
          f"{diff:+.3f} (t={diff / se:.1f})")
    print(f"  votes: appointer {int(P.in_office.sum())}, other same-party {int(P.sp_other.sum())}, "
          f"other party {int((P.same_party == 0).sum())}")
    specs = {
        "A": _per_justice(P, "y ~ in_office + pet", "in_office", lambda g: g.in_office == 1, lambda g: g.in_office == 0),
        "B": _per_justice(P[(P.in_office == 1) | (P.sp_other == 1)], "y ~ in_office + pet", "in_office",
                          lambda g: g.in_office == 1, lambda g: g.in_office == 0),
        "C": _per_justice(P, "y ~ in_office + same_party + pet", "in_office",
                          lambda g: g.in_office == 1, lambda g: g.sp_other == 1),
        "D": _per_justice(P[P.in_office == 0], "y ~ same_party + pet", "same_party",
                          lambda g: g.same_party == 1, lambda g: g.same_party == 0),
    }
    A = specs["A"]
    for name, R in specs.items():
        both = R.index.intersection(A.index)
        rho = spearmanr(R.loc[both, "shrunk"], A.loc[both, "shrunk"]).statistic
        cur = sum(j in R.index for j in CURRENT)
        print(f"  {name}: {len(R)} justices measurable ({cur} of the current nine), mean {R.attrs['mu'] * 100:+.1f}, "
              f"tau {R.attrs['tau'] * 100:.1f} points, Spearman with A {rho:.2f} over {len(both)}")
    print("  per justice: raw ± se -> shrunk, score (votes in/out); '-' not measurable")
    names = sorted(A.attrs["n"], key=lambda j: (j not in CURRENT, j))
    for j in names:
        cells = []
        for name, R in specs.items():
            if j in R.index:
                r = R.loc[j]
                cells.append(f"{name} {r.b * 100:+5.1f}±{r.se * 100:4.1f} -> {r.shrunk * 100:+5.1f} {r.score:5.1f} "
                             f"({int(r.n_in)}/{int(r.n_out)})")
            else:
                n = R.attrs["n"].get(j, (0, 0))
                cells.append(f"{name} - ({n[0]}/{n[1]})".ljust(40))
        print(f"    {j:13} " + " | ".join(cells))
    return specs


def _split_half(P: pd.DataFrame, formula: str) -> tuple[int, float]:
    """(justices, Spearman-Brown reliability) of the per-justice in_office
    estimate between odd and even terms."""
    rows = []
    for _, g in P.groupby("justiceName"):
        est = []
        for half in (0, 1):
            h = g[g.term % 2 == half]
            if h.in_office.nunique() < 2 or min(h.in_office.sum(), (1 - h.in_office).sum()) < 8:
                break
            est.append(smf.ols(formula, h).fit().params["in_office"])
        if len(est) == 2:
            rows.append(est)
    r = np.corrcoef(np.array(rows).T)[0, 1]
    return len(rows), 2 * r / (1 + r)


def _against_colleagues(P: pd.DataFrame) -> pd.DataFrame:
    """Each vote minus the mean vote of the justice's colleagues on the same
    case: rel_all over every colleague; rel_out over colleagues the sitting
    president did not appoint (so co-appointees sharing one loyalty don't
    cancel), missing where no such colleague voted."""
    s, n = P.groupby("caseId").y.transform("sum"), P.groupby("caseId").y.transform("size")
    out = (P.in_office == 0).astype(int)
    os_, on = (P.y * out).groupby(P.caseId).transform("sum"), out.groupby(P.caseId).transform("sum")
    return P.assign(rel_all=P.y - (s - P.y) / (n - 1).replace(0, np.nan),
                    rel_out=P.y - (os_ - P.y * out) / (on - out).replace(0, np.nan))


ERA_SPECS = (("A", "y", "{f} ~ {x} + pet"), ("E", "rel_all", "{f} ~ {x}"), ("E-excl", "rel_out", "{f} ~ {x}"))


def era(P: pd.DataFrame) -> None:
    """Is the appointer's time in office standing in for the era? It is
    always the start of a justice's career, and the government's win rate
    fell from the 1980s on (Epstein & Posner 2018). Held fixed by term
    fixed effects, by era, and by the justice's vote against their
    colleagues' on the same case (E; E-excl leaves out colleagues the
    sitting president appointed). Then the gate: a placebo window of the
    appointer's length, k terms into the rest of the career (the real
    appointer votes dropped), which a measure free of the career-timing
    confound should put at zero for every k."""
    print("\n== 7. Loyalty or era ==")
    print("  government vote share by decade: " + ", ".join(
        f"{d}s {v:.2f}" for d, v in P.groupby(P.term // 10 * 10).y.mean().items()))
    P = _against_colleagues(P)
    print(f"  votes with no colleague: {int(P.rel_all.isna().sum())}; with no colleague the sitting president "
          f"did not appoint: {int(P.rel_out.isna().sum())}")

    def pooled(d, formula, x):
        f = smf.ols(formula + " + C(justiceName)", d).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(d.justiceName)[0]})
        return f.params[x], f.tvalues[x]

    for label, d, formula in (
        ("+ term fixed effects", P, "y ~ in_office + pet + C(term)"),
        *((f"{lo}-{hi}, + term fixed effects", P[P.term.between(lo, hi)], "y ~ in_office + pet + C(term)")
          for lo, hi in ((1937, 1952), (1953, 1980), (1981, 2024))),
        *((name, P.dropna(subset=[col]), tpl.format(f=col, x="in_office")) for name, col, tpl in ERA_SPECS),
    ):
        b, t = pooled(d, formula, "in_office")
        print(f"  pooled, justice fixed effects, {label}: {b:+.3f} (t={t:.1f}), N={len(d)}")
    fits = {}
    for name, col, tpl in ERA_SPECS:
        d = P.dropna(subset=[col])
        fits[name] = _per_justice(d, tpl.format(f=col, x="in_office"), "in_office",
                                  lambda g: g.in_office == 1, lambda g: g.in_office == 0)
        R, A = fits[name], fits["A"]
        both = R.index.intersection(A.index)
        n, rel = _split_half(d, tpl.format(f=col, x="in_office"))
        print(f"  {name}: {len(R)} justices ({sum(j in R.index for j in CURRENT)} current), mean {R.attrs['mu'] * 100:+.1f}, "
              f"tau {R.attrs['tau'] * 100:.1f}, Spearman with A {spearmanr(R.loc[both, 'shrunk'], A.loc[both, 'shrunk']).statistic:.2f}, "
              f"split-half reliability {rel:.2f} ({n} justices)")
    print("  per justice: shrunk ± se, score; for E-excl also the weight on the justice's own estimate, tau2 / (tau2 + se2)")
    tau2 = fits["E-excl"].attrs["tau"] ** 2
    for j in sorted(fits["A"].index, key=lambda j: (j not in CURRENT, j)):
        cells = []
        for name, R in fits.items():
            if j not in R.index:
                cells.append(f"{name} -")
                continue
            r = R.loc[j]
            w = f" w {tau2 / (tau2 + r.se ** 2):.2f}" if name == "E-excl" else ""
            cells.append(f"{name} {r.shrunk * 100:+5.1f} ± {r.shrunk_se * 100:.1f}, {r.score:5.1f}{w}")
        print(f"    {j:13} " + " | ".join(cells))

    print("  placebo: pooled effect of a fake window k terms after the appointer's (t); real effect for comparison")
    Q = P[P.in_office == 0]
    length = P[P.in_office == 1].groupby("justiceName").term.nunique()
    rows = []
    for k in range(25):
        parts = []
        for j, g in Q.groupby("justiceName"):
            terms = sorted(g.term.unique())
            if j in length.index and k + length[j] < len(terms):
                parts.append(g.assign(fake=g.term.isin(terms[k:k + length[j]]).astype(int)))
        if len(parts) < 3:
            break
        D = pd.concat(parts)
        row = {"k": k, "justices": len(parts)}
        for name, col, tpl in ERA_SPECS:
            row[name], row[name + " t"] = pooled(D.dropna(subset=[col]), tpl.format(f=col, x="fake"), "fake")
        rows.append(row)
    T = pd.DataFrame(rows).set_index("k")
    for k, r in T.iterrows():
        print(f"    k={k:2} ({int(r.justices):2} justices) " + "  ".join(
            f"{name} {r[name] * 100:+5.1f} ({r[name + ' t']:+.1f})" for name, _, _ in ERA_SPECS))
    for name, col, tpl in ERA_SPECS:
        real, _ = pooled(P.dropna(subset=[col]), tpl.format(f=col, x="in_office"), "in_office")
        v = T[name]
        print(f"    {name:6} real {real * 100:+.1f}; placebo k=0 {v.iloc[0] * 100:+.1f}, k=1 {v.iloc[1] * 100:+.1f}; over all "
              f"{len(v)} shifts mean {v.mean() * 100:+.1f}, sd {v.std() * 100:.1f}, |t| > 1.96 in "
              f"{(T[name + ' t'].abs() > 1.96).mean():.0%}")


def against_ideology(sc: pd.DataFrame, mq: pd.DataFrame) -> None:
    """Would "votes against their own ideological side" measure fairness?
    In the justice-centered Database, `direction` is the justice's own
    vote (1 conservative, 2 liberal). Divided decisions only: unanimous
    ones split about evenly either way and swamp everything."""
    print("\n== 6. Votes against a justice's own side (Martin-Quinn) ==")
    v = sc[sc.decisionType.isin([1, 7]) & sc.direction.isin([1, 2]) & (sc.minVotes > 0)]
    med = mq.groupby("term").post_mn.median()
    side = mq.assign(rel=mq.post_mn - mq.term.map(med))[["justiceName", "term", "rel"]]
    m = v.merge(side, on=["justiceName", "term"])
    m = m[m.rel.abs() > 0.05]
    m = m.assign(against=np.where(m.rel < 0, m.direction == 1, m.direction == 2).astype(int))
    g = m.groupby("justiceName").agg(against=("against", "mean"), dist=("rel", lambda s: s.abs().mean()),
                                     n=("against", "size"))
    g = g[g.n >= 300]
    rho = spearmanr(g.against, g.dist)
    print(f"  against-side rate vs mean distance from the median: Spearman {rho.statistic:.2f} "
          f"(p {rho.pvalue:.1g}, {len(g)} justices with 300+ divided votes)")
    for j in CURRENT:
        if j in g.index:
            print(f"    {j:12} {g.loc[j, 'against']:.1%} against their side, distance {g.loc[j, 'dist']:.2f}")


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
    same_party(P)
    against_ideology(sc, mq)
    era(P)
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
