"""Reproduce the evidence behind Constituent Alignment's design (v6.13-v6.27).

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
  - Every Senate (101st-119th) and House (101st-111th) roll call from
    Voteview (voteview.com, Lewis et al.) -- not versioned upstream, so
    the newest congress can shift slightly between downloads; and the
    House member exports for the 112th-119th (section 14: thin records and
    the sitting House)
  - MIT Election Data + Science Lab, U.S. Senate 1976-2024 and President
    1976-2024 (Harvard Dataverse doi:10.7910/DVN/PEJ5QU, 10.7910/DVN/42MVDX)
  - U.S. House primary elections 1956-2010 (Pettigrew, Owen & Wanless;
    Harvard Dataverse doi:10.7910/DVN/26448)

The shipped expectation and vote shape are the scorer's own functions
(score_calculator.compute_constituent_reference, _expected_break_rate,
seat_residual, _vote_shape), so the evidence always describes the formula
that ships (section 10 of the note), and section 14 scores positions with
position_congruence_score and the weight in app/data/position_confidence.json
(written by scripts/calibrate_position_confidence.py, which section 14 reads
rather than reruns: it takes about an hour). Sections 8-9 were measured on v6.15's
method — a least-squares expectation and a percentage-point scale, peaking
one scale above the expectation — which this script keeps as a labelled
local copy (v615_expectation, v615_score) so those numbers still reproduce
and v6.16 can be compared against it like for like. Run it with the backend's environment plus the research-only
dependencies (scripts/requirements-research.txt):
    pip install -r requirements.txt -r scripts/requirements-research.txt
Run:
    python backend/scripts/research_constituent_alignment.py [--cache DIR]
"""

import argparse
import json
import pathlib
import statistics
import sys
import unicodedata
import urllib.request
import warnings

import numpy as np
import pandas as pd
import pyreadr
import rdata
import statsmodels.formula.api as smf

warnings.filterwarnings("ignore")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.contact import BOT_USER_AGENT  # noqa: E402
from app.pipeline.analyze import score_calculator  # noqa: E402
from app.pipeline.analyze.party_line_record import measure_key  # noqa: E402

RAW = "https://raw.githubusercontent.com"
DATAVERSE = "https://dataverse.harvard.edu/api/access/datafile"
VOTEVIEW = "https://voteview.com/static/data/out"
SOURCES = {
    "house.csv": f"{RAW}/MEDSL/constituency-returns/fe67c056502fc09ddb1ace2ff8f87c53233a744e/1976-2018-house.csv",
    "senate.csv": f"{RAW}/MEDSL/constituency-returns/fe67c056502fc09ddb1ace2ff8f87c53233a744e/1976-2018-senate.csv",
    "president.csv": f"{RAW}/MEDSL/constituency-returns/fe67c056502fc09ddb1ace2ff8f87c53233a744e/1976-2016-president.csv",
    "pres_by_cd.RData": f"{RAW}/cran/politicaldata/bbd681a4090790ee91b3a5d00c89572926f1b891/data/pres_results_by_cd.RData",
    "rcx.rda": f"{RAW}/uniofessex/asmcjr/52278b71a3accb8ba343d8350b802c24892e6508/data/rcx.rda",
    "hr108.rda": f"{RAW}/uniofessex/asmcjr/52278b71a3accb8ba343d8350b802c24892e6508/data/hr108.rda",
    "hr111.rda": f"{RAW}/uniofessex/asmcjr/52278b71a3accb8ba343d8350b802c24892e6508/data/hr111.rda",
    "s109.rda": f"{RAW}/cran/pscl/8220d032b199d0a9ce625c6c48a0f714e07bc432/data/s109.rda",
    "senate_1976_2024.csv": f"{DATAVERSE}/13887039?format=original",
    "president_1976_2024.csv": f"{DATAVERSE}/13887042",
    "house_primaries.dta": f"{DATAVERSE}/4271583?format=original",
    # Cooperative Election Study 2024 Common Content (doi:10.7910/DVN/X11EP6):
    # respondents' self-placed ideology by state, for section 13.
    "ces24.csv": f"{DATAVERSE}/12050325",
}
SENATES, HOUSES = range(101, 120), range(101, 112)  # the 119th Senate: party means only (no election yet)
for _c in SENATES:
    for _k in ("votes", "members", "rollcalls"):
        SOURCES[f"S{_c}_{_k}.csv"] = f"{VOTEVIEW}/{_k}/S{_c}_{_k}.csv"
for _c in HOUSES:
    for _k in ("votes", "members", "rollcalls"):
        SOURCES[f"H{_c}_{_k}.csv"] = f"{VOTEVIEW}/{_k}/H{_c}_{_k}.csv"
for _c in range(HOUSES.stop, 120):  # section 14: thin records and the sitting House, members only
    SOURCES[f"H{_c}_members.csv"] = f"{VOTEVIEW}/members/H{_c}_members.csv"
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
            # Harvard Dataverse answers Python's default User-Agent with 403.
            req = urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT})
            with urllib.request.urlopen(req) as resp:
                path.write_bytes(resp.read())
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
                         brk=against.sum() / max(cast.sum(), 1), n=int(cast.sum())))
    return pd.DataFrame(rows), int(unity.sum()), V.shape[1]


def kinked_fit(df):
    """The shipped reference: per party, brk = a + b*al + c*min(al, 0)."""
    fits = {p: smf.ols("brk ~ alignment + I(alignment.clip(upper=0))", g).fit() for p, g in df.groupby("party")}
    return pd.concat([fits[p].fittedvalues for p in fits]), fits


def loyalty_tests(m, p):
    hr = read_r(p["hr108.rda"], "hr108")
    R, n_unity, n_all = unity_breaks(hr)
    M = m[m.cong == 108].merge(R[["id", "brk", "n"]], on="id")
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

    # Is there such a thing as breaking too much? A score peaked at the
    # expectation (falling off both ways), the shipped v6.15 shape (rising
    # to the saturation point, falling past it), and the crossing-side slope
    # past saturation. From here on the expectation and scale are v6.15's
    # (v615_expectation, the method sections 8-9 measured) alongside the
    # scorer's own v6.16 ones (shipped_expectation), not the kinked_fit the
    # v6.13 sections above used.
    shipped = both_expectations(M[["id", "party", "alignment", "brk", "n"]].reset_index(drop=True))
    if shipped is None:
        print("breaking far above expectation: the scorer would not measure a reference here — skipped")
        return hr
    S = S.merge(shipped[["id", "dev", "p90", "res", "scale"]].rename(columns={"dev": "dev14"}), on="id")
    b0 = smf.ols(base, S).fit()
    dev, p90 = S.dev14, shipped.p90.iloc[0]
    S["dev_party"] = dev / dev.std()
    S["neg"] = S.dev_party.clip(upper=0)
    S["absdev"] = S.dev_party.abs()
    S["peaked"] = 100 - 100 * (dev.abs() / p90).clip(0, 1)
    S["v615"] = v615_score(dev, p90, S.n)
    S["v613"] = 50 + 50 * (dev / p90).clip(-1, 1)  # same chamber p90: like for like
    print("breaking far above expectation:")
    for k, label in (("absdev", "folded |deviation| (per SD)"), ("peaked", "peaked score (per point)"),
                     ("v613", "v6.13 score, chamber p90 (per pt)"), ("v615", "v6.15 score (per point)")):
        r = smf.ols(f"{base} + {k}", S).fit(cov_type="HC1")
        print(f"  {label:30s} {r.params[k]:7.3f} (t={r.tvalues[k]:.1f}) dR2={r.rsquared - b0.rsquared:.4f}")
    r = smf.ols(f"{base} + dev_party + I(dev_party**2)", S).fit(cov_type="HC1")
    print(f"  quadratic term {r.params['I(dev_party ** 2)']:.2f} (t={r.tvalues['I(dev_party ** 2)']:.1f})")
    knot = p90 / dev.std()
    S["pos1"], S["pos2"] = S.dev_party.clip(0, knot), (S.dev_party - knot).clip(lower=0)
    r = smf.ols(f"{base} + neg + pos1 + pos2", S).fit(cov_type="HC1")
    print(f"  crossing slope up to saturation {r.params['pos1']:.2f} (t={r.tvalues['pos1']:.1f}); "
          f"beyond it {r.params['pos2']:.2f} (t={r.tvalues['pos2']:.1f}, n={int((S.pos2 > 0).sum())})")
    print("loyal-side scale (gaps below the expectation where the score reaches 0):")
    for k in (1, 2, 4, 8):
        S["sc"] = v615_score(dev, p90, S.n, k)
        r = smf.ols(f"{base} + sc", S).fit(cov_type="HC1")
        print(f"  {k}x: {r.params['sc']:.3f}/pt (t={r.tvalues['sc']:.1f}) dR2={r.rsquared - b0.rsquared:.4f}")
    S["sc"] = 50.0 + (v615_score(dev, p90, S.n) - 50.0) * (dev >= 0)
    r = smf.ols(f"{base} + sc", S).fit(cov_type="HC1")
    print(f"  loyalty held at 50: {r.params['sc']:.3f}/pt (t={r.tvalues['sc']:.1f}) dR2={r.rsquared - b0.rsquared:.4f}")
    S = S.assign(dev=S.dev14, p90=p90)
    compare_v616(S, "y", base.split("~", 1)[1].strip(), lambda f: smf.ols(f, S).fit(cov_type="HC1"), S.party)
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


# ------------------------------------- breaking far above expectation
# Is there such a thing as breaking too much? Two audiences, because
# "what voters sent them to do" depends on which voters (Fenno 1978): the
# whole seat (general election, every Senate election 1990-2024) and the
# member's own party (House primaries 1990-2010).

def _shrink(shape, n):
    """seat_relative_vote_score's pull toward 50 below full confidence."""
    n = np.broadcast_to(np.asarray(n, float), np.shape(shape))
    return 50 + (np.asarray(shape) - 50) * np.minimum(n / score_calculator.CONSTITUENT_FULL_CONFIDENCE_VOTES, 1)


def v615_score(dev, p90, n, loyal_scale=4.0):
    """v6.15's vote component, kept here (it is no longer the scorer's) for
    sections 8-9 and the comparison: 50 at the expectation, 0 at loyal_scale
    percentage-point scales below it, 100 at one scale above, back to 0 at
    three."""
    dev = np.asarray(dev, float)
    x = dev / np.broadcast_to(np.asarray(p90, float), dev.shape)  # one per chamber-congress, or a scalar
    shape = np.where(x < 0, 50 + 50 * np.maximum(x / loyal_scale, -1),
                     np.where(x <= 1, 50 + 50 * x, np.maximum(0, 100 - 50 * (x - 1))))
    return _shrink(shape, n)


def v616_score(res, scale, n, groups, crossing_zero=None, loyal_zero=None):
    """The shipped vote component: score_calculator.seat_relative_vote_score
    on the member's residual (standard deviations per vote) at their party's
    scale, with the shipped zero points unless the sweep gives others. Thin
    records are pulled toward the typical score, as the scorer does: the
    median shape over full-confidence members of the same group (`groups`
    labels each row's congress x party), for whichever shape is being
    scored. Over the rows given — for the election tests, the incumbents
    tested rather than the whole chamber; on party-unity votes nearly every
    member has far more than 20, so the pull rarely applies at all."""
    res = np.asarray(res, float)
    scale = np.broadcast_to(np.asarray(scale, float), res.shape)
    n = np.broadcast_to(np.asarray(n, float), res.shape)
    groups = np.asarray(groups)
    shape = np.array([score_calculator._vote_shape(r, s, crossing_zero, loyal_zero) for r, s in zip(res, scale)])
    full = n >= score_calculator.CONSTITUENT_FULL_CONFIDENCE_VOTES
    typical = np.full(res.shape, 50.0)
    for g in np.unique(groups):
        in_group = groups == g
        if (in_group & full).any():
            typical[in_group] = np.median(shape[in_group & full])
    return typical + (shape - typical) * np.minimum(n / score_calculator.CONSTITUENT_FULL_CONFIDENCE_VOTES, 1)


def ascii_upper(s: pd.Series) -> pd.Series:
    return s.map(lambda x: unicodedata.normalize("NFD", str(x)).encode("ascii", "ignore").decode().upper())


def measure_units(R: pd.DataFrame) -> pd.Series:
    """rollnumber -> the measure a roll call is a stage of (section 12: a
    nominee's cloture and confirmation, a bill's cloture and passage), or the
    roll call itself when it is its own question -- the pipeline's own rule
    (party_line_record.measure_key)."""
    keys = [measure_key(q, b if isinstance(b, str) else None) for q, b in zip(R.vote_question.fillna(""), R.bill_number)]
    return pd.Series([k or f"rc{n}" for k, n in zip(keys, R.rollnumber)], index=R.rollnumber)


def voteview_breaks(p, chamber_prefix, c, centerward=False, once_per_measure=False):
    """Per-member share of party-unity votes cast against their party's
    majority, from Voteview's per-congress CSVs, plus the members. An
    Independent is scored as the party they caucus with, as the pipeline
    does (normalize_votes._infer_caucus_party): here, the party whose
    majority they vote with more often on party-unity roll calls.

    centerward (section 11): count a break only when it goes toward the
    other party — on that roll call, the member's party's defectors sit
    nearer the other party (mean NOMINATE dim1) than the party as a whole.
    A break from the flank (the defectors further from the other party
    than the party, as when a party's hardliners vote down its own bill)
    is not counted. The denominator stays every party-unity vote.

    once_per_measure (section 12): every stage of one measure counts once --
    a member broke on the measure when they broke on any of its party-unity
    roll calls, and the denominator is measures, not roll calls."""
    V = pd.read_csv(p[f"{chamber_prefix}{c}_votes.csv"])
    M = pd.read_csv(p[f"{chamber_prefix}{c}_members.csv"])
    M = M[M.chamber != "President"]
    M = M[~M.icpsr.duplicated(keep=False) & M.party_code.isin([100, 200, 328])].copy()  # drops party switchers
    V = V.merge(M[["icpsr", "party_code"]], on="icpsr")
    V["yea"], V["nay"] = V.cast_code.isin(YEA), V.cast_code.isin(NAY)
    V = V[V.yea | V.nay]
    share = V[V.party_code != 328].groupby(["rollnumber", "party_code"]).yea.mean().unstack()
    split = share[((share[100] > .5) & (share[200] < .5)) | ((share[100] < .5) & (share[200] > .5))]
    ind = V[(V.party_code == 328) & V.rollnumber.isin(split.index)]
    with_d = (ind.yea == ind.rollnumber.map(split[100] > .5)).groupby(ind.icpsr).mean()
    caucus = {i: (100 if d > .5 else 200) for i, d in with_d.items()}
    M["party_code"] = [caucus.get(i, pc) if pc == 328 else pc for i, pc in zip(M.icpsr, M.party_code)]
    M = M[M.party_code.isin([100, 200])]
    V["party_code"] = [caucus.get(i, pc) if pc == 328 else pc for i, pc in zip(V.icpsr, V.party_code)]
    V = V[V.party_code.isin([100, 200])]
    share = V.groupby(["rollnumber", "party_code"]).yea.mean().unstack()
    unity = share[((share[100] > .5) & (share[200] < .5)) | ((share[100] < .5) & (share[200] > .5))]
    V = V[V.rollnumber.isin(unity.index)]
    pmaj = V.rollnumber.map(unity[100] > .5).where(V.party_code == 100, V.rollnumber.map(unity[200] > .5))
    V["against"] = V.yea != pmaj.astype(bool)
    if centerward:
        V["dim1"] = V.icpsr.map(M.set_index("icpsr").nominate_dim1)
        party_mean = V.groupby(["rollnumber", "party_code"]).dim1.transform("mean")
        defector_mean = V[V.against].groupby(["rollnumber", "party_code"]).dim1.mean()
        dmean = pd.Series(list(zip(V.rollnumber, V.party_code)), index=V.index).map(defector_mean)
        # Democrats sit at negative dim1, Republicans positive: toward the
        # other party is up for a Democrat, down for a Republican.
        toward = np.where(V.party_code == 100, dmean > party_mean, dmean < party_mean)
        V["against"] = V.against & toward
    if once_per_measure:
        V["unit"] = V.rollnumber.map(measure_units(pd.read_csv(p[f"{chamber_prefix}{c}_rollcalls.csv"])))
        V = V.groupby(["icpsr", "unit"], as_index=False).against.any()
    M["brk"] = M.icpsr.map(V.groupby("icpsr").against.mean())
    M["n"] = M.icpsr.map(V.groupby("icpsr").size())
    M["party"] = M.party_code.map({100: "D", 200: "R"})
    M["last"] = ascii_upper(M.bioname).str.split(",").str[0].str.replace(r"[^A-Z ]", "", regex=True).str.strip().str.split().str[-1]
    return M[M.brk.notna()]


def v615_expectation(M):
    """v6.15's reference, kept for sections 8-9: per-party least squares of
    break rate on seat alignment (kinked when enough seats lean away) over
    members with a full-confidence vote count, and the 90th percentile of
    |break rate - expected| in percentage points. Each member's expected
    rate (exp), deviation (dev) and that scale (p90); None when either party
    has too few members to fit."""
    full = M[M.n >= score_calculator.CONSTITUENT_FULL_CONFIDENCE_VOTES]
    fits, devs = {}, []
    for party in ("D", "R"):
        g = full[full.party == party]
        if len(g) < score_calculator._MIN_CONSTITUENT_REFERENCE_PARTY:
            return None
        kinked = int((g.alignment < 0).sum()) >= score_calculator._MIN_OPPOSED_SEATS_FOR_KINK
        cols = [np.ones(len(g)), g.alignment.values] + ([np.minimum(g.alignment.values, 0)] if kinked else [])
        coef, *_ = np.linalg.lstsq(np.column_stack(cols), g.brk.values, rcond=None)
        fits[party] = {"a": coef[0], "b": coef[1], "b_opposed": coef[2] if kinked else 0.0}
        devs += list(np.abs(g.brk.values - [score_calculator._expected_break_rate(fits[party], a) for a in g.alignment]))
    M = M.copy()
    M["exp"] = [score_calculator._expected_break_rate(fits[p], a) for p, a in zip(M.party, M.alignment)]
    M["dev"] = M.brk - M.exp
    M["p90"] = float(np.quantile(devs, score_calculator.SATURATION_QUANTILE))
    return M


def shipped_expectation(M):
    """The shipped reference, from the scorer itself: compute_constituent_
    reference over members with a full-confidence vote count (the filter
    constituent_reference_inputs applies), then each member's expected rate
    (exp16), residual in standard deviations per vote (res) and their
    party's scale. None when the scorer would not measure one (too few
    full-confidence members per party)."""
    full = M[M.n >= score_calculator.CONSTITUENT_FULL_CONFIDENCE_VOTES]
    ref = score_calculator.compute_constituent_reference(
        list(zip(full.party, full.alignment, full.brk, full.n)))
    if ref is None:
        return None
    M = M.copy()
    M["exp16"] = [score_calculator._expected_break_rate(ref["expected"][p], a)
                  for p, a in zip(M.party, M.alignment)]
    M["res"] = [score_calculator.seat_residual(b, e, int(n)) for b, e, n in zip(M.brk, M.exp16, M.n)]
    M["scale"] = [ref["expected"][p]["scale"] for p in M.party]
    # The alternative section 10 rejects: one scale pooled across both
    # parties' full-confidence members.
    full_res = M[M.n >= score_calculator.CONSTITUENT_FULL_CONFIDENCE_VOTES].res.abs()
    M["scale_pooled"] = float(np.quantile(full_res, score_calculator.SATURATION_QUANTILE))
    return M


def both_expectations(M):
    """v6.15's columns (exp, dev, p90) and v6.16's (exp16, res, scale) on the
    same members, or None unless both methods measure a reference."""
    old, new = v615_expectation(M), shipped_expectation(M)
    if old is None or new is None:
        return None
    return old.assign(exp16=new.exp16.values, res=new.res.values, scale=new.scale.values,
                      scale_pooled=new.scale_pooled.values)


def overbreak_terms(S):
    S = S.reset_index(drop=True)
    S["gid"] = pd.factorize(S.icpsr)[0]
    S["dz"] = S.dev / S.dev.std()
    S["absdz"] = S.dz.abs()
    knot = (S.p90 / S.dev.std()).median()
    S["neg"], S["pos1"], S["pos2"] = S.dz.clip(upper=0), S.dz.clip(0, knot), (S.dz - knot).clip(lower=0)
    return S


def print_overbreak(S, y, base):
    def fit(f):
        return smf.ols(f"{y} ~ {base} + {f}", S).fit(cov_type="cluster", cov_kwds={"groups": S.gid})
    r0, r1, r2, r3 = fit("dz"), fit("absdz"), fit("dz + I(dz**2)"), fit("neg + pos1 + pos2")
    print(f"  signed deviation {r0.params['dz']:.2f} (t={r0.tvalues['dz']:.1f}); "
          f"folded |deviation| {r1.params['absdz']:.2f} (t={r1.tvalues['absdz']:.1f}); "
          f"squared term {r2.params['I(dz ** 2)']:.2f} (t={r2.tvalues['I(dz ** 2)']:.1f})")
    print(f"  loyal side {r3.params['neg']:.2f} (t={r3.tvalues['neg']:.1f}); crossing up to saturation "
          f"{r3.params['pos1']:.2f} (t={r3.tvalues['pos1']:.1f}); past saturation {r3.params['pos2']:.2f} "
          f"(t={r3.tvalues['pos2']:.1f}, n={int((S.pos2 > 0).sum())})")


def compare_v616(S, y, base, fit, groups):
    """Section 10: v6.15's score against v6.16's on the same members and the
    same outcome — and, to separate the two changes, v6.16's shape read on
    v6.15's percentage-point scale — then v6.16's zero points swept. Each
    row: coefficient per score point, t, and the R^2 the score adds to the
    controls (scale-free, so rows compare)."""
    b0 = fit(f"{y} ~ {base}")
    print(" v6.15 vs v6.16 (section 10):")
    for label, sc in (("v6.15 score (points, peak one scale above)", v615_score(S.dev, S.p90, S.n)),
                      ("v6.16 shape on v6.15's point scale", v616_score(S.dev, S.p90, S.n, groups)),
                      ("v6.16 score (SD per vote, shipped)", v616_score(S.res, S.scale, S.n, groups))):
        S["sc"] = sc
        r = fit(f"{y} ~ {base} + sc")
        print(f"  {label:44s} {r.params['sc']:7.3f}/pt (t={r.tvalues['sc']:.1f}) dR2={r.rsquared - b0.rsquared:.4f}")
    print("  v6.16 zero points (crossing / loyal, in scales):")
    for cz, lz in ((1.0, 1.0), (1.0, 3.0), (1.5, 3.0), (1.5, 6.0), (2.0, 4.0), (3.0, 6.0)):
        S["sc"] = v616_score(S.res, S.scale, S.n, groups, cz, lz)
        r = fit(f"{y} ~ {base} + sc")
        print(f"   {cz:.1f} / {lz:.1f}: {r.params['sc']:.3f}/pt (t={r.tvalues['sc']:.1f}) dR2={r.rsquared - b0.rsquared:.4f}")


def _label(centerward, once_per_measure):
    if once_per_measure:
        return "Centerward, each measure once (section 12)" if centerward else "Each measure once (section 12)"
    return "Centerward breaks only (section 11)" if centerward else "Breaking far above expectation"


def senate_general_test(p, centerward=False, once_per_measure=False):
    pres = pd.read_csv(p["president_1976_2024.csv"])
    sen = pd.read_csv(p["senate_1976_2024.csv"])
    st, nat = two_party_r(pres, ["year", "state_po"]), two_party_r(pres, "year")
    g = sen[sen.stage.str.lower() == "gen"].copy()
    g["pty"] = g.party_simplified.map({"DEMOCRAT": "D", "REPUBLICAN": "R"})
    g = g[g.pty.notna()]
    g["cl"] = last_name(ascii_upper(g.candidate))
    g["race"] = g.special.astype(str)
    tot = g.groupby(["year", "state_po", "race", "pty"]).candidatevotes.sum().unstack(fill_value=0)
    cands = g.groupby(["year", "state_po", "race", "pty"]).cl.apply(set).reset_index()
    rows, party_means = [], []
    for c in SENATES:
        yr, py = 1788 + 2 * c, max(y for y in nat.index if y <= 1786 + 2 * c)
        M = voteview_breaks(p, "S", c, centerward, once_per_measure)
        M["presR"] = [st.get((py, s), np.nan) for s in M.state_abbrev]
        M["sign"] = np.where(M.party == "R", 1.0, -1.0)
        M["alignment"] = ((M.presR - nat[py]) * 100 * M.sign / 15).clip(-1, 1)
        M["x"] = np.where(M.party == "R", M.presR, 1 - M.presR) * 100
        M = both_expectations(M)
        if M is None:
            continue
        v14, v13 = v615_score(M.dev, M.p90, M.n), 50 + 50 * (M.dev / M.p90).clip(-1, 1)
        v16, pooled = v616_score(M.res, M.scale, M.n, M.party), v616_score(M.res, M.scale_pooled, M.n, M.party)
        party_means.append({"senate": c, **{
            f"{party} {v}": round(float(x[(M.party == party).values].mean()), 1)
            for party in ("D", "R")
            for v, x in (("v6.13", v13.values), ("v6.15", v14), ("v6.16", v16), ("pooled", pooled))}})
        M["year"] = yr
        c2 = cands[cands.year == yr]
        for i, r in M.iterrows():
            hit = c2[(c2.state_po == r.state_abbrev) & (c2.pty == r.party) & c2.cl.map(lambda s, n=r["last"]: n in s)]
            if len(hit) == 1:
                t = tot.loc[(yr, r.state_abbrev, hit.race.iloc[0])]
                M.loc[i, "own"] = (t[r.party] / (t.R + t.D) * 100) if t.R > 0 and t.D > 0 else np.nan
        rows.append(M)
    S = pd.concat(rows, ignore_index=True)
    S = overbreak_terms(S[S.own.notna()])
    S["fe"] = S.year.astype(str) + S.party
    print(f"\n== {_label(centerward, once_per_measure)}, Senate general elections 1990-2024: "
          f"N = {len(S)} contested incumbents, {S.year.nunique()} elections ==")
    print_overbreak(S, "own", "x + I(x**2) + C(fe)")
    for label, d in (("1990-2008", S[S.year <= 2008]), ("2010-2024", S[S.year >= 2010])):
        print(f" {label} (N={len(d)}):")
        print_overbreak(overbreak_terms(d), "own", "x + I(x**2) + C(fe)")
    P = pd.DataFrame(party_means)
    for v in ("v6.13", "v6.15", "v6.16", "pooled"):
        P[f"gap {v}"] = P[f"D {v}"] - P[f"R {v}"]
    print(" mean vote score by party, every Senate (D minus R = gap):")
    print(P.round(1).to_string(index=False))
    print(f"  mean |gap|: v6.13 {P['gap v6.13'].abs().mean():.1f}, v6.15 {P['gap v6.15'].abs().mean():.1f}, "
          f"v6.16 {P['gap v6.16'].abs().mean():.1f} (one pooled scale instead: "
          f"{P['gap pooled'].abs().mean():.1f}, largest {P['gap pooled'].abs().max():.1f})")
    print(" loyal-side scale (gaps below the expectation where the score reaches 0):")
    b0 = smf.ols("own ~ x + I(x**2) + C(fe)", S).fit()
    for k in (1, 2, 4, 8):
        S["sc"] = v615_score(S.dev, S.p90, S.n, k)
        r = smf.ols("own ~ x + I(x**2) + C(fe) + sc", S).fit(cov_type="cluster", cov_kwds={"groups": S.gid})
        print(f"  {k}x: {r.params['sc']:.3f}/pt (t={r.tvalues['sc']:.1f}) dR2={r.rsquared - b0.rsquared:.4f}")
    compare_v616(S, "own", "x + I(x**2) + C(fe)",
                 lambda f: smf.ols(f, S).fit(cov_type="cluster", cov_kwds={"groups": S.gid}), S.fe)


def house_primary_test(p, centerward=False, once_per_measure=False):
    P = pd.read_stata(p["house_primaries.dta"])
    P = P[(P.inc == 1) & P.year.between(1990, 2010) & (P.runoff == 0)].copy()
    P["party"] = P.party.astype(int).map({0: "R", 1: "D"})
    P["last"] = ascii_upper(P.candidate.astype(str).str.split("_").str[0]).str.replace(r"[^A-Z]", "", regex=True)
    pres = pd.read_csv(p["president_1976_2024.csv"])
    P = P.merge(pres[["state", "state_po"]].drop_duplicates(), left_on=P.state.astype(str).str.upper(), right_on="state")
    nat = two_party_r(pres, "year")
    rows = []
    for c in HOUSES:
        yr = 1788 + 2 * c
        M = voteview_breaks(p, "H", c, centerward, once_per_measure)
        M["last"] = ascii_upper(M.bioname).str.split(",").str[0].str.replace(r"[^A-Z]", "", regex=True)
        M = M.merge(P[P.year == yr][["state_po", "party", "last", "candnumber", "candpct", "winner", "prez"]],
                    left_on=["state_abbrev", "party", "last"], right_on=["state_po", "party", "last"])
        M = M.drop_duplicates("icpsr", keep=False)
        py = max(y for y in nat.index if y < yr)
        M["alignment"] = ((M.prez - np.where(M.party == "R", nat[py], 1 - nat[py]) * 100) / 15).clip(-1, 1)
        M = both_expectations(M[M.prez.notna()].reset_index(drop=True))
        if M is None:
            continue
        M["fe"] = f"{yr}" + M.party
        rows.append(M)
    A = overbreak_terms(pd.concat(rows, ignore_index=True))
    A["challenged"], A["lost"], A["pshare"] = (A.candnumber > 1) * 1.0, (A.winner == 0) * 1.0, A.candpct * 100
    print(f"\n== {_label(centerward, once_per_measure)}, own-party voters: House primaries 1990-2010, "
          f"N = {len(A)} incumbents, {A.challenged.mean():.0%} challenged, {int(A.lost.sum())} lost ==")
    print(" drew a primary challenger (linear probability):")
    print_overbreak(A, "challenged", "alignment + C(fe)")
    print(" lost the primary (linear probability):")
    print_overbreak(A, "lost", "alignment + C(fe)")
    Cd = overbreak_terms(A[A.challenged == 1])
    print(f" incumbent's primary vote share, contested primaries (N={len(Cd)}):")
    print_overbreak(Cd, "pshare", "alignment + C(fe)")
    Cd["bin"] = pd.cut(Cd.dz, [-99, -1, 0, 1, 2, 99])
    print(Cd.groupby("bin", observed=True).pshare.agg(["size", "mean"]).round(1).to_string())
    for y, D in (("pshare", Cd), ("challenged", A), ("lost", A)):
        print(f" {y}:")
        compare_v616(D, y, "alignment + C(fe)",
                     lambda f, D=D: smf.ols(f, D).fit(cov_type="cluster", cov_kwds={"groups": D.gid}), D.fe)


_FIPS_STATE = {
    1: "AL", 2: "AK", 4: "AZ", 5: "AR", 6: "CA", 8: "CO", 9: "CT", 10: "DE", 12: "FL", 13: "GA", 15: "HI",
    16: "ID", 17: "IL", 18: "IN", 19: "IA", 20: "KS", 21: "KY", 22: "LA", 23: "ME", 24: "MD", 25: "MA",
    26: "MI", 27: "MN", 28: "MS", 29: "MO", 30: "MT", 31: "NE", 32: "NV", 33: "NH", 34: "NJ", 35: "NM",
    36: "NY", 37: "NC", 38: "ND", 39: "OH", 40: "OK", 41: "OR", 42: "PA", 44: "RI", 45: "SC", 46: "SD",
    47: "TN", 48: "TX", 49: "UT", 50: "VT", 51: "VA", 53: "WA", 54: "WV", 55: "WI", 56: "WY",
}


def seat_expectation_test(p):
    """Section 13: should a senator's expected position come from how the
    state's voters place themselves rather than how it votes for president?
    Leave-one-out error of each per-party fit of the newest Senate's
    Nokken-Poole positions."""
    print("\n== 13. The seat's expected position: partisan lean or voters' ideology ==")
    ces = pd.read_csv(p["ces24.csv"], usecols=["inputstate", "ideo5", "commonweight"], low_memory=False)
    ces = ces[ces.ideo5.between(1, 5) & (ces.commonweight > 0)]
    ces["state"] = ces.inputstate.map(_FIPS_STATE)
    ideology = ces.dropna(subset=["state"]).groupby("state").apply(
        lambda g: np.average(g.ideo5, weights=g.commonweight))
    m = pd.read_csv(p["S119_members.csv"])
    m = m[(m.chamber == "Senate") & m.nokken_poole_dim1.notna()].copy()
    m["party"] = m.party_code.map({100: "D", 200: "R", 328: "D"})  # Independents caucus with Democrats
    m["pvi"] = m.state_abbrev.map(score_calculator._state_pvi())
    m["ideology"] = m.state_abbrev.map(ideology)
    m = m.dropna(subset=["party", "pvi", "ideology"])
    for party, g in m.groupby("party"):
        for label, formula in (("partisan lean", "nokken_poole_dim1 ~ pvi"),
                               ("voters' ideology", "nokken_poole_dim1 ~ ideology"),
                               ("both", "nokken_poole_dim1 ~ pvi + ideology")):
            preds = []
            for i in g.index:
                fit = smf.ols(formula, g.drop(i)).fit()
                preds.append(float(fit.predict(g.loc[[i]]).iloc[0]))
            rmse = float(np.sqrt(np.mean((g.nokken_poole_dim1 - np.array(preds)) ** 2)))
            print(f"  {party} (n={len(g)}) {label:17} leave-one-out RMSE {rmse:.3f}")
    # Two senators of one party from one state share an electorate; how far
    # apart they sit is the spread no seat-level expectation can explain.
    pairs = m.groupby(["state_abbrev", "party"]).nokken_poole_dim1.agg(["count", "max", "min"])
    pairs = pairs[pairs["count"] == 2]
    print(f"  same-state, same-party pairs: {len(pairs)}, median gap {float((pairs['max'] - pairs['min']).median()):.3f}, "
          f"largest {float((pairs['max'] - pairs['min']).max()):.3f}")


# ------------------------------- 14. position congruence's score scale
# Section 1 tested the position as a continuous z-score. The shipped score
# is a transform of it: clipped at a saturation point, 50 at the seat's
# expectation. These tests put that transform, and the alternatives, in
# front of the same outcomes.

def _p90(x) -> float:
    """fetch/voteview.py's saturation: statistics.quantiles(n=10)[8]."""
    return statistics.quantiles([float(v) for v in x], n=10)[8]


def _per_party_extremity(M, pos="nokken_poole_dim1", lean="pvi"):
    """fetch/voteview.py's construct: per-party OLS of position on seat lean,
    residual signed toward the party flank."""
    M = M.copy()
    for party, g in M.groupby("party"):
        b, a = np.polyfit(g[lean], g[pos], 1)
        res = g[pos] - (a + b * g[lean])
        M.loc[g.index, "ext"] = -res if party == "D" else res
    return M


def _weights(M, chamber):
    """The shipped reliability weight (v6.27): n / (n + n0), n the member's
    scaled roll calls that Congress (none reported: 0), n0 the chamber's
    from app/data/position_confidence.json."""
    half = score_calculator._position_half_weight_votes(chamber)
    n = pd.to_numeric(M.get("nominate_number_of_votes"), errors="coerce").fillna(0)
    return np.array([score_calculator.position_confidence(int(v), half) for v in n])


def _drop_placeholders(M):
    """Voteview's 0, 0 for a member it could not scale is no estimate."""
    return M[~((M.nokken_poole_dim1 == 0) & (M.nokken_poole_dim2 == 0))]


def _congruence_scales(M):
    """Saturation per member under each design, over every member as the
    pipeline measures it: pooled across both parties (shipped) and one per
    party."""
    M = M.copy()
    M["sat_pooled"] = _p90(M.ext.abs())
    M["sat_party"] = M.party.map({p: _p90(g.ext.abs()) for p, g in M.groupby("party")})
    return M


def _linear(ext, sat, w):
    """The shipped score: score_calculator.position_congruence_score, its
    distance from 50 scaled by the reliability weight."""
    return np.array([50 + (score_calculator.position_congruence_score(e, s) - 50) * k
                     for e, s, k in zip(ext, sat, w)])


def _peaked(ext, sat, w):
    """The alternative: 100 at the expectation, falling to 0 at saturation
    either way, the vote part's shape since v6.16, read on the weighted
    extremity."""
    x = np.asarray(w, float) * np.asarray(ext, float) / np.asarray(sat, float)
    return 100.0 - 100.0 * np.minimum(np.abs(x), 1.0)


CONGRUENCE_DESIGNS = (
    ("linear, pooled scale (shipped)", _linear, "sat_pooled"),
    ("linear, per-party scale", _linear, "sat_party"),
    ("peaked, pooled scale", _peaked, "sat_pooled"),
    ("peaked, per-party scale", _peaked, "sat_party"),
)


def _compare_designs(S, y, base, groups):
    b0 = smf.ols(f"{y} ~ {base}", S).fit()
    for label, shape, sat in CONGRUENCE_DESIGNS:
        S["sc"] = shape(S.ext, S[sat], S.w)
        r = smf.ols(f"{y} ~ {base} + sc", S).fit(cov_type="cluster", cov_kwds={"groups": groups})
        print(f"  {label:32s} {r.params['sc']:7.3f}/pt (t={r.tvalues['sc']:5.1f}) dR2={r.rsquared - b0.rsquared:.4f}")


def _which_unit(S, y, base, groups):
    """Do voters respond to a NOMINATE unit of extremity (one pooled scale)
    or to a unit of the member's own party's spread (one scale per party)?
    Each form with a Republican interaction: the right unit is the one whose
    slope is the same for both parties."""
    S = S.assign(isR=(S.party == "R") * 1.0, raw=S.ext, rel=S.ext / S.sat_party)
    for k, unit in (("raw", "NOMINATE unit"), ("rel", "own party's saturation")):
        r = smf.ols(f"{y} ~ {base} + {k} + {k}:isR", S).fit(cov_type="cluster", cov_kwds={"groups": groups})
        print(f"   per {unit}: D {r.params[k]:.2f} (t={r.tvalues[k]:.1f}); R minus D "
              f"{r.params[k + ':isR']:+.2f} (t={r.tvalues[k + ':isR']:.1f}, p={r.pvalues[k + ':isR']:.2f})")


def _senate_returns(p):
    pres = pd.read_csv(p["president_1976_2024.csv"])
    sen = pd.read_csv(p["senate_1976_2024.csv"])
    g = sen[sen.stage.str.lower() == "gen"].copy()
    g["pty"] = g.party_simplified.map({"DEMOCRAT": "D", "REPUBLICAN": "R"})
    g = g[g.pty.notna()]
    g["cl"] = last_name(ascii_upper(g.candidate))
    g["race"] = g.special.astype(str)
    tot = g.groupby(["year", "state_po", "race", "pty"]).candidatevotes.sum().unstack(fill_value=0)
    cands = g.groupby(["year", "state_po", "race", "pty"]).cl.apply(set).reset_index()
    return two_party_r(pres, ["year", "state_po"]), two_party_r(pres, "year"), tot, cands


def _attach_senate_share(M, yr, tot, cands):
    """Own-party two-party share in the senator's race at the end of the
    congress, where exactly one candidate matches; NaN otherwise."""
    c2 = cands[cands.year == yr]
    M["own"] = np.nan
    for i, r in M.iterrows():
        hit = c2[(c2.state_po == r.state_abbrev) & (c2.pty == r.party) & c2.cl.map(lambda s, n=r["last"]: n in s)]
        if len(hit) == 1:
            t = tot.loc[(yr, r.state_abbrev, hit.race.iloc[0])]
            M.loc[i, "own"] = (t[r.party] / (t.R + t.D) * 100) if t.R > 0 and t.D > 0 else np.nan
    return M


def _senate_positions(p, c, st, nat):
    M = voteview_breaks(p, "S", c)
    M = _drop_placeholders(M[M.nokken_poole_dim1.notna()]).copy()
    py = max(y for y in nat.index if y <= 1786 + 2 * c)
    M["presR"] = [st.get((py, s), np.nan) for s in M.state_abbrev]
    M = M[M.presR.notna()].copy()
    M["pvi"] = (M.presR - nat[py]) * 100
    M["x"] = np.where(M.party == "R", M.presR, 1 - M.presR) * 100
    M["w"] = _weights(M, "senate")
    return M


def _sitting_effect(p):
    """v6.26 against v6.27 on the sitting Congress's Voteview exports,
    through the pipeline's own build and score: every positioned member,
    then each member whose position counts at under 90% (seat, count,
    before, after). Both sides leave out Voteview's placeholders, which
    v6.26 read as positions."""
    from app.pipeline.fetch.voteview import build_chamber_ideal_points
    for chamber, letter in (("senate", "S"), ("house", "H")):
        half = score_calculator._position_half_weight_votes(chamber)
        rows = pd.read_csv(p[f"{letter}119_members.csv"], dtype=str).fillna("").to_dict("records")
        rows = [r for r in rows if r["chamber"] != "President"]
        spvi, dpvi = score_calculator._state_pvi(), score_calculator._district_pvi()
        data, _ = build_chamber_ideal_points(rows, chamber, spvi, dpvi, half_weight_votes=half)
        changes, thin = [], []
        for bio, e in _extremities(data, rows, chamber, spvi, dpvi).items():
            n = data["votes"].get(bio, 0)
            before = score_calculator.position_congruence_score(e, data["extremity_p90"])
            after = score_calculator.position_congruence_score(e, data["extremity_p90"], n, half)
            changes.append(abs(after - before))
            if score_calculator.position_confidence(n, half) < 0.9:
                row = next(r for r in rows if r["bioguide_id"] == bio)
                seat = row["state_abbrev"] + ("" if chamber == "senate" else f"-{row['district_code']}")
                thin.append(f"{seat} {row['nominate_number_of_votes'] or 'no count'}: {before:.1f} -> {after:.1f}")
        print(f"  {chamber}: saturation {data['extremity_p90']}; mean |change| {np.mean(changes):.2f} on the "
              f"component, {0.3 * np.mean(changes):.2f} on Constituent Alignment, over {len(changes)} members")
        print("   weighted under 90%: " + "; ".join(thin))


def _extremities(data, rows, chamber, spvi, dpvi):
    from app.pipeline.fetch.voteview import PARTY_CODES, _seat_pvi_for
    out = {}
    for r in rows:
        bio, party = r["bioguide_id"], PARTY_CODES.get(int(float(r["party_code"] or 0)))
        pvi = _seat_pvi_for(r, chamber, spvi, dpvi)
        if bio in data["members"] and party in data["fit"] and pvi is not None:
            res = data["members"][bio] - (data["fit"][party]["a"] + data["fit"][party]["b"] * pvi)
            out[bio] = -res if party == "D" else res
    return out


def position_scale_test(p, m):
    print("\n== 14. Position congruence's score scale ==")
    # House generals 1994-2010: section 1's panel with each member's
    # congress-specific Nokken-Poole position and count from Voteview, per
    # party fits on seat lean and the scales over every member of the
    # congress, as the pipeline measures them; the regression on the
    # contested incumbents with section 1's controls.
    parts = []
    for c, g in m.groupby("cong"):
        vv = pd.read_csv(p[f"H{c}_members.csv"])[["icpsr", "nokken_poole_dim1", "nokken_poole_dim2",
                                                  "nominate_number_of_votes"]]
        g = _drop_placeholders(g.merge(vv, left_on="id", right_on="icpsr").dropna(subset=["nokken_poole_dim1"]))
        g = _per_party_extremity(g)
        g["w"] = _weights(g, "house")
        parts.append(_congruence_scales(g))
    S = contested(pd.concat(parts)).reset_index(drop=True)
    print(f" House generals 1994-2010 (Nokken-Poole, N={len(S)}), own-party share:")
    _compare_designs(S, "y", "x + lterms + C(fe)", S.id)
    print("  which unit of extremity voters respond to:")
    _which_unit(S, "y", "x + lterms + C(fe)", S.id)

    # Senate generals 1990-2024 on congress-specific Nokken-Poole positions.
    st, nat, tot, cands = _senate_returns(p)
    rows, balance = [], []
    for c in SENATES:
        M = _congruence_scales(_per_party_extremity(_senate_positions(p, c, st, nat)))
        if c == max(SENATES):
            print(f" the {c}th Senate's saturation: pooled {M.sat_pooled.iloc[0]:.3f}, "
                  + ", ".join(f"{q} {M[M.party == q].sat_party.iloc[0]:.3f}" for q in ("D", "R"))
                  + f"; Republicans at 0: {(_linear(M.ext, M.sat_pooled, np.ones(len(M)))[(M.party == 'R').values] <= 0).mean():.1%}")
        # The scale's effect on party balance, so unweighted: every member
        # read at full strength.
        for label, shape, sat in CONGRUENCE_DESIGNS:
            sc = shape(M.ext, M[sat], np.ones(len(M)))
            for party in ("D", "R"):
                x = sc[(M.party == party).values]
                balance.append({"chamber": "Senate", "congress": c, "design": label, "party": party,
                                "mean": x.mean(), "sd": x.std(), "at0": (x <= 0).mean(), "at100": (x >= 100).mean()})
        if c < max(SENATES):
            rows.append(_attach_senate_share(M, 1788 + 2 * c, tot, cands).assign(year=1788 + 2 * c))
    S = pd.concat(rows, ignore_index=True)
    S = S[S.own.notna()].reset_index(drop=True)
    S["fe"] = S.year.astype(str) + S.party
    print(f" Senate generals 1990-2024 (Nokken-Poole, N={len(S)}), own-party share:")
    _compare_designs(S, "own", "x + I(x**2) + C(fe)", pd.factorize(S.icpsr)[0])
    S["z"] = S.ext / S.ext.std()
    S["pos"], S["neg"] = S.z.clip(lower=0), S.z.clip(upper=0)
    r = smf.ols("own ~ x + I(x**2) + C(fe) + pos + neg", S).fit(cov_type="cluster", cov_kwds={"groups": pd.factorize(S.icpsr)[0]})
    print(f"  symmetry: flank-ward {r.params['pos']:.2f} (t={r.tvalues['pos']:.1f}), center-ward "
          f"{r.params['neg']:.2f} (t={r.tvalues['neg']:.1f}), equal slopes p={float(r.t_test('pos = neg').pvalue):.2f}")
    print("  which unit of extremity voters respond to:")
    _which_unit(S, "own", "x + I(x**2) + C(fe)", pd.factorize(S.icpsr)[0])

    # Own-party voters: House primaries 1990-2010, Nokken-Poole positions.
    P = pd.read_stata(p["house_primaries.dta"])
    P = P[(P.inc == 1) & P.year.between(1990, 2010) & (P.runoff == 0)].copy()
    P["party"] = P.party.astype(int).map({0: "R", 1: "D"})
    P["last"] = ascii_upper(P.candidate.astype(str).str.split("_").str[0]).str.replace(r"[^A-Z]", "", regex=True)
    pres = pd.read_csv(p["president_1976_2024.csv"])
    P = P.merge(pres[["state", "state_po"]].drop_duplicates(), left_on=P.state.astype(str).str.upper(), right_on="state")
    rows = []
    for c in HOUSES:
        yr = 1788 + 2 * c
        M = voteview_breaks(p, "H", c)
        M["last"] = ascii_upper(M.bioname).str.split(",").str[0].str.replace(r"[^A-Z]", "", regex=True)
        M = M.merge(P[P.year == yr][["state_po", "party", "last", "candnumber", "candpct", "winner", "prez"]],
                    left_on=["state_abbrev", "party", "last"], right_on=["state_po", "party", "last"])
        M = M.drop_duplicates("icpsr", keep=False)
        M = _drop_placeholders(M[M.prez.notna() & M.nokken_poole_dim1.notna()]).reset_index(drop=True)
        # prez is the own party's presidential share; per-party fits make
        # it equivalent to the R-signed lean.
        M = _per_party_extremity(M, lean="prez")
        M["w"] = _weights(M, "house")
        M = _congruence_scales(M)
        M["fe"] = f"{yr}" + M.party
        rows.append(M)
    A = pd.concat(rows, ignore_index=True)
    A["challenged"], A["pshare"] = (A.candnumber > 1) * 1.0, A.candpct * 100
    Cd = A[A.challenged == 1].reset_index(drop=True)
    print(f" House primaries 1990-2010, contested (Nokken-Poole, N={len(Cd)}), incumbent's primary share:")
    _compare_designs(Cd, "pshare", "prez + C(fe)", pd.factorize(Cd.icpsr)[0])
    print(f" House primaries 1990-2010 (N={len(A)}), drew a challenger:")
    _compare_designs(A, "challenged", "prez + C(fe)", pd.factorize(A.icpsr)[0])

    # Party balance: every Senate, each design.
    B = pd.DataFrame(balance)
    print(" party balance, every Senate 101st-119th, unweighted (mean over Senates):")
    for label, _, _ in CONGRUENCE_DESIGNS:
        d = B[B.design == label]
        w = d.pivot(index="congress", columns="party", values=["mean", "at0", "at100", "sd"])
        print(f"  {label:32s} mean D {w['mean'].D.mean():5.1f} R {w['mean'].R.mean():5.1f} | "
              f"at 0: D {w['at0'].D.mean():5.1%} R {w['at0'].R.mean():5.1%} | "
              f"at 100: D {w['at100'].D.mean():5.1%} R {w['at100'].R.mean():5.1%} | "
              f"SD D {w['sd'].D.mean():4.1f} R {w['sd'].R.mean():4.1f} | "
              f"worst-Senate |at-0 gap| {(w['at0'].D - w['at0'].R).abs().max():.1%}")
    w = B[B.design == CONGRUENCE_DESIGNS[0][0]].pivot(index="congress", columns="party", values="at0")
    print("  shipped, share at 0 by Senate (R minus D): "
          + ", ".join(f"{c}:{(w.R[c] - w.D[c]) * 100:+.0f}" for c in w.index))

    # Thin records. First the proxy v6.27's first draft used (a member's
    # Nokken-Poole position against their career DW-NOMINATE one), and why
    # it can't calibrate anything: its least-squares fit of
    # gap^2 = drift + k / votes swings with which few thin members are in
    # it. Then the shipped measurement, from the calibration's own file.
    from calibrate_position_confidence import CONGRESSES
    T = []
    for prefix in ("S", "H"):
        for c in CONGRESSES:
            mm = pd.read_csv(p[f"{prefix}{c}_members.csv"])
            mm = mm[(mm.chamber != "President") & mm.nokken_poole_dim1.notna() & mm.nominate_dim1.notna()
                    & (mm.nominate_number_of_votes > 0)]
            T.append(mm.assign(ch=prefix, c=c, placeholder=(mm.nokken_poole_dim1 == 0) & (mm.nokken_poole_dim2 == 0)))
    T = pd.concat(T, ignore_index=True)
    T["gap2"] = (T.nokken_poole_dim1 - T.nominate_dim1) ** 2

    def k_over_drift(d):
        b, a = np.polyfit(1 / d.nominate_number_of_votes, d.gap2, 1)
        return b / a
    real = T[~T.placeholder]
    print(f" thin records, the career-gap proxy ({len(T)} member-congresses, {int(T.placeholder.sum())} placeholders):")
    print(f"  k/drift: all {k_over_drift(T):.0f}; placeholders out {k_over_drift(real):.0f}; Senate "
          f"{k_over_drift(real[real.ch == 'S']):.0f}; House {k_over_drift(real[real.ch == 'H']):.0f}; "
          f"5+ votes {k_over_drift(real[real.nominate_number_of_votes >= 5]):.0f}")
    rng = np.random.default_rng(0)
    ids = real.icpsr.unique()
    boot = []
    for _ in range(300):
        pick = pd.Series(rng.choice(ids, len(ids)))
        boot.append(k_over_drift(real.merge(pick.rename("icpsr").to_frame(), on="icpsr")))
    print("  member-clustered bootstrap, placeholders out: 5th/50th/95th percentile "
          + "/".join(f"{q:.0f}" for q in np.percentile(boot, [5, 50, 95])))
    real = real.assign(gap=np.sqrt(real.gap2),
                       bin=pd.cut(real.nominate_number_of_votes, [0, 25, 50, 75, 100, 150, 200, 300, 500, 10_000]))
    print(real.groupby("bin", observed=True).gap.agg(["size", "median"]).round(3).to_string())
    shipped = json.loads((pathlib.Path(__file__).resolve().parents[1] / "app" / "data"
                          / "position_confidence.json").read_text())
    print(" thin records, shipped (scripts/calibrate_position_confidence.py):")
    for chamber, c in shipped["chambers"].items():
        print(f"  {chamber}: weight by votes {c['weights']}, median |error| {c['median_abs_error']}, "
              f"own fit {c['half_weight_votes']}")
    print(" v6.26 -> v6.27 on the sitting Congress:")
    _sitting_effect(p)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", default=".research-cache/constituent-alignment", type=pathlib.Path)
    paths = fetch(ap.parse_args().cache)
    m = build_panel(paths)
    position_tests(m)
    hr = loyalty_tests(m, paths)
    nokken_poole_test(m, hr)
    senate_test(paths)
    senate_general_test(paths)
    house_primary_test(paths)
    # Section 11: the same tests, counting only breaks toward the other party.
    senate_general_test(paths, centerward=True)
    house_primary_test(paths, centerward=True)
    # Section 12: every stage of one measure (cloture, then confirmation) counted once.
    senate_general_test(paths, centerward=True, once_per_measure=True)
    house_primary_test(paths, centerward=True, once_per_measure=True)
    # Section 13: the seat's expected position from voters' own ideology.
    seat_expectation_test(paths)
    # Section 14: position congruence's score scale, shape and thin records.
    position_scale_test(paths, m)


if __name__ == "__main__":
    main()
