"""Reproduce the evidence behind Constituent Alignment's v6.13 design.

Every number in docs/research/constituent-alignment.md comes from this
script. It tests each candidate design choice the way the studies behind
the construct validate theirs (Canes-Wrone, Brady & Cogan 2002; Carson,
Koger, Lebo & Young 2010): does the measure predict how an incumbent does
with their own voters once district partisanship, the national tide and
seniority are controlled?

Outcome: incumbent's own-party share of the two-party House vote, contested
races with the incumbent on the ballot. Control for district partisanship:
own-party share of the two-party presidential vote in the district.

Data (public, fetched at pinned commits into --cache):
  - MIT Election Data + Science Lab, U.S. House / Senate / President
    returns 1976-2018 (github.com/MEDSL/constituency-returns)
  - Presidential vote by congressional district (R package politicaldata,
    github.com/cran/politicaldata)
  - DW-NOMINATE for the 103rd-111th Houses, and 108th House / 111th Senate
    roll-call matrices (Armstrong et al., "Analyzing Spatial Models of
    Choice and Judgment", github.com/uniofessex/asmcjr)
  - 109th Senate roll calls (R package pscl, github.com/cran/pscl)

Research-only dependencies, not in requirements.txt:
    pip install pandas statsmodels rdata pyreadr
Run:
    python backend/scripts/research_constituent_alignment.py [--cache DIR]
"""

import argparse
import pathlib
import urllib.request
import warnings

import numpy as np
import pandas as pd
import pyreadr
import rdata
import statsmodels.formula.api as smf

warnings.filterwarnings("ignore")

RAW = "https://raw.githubusercontent.com"
SOURCES = {
    "house.csv": f"{RAW}/MEDSL/constituency-returns/fe67c056502fc09ddb1ace2ff8f87c53233a744e/1976-2018-house.csv",
    "senate.csv": f"{RAW}/MEDSL/constituency-returns/fe67c056502fc09ddb1ace2ff8f87c53233a744e/1976-2018-senate.csv",
    "president.csv": f"{RAW}/MEDSL/constituency-returns/fe67c056502fc09ddb1ace2ff8f87c53233a744e/1976-2016-president.csv",
    "pres_by_cd.RData": f"{RAW}/cran/politicaldata/bbd681a4090790ee91b3a5d00c89572926f1b891/data/pres_results_by_cd.RData",
    "rcx.rda": f"{RAW}/uniofessex/asmcjr/52278b71a3accb8ba343d8350b802c24892e6508/data/rcx.rda",
    "hr108.rda": f"{RAW}/uniofessex/asmcjr/52278b71a3accb8ba343d8350b802c24892e6508/data/hr108.rda",
    "hr111.rda": f"{RAW}/uniofessex/asmcjr/52278b71a3accb8ba343d8350b802c24892e6508/data/hr111.rda",
    "s109.rda": f"{RAW}/cran/pscl/8220d032b199d0a9ce625c6c48a0f714e07bc432/data/s109.rda",
}
# Congress -> presidential year whose district results describe the same
# district lines. The outcome is the election at the END of the congress.
CONGRESSES = {103: 1992, 104: 1996, 105: 1996, 106: 2000, 108: 2004, 109: 2004, 110: 2008, 111: 2008}
YEA, NAY = [1, 2, 3], [4, 5, 6]


def fetch(cache: pathlib.Path) -> dict[str, pathlib.Path]:
    cache.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, url in SOURCES.items():
        path = cache / name
        if not path.exists():
            print(f"fetching {url}")
            urllib.request.urlretrieve(url, path)
        paths[name] = path
    return paths


def read_r(path: pathlib.Path, key: str):
    return rdata.conversion.convert(rdata.parser.parse_file(path))[key]


def two_party_r(df: pd.DataFrame, by) -> pd.Series:
    col = "party_simplified" if "party_simplified" in df else "party"
    party = df[col].fillna("").str.lower()
    r = df[party == "republican"].groupby(by).candidatevotes.sum()
    d = df[party.str.startswith("democrat")].groupby(by).candidatevotes.sum()
    return r / (r + d)


def last_name(s: pd.Series) -> pd.Series:
    return (s.fillna("").str.upper().str.replace(r"\b(JR|SR|II|III|IV)\b", "", regex=True)
            .str.replace(r"[^A-Z ]", "", regex=True).str.strip().str.split().str[-1])


# ---------------------------------------------------------------- panel

def build_panel(p: dict) -> pd.DataFrame:
    rcx = read_r(p["rcx.rda"], "rcx")
    house = pd.read_csv(p["house.csv"], encoding="latin-1")
    pres = pd.read_csv(p["president.csv"], encoding="latin-1")
    by_cd = pyreadr.read_r(p["pres_by_cd.RData"])["pres_results_by_cd"]

    ic2po = house.drop_duplicates("state_ic").set_index("state_ic")["state_po"].to_dict()
    nat = two_party_r(pres, "year")

    hist = rcx[rcx.dist < 98][["id", "cong"]].drop_duplicates()
    m = rcx[rcx.cong.isin(CONGRESSES) & rcx.party.isin([100, 200]) & (rcx.dist >= 0) & (rcx.dist < 98)].copy()
    m["state_po"] = m.state.map(ic2po)
    m["party"] = m.party.map({100: "D", 200: "R"})
    m["elec_year"] = 1788 + 2 * m.cong
    m["pres_year"] = m.cong.map(CONGRESSES)
    m["district"] = m.dist.replace(0, 1)
    m["last"] = m.name.str.upper().str.replace(r"[^A-Z ]", "", regex=True).str.strip().str.split().str[0]
    m["terms"] = [((hist.id == i) & (hist.cong <= c)).sum() for i, c in zip(m.id, m.cong)]

    cd = by_cd.copy()
    cd["district"] = cd.district.astype(int)
    cd["presR"] = cd.rep / (cd.rep + cd.dem)
    cd = cd.groupby(["state_abb", "district", "year"], as_index=False).presR.mean()  # source repeats rows
    m = m.merge(cd.rename(columns={"state_abb": "state_po", "year": "pres_year"}),
                on=["state_po", "pres_year", "district"], how="left")

    h = house[(house.stage == "gen") & ~house.special.fillna(False).astype(bool)].copy()
    party = h.party.fillna("").str.lower()
    h["pty"] = np.where(party.str.startswith("democrat"), "D", np.where(party == "republican", "R", ""))
    h = h[h.pty != ""]
    h["district"] = h.district.replace(0, 1)
    h["cand_last"] = last_name(h.candidate)
    votes = h.groupby(["year", "state_po", "district", "pty"]).candidatevotes.sum().unstack(fill_value=0)
    votes["houseR"] = votes.R / (votes.R + votes.D)
    votes["contested"] = (votes.R > 0) & (votes.D > 0)
    m = m.merge(votes.reset_index()[["year", "state_po", "district", "houseR", "contested"]]
                .rename(columns={"year": "elec_year"}), on=["elec_year", "state_po", "district"], how="left")
    names = (h.groupby(["year", "state_po", "district", "pty"]).cand_last.apply(set).reset_index()
             .rename(columns={"year": "elec_year", "pty": "party"}))
    m = m.merge(names, on=["elec_year", "state_po", "district", "party"], how="left")
    m["ran"] = [isinstance(s, set) and n in s for n, s in zip(m["last"], m.cand_last)]
    m = m[m.presR.notna()].drop_duplicates(["cong", "id"])

    m["sign"] = np.where(m.party == "R", 1.0, -1.0)
    m["pvi"] = (m.presR - m.pres_year.map(nat)) * 100  # + = R lean
    m["alignment"] = (m.pvi * m.sign / 15).clip(-1, 1)  # score_calculator._signed_state_alignment
    m["own_pres"] = np.where(m.party == "R", m.presR, 1 - m.presR)
    m["own_house"] = np.where(m.party == "R", m.houseR, 1 - m.houseR)
    m["lterms"] = np.log(m.terms)

    parts = []
    for _, g in m.groupby("cong"):
        g = g.copy()
        for _, gp in g.groupby("party"):  # A: per-party OLS on seat lean (the shipped design)
            b, a = np.polyfit(gp.pvi, gp.dwnom1, 1)
            g.loc[gp.index, "resA"] = gp.dwnom1 - (a + b * gp.pvi)
        g["resB"] = g.dwnom1 - smf.ols("dwnom1 ~ pvi + C(party)", g).fit().fittedvalues  # B: pooled slope
        parts.append(g)
    m = pd.concat(parts)
    m["extA"], m["extB"], m["extC"] = m.resA * m.sign, m.resB * m.sign, m.dwnom1 * m.sign
    for k in ("extA", "extB", "extC"):
        m[k + "_z"] = (m[k] - m[k].mean()) / m[k].std()
    return m


def contested(m: pd.DataFrame) -> pd.DataFrame:
    s = m[m.contested.astype(bool) & m.ran & m.own_house.between(0.2, 0.95)].copy()
    s["y"], s["x"] = s.own_house * 100, s.own_pres * 100
    s["fe"] = s.elec_year.astype(str) + s.party
    return s


def clustered(formula, d):
    return smf.ols(formula, d).fit(cov_type="cluster", cov_kwds={"groups": d.id})


def loyo_rmse(formula, d):
    """Leave-one-election-out: fit on the other elections, predict the held
    one; its party-level mean residual (the tide) is removed, since no
    model could know it in advance."""
    errs = []
    for yr in sorted(d.elec_year.unique()):
        tr, te = d[d.elec_year != yr], d[d.elec_year == yr]
        resid = te.y - smf.ols(formula.replace("C(fe)", "C(party)"), tr).fit().predict(te)
        errs.append((resid - resid.groupby(te.party).transform("mean")).values)
    e = np.concatenate(errs)
    return np.sqrt(np.mean(e ** 2))


# ------------------------------------------------------------- analyses

def position_tests(m):
    S = contested(m)
    base = "y ~ x + lterms + C(fe)"
    b0 = clustered(base, S)
    print(f"\n== Position: N = {len(S)} incumbent-elections, {S.elec_year.nunique()} elections ==")
    print(f"{'measure':34s} {'coef/SD':>8s} {'t':>6s} {'dR2':>7s} {'LOYO RMSE':>10s}")
    print(f"{'base':34s} {'':>8s} {'':>6s} {'':>7s} {loyo_rmse(base, S):10.3f}")
    for k, label in (("extA", "A per-party residual (shipped)"), ("extB", "B pooled-slope residual"),
                     ("extC", "C raw party-signed dim1")):
        r = clustered(f"{base} + {k}_z", S)
        print(f"{label:34s} {r.params[k + '_z']:8.2f} {r.tvalues[k + '_z']:6.1f} "
              f"{r.rsquared - b0.rsquared:7.4f} {loyo_rmse(f'{base} + {k}_z', S):10.3f}")

    print("\nper election (A, flexible partisanship):")
    for yr, d in S.groupby("elec_year"):
        r = smf.ols("y ~ (x + I(x**2))*C(party) + lterms + extA_z", d).fit(cov_type="HC1")
        print(f"  {yr}: {r.params['extA_z']:6.2f} (t={r.tvalues['extA_z']:5.1f}, n={len(d)})")

    flex = "y ~ x*C(fe) + I(x**2)*C(fe) + lterms"
    r = clustered(f"{flex} + extA_z", S)
    print(f"\npooled, flexible partisanship: {r.params['extA_z']:.2f} (t={r.tvalues['extA_z']:.1f})")

    print("\nsafe-seat scaling (pre-v6.13 zeroed the flank penalty in safe seats):")
    S["safety"] = pd.cut(S.alignment, [-1.01, 0.0, 0.5, 1.01], labels=["opposed/swing", "lean own", "safe own"])
    for s, d in S.groupby("safety", observed=True):
        r = clustered(f"{base} + extA_z", d)
        print(f"  {s:14s}: {r.params['extA_z']:6.2f} (t={r.tvalues['extA_z']:5.1f}, n={len(d)})")
    r = clustered(f"{flex} + extA_z*alignment", S)
    print(f"  interaction extA x alignment: {r.params['extA_z:alignment']:.2f} (t={r.tvalues['extA_z:alignment']:.1f})")
    a = S.alignment.clip(lower=0)
    S["extA_sev"] = S.extA_z.clip(lower=0) * (1 - a) + S.extA_z.clip(upper=0) * np.maximum(0.25, 1 - 0.75 * a)
    for k, label in (("extA_z", "unscaled (shipped)"), ("extA_sev", "pre-v6.13 safety-scaled")):
        r = clustered(f"{base} + {k}", S)
        print(f"  {label:24s} dR2={r.rsquared - b0.rsquared:.4f}  LOYO={loyo_rmse(f'{base} + {k}', S):.3f}")

    S["pos"], S["neg"] = S.extA_z.clip(lower=0), S.extA_z.clip(upper=0)
    r = clustered(f"{flex} + pos + neg", S)
    print(f"\nsymmetry: flank-ward {r.params['pos']:.2f} (t={r.tvalues['pos']:.1f}), "
          f"center-ward {r.params['neg']:.2f} (t={r.tvalues['neg']:.1f}), "
          f"equal slopes p={float(r.t_test('pos = neg').pvalue):.2f}")


def unity_breaks(obj):
    """Party-unity votes (majorities of the two parties opposed) and each
    member's share of them cast against their own party's majority."""
    V = np.asarray(obj["votes"].values)
    L = obj["legis.data"].reset_index(drop=True)
    yea, nay = np.isin(V, YEA), np.isin(V, NAY)

    def share(mask):
        return yea[mask].sum(0) / np.maximum(yea[mask].sum(0) + nay[mask].sum(0), 1)
    dy, ry = share((L.party == "D").values), share((L.party == "R").values)
    unity = ((dy > .5) & (ry < .5)) | ((dy < .5) & (ry > .5))
    rows = []
    for i in range(len(L)):
        if L.party[i] not in ("D", "R") or L.state[i] == "USA":
            continue
        pmaj = (dy > .5) if L.party[i] == "D" else (ry > .5)
        cast = unity & (yea[i] | nay[i])
        against = ((yea[i] & ~pmaj) | (nay[i] & pmaj)) & cast
        rows.append(dict(id=int(L.icpsrLegis[i]), state=L.state[i], party=L.party[i],
                         name=str(obj["legis.data"].index[i]).split(" (")[0].upper(),
                         brk=against.sum() / max(cast.sum(), 1)))
    return pd.DataFrame(rows), int(unity.sum()), V.shape[1]


def kinked_fit(df):
    """The shipped reference: per party, brk = a + b*al + c*min(al, 0)."""
    fits = {p: smf.ols("brk ~ alignment + I(alignment.clip(upper=0))", g).fit() for p, g in df.groupby("party")}
    return pd.concat([fits[p].fittedvalues for p in fits]), fits


def loyalty_tests(m, p):
    hr = read_r(p["hr108.rda"], "hr108")
    R, n_unity, n_all = unity_breaks(hr)
    M = m[m.cong == 108].merge(R[["id", "brk"]], on="id")
    print(f"\n== Loyalty: 108th House, {n_unity} party-unity roll calls of {n_all}; outcome 2004 ==")

    hand = np.where(M.alignment >= 0, .03 + .05 * (1 - M.alignment), .08 + .12 * (-M.alignment))
    M["exp_hand"] = hand
    M["exp_party"], fits = kinked_fit(M)
    M["exp_pooled"] = smf.ols("brk ~ alignment + I(alignment.clip(upper=0))", M).fit().fittedvalues
    M["bin"] = pd.cut(M.alignment, [-1.01, -.34, 0, .34, .67, 1.01])
    print("break rate by seat alignment (full chamber) vs the pre-v6.13 hand-set curve:")
    print(M.groupby("bin", observed=True).agg(n=("brk", "size"), measured_mean=("brk", "mean"),
                                              hand_curve=("exp_hand", "mean")).round(3).to_string())
    for party, f in fits.items():
        print(f"  measured {party}: {f.params.round(3).to_dict()}")

    S = contested(M)
    for k in ("party", "pooled"):
        S[f"dev_{k}"] = (S.brk - S[f"exp_{k}"]) / (S.brk - S[f"exp_{k}"]).std()
    S["brk_z"] = (S.brk - S.brk.mean()) / S.brk.std()

    def floored(r):
        if r.brk >= r.exp_hand:
            return 50 + 50 * min((r.brk - r.exp_hand) / .25, 1) * max(.25, 1 - .75 * max(r.alignment, 0))
        return 50.0
    S["floored"] = S.apply(floored, axis=1)
    dev = S.brk - S.exp_party
    S["symmetric"] = 50 + 50 * (dev / dev.abs().quantile(.9)).clip(-1, 1)
    base = "y ~ x + I(x**2) + C(party) + lterms"
    b0 = smf.ols(base, S).fit()
    print(f"\nN = {len(S)} contested 2004 incumbents")
    print(f"{'measure':46s} {'coef':>7s} {'t':>6s} {'dR2':>7s}")
    for k, label in (("brk_z", "raw break rate (per SD)"),
                     ("dev_pooled", "vs pooled measured expectation (per SD)"),
                     ("dev_party", "vs per-party measured expectation (per SD)"),
                     ("floored", "pre-v6.13 floored score (per point)"),
                     ("symmetric", "v6.13 symmetric score (per point)"),
                     ("extA_z", "position residual A (per SD)")):
        r = smf.ols(f"{base} + {k}", S).fit(cov_type="HC1")
        print(f"{label:46s} {r.params[k]:7.3f} {r.tvalues[k]:6.1f} {r.rsquared - b0.rsquared:7.4f}")
    r = smf.ols(f"{base} + dev_party + extA_z", S).fit(cov_type="HC1")
    print(f"joint: votes {r.params['dev_party']:.2f} (t={r.tvalues['dev_party']:.1f}), "
          f"position {r.params['extA_z']:.2f} (t={r.tvalues['extA_z']:.1f}); "
          f"corr {S.dev_party.corr(S.extA_z):.2f}")

    S["neg"], S["pos"] = S.dev_party.clip(upper=0), S.dev_party.clip(lower=0)
    r = smf.ols(f"{base} + neg + pos", S).fit(cov_type="HC1")
    print(f"below-expected (loyal) slope {r.params['neg']:.2f} (t={r.tvalues['neg']:.1f}); "
          f"above-expected slope {r.params['pos']:.2f} (t={r.tvalues['pos']:.1f})")
    for safe, g in S.groupby(S.alignment > .5):
        rr = smf.ols(f"{base} + dev_party", g).fit(cov_type="HC1")
        print(f"  {'safe' if safe else 'not safe'} seats: {rr.params['dev_party']:.2f} (t={rr.tvalues['dev_party']:.1f}, n={len(g)})")
    S["flank"] = (S.extA > 0).astype(int)
    r = smf.ols(f"{base} + neg + pos + pos:flank + flank", S).fit(cov_type="HC1")
    print(f"flank-side defectors (Kirkland & Slapin): extra slope {r.params['pos:flank']:.2f} "
          f"(t={r.tvalues['pos:flank']:.1f}); n={int(((S.flank == 1) & (S.pos > 0)).sum())}")

    # Is there such a thing as breaking too much? A peaked score (best at the
    # expectation, falling off both ways) against the shipped monotone one,
    # and the crossing-side slope past the saturation point.
    S["absdev"] = S.dev_party.abs()
    S["peaked"] = 100 - 100 * (dev.abs() / dev.abs().quantile(.9)).clip(0, 1)
    print("breaking far above expectation:")
    for k, label in (("absdev", "folded |deviation| (per SD)"), ("peaked", "peaked score (per point)")):
        r = smf.ols(f"{base} + {k}", S).fit(cov_type="HC1")
        print(f"  {label:30s} {r.params[k]:7.3f} (t={r.tvalues[k]:.1f}) dR2={r.rsquared - b0.rsquared:.4f}")
    r = smf.ols(f"{base} + dev_party + I(dev_party**2)", S).fit(cov_type="HC1")
    print(f"  quadratic term {r.params['I(dev_party ** 2)']:.2f} (t={r.tvalues['I(dev_party ** 2)']:.1f})")
    knot = dev.abs().quantile(.9) / dev.std()
    S["pos1"], S["pos2"] = S.dev_party.clip(0, knot), (S.dev_party - knot).clip(lower=0)
    r = smf.ols(f"{base} + neg + pos1 + pos2", S).fit(cov_type="HC1")
    print(f"  crossing slope up to saturation {r.params['pos1']:.2f} (t={r.tvalues['pos1']:.1f}); "
          f"beyond it {r.params['pos2']:.2f} (t={r.tvalues['pos2']:.1f}, n={int((S.pos2 > 0).sum())})")
    return hr


def nokken_poole_test(m, hr):
    """Congress-specific position (first principal component of this
    congress's own roll calls — the Nokken-Poole idea) vs career-constrained
    DW-NOMINATE, predicting 2004."""
    V = np.asarray(hr["votes"].values)
    L = hr["legis.data"].reset_index(drop=True)
    X = np.where(np.isin(V, YEA), 1.0, np.where(np.isin(V, NAY), -1.0, np.nan))
    minority = np.nanmin(np.stack([np.nanmean(X == 1, 0), np.nanmean(X == -1, 0)]), 0)
    X = X[:, minority > .025]  # drop near-unanimous votes
    u, s, _ = np.linalg.svd(np.nan_to_num(X - np.nanmean(X, 0)), full_matrices=False)
    L["pc1"], L["id"] = u[:, 0] * s[0], L.icpsrLegis.astype(int)
    M = m[m.cong == 108].merge(L[["id", "pc1"]], on="id")
    if np.corrcoef(M.pc1, M.dwnom1)[0, 1] < 0:
        M["pc1"] *= -1
    for _, g in M.groupby("party"):
        for k in ("pc1", "dwnom1"):
            b, a = np.polyfit(g.pvi, g[k], 1)
            M.loc[g.index, "r_" + k] = g[k] - (a + b * g.pvi)
    for k in ("r_pc1", "r_dwnom1"):
        M[k] = M[k] * M.sign / (M[k] * M.sign).std()
    S = contested(M)
    base = "y ~ x + I(x**2) + C(party) + lterms"
    b0 = smf.ols(base, S).fit()
    print(f"\n== Position source: 108th House -> 2004 (corr of the two positions {np.corrcoef(M.pc1, M.dwnom1)[0, 1]:.3f}) ==")
    for k, label in (("r_dwnom1", "DW-NOMINATE (career-constrained)"), ("r_pc1", "congress-specific")):
        r = smf.ols(f"{base} + {k}", S).fit(cov_type="HC1")
        print(f"{label:34s} {r.params[k]:6.2f} (t={r.tvalues[k]:4.1f}) dR2={r.rsquared - b0.rsquared:.4f}")
    r = smf.ols(f"{base} + r_dwnom1 + r_pc1", S).fit(cov_type="HC1")
    print(f"joint: DW {r.params['r_dwnom1']:.2f} (t={r.tvalues['r_dwnom1']:.1f}), "
          f"congress-specific {r.params['r_pc1']:.2f} (t={r.tvalues['r_pc1']:.1f})")


def senate_test(p):
    pres = pd.read_csv(p["president.csv"], encoding="latin-1")
    sen = pd.read_csv(p["senate.csv"], encoding="latin-1")
    sen["p"] = sen.party.fillna("").str.lower()
    out = []
    for file, key, pres_year, elec_year in (("s109.rda", "s109", 2004, 2006), ("hr111.rda", "hr111", 2008, 2010)):
        B, _, _ = unity_breaks(read_r(p[file], key))
        nat = two_party_r(pres[pres.year == pres_year], "year").iloc[0]
        st = two_party_r(pres[pres.year == pres_year], "state_po")
        B["sign"] = np.where(B.party == "R", 1, -1)
        B["alignment"] = ((st.reindex(B.state).values - nat) * 100 * B.sign / 15).clip(-1, 1)
        B["x"] = np.where(B.party == "R", st.reindex(B.state).values, 1 - st.reindex(B.state).values) * 100
        B["exp"], _ = kinked_fit(B)
        s = sen[(sen.year == elec_year) & (sen.stage == "gen") & ~sen.special.fillna(False).astype(bool)].copy()
        s["pty"] = np.where(s.p.str.startswith("democrat"), "D", np.where(s.p == "republican", "R", ""))
        s = s[s.pty != ""]
        s["name"] = last_name(s.candidate)
        tot = s.groupby(["state_po", "pty"]).candidatevotes.sum().unstack(fill_value=0)
        s = s.merge(tot.reset_index(), on="state_po")
        s["own"] = np.where(s.pty == "R", s.R / (s.R + s.D), s.D / (s.R + s.D)) * 100
        j = B.merge(s[["state_po", "pty", "name", "own", "R", "D"]]
                    .rename(columns={"state_po": "state", "pty": "party"}), on=["state", "party", "name"])
        j = j[(j.R > 0) & (j.D > 0)]
        j["yr"] = elec_year
        out.append(j)
    J = pd.concat(out)
    J["dev"] = (J.brk - J.exp) / (J.brk - J.exp).std()
    J["neg"], J["pos"] = J.dev.clip(upper=0), J.dev.clip(lower=0)
    r = smf.ols("own ~ x + C(yr)*C(party) + dev", J).fit(cov_type="HC1")
    rr = smf.ols("own ~ x + C(yr)*C(party) + neg + pos", J).fit(cov_type="HC1")
    print(f"\n== Senate replication: 2006 + 2010, N = {len(J)} contested incumbents ==")
    print(f"deviation from measured expectation: {r.params['dev']:.2f} (t={r.tvalues['dev']:.1f}); "
          f"loyal side {rr.params['neg']:.2f} (t={rr.tvalues['neg']:.1f}), "
          f"crossing side {rr.params['pos']:.2f} (t={rr.tvalues['pos']:.1f})")
    J["absdev"] = J.dev.abs()
    r = smf.ols("own ~ x + C(yr)*C(party) + absdev", J).fit(cov_type="HC1")
    print(f"folded |deviation|: {r.params['absdev']:.2f} (t={r.tvalues['absdev']:.1f})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", default=".research-cache/constituent-alignment", type=pathlib.Path)
    paths = fetch(ap.parse_args().cache)
    m = build_panel(paths)
    position_tests(m)
    hr = loyalty_tests(m, paths)
    nokken_poole_test(m, hr)
    senate_test(paths)


if __name__ == "__main__":
    main()
