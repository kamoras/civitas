"""The bill page's box lines (services/bill_boxes.py): templates filled from
the shaped record, counts that agree with the lists behind them, and the
CRS summary quoted verbatim."""

from app.services import bill_boxes
from app.services.bill_journey import journey, passage


def _action(date, codes, text, roll_calls=(), type_="Floor"):
    return {"date": date, "text": text, "type": type_, "codes": list(codes),
            "rollCalls": list(roll_calls), "term": None, "meaning": None}


ROLL_145 = {"chamber": "house", "number": 145, "session": 1, "sourceUrl": None}
ROLL_372 = {"chamber": "senate", "number": 372, "session": 1, "sourceUrl": None}

# Newest first, as Congress.gov lists them.
ACTIONS = [
    _action("2025-07-04", ["E40000", "36000"], "Became Public Law No: 119-21.", type_="President"),
    _action("2025-07-03", ["E20000", "28000"], "Presented to President."),
    _action("2025-07-01", ["17000"], "Passed Senate with an amendment by Yea-Nay Vote. 51 - 50.", [ROLL_372]),
    _action("2025-05-22", ["H37100", "8000"], "On passage Passed by the Yeas and Nays: 215 - 214.", [ROLL_145]),
    _action("2025-05-20", ["H12100", "1010"], "The House Committee on the Budget reported an original measure.",
            type_="Committee"),
]


def _vote(chamber, number, yeas, nays, rejected=False, label="Passed", tie=None, parties=None):
    return {"chamber": chamber, "session": 1, "number": number, "kind": "question", "yeas": yeas, "nays": nays,
            "rejected": rejected, "resultLabel": label, "result": label, "tieBreaker": tie,
            "parties": parties or []}


def _record(**over):
    record = {
        "billId": "HR.1", "billLabel": "H.R. 1", "introducedDate": "2025-05-20",
        "policyArea": "Economics and Public Finance",
        "latestAction": {"actionDate": "2025-07-04", "text": "Became Public Law No: 119-21."},
        "cosponsors": [], "unavailable": [],
        "actions": ACTIONS, "journey": journey(ACTIONS, "hr"), "passage": passage(ACTIONS),
        "votes": [
            _vote("senate", 372, 50, 50, label="Bill Passed",
                  tie={"byWhom": "Vice President of the United States", "vote": "Yea"}),
            _vote("senate", 371, 45, 55, rejected=True, label="Motion Rejected"),
            _vote("senate", 370, 44, 56, rejected=True, label="Motion Rejected"),
            _vote("house", 145, 215, 214, parties=[
                {"party": "R", "yea": 215, "nay": 2}, {"party": "D", "yea": 0, "nay": 212}]),
        ],
        "summary": {"actionDesc": "Passed Senate", "actionDate": "2025-07-01", "paragraphs": [
            "One Big Beautiful Bill Act",
            "This bill reduces taxes, as set out in H. Con. Res. 14. It also raises the debt limit.",
            "A second paragraph.",
        ]},
        "cboCostEstimates": [{"pubDate": "2025-06-17T00:00:00Z"}, {"pubDate": "2025-06-04T00:00:00Z"}],
        "textVersions": [{"type": "Reported in House", "date": "2025-05-20"},
                         {"type": "Public Law", "date": "2025-07-05"}],
        "floorDebate": [], "floorSpeechCount": 0,
        "coverage": {"floor": {"held": True}},
    }
    record.update(over)
    return record


def test_a_law_leads_with_its_number_and_days_from_introduction():
    status = bill_boxes.boxes(_record(), [{"number": "119-21", "type": "Public Law"}])["status"]
    assert status["headline"] == "Public Law 119-21, 45 days after it was introduced"
    assert status["figure"] == {"type": "stat", "value": 45, "caption": "days, bill to law"}
    assert status["dek"] == ("Introduced May 20, 2025. Passed the House May 22, 2025 and the Senate "
                             "July 1, 2025. Became law July 4, 2025.")
    assert status["tone"] == "done"


def test_a_bill_in_committee_counts_only_the_steps_taken():
    actions = [_action("2025-05-22", ["10000"], "Introduced in Senate", type_="IntroReferral")]
    status = bill_boxes.boxes(_record(actions=actions, journey=journey(actions, "s"), passage=[]))["status"]
    assert status["headline"] == "In committee since May 22, 2025"
    assert status["figure"]["value"] == 1
    assert status["dek"] == "Introduced May 22, 2025. Next: Senate floor."


def test_each_chamber_decision_carries_its_held_split_and_party_line():
    boxes = bill_boxes.boxes(_record())
    house, senate = boxes["passage"]
    assert house["headline"] == "Passed the House by a recorded vote, 215 to 214"
    assert house["dek"] == "Republicans 215 yea, 2 nay. Democrats 0 yea, 212 nay."
    assert house["figure"]["yeas"] == 215 and house["figure"]["caption"] == "Passed"
    assert senate["headline"].endswith("50 to 50; Vice President of the United States voted Yea")
    assert senate["figure"]["caption"] == "Tie broken"


def test_a_voice_vote_is_explained_and_has_no_figure():
    actions = [_action("2025-12-15", ["H37300", "8000"], "On motion to suspend the rules and pass the bill "
                                                         "Agreed to by voice vote.")]
    (box,) = bill_boxes.boxes(_record(actions=actions, passage=passage(actions), votes=[]))["passage"]
    assert box["headline"] == "Passed the House by voice vote, under suspension of the rules"
    assert box["figure"] is None
    assert box["dek"]  # the glossary's line on a voice vote


def test_vote_counts_agree_with_the_votes_listed():
    votes = bill_boxes.boxes(_record())["votes"]
    assert votes["headline"] == "4 recorded votes on this bill"
    assert votes["dek"] == "2 carried and 2 failed."
    assert votes["results"][0] == {"label": "Motion Rejected", "count": 2}
    assert sum(r["count"] for r in votes["results"]) == 4


def test_history_cost_text_and_sponsor_lines():
    b = bill_boxes.boxes(_record(cosponsors=[{"party": "D"}, {"party": "D"}, {"party": "I"}]))
    assert b["history"]["headline"] == "5 actions from May 20, 2025 to July 4, 2025"
    assert b["history"]["dek"] == "By Congress.gov's action type: floor 3, President 1, committee 1."
    assert b["cost"]["dek"] == "From June 4, 2025 to June 17, 2025."
    assert b["text"]["dek"] == "Latest: Public Law, July 5, 2025."
    assert b["sponsor"]["dek"].startswith("3 cosponsors: 2 Democrats, 1 Independent.")
    assert b["floor"]["headline"] == "No floor speech heading names H.R. 1"


def test_the_summary_is_quoted_verbatim_from_its_first_prose_sentence():
    summary = bill_boxes.boxes(_record())["summary"]
    # The title line is skipped; "H. Con. Res. 14." is not a sentence end
    # before "It": the number is, so the quote ends there.
    assert summary["text"] == "This bill reduces taxes, as set out in H. Con. Res. 14."
    assert summary["source"] == "Congressional Research Service, Passed Senate, July 1, 2025"
    assert bill_boxes.first_sentence("Amends the U.S. Code. Then more.") == "Amends the U.S. Code. Then more."
    assert bill_boxes.first_sentence("It does a thing. Then more.") == "It does a thing."


def test_a_record_with_nothing_on_it_has_no_boxes_to_fill():
    b = bill_boxes.boxes(_record(actions=[], journey=None, passage=[], votes=[], summary=None,
                                 cboCostEstimates=[], textVersions=[]))
    assert b["status"] is None and b["summary"] is None and b["votes"] is None
    assert b["history"] is None and b["cost"] is None and b["text"] is None and b["passage"] == []
