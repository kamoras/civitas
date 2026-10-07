"""Seed a database with a small, fictional dataset for auditing the UI.

The Lighthouse job used to audit only error/empty shells: it ran the
frontend with no backend, so no page with real content — score tables,
profiles, ballot pages, document pages — was ever checked for
accessibility. This seeds enough of every model those pages read that they
render the way they do in production, and CI audits them.

Every person and place-specific record here is invented (names are
placeholders, not real officials). Deterministic: the same database every
run, so an audit result changes only when the UI does.

Usage:
    DATABASE_URL=sqlite:////tmp/civitas-ui.db python scripts/seed_ui_fixture.py
"""

import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import models as M  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402

TODAY = "2026-09-26"

SENATE = [
    ("CT", "D", "Avery Holt"), ("TX", "R", "Blake Rivera"), ("PA", "D", "Casey Moreno"),
    ("OH", "R", "Drew Patel"), ("GA", "D", "Emerson Liu"), ("UT", "R", "Finley Okafor"),
    ("ME", "I", "Gray Nakamura"), ("AZ", "D", "Harper Vance"), ("FL", "R", "Indigo Walsh"),
    ("MI", "D", "Jordan Kim"), ("WY", "R", "Kendall Brooks"), ("NV", "D", "Logan Reyes"),
]
STAGES = ["INTRODUCED", "REFERRED", "IN_COMMITTEE", "PASSED_CHAMBER", "ENACTED"]


def _initials(name: str) -> str:
    return "".join(w[0] for w in name.split())[:4]


def seed(db) -> None:
    rng = random.Random(7)

    for i, (state, party, name) in enumerate(SENATE):
        sid = f"S{i:03d}"
        db.add(M.Senator(
            id=sid, bioguide_id=sid, name=name, state=state, party=party,
            caucus_party="D" if party == "I" else party, years_in_office=rng.randint(1, 20),
            initials=_initials(name), is_current=True,
            score_funding_independence=rng.randint(25, 90),
            score_constituent_alignment=rng.randint(25, 90),
            score_legislative_effectiveness=rng.randint(25, 90),
            score_promise_persistence=50, score_funding_diversity=rng.randint(30, 90),
            total_raised=rng.randint(3, 30) * 1e6, total_contributions=rng.randint(3, 30) * 1e6,
            total_from_pacs=rng.randint(1, 5) * 1e6, small_donor_percentage=rng.randint(5, 40),
            ideology_score=round(rng.uniform(0, 1), 3), leadership_score=round(rng.uniform(0, 1), 3),
            bipartisanship_score=round(rng.uniform(0, 1), 3),
        ))
        for j in range(6):
            db.add(M.Donor(senator_id=sid, name=f"Example Industry PAC {j}", type="PAC",
                           total=rng.randint(5, 50) * 1000, rank=j + 1, industry="TECH"))
        for ind, label in (("TECH", "Technology"), ("FIN", "Finance"), ("HEALTH", "Health")):
            db.add(M.IndustryDonation(senator_id=sid, industry=ind, name=label, total=rng.randint(1, 9) * 1e5))
        for j in range(8):
            db.add(M.KeyVote(
                senator_id=sid, bill_name=f"Infrastructure Resilience Act {j}", bill_id=f"s{100 + j}-119",
                date=f"2026-0{j % 9 + 1}-1{j % 9}", vote=rng.choice(["Yea", "Nay"]),
                voted_with_party=rng.random() > 0.2, policy_area="INFRASTRUCTURE",
                vote_category="key" if j < 4 else "recent",
            ))
        for j in range(5):
            db.add(M.SponsoredBill(
                senator_id=sid, bill_id=f"S.{200 + i * 10 + j}", title=f"Rural Broadband Access Act, part {j}",
                congress=119, bill_type="s", stage=rng.choice(STAGES), introduced_date="2025-03-01",
                commemorative=j == 0,
            ))

    for d in range(1, 6):
        rid = f"R-CT{d}"
        db.add(M.Representative(
            id=rid, bioguide_id=rid, name=f"Morgan Example {d}", state="CT", district=d,
            party="D" if d != 5 else "R", is_current=True, years_in_office=d * 2,
            initials="ME", ideology_score=round(rng.uniform(0, 1), 3),
            score_funding_independence=rng.randint(25, 90), score_constituent_alignment=rng.randint(25, 90),
            score_legislative_effectiveness=rng.randint(25, 90),
            total_raised=rng.randint(1, 5) * 1e6, total_contributions=rng.randint(1, 5) * 1e6,
            total_from_pacs=rng.randint(1, 9) * 1e5, small_donor_percentage=rng.randint(5, 40),
        ))

    for i in range(3):
        db.add(M.ActionIssue(
            date=TODAY, rank=i + 1, title=f"Congress weighs changes to disaster relief funding ({i + 1})",
            summary="Lawmakers are debating how federal disaster aid is allocated after a string of costly storms.",
            facts=json.dumps(["The fund was replenished in March.", "A House bill would change the cost-share formula."]),
            actions=json.dumps([{"text": "Read the bill text", "type": "read", "url": "https://www.congress.gov"}]),
            source_urls=json.dumps(["https://apnews.com/", "https://www.npr.org/"]),
            source_names=json.dumps(["AP", "NPR"]), policy_areas=json.dumps(["WELFARE"]), is_current=True,
        ))

    for i in range(6):
        db.add(M.ExploreDocument(
            doc_type="Proposed Rule" if i % 2 else "Executive Order", source="Federal Register",
            title=f"Energy efficiency standards for residential appliances {i}",
            body="The Department proposes amended standards. " * 20, date=f"2026-09-{i + 1:02d}",
            chamber="Regulatory", agency_name="Department of Energy",
            comment_url=f"https://www.regulations.gov/commenton/DOE-2026-000{i}" if i % 2 else None,
            comments_close_on=f"2026-10-1{i}" if i % 2 else None,
        ))

    db.add(M.Race(id="2026-S-CT", cycle_year=2026, office="S", state="CT"))
    db.add(M.Candidate(id="CS1", race_id="2026-S-CT", name="Sam Example", party="DEM",
                       confirmed_general=True, contributions=4e6))
    db.add(M.Candidate(id="CS2", race_id="2026-S-CT", name="Pat Example", party="REP",
                       confirmed_general=True, contributions=2e6))
    for d in range(1, 6):
        race = f"2026-H-CT-{d:02d}"
        db.add(M.Race(id=race, cycle_year=2026, office="H", state="CT", district=d))
        for k, (party, name) in enumerate((("DEM", "Dana"), ("REP", "Riley"), ("LIB", "Lee"))):
            db.add(M.Candidate(
                id=f"C{d}{k}", race_id=race, name=f"{name} Candidate {d}", party=party,
                confirmed_general=True, has_raised_funds=True,
                contributions=rng.randint(1, 20) * 1e5, cash_on_hand=rng.randint(1, 9) * 1e5,
            ))

    for number, name, party, start, end in (
        (1, "President Example One", "Independent", "1901-03-04", "1909-03-04"),
        (2, "President Example Two", "Democratic", "1909-03-04", "1913-03-04"),
        (3, "President Example Three", "Republican", "1913-03-04", "1921-03-04"),
    ):
        db.add(M.President(id=f"P{number}", name=name, party=party, number=number,
                           term_start=start, term_end=end,
                           score_public_mandate=rng.randint(30, 90), score_effectiveness=rng.randint(30, 90),
                           score_historical_legacy=rng.randint(30, 90)))
    for i, name in enumerate(("Jane Example", "John Sample", "Ruth Placeholder")):
        db.add(M.Justice(id=f"J{i}", name=name, last_name=name.split()[-1], is_active=True,
                         score_loyalty=rng.randint(30, 90), loyalty=rng.uniform(-0.1, 0.2), loyalty_se=0.04,
                         loyalty_votes_in=rng.randint(100, 400), loyalty_votes_out=rng.randint(100, 400),
                         loyalty_rate_in=0.55, loyalty_rate_out=0.5, loyalty_through_term=2025))

    db.commit()


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
    print("seeded")


if __name__ == "__main__":
    main()
