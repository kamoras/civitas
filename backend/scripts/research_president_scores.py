"""Reproduce the evidence behind presidential Effectiveness's president-v5
design, and regenerate the bundled fallback stats it needs before a
deployment's first run.

Every number in docs/research/president-scores.md comes from this script:

  1. Term GDP growth for every presidency 1817-2016 through this repo's own
     historical_gdp.compute_term_gdp_growth, on the Maddison Project's US
     real GDP (GDP per capita x population; R package `maddison`, pinned).
     Compares the spread before and after 1947, and what the old hand-set
     curve (25 + g/5 x 55) did to each era.
  2. The same term growth for 13 other advanced economies over the same
     years: how much of a US president's term growth is shared with peers.
  3. Jobs per year for presidencies since 1945 from BLS household-survey
     employment (datasets/employment-us), absolute vs percent growth
     against era.

With --write-fallback it updates the gdp_growth_prewar / gdp_growth_postwar /
jobs_per_year entries of app/data/president_reference.json. The pipeline
measures all of these from its own sources every run and those take
precedence; the bundled values only serve the API before the first run.

Research-only dependencies, not in requirements.txt:
    pip install pandas scipy rdata
Run from backend/:
    PYTHONPATH=. python scripts/research_president_scores.py [--cache DIR] [--write-fallback]
"""

import argparse
import datetime
import json
import pathlib
import statistics
import urllib.request
import warnings

import numpy as np
import pandas as pd
import rdata
from scipy import stats

from app.pipeline.analyze.president_scorer import _GDP_REGIME_SPLIT_YEAR
from app.pipeline.fetch.historical_gdp import compute_term_gdp_growth

warnings.filterwarnings("ignore")

MADDISON_URL = ("https://raw.githubusercontent.com/cran/maddison/"
                "{commit}/data/maddison.rda")
EMPLOYMENT_URL = "https://raw.githubusercontent.com/datasets/employment-us/{commit}/data/aat1.csv"
PEERS = ["GBR", "FRA", "DEU", "NLD", "CAN", "AUS", "SWE", "ITA", "BEL", "DNK", "CHE", "NOR", "JPN"]
REFERENCE = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "president_reference.json"
# Presidencies by start/end year (public record). Presidencies sharing a
# term are combined where the annual series can't separate them.
TERMS = [
    ("Monroe", 1817, 1825), ("JQ Adams", 1825, 1829), ("Jackson", 1829, 1837), ("Van Buren", 1837, 1841),
    ("Tyler", 1841, 1845), ("Polk", 1845, 1849), ("Taylor/Fillmore", 1849, 1853), ("Pierce", 1853, 1857),
    ("Buchanan", 1857, 1861), ("Lincoln", 1861, 1865), ("A. Johnson", 1865, 1869), ("Grant", 1869, 1877),
    ("Hayes", 1877, 1881), ("Garfield/Arthur", 1881, 1885), ("Cleveland I", 1885, 1889),
    ("B. Harrison", 1889, 1893), ("Cleveland II", 1893, 1897), ("McKinley", 1897, 1901),
    ("T. Roosevelt", 1901, 1909), ("Taft", 1909, 1913), ("Wilson", 1913, 1921),
    ("Harding/Coolidge", 1921, 1929), ("Hoover", 1929, 1933), ("F. Roosevelt", 1933, 1945),
    ("Truman", 1945, 1953), ("Eisenhower", 1953, 1961), ("Kennedy/Johnson", 1961, 1969),
    ("Nixon/Ford", 1969, 1977), ("Carter", 1977, 1981), ("Reagan", 1981, 1989), ("G.H.W. Bush", 1989, 1993),
    ("Clinton", 1993, 2001), ("G.W. Bush", 2001, 2009), ("Obama", 2009, 2017),
    ("Trump I", 2017, 2021), ("Biden", 2021, 2025),
]


def fetch(url: str, path: pathlib.Path) -> pathlib.Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, path)
    return path


def maddison_gdp(cache: pathlib.Path) -> dict[str, dict[int, float]]:
    path = fetch(MADDISON_URL.format(commit="d70a9648179fc0e08f95b481802a605e04d42fe8"), cache / "maddison.rda")
    m = rdata.conversion.convert(rdata.parser.parse_file(path))["maddison"]
    out = {}
    for iso in ["USA", *PEERS]:
        d = m[(m.iso3c == iso) & m.rgdpnapc.notna() & m["pop"].notna()]
        out[iso] = {int(y): float(g * p) for y, g, p in zip(d.year, d.rgdpnapc, d["pop"])}
    return out


def gdp_tests(gdp):
    rows = []
    for name, start, end in TERMS:
        g = compute_term_gdp_growth(gdp["USA"], start, end)
        if g is None:
            continue
        peers = [compute_term_gdp_growth(gdp[c], start, end) for c in PEERS]
        peers = [p for p in peers if p is not None]
        rows.append(dict(president=name, start=start, growth=g,
                         old_score=max(0.0, min(100.0, 25 + g / 5 * 55)),
                         peer_median=np.median(peers) if len(peers) >= 4 else np.nan))
    d = pd.DataFrame(rows)
    print("== Term real-GDP growth (first year excluded), Maddison Project ==")
    print(d.round(2).to_string(index=False))
    fallback = {}
    for key, part in (("gdp_growth_prewar", d[d.start < _GDP_REGIME_SPLIT_YEAR]),
                      ("gdp_growth_postwar", d[d.start >= _GDP_REGIME_SPLIT_YEAR])):
        at_bound = ((part.old_score <= 0) | (part.old_score >= 100)).mean()
        print(f"\n{key}: mean {part.growth.mean():.2f}, SD {part.growth.std():.2f} (n={len(part)}); "
              f"old curve pinned {at_bound:.0%} at 0 or 100")
        fallback[key] = {"mean": round(statistics.mean(part.growth), 4),
                         "stdev": round(statistics.stdev(part.growth), 4), "n": len(part)}
    p = d.dropna(subset=["peer_median"])
    for label, part in (("all", p), (f"before {_GDP_REGIME_SPLIT_YEAR}", p[p.start < _GDP_REGIME_SPLIT_YEAR]),
                        (f"{_GDP_REGIME_SPLIT_YEAR} on", p[p.start >= _GDP_REGIME_SPLIT_YEAR])):
        r, pv = stats.pearsonr(part.peer_median, part.growth)
        print(f"US term growth vs median of {len(PEERS)} peer economies, {label}: r={r:.2f}, "
              f"shared variance R^2={r * r:.2f} (n={len(part)}, p={pv:.1e})")
    return fallback


def jobs_tests(cache: pathlib.Path):
    path = fetch(EMPLOYMENT_URL.format(commit="e3a2ae5bb5418494538935d946291a8c5edc8d52"), cache / "aat1.csv")
    employed = pd.read_csv(path).set_index("year").employed_total
    rows = []
    for name, start, end in TERMS:
        if start < 1945:
            continue
        base, last = start + 1, min(end, int(employed.index.max()))
        if base in employed and last in employed and last > base:
            years = last - base
            jobs = (employed[last] - employed[base]) / 1000
            rows.append(dict(president=name, start=start, jobs_per_year_m=jobs / years,
                             pct_per_year=((employed[last] / employed[base]) ** (1 / years) - 1) * 100))
    d = pd.DataFrame(rows)
    print("\n== Jobs per year since 1945 (BLS household survey, from the term's second year) ==")
    print(d.round(2).to_string(index=False))
    for col in ("jobs_per_year_m", "pct_per_year"):
        rho, pv = stats.spearmanr(d.start, d[col])
        print(f"Spearman(term start, {col}) = {rho:.2f} (p={pv:.2f})")
    return {"jobs_per_year": {"mean": round(statistics.mean(d.jobs_per_year_m), 4),
                              "stdev": round(statistics.stdev(d.jobs_per_year_m), 4), "n": len(d)}}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", default=".research-cache/presidents", type=pathlib.Path)
    ap.add_argument("--write-fallback", action="store_true")
    args = ap.parse_args()
    fallback = {**gdp_tests(maddison_gdp(args.cache)), **jobs_tests(args.cache)}
    if args.write_fallback:
        ref = json.loads(REFERENCE.read_text())
        ref["presidents"].update(fallback)
        ref["_as_of"] = datetime.date.today().isoformat()
        ref["_source"] += (
            " gdp_growth_prewar/postwar: Maddison Project US real GDP through "
            "historical_gdp.compute_term_gdp_growth, presidencies 1817-2016 (combined where "
            "the annual series can't separate them); jobs_per_year: BLS household-survey "
            "employment (datasets/employment-us), presidencies since 1945. Both are proxies "
            "for the pipeline's own MeasuringWorth / BLS payroll series, which replace them "
            "on the first run; written by backend/scripts/research_president_scores.py. "
            "rulemaking_finalized_pct has no fallback: Agency Alignment is omitted until "
            "the first run measures it."
        )
        REFERENCE.write_text(json.dumps(ref, indent=1, sort_keys=True) + "\n")
        print(f"\nwrote {REFERENCE}")


if __name__ == "__main__":
    main()
