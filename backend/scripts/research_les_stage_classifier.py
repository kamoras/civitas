"""Does bill_stage.py sort bills into stages the way Volden & Wiseman do?

research_les_stage_weighting.py measures the LES formula on V&W's own
per-member stage counts. This measures the other half: whether the stage
Civitas gives each bill from its Congress.gov actions
(classify_bill_stage_from_actions, mapped to LES stages by
_LES_STAGE_ORDER) reproduces those counts.

For the 118th Congress, every House and Senate bill and joint resolution in
GovInfo's BILLSTATUS bulk data (the actions Congress.gov serves, with the
same action codes and types) is classified, counted per sponsor at each of
the five stages it reaches, and compared per member with V&W's published
counts (all three significance tiers summed). Members are joined on ICPSR
number through Voteview's member file (bioguide id -> ICPSR).

Usage:
    python backend/scripts/research_les_stage_classifier.py [--cache DIR]

Downloads ~60 MB of BILLSTATUS zips, the Voteview member file and the two
CEL spreadsheets into --cache on first run. Needs pandas, openpyxl and xlrd.
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import sys
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from research_les_stage_weighting import HOUSE_URL, SENATE_URL, STAGES, TIERS, _fetch, _load  # noqa: E402

from app.pipeline.analyze.bill_stage import became_law_action, classify_bill_stage_from_actions  # noqa: E402
from app.pipeline.analyze.score_calculator import _LES_STAGE_ORDER  # noqa: E402

CONGRESS = 118
BILLSTATUS_URL = "https://www.govinfo.gov/bulkdata/BILLSTATUS/{c}/{t}/BILLSTATUS-{c}-{t}.zip"
VOTEVIEW_URL = "https://voteview.com/static/data/out/members/HS{c}_members.csv"
# V&W count standalone bills: bills and joint resolutions.
BILL_TYPES = {"house": ("hr", "hjres"), "senate": ("s", "sjres")}


def _bills(path: pathlib.Path):
    """(sponsor bioguide id, actions) for every bill in a BILLSTATUS zip."""
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            bill = ET.fromstring(z.read(name)).find("bill")
            sponsor = bill.findtext("sponsors/item/bioguideId")
            actions = [
                {"actionCode": a.findtext("actionCode"), "type": a.findtext("type"), "text": a.findtext("text") or ""}
                for a in bill.findall("actions/item")
            ]
            yield sponsor, actions


def our_counts(chamber: str, cache: pathlib.Path, icpsr_of: dict[str, int]) -> dict[int, list[int]]:
    """Per ICPSR number, bills reaching each LES stage as Civitas classifies them."""
    counts: dict[int, list[int]] = {}
    for bill_type in BILL_TYPES[chamber]:
        for sponsor, actions in _bills(_fetch(BILLSTATUS_URL.format(c=CONGRESS, t=bill_type), cache)):
            icpsr = icpsr_of.get(sponsor)
            if icpsr is None:
                continue
            is_law = any(became_law_action(a["text"]) for a in actions)
            reached = _LES_STAGE_ORDER[classify_bill_stage_from_actions(actions, is_law).value]
            member = counts.setdefault(icpsr, [0] * len(STAGES))
            for k in range(reached):
                member[k] += 1
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=pathlib.Path, default=pathlib.Path("/tmp/les-cache"))
    cache = ap.parse_args().cache

    with open(_fetch(VOTEVIEW_URL.format(c=CONGRESS), cache)) as f:
        icpsr_of = {row["bioguide_id"]: int(row["icpsr"]) for row in csv.DictReader(f) if row["bioguide_id"]}

    for chamber in ("house", "senate"):
        ours = our_counts(chamber, cache, icpsr_of)
        raw = pd.read_excel(_fetch(HOUSE_URL if chamber == "house" else SENATE_URL, cache))
        icpsr_col = next(c for c in raw.columns if "icpsr" in str(c).lower())
        df = _load(chamber, cache)
        df["icpsr"] = raw.loc[df.index, icpsr_col]
        df = df[(df.congress == CONGRESS) & df.icpsr.notna()]

        rows = [
            ([sum(row[f"{t}_{s}"] for t in TIERS) for s in STAGES], ours[int(row.icpsr)])
            for _, row in df.iterrows() if int(row.icpsr) in ours
        ]
        print(f"== {chamber}, {CONGRESS}th Congress: {len(rows)} of {len(df)} members matched")
        print(f"{'stage':6} {'V&W total':>10} {'Civitas':>8} {'Spearman':>9} {'exact':>6}")
        for k, stage in enumerate(STAGES):
            theirs = pd.Series([r[0][k] for r in rows], dtype=float)
            civitas = pd.Series([r[1][k] for r in rows], dtype=float)
            exact = (theirs == civitas).mean()
            print(f"{stage:6} {theirs.sum():10.0f} {civitas.sum():8.0f} "
                  f"{theirs.corr(civitas, method='spearman'):9.3f} {exact:6.2f}")
        print()


if __name__ == "__main__":
    main()
