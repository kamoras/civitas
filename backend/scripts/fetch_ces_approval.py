"""Regenerate app/data/ces_member_approval.json: how each member's own
constituents rated them, split by the respondent's party, from the
Cooperative Election Study (CES) Common Content.

Every CES respondent is asked to rate their own House member and both
senators, named for them ("Do you approve of the way each is doing their
job?", CC24_312f/g/h for the House member and senators 1 and 2), and states
their party identification (pid3). That is the data the About page named as
missing for Constituent Alignment: approval of a member among their own
state's or district's Democrats, Republicans and independents, which the
roll-call record cannot supply. Informational, not scored.

Estimates:
  - Approval share among respondents who gave an opinion (strongly or
    somewhat approve, over those two plus the two disapprove options; "not
    sure" is counted separately), weighted by the survey's `commonweight`.
  - `n` is respondents; `n_eff` the Kish effective sample size of the
    weights, (sum w)^2 / sum w^2, which is what the sampling error follows.
  - House districts hold ~100 respondents, and a member's out-party
    respondents can number a dozen. Each (chamber, member's party,
    respondent's party) cell is therefore shrunk toward that cell's typical
    member by beta-binomial empirical Bayes: the prior mean is the n_eff-
    weighted mean approval, and its strength k (pseudo-respondents) comes
    from the method of moments, k = mu(1-mu)/tau^2 - 1, where tau^2 is the
    spread of true approval between members: the observed spread less the
    average sampling variance. The shrunk value is
    (rate*n_eff + mu*k) / (n_eff + k). Both the raw rate and the shrunk one
    are written, with mu and k, so any figure can be re-derived.

Members are matched to the survey by the respondent's state, district
(cdid118, the district the member served when the survey was fielded) and
the name the survey showed them (CurrentHouseName / CurrentSen1Name /
CurrentSen2Name); app/services/constituent_survey.py joins that to a member
record.

Data: CES 2024 Common Content (Harvard Dataverse doi:10.7910/DVN/X11EP6,
CC0), pre-election wave, fielded October-November 2024. Rerun when a new
Common Content is released and point CES_DATAFILE at it.

Run from the repo (network required unless --csv is given):
    cd backend && python3 scripts/fetch_ces_approval.py [--csv path/to/CCES24.csv]
"""

import argparse
import csv
import json
import pathlib
import statistics
import sys
import tempfile
import urllib.request
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fetch_state_leg_crosswalk import _FIPS  # noqa: E402  (one state table)

CES_DOI = "doi:10.7910/DVN/X11EP6"
CES_DATAFILE = 12050325  # CCES24_Common_OUTPUT_vv_topost_final.csv
CES_URL = f"https://dataverse.harvard.edu/api/access/datafile/{CES_DATAFILE}"
SURVEY = "CES 2024 Common Content (pre-election wave, Oct-Nov 2024)"
OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "ces_member_approval.json"

STATE_OF_FIPS = {int(fips): state for state, fips in _FIPS.items()}

# (approval item, name shown, party shown, chamber) per rated member.
ITEMS = (
    ("CC24_312f", "CurrentHouseName", "CurrentHouseParty", "house"),
    ("CC24_312g", "CurrentSen1Name", "CurrentSen1Party", "senate"),
    ("CC24_312h", "CurrentSen2Name", "CurrentSen2Party", "senate"),
)
# The questionnaire's codes (CES 2024 guide): 1-2 approve, 3-4 disapprove,
# 5 not sure; pid3 1 Democrat, 2 Republican, anything else independent/other.
APPROVE, DISAPPROVE, NOT_SURE = {"1", "2"}, {"3", "4"}, {"5"}
RESPONDENT_PARTY = {"1": "D", "2": "R"}
MEMBER_PARTY = {"Democratic": "D", "Republican": "R"}


def _download() -> pathlib.Path:
    path = pathlib.Path(tempfile.gettempdir()) / "ces_common_content.csv"
    if not path.exists():
        print(f"downloading {CES_URL} ...")
        urllib.request.urlretrieve(CES_URL, path)
    return path


def tally(rows) -> dict:
    """{(state, chamber, district, name, member_party): {respondent_party:
    [sum w approve, sum w opinion, sum w^2 opinion, n opinion, n not sure]}}"""
    cells: dict = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0, 0, 0]))
    for row in rows:
        state = STATE_OF_FIPS.get(int(row["inputstate"])) if row.get("inputstate", "").isdigit() else None
        try:
            w = float(row.get("commonweight") or 0)
        except ValueError:
            continue
        if not state or w <= 0:
            continue
        rparty = RESPONDENT_PARTY.get(row.get("pid3", ""), "I")
        for item, name_col, party_col, chamber in ITEMS:
            name = (row.get(name_col) or "").strip()
            answer = row.get(item, "")
            if not name or answer not in APPROVE | DISAPPROVE | NOT_SURE:
                continue
            district = row.get("cdid118", "") if chamber == "house" else ""
            key = (state, chamber, district, name, MEMBER_PARTY.get(row.get(party_col, ""), "I"))
            acc = cells[key][rparty]
            if answer in NOT_SURE:
                acc[4] += 1
                continue
            acc[0] += w if answer in APPROVE else 0.0
            acc[1] += w
            acc[2] += w * w
            acc[3] += 1
    return cells


def _estimate(acc: list) -> dict | None:
    approve_w, opinion_w, sq_w, n, not_sure = acc
    if n == 0 or opinion_w <= 0:
        return None
    return {
        "rate": approve_w / opinion_w,
        "n": n,
        "n_eff": opinion_w * opinion_w / sq_w,
        "not_sure": not_sure,
    }


def priors(members: list[dict]) -> dict:
    """Beta-binomial method-of-moments prior per (chamber, member party,
    respondent party): mean mu and strength k in pseudo-respondents."""
    groups: dict = defaultdict(list)
    for m in members:
        for rparty, est in m["by_party"].items():
            groups[(m["chamber"], m["member_party"], rparty)].append(est)
            # Chamber-wide, whatever the member's party: the prior for a
            # member whose own party has too few members for one (the
            # Senate's independents). Wide, so it shrinks little.
            groups[(m["chamber"], "*", rparty)].append(est)
    out = {}
    for (chamber, mparty, rparty), ests in groups.items():
        if len(ests) < 10:
            continue
        total = sum(e["n_eff"] for e in ests)
        mu = sum(e["rate"] * e["n_eff"] for e in ests) / total
        observed = sum(e["n_eff"] * (e["rate"] - mu) ** 2 for e in ests) / total
        sampling = statistics.mean(mu * (1 - mu) / e["n_eff"] for e in ests)
        tau2 = observed - sampling
        # No spread between members beyond sampling noise: the survey can't
        # tell one member's approval in this cell from another's, so the
        # cell is not measurable per member (k None). In the 2024 data that
        # is every House member's approval among independents and among
        # the other party's voters: a district's hundred-odd respondents
        # hold too few of them.
        k = max(mu * (1 - mu) / tau2 - 1, 0.0) if tau2 > 0 else None
        out[f"{chamber}/{mparty}/{rparty}"] = {
            "mu": round(mu, 4), "k": None if k is None else round(k, 1), "members": len(ests),
        }
    return out


def build(cells: dict) -> dict:
    members = []
    for (state, chamber, district, name, mparty), by_rparty in cells.items():
        by_party = {rp: e for rp, acc in by_rparty.items() if (e := _estimate(acc))}
        if by_party:
            members.append({
                "state": state, "chamber": chamber, "district": district or None,
                "name": name, "member_party": mparty, "by_party": by_party,
            })
    prior = priors(members)
    for m in members:
        for rparty, est in m["by_party"].items():
            p = prior.get(f"{m['chamber']}/{m['member_party']}/{rparty}") or prior.get(f"{m['chamber']}/*/{rparty}")
            if p is None or p["k"] is None:
                # Too few members in the cell to estimate a prior, or no
                # measurable difference between members: no per-member value.
                est["shrunk"] = None
            else:
                est["shrunk"] = round((est["rate"] * est["n_eff"] + p["mu"] * p["k"]) / (est["n_eff"] + p["k"]), 4)
            est["rate"] = round(est["rate"], 4)
            est["n_eff"] = round(est["n_eff"], 1)
    members.sort(key=lambda m: (m["chamber"], m["state"], m["district"] or "", m["name"]))
    return {"priors": prior, "members": members}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--csv", type=pathlib.Path, help="a local copy of the Common Content CSV")
    args = parser.parse_args()
    path = args.csv or _download()
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        result = build(tally(csv.DictReader(f)))
    out = {
        "_source": (
            f"{SURVEY}, Harvard Dataverse {CES_DOI} (CC0), datafile {CES_DATAFILE}. Approval of the "
            "respondent's own House member and senators (CC24_312f/g/h) by the respondent's party "
            "(pid3), weighted by commonweight, among respondents giving an opinion; shrunk per "
            "(chamber, member party, respondent party) by beta-binomial empirical Bayes (see the "
            "script). Generated by backend/scripts/fetch_ces_approval.py."
        ),
        "survey": SURVEY,
        "fielded": "2024-10/2024-11",
        "fielded_year": 2024,
        **result,
    }
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT}: {len(result['members'])} members; priors {result['priors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
