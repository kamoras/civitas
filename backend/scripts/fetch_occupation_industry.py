"""Generate app/data/occupation_industry.json.gz: the industry each occupation
works in, for classifying campaign contributions by the donor's occupation.

Itemized FEC contributions name the donor's occupation and employer, and the
FEC aggregates both per committee. The occupation totals cover far more of a
campaign's money in a few requests than the employer totals do (2026-10-01,
a large 2022 Senate campaign: one page of occupations $12.9M of job-bearing
money, one page of employers $1.6M), but an occupation names a job, not an
industry. This file says which industry a job is in, from data:

- The Census Bureau's American Community Survey, 2023 1-year Public Use
  Microdata Sample (persons): every employed respondent's occupation (SOC
  code, SOCP) and industry (NAICS code, NAICSP), weighted (PWGTP). An
  occupation is assigned an industry only when a MAJORITY of its workers are
  in that one industry. Lawyers are in legal services; chief executives are
  spread across every industry, so "CEO" is assigned none. Not a word list:
  the workforce says so.
- Each Census industry code is put into a Civitas industry by NAICS_INDUSTRY
  below, a crosswalk between two published taxonomies (NAICS, the federal
  industry classification the Census codes are, and the Civitas industries,
  which follow the Center for Responsive Politics' sectors). It is structured
  metadata, tier 1 of the classification strategy like the FEC's committee
  type codes, not a reading of names. The embedding classifier was tried on
  the Census industry labels first and measured unfit for it (2026-10-01):
  "Computer Systems Design And Related Services" came out HEALTHCARE, 13
  occupations landed in GUNS, and 51 of 265 labels got no industry.
- O*NET 29.3 (US Department of Labor, CC BY 4.0) titles, alternate titles and
  sample reported titles, each with its O*NET-SOC code, give the spellings an
  occupation goes by ("CPA", "Realtor", "RN"), so a donor's occupation string
  is matched exactly where it can be.

A string that matches no title is not given an industry. Matching it to the
nearest occupation label by embedding was tried and measured unfit
(2026-10-01, 4,000 O*NET alternate titles whose occupations are known):
the nearest label's industry was right 44-50% of the time overall and 71%
even among the 184 closest matches, and it sent "HOMEMAKER" to model makers
(manufacturing) and "NOT EMPLOYED" to sales supervisors. Unattributed money
is counted as such, never guessed into an industry.

Run in the backend image:
    python scripts/fetch_occupation_industry.py --download --work /tmp/occ
Rerun when a new ACS release or O*NET database comes out; commit the file.
"""

import argparse
import csv
import gzip
import io
import json
import pathlib
import re
import sys
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.contact import BOT_USER_AGENT  # noqa: E402
from app.pipeline.transform.occupation_industry import normalize_title  # noqa: E402

PUMS_YEAR = 2023
SOURCES = {
    "csv_pus.zip": f"https://www2.census.gov/programs-surveys/acs/data/pums/{PUMS_YEAR}/1-Year/csv_pus.zip",
    "pumsdict.csv": (
        "https://www2.census.gov/programs-surveys/acs/tech_docs/pums/data_dict/"
        f"PUMS_Data_Dictionary_{PUMS_YEAR}.csv"
    ),
    "onet.zip": "https://www.onetcenter.org/dl_files/database/db_29_3_text.zip",
}
# ESR, employment status recode: civilian employed at work / with a job but
# not at work, Armed Forces at work / with a job but not at work.
EMPLOYED = {"1", "2", "4", "5"}
# An occupation is assigned the industry most of its workers are in.
MAJORITY = 0.5
OUT = pathlib.Path(__file__).resolve().parents[1] / "app/data/occupation_industry.json.gz"

# NAICS code (or its leading digits) -> Civitas industry; None where NAICS
# names an industry Civitas has no category for (wholesale trade,
# accommodation and food, government, social assistance, management
# consulting). The longest matching key wins, and a full Census code (with
# its letters) outranks any prefix. Groupings follow CRP's sectors where
# Civitas mirrors them: accounting with finance, architecture and
# engineering with construction, food processing and beverages with
# agribusiness, coal and metal mining with energy, refining, pipelines and
# fuel retail with oil and gas.
NAICS_INDUSTRY: dict[str, str | None] = {
    "11": "AGRIBUSINESS",
    "211": "OIL_GAS", "213": "OIL_GAS", "324": "OIL_GAS", "486": "OIL_GAS", "4247": "OIL_GAS",
    "4571": "OIL_GAS", "4572": "OIL_GAS",
    "212": "ENERGY",
    "22": None, "2211": "ENERGY", "2212": "ENERGY", "221M": "ENERGY", "221MP": "ENERGY",
    "23": "CONSTRUCTION", "5413": "CONSTRUCTION",
    "31": "MANUFACTURING", "32": "MANUFACTURING", "33": "MANUFACTURING",
    "311": "AGRIBUSINESS", "3121": "AGRIBUSINESS",
    "3122": "TOBACCO",
    "3254": "PHARMA", "3391": "PHARMA", "424M": "PHARMA",
    "33299": "GUNS",
    "33641": "DEFENSE",
    "3341": "TECH", "334M2": "TECH",
    "334M1": "TELECOM",
    "42": None, "4244": "AGRIBUSINESS", "4245": "AGRIBUSINESS", "4248": "AGRIBUSINESS", "42491": "AGRIBUSINESS",
    "44": "RETAIL", "45": "RETAIL",
    "48": "TRANSPORT", "49": "TRANSPORT", "491": None,
    "51": None, "512": "MEDIA", "5131": "MEDIA", "516": "MEDIA",
    "5132": "TECH", "518": "TECH", "51929": "TECH",
    "517": "TELECOM",
    "52": "FINANCE",
    "524": "INSURANCE",
    "53": None, "531": "REAL_ESTATE",
    "54": None, "5411": "LAWYERS", "5412": "FINANCE", "5415": "TECH", "5418": "MEDIA",
    "55": None, "56": None,
    "61": "EDUCATION",
    "62": "HEALTHCARE", "624": None,
    "71": None, "711": "MEDIA", "7111": "MEDIA", "7112": "MEDIA", "7115": "MEDIA",
    "72": None,
    "81": None, "81393": "LABOR_UNIONS",
    "92": None,
    "999920": None,
}


def industry_of_code(code: str) -> str | None:
    """The Civitas industry of a Census NAICS code: an exact key first, then
    the longest key the code starts with."""
    if code in NAICS_INDUSTRY:
        return NAICS_INDUSTRY[code]
    best = max((k for k in NAICS_INDUSTRY if code.startswith(k)), key=len, default=None)
    return NAICS_INDUSTRY[best] if best is not None else None


def fetch(work: pathlib.Path) -> None:
    work.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        path = work / name
        if not path.exists():
            print(f"fetching {url}")
            req = urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT})
            with urllib.request.urlopen(req) as resp, path.open("wb") as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)


def labels(dictionary: pathlib.Path) -> tuple[dict[str, str], dict[str, str]]:
    """SOCP and NAICSP code -> label, with the label's "MGR-" style prefix
    (the Census occupation or industry group) removed."""
    socp, naicsp = {}, {}
    with dictionary.open(newline="", encoding="latin-1") as f:
        for row in csv.reader(f):
            if len(row) >= 7 and row[0] == "VAL" and row[1] in ("SOCP", "NAICSP") and row[4].strip("b"):
                label = re.sub(r"^[A-Z]{3}-", "", row[6]).strip()
                (socp if row[1] == "SOCP" else naicsp)[row[4]] = label
    return socp, naicsp


def crosstab(pums: pathlib.Path) -> dict[str, dict[str, int]]:
    """Weighted employed persons, SOCP -> NAICSP -> weight."""
    table: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    with zipfile.ZipFile(pums) as z:
        for name in sorted(n for n in z.namelist() if n.endswith(".csv")):
            with z.open(name) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding="latin-1", newline=""))
                header = next(reader)
                i_socp, i_naics, i_w, i_esr = (header.index(c) for c in ("SOCP", "NAICSP", "PWGTP", "ESR"))
                for row in reader:
                    if row[i_esr] in EMPLOYED and row[i_socp] and row[i_naics]:
                        table[row[i_socp]][row[i_naics]] += int(row[i_w])
            print(f"  read {name}")
    return table


def industry_of_naics(naicsp: dict[str, str]) -> dict[str, str | None]:
    return {code: industry_of_code(code) for code in naicsp}


def occupations(table, socp_labels, naics_industry) -> dict[str, dict]:
    out = {}
    for socp, row in table.items():
        total = sum(row.values())
        by_industry: dict[str | None, int] = defaultdict(int)
        for naics, weight in row.items():
            by_industry[naics_industry.get(naics)] += weight
        shares = sorted(((ind, w / total) for ind, w in by_industry.items()), key=lambda x: -x[1])
        top_industry, top_share = next(((i, s) for i, s in shares if i is not None), (None, 0.0))
        out[socp] = {
            "title": socp_labels.get(socp, socp),
            "industry": top_industry if top_share > MAJORITY else None,
            "share": round(top_share, 3),
            "workers": total,
        }
    return out


def socp_for(soc6: str, socp_codes: set[str]) -> str | None:
    """The PUMS occupation an O*NET SOC code falls in: the same code, the
    PUMS group whose X positions it fills ("231011" -> "2310XX"), or the
    group a trailing-zero code stands for ("419022" -> "419020", "251011"
    -> "251000"), the most specific one."""
    if soc6 in socp_codes:
        return soc6
    for code in socp_codes:
        if "X" in code and all(c == "X" or c == s for c, s in zip(code, soc6)):
            return code
    groups = [c for c in socp_codes if c.endswith("0") and "X" not in c and soc6.startswith(c.rstrip("0"))]
    return max(groups, key=lambda c: len(c.rstrip("0")), default=None)


def titles(onet: pathlib.Path, occ: dict[str, dict]) -> dict[str, str]:
    """Normalized title -> SOCP, from O*NET's titles."""
    codes = set(occ)
    candidates: dict[str, set[str]] = defaultdict(set)
    files = {
        "Occupation Data.txt": ("O*NET-SOC Code", ["Title"]),
        "Alternate Titles.txt": ("O*NET-SOC Code", ["Alternate Title", "Short Title"]),
        "Sample of Reported Titles.txt": ("O*NET-SOC Code", ["Reported Job Title"]),
    }
    with zipfile.ZipFile(onet) as z:
        for fname, (code_col, title_cols) in files.items():
            member = next(n for n in z.namelist() if n.endswith(fname))
            reader = csv.DictReader(io.TextIOWrapper(z.open(member), encoding="utf-8"), delimiter="\t")
            for row in reader:
                socp = socp_for(row[code_col].replace("-", "")[:6], codes)
                if socp is None:
                    continue
                for col in title_cols:
                    if row.get(col) and row[col] != "n/a":
                        candidates[normalize_title(row[col])].add(socp)
    mapping = {}
    for title, socps in candidates.items():
        # A title several occupations go by ("Professor", "Engineer") belongs
        # to the industry most of the people in any of them work in, pooled
        # by workers, when that is still a majority; it maps to the largest
        # of its occupations in that industry. No majority: left out.
        by_industry: dict[str | None, int] = defaultdict(int)
        for socp in socps:
            by_industry[occ[socp]["industry"]] += occ[socp]["workers"]
        industry, weight = max(by_industry.items(), key=lambda x: x[1])
        if weight / sum(by_industry.values()) > MAJORITY:
            mapping[title] = max(
                (s for s in socps if occ[s]["industry"] == industry), key=lambda s: occ[s]["workers"],
            )
    return mapping


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", type=pathlib.Path, default=pathlib.Path(".research-cache/occupation-industry"))
    ap.add_argument("--download", action="store_true")
    args = ap.parse_args()
    if args.download:
        fetch(args.work)
    socp_labels, naicsp_labels = labels(args.work / "pumsdict.csv")
    print(f"{len(socp_labels)} occupations, {len(naicsp_labels)} industries in the dictionary")
    # The crosstab takes the longest (2.4 GB of records); kept beside the
    # downloads so a rerun reads it back.
    cached = args.work / f"crosstab_{PUMS_YEAR}.json"
    if cached.exists():
        table = json.loads(cached.read_text())
    else:
        table = crosstab(args.work / "csv_pus.zip")
        cached.write_text(json.dumps(table))
    naics_industry = industry_of_naics(naicsp_labels)
    occ = occupations(table, socp_labels, naics_industry)
    mapping = titles(args.work / "onet.zip", occ)
    assigned = sum(1 for o in occ.values() if o["industry"])
    workers = sum(o["workers"] for o in occ.values())
    print(f"{assigned}/{len(occ)} occupations assigned an industry "
          f"({sum(o['workers'] for o in occ.values() if o['industry']) / workers:.0%} of workers); "
          f"{len(mapping)} titles")
    data = {
        "_source": (
            f"Census Bureau ACS {PUMS_YEAR} 1-year PUMS persons (SOCP x NAICSP, PWGTP, employed), "
            "O*NET 29.3 titles (US Department of Labor, CC BY 4.0); Census industry labels classified "
            "by transform/industry_classifier. Regenerate with backend/scripts/fetch_occupation_industry.py."
        ),
        "_generated": date.today().isoformat(),
        "majority": MAJORITY,
        "naics_industry": {f"{c} {naicsp_labels[c]}": i for c, i in sorted(naics_industry.items())},
        "occupations": dict(sorted(occ.items())),
        "titles": dict(sorted(mapping.items())),
    }
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
