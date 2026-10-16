"""A bill's path from its official actions (services/bill_journey.py)."""

import re

from app.services import bill_journey

LOC = {"code": 9, "name": "Library of Congress"}
SENATE = {"code": 0, "name": "Senate"}
HOUSE = {"code": 2, "name": "House floor actions"}


def _a(date, text, code=None, source=LOC, votes=None):
    return {"actionDate": date, "text": text, "actionCode": code, "sourceSystem": source,
            "recordedVotes": votes or []}


def _rolls(a):
    return [{"chamber": v["chamber"].lower(), "number": v["rollNumber"], "session": v["sessionNumber"]}
            for v in a.get("recordedVotes") or []]


# A Senate bill that became law with no roll call (shaped on S. 240, 119th
# Congress), newest first as Congress.gov lists it.
S_BILL = [
    _a("2026-10-15", "Became Public Law No: 119-122.", "E40000"),
    _a("2026-10-15", "Became Public Law No: 119-122.", "36000"),
    _a("2026-10-15", "Signed by President.", "E30000"),
    _a("2026-10-15", "Signed by President.", "36000"),
    _a("2026-10-05", "Presented to President.", "E20000", HOUSE),
    _a("2026-10-05", "Presented to President.", "28000"),
    _a("2026-09-24", "Senate agreed to the House amendment to S. 240 by Unanimous Consent. "
       "(consideration: CR S5020)", None, SENATE),
    _a("2026-09-24", "Resolving differences -- Senate actions: Senate agreed to the House amendment "
       "to S. 240 by Unanimous Consent.  ", "20500"),
    _a("2026-09-14", "Motion to reconsider laid on the table Agreed to without objection.", "H38310", HOUSE),
    _a("2026-09-14", "On motion to suspend the rules and pass the bill, as amended Agreed to by voice vote. "
       "(text: CR H5570)", "H37300", HOUSE),
    _a("2026-09-14", "Passed/agreed to in House: On motion to suspend the rules and pass the bill, as "
       "amended Agreed to by voice vote.", "8000"),
    _a("2025-12-15", "Held at the desk.", "H15000", HOUSE),
    _a("2025-12-11", "Passed Senate without amendment by Unanimous Consent. (consideration: CR S8691-8692)",
       None, SENATE),
    _a("2025-12-11", "Passed/agreed to in Senate: Passed Senate without amendment by Unanimous Consent.",
       "17000"),
    _a("2025-11-04", "Committee on Indian Affairs. Reported by Senator Murkowski without amendment.",
       "14000"),
    _a("2025-01-24", "Read twice and referred to the Committee on Indian Affairs.", None, SENATE),
    _a("2025-01-24", "Introduced in Senate", "10000"),
]


def test_twins_fold_into_the_chambers_wording_with_every_code():
    acts = bill_journey.fold_actions(S_BILL, _rolls)
    texts = [a["text"] for a in acts]
    assert texts.count("Became Public Law No: 119-122.") == 1
    assert texts.count("Signed by President.") == 1
    assert texts.count("Presented to President.") == 1
    house = next(a for a in acts if a["text"].startswith("On motion to suspend"))
    assert set(house["codes"]) == {"H37300", "8000"}
    assert not any(t.startswith("Passed/agreed to in") or t.startswith("Resolving differences --") for t in texts)
    # A law number after "No:" never matches anything as a restatement.
    assert len(acts) == len(S_BILL) - 6


def test_each_code_gets_its_meaning_and_uncoded_actions_none():
    acts = {a["text"]: a for a in bill_journey.fold_actions(S_BILL, _rolls)}
    assert acts["Held at the desk."]["term"] == "Held at the desk"
    assert acts["Motion to reconsider laid on the table Agreed to without objection."]["meaning"]
    assert acts["Read twice and referred to the Committee on Indian Affairs."]["term"] is None


def test_how_it_passed_says_unanimous_consent_and_voice_vote_never_no_votes():
    passage = bill_journey.passage(bill_journey.fold_actions(S_BILL, _rolls))
    assert [p["sentence"] for p in passage] == [
        "Passed the Senate by unanimous consent on December 11, 2025; no roll call is taken for that.",
        "Passed the House by voice vote on September 14, 2026, under suspension of the rules, which "
        "needs two thirds; no names are recorded for that.",
        "The Senate agreed to the House's version by unanimous consent on September 24, 2026; no roll "
        "call is taken for that.",
    ]
    assert passage[0]["methodTerm"] == "Unanimous consent" and passage[0]["methodMeaning"]


def test_a_recorded_vote_is_named_by_its_roll_call():
    acts = bill_journey.fold_actions([
        _a("2025-05-22", "On passage Passed by the Yeas and Nays: 215 - 214 (Roll no. 145).", "H37100", HOUSE,
           votes=[{"chamber": "House", "rollNumber": 145, "sessionNumber": 1}]),
        _a("2025-05-22", "Passed/agreed to in House: On passage Passed by the Yeas and Nays: 215 - 214 "
           "(Roll no. 145).", "8000"),
        _a("2025-05-20", "Introduced in House", "1000"),
    ], _rolls)
    [p] = bill_journey.passage(acts)
    assert p["method"] == "rollCall" and p["rollCall"]["number"] == 145
    assert p["sentence"] == "Passed the House by a recorded vote on May 22, 2025 (roll call 145)."


def test_the_journey_dates_every_stop_a_law_passed():
    stops = bill_journey.journey(bill_journey.fold_actions(S_BILL, _rolls), "s")["stops"]
    assert [(s["key"], s["status"], s["date"]) for s in stops] == [
        ("introduced", "done", "2025-01-24"),
        ("committee", "done", "2025-11-04"),
        ("floor-senate", "done", "2025-12-11"),
        ("floor-house", "done", "2026-09-14"),
        ("differences", "done", "2026-09-24"),
        ("president", "done", "2026-10-05"),
        ("law", "done", "2026-10-15"),
    ]
    assert stops[1]["after"] == "9 months later"
    assert stops[2]["after"] == "37 days later"


def test_a_bill_in_committee_is_current_there_and_the_rest_is_ahead():
    acts = bill_journey.fold_actions([
        _a("2026-03-02", "Referred to the House Committee on Agriculture.", "H11100", HOUSE),
        _a("2026-03-02", "Introduced in House", "1000"),
    ], _rolls)
    stops = bill_journey.journey(acts, "hr")["stops"]
    assert [(s["key"], s["status"]) for s in stops] == [
        ("introduced", "done"), ("committee", "current"), ("floor-house", "ahead"),
        ("floor-senate", "ahead"), ("president", "ahead"), ("law", "ahead"),
    ]
    assert stops[1]["date"] == "2026-03-02"


def test_a_simple_resolution_has_one_chamber_and_no_president():
    acts = bill_journey.fold_actions([
        _a("2026-04-30", "Submitted in the Senate, considered, and agreed to without amendment by Unanimous "
           "Consent.", None, SENATE),
        _a("2026-04-30", "Passed/agreed to in Senate: Submitted in the Senate, considered, and agreed to "
           "without amendment by Unanimous Consent.", "17000"),
        _a("2026-04-30", "Submitted in Senate", "10025"),
    ], _rolls)
    stops = bill_journey.journey(acts, "sres")["stops"]
    assert [s["key"] for s in stops] == ["introduced", "committee", "floor-senate"]
    assert stops[1]["status"] == "skipped"
    assert stops[2]["status"] == "current"


def test_no_actions_is_no_journey():
    assert bill_journey.journey([], "hr") is None


def test_the_glossary_is_plain_fixed_text():
    g = bill_journey.glossary()
    codes = [c for t in g["terms"] for c in t["codes"]]
    assert len(codes) == len(set(codes)), "a code has one meaning"
    for entry in [*g["terms"], *g["methods"].values()]:
        assert "—" not in entry["meaning"] and "—" not in entry["term"]
        # Short sentences: no more than two, none over 25 words.
        sentences = [x for x in re.split(r"(?<=[.!?])\s+", entry["meaning"]) if x]
        assert len(sentences) <= 2 and all(len(x.split()) <= 25 for x in sentences), entry
