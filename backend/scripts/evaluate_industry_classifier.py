"""Measure the donor industry name classifier against FEC and SEC records.

The name classifier (transform/industry_classifier.py) reads an industry
from an organization's name by embedding similarity to the prototype texts
in INDUSTRY_DESCRIPTIONS. Its prototypes and thresholds are the tuning
surface; per the repo's calibration discipline they move only on a
measurement, and this is the instrument.

**Labels are derived from records, not hand-labelled.** Every PAC in the
FEC's committee master file states its organization type; a labor
organization's PAC is LABOR_UNIONS, and a corporation's PAC whose sponsor
is an SEC-registered issuer takes the industry of the SIC code the SEC
assigned it (sec_tickers.industry_for_sic). Those are the cases the
pipeline now decides from the records themselves (fec.structured_industry);
the classifier still decides the rest — trade and membership associations,
private companies, foreign parents' subsidiaries. This measures it on the
labelled kinds, which says how it reads company and union names. It says
nothing direct about associations, which no record here labels.

Each committee is classified under the name the pipeline classifies
(normalize_finance.committee_donor_name): the sponsor when there is one.
Reported: accuracy over all labelled committees (an abstention, below the
spread threshold, counts as wrong — the pipeline then sends it to kNN),
coverage, accuracy where it answered, the same over committees whose name
shares no word with any prototype text (how it generalizes, since a
prototype that names a company is not evidence it reads names), and the
most frequent confusions.

Run on a machine with the embedding model and network access:
    cd backend && .venv/bin/python scripts/evaluate_industry_classifier.py
"""

import argparse
import asyncio
import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.http_client import make_async_client  # noqa: E402
from app.pipeline.fetch.fec import (  # noqa: E402
    committee_master_cycles,
    fetch_committee_master,
    is_political_committee,
)
from app.pipeline.fetch.sec_tickers import issuer_industries  # noqa: E402
from app.pipeline.transform.industry_classifier import (  # noqa: E402
    INDUSTRY_DESCRIPTIONS,
    classify_industries_batch_scored,
)
from app.pipeline.transform.normalize_finance import committee_donor_name  # noqa: E402

_PAC_TYPES = {"Q", "N"}


async def labelled_committees() -> dict[str, str]:
    """{name the pipeline classifies: industry from the records}."""
    db = SessionLocal()
    async with make_async_client() as client:
        master = await fetch_committee_master(client, db, committee_master_cycles())
        pacs = {
            cid: m for cid, m in master.items()
            if m.get("type") in _PAC_TYPES and not is_political_committee(m)
        }
        sponsors = sorted({m["connectedOrg"] for m in pacs.values() if m.get("orgType") == "C" and m.get("connectedOrg")})
        _, by_name = await issuer_industries(client, db, [], sponsors)
    labels: dict[str, str] = {}
    for cid, m in pacs.items():
        name = committee_donor_name(m, cid)
        if m.get("orgType") == "L":
            labels[name] = "LABOR_UNIONS"
        elif m.get("orgType") == "C" and by_name.get(m.get("connectedOrg") or ""):
            labels[name] = by_name[m["connectedOrg"]]
    return labels


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[A-Z0-9]+", text.upper()) if len(w) > 2}


def report(labels: dict[str, str], title: str) -> None:
    predicted = classify_industries_batch_scored(list(labels))
    answered = [n for n in labels if n in predicted]
    right = [n for n in answered if predicted[n][0] == labels[n]]
    print(f"\n{title}: {len(labels)} committees")
    print(f"  accuracy (abstain = wrong) {len(right) / len(labels):.3f}")
    print(f"  coverage                   {len(answered) / len(labels):.3f}")
    print(f"  accuracy where answered    {len(right) / max(len(answered), 1):.3f}")
    confusions = collections.Counter((labels[n], predicted[n][0]) for n in answered if predicted[n][0] != labels[n])
    for (truth, got), count in confusions.most_common(12):
        print(f"    {truth:>14} read as {got:<14} {count}")


def main() -> None:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    labels = asyncio.run(labelled_committees())
    prototype_words = set().union(*(_words(d) for d in INDUSTRY_DESCRIPTIONS.values()))
    report(labels, "All labelled committees")
    report({n: i for n, i in labels.items() if not (_words(n) & prototype_words)},
           "Names sharing no word with a prototype")
    by_kind = collections.Counter(labels.values())
    print("\nLabels:", ", ".join(f"{k} {v}" for k, v in by_kind.most_common()))


if __name__ == "__main__":
    main()
