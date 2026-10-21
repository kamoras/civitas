"""The state ballot page's boxes (services/ballot_boxes.py): templated
lines and counted figures from the payload, never a description of what a
measure does."""

from app.services.ballot_boxes import ballot_boxes, contest_count


def _cand(party="DEM", **kw):
    return {"name": "X", "displayName": kw.pop("name", "Pat Doe"), "party": party,
            "partyGroup": party, "confirmed": True, **kw}


def _ballot(**kw):
    base = {
        "state": "CT", "stateName": "Connecticut", "cycleYear": 2026,
        "primaryDate": "2026-08-11", "statePvi": -8, "electoralVotes": None,
        "ballotBasis": {"basis": "nominees"},
        "senateRaces": [], "nextSenateElection": 2028,
        "houseRaces": [
            {"id": f"h{i}", "district": i, "candidates": [_cand(), _cand("REP")]}
            for i in range(1, 6)
        ],
        "coverage": [{"race": {"id": "h1"}}] * 3,
        "measures": [], "measureCoverage": {"status": "confirmed_none", "basis": "source"},
        "statewideRaces": [{"office": f"o{i}", "nominees": []} for i in range(5)],
        "statewideCoverage": {"status": "covered", "ballotList": False},
        "stateLegRaces": [], "judicialRaces": [],
        "judicialCoverage": {"status": "not_yet_covered"},
    }
    return base | kw


def test_summary_counts_and_lean():
    boxes = ballot_boxes(_ballot())
    summary = {b["key"]: b for b in boxes["summary"]}
    # One House race per voter plus five statewide offices.
    assert boxes["contestCount"] == 6
    assert summary["contests"]["figure"] == {"type": "stat", "value": 6, "caption": "contests"}
    assert summary["candidates"]["title"] == "10 federal candidates"
    assert summary["candidates"]["dek"] == "Nominees from the August 11, 2026 primary results."
    assert summary["lean"]["title"] == "Connecticut leans D+8"
    assert summary["lean"]["figure"]["text"] == "D+8"
    assert "electoral" not in summary


def test_contest_lines():
    contests = ballot_boxes(_ballot())["contests"]
    assert contests["house"]["figure"]["value"] == 5
    assert contests["statewide"]["dek"] == "5 offices on the ballot. From primary results."
    assert "2028" in contests["no-senate"]["dek"]
    assert contests["measures"]["figure"] == {"type": "stat", "value": 0, "caption": "measures"}
    assert contests["news"]["figure"]["value"] == 3
    # A section nobody has read is no contest and gets no line.
    assert "judicial" not in contests


def test_unread_measures_never_read_as_zero():
    contests = ballot_boxes(_ballot(measureCoverage={"status": "not_yet_covered"}))["contests"]
    assert contests["measures"]["figure"] is None
    assert contests["measures"]["dek"].startswith("Not loaded yet")


def test_stale_measures_say_so_and_removed_ones_dont_count():
    b = _ballot(
        measures=[{"status": "active"}, {"status": "removed"}],
        measureCoverage={"status": "ingest_failed"},
    )
    m = ballot_boxes(b)["contests"]["measures"]
    assert m["figure"]["value"] == 1
    assert "last successful read" in m["dek"]
    assert contest_count(b) == 7  # one House race, five offices, one measure


def test_filers_and_records_and_outside_parties():
    b = _ballot(
        ballotBasis={"basis": "filers"},
        houseRaces=[{"id": "h1", "district": 0, "candidates": [
            _cand(confirmed=False, candidateStatus="C", incumbentRecord={"id": "m"}),
            _cand("LIB", confirmed=False, hasRaisedFunds=True),
            # Not running by the page's rule: not counted.
            _cand(confirmed=False),
        ]}],
    )
    boxes = ballot_boxes(b)
    cand = next(x for x in boxes["summary"] if x["key"] == "candidates")
    assert cand["title"] == "2 registered with the FEC, 1 with a voting record in Congress"
    assert cand["dek"].endswith("1 candidate outside the two major parties.")
    assert boxes["contests"]["house"]["dek"].startswith("One statewide seat")


def test_statewide_seats_by_district_count_once():
    seats = [{"office": f"c{i}", "officeCode": "EC", "seat": str(i), "electedBy": "district",
              "nominees": []} for i in range(1, 6)]
    b = _ballot(statewideRaces=seats, houseRaces=[], statePvi=0, electoralVotes=4)
    boxes = ballot_boxes(b)
    assert boxes["contestCount"] == 1
    assert boxes["contests"]["statewide"]["dek"].startswith("1 office")
    summary = {x["key"]: x for x in boxes["summary"]}
    assert summary["lean"]["title"] == "Connecticut votes about evenly"
    assert summary["electoral"]["title"] == "4 electoral votes for president"


def test_counts_each_chamber_court_and_office_but_not_page_sections():
    # Contests a voter marks: one per legislative chamber, each court, each
    # office. The "not loaded" placeholders and news coverage are sections.
    b = _ballot(
        houseRaces=[{"id": "h1", "district": 1, "candidates": []},
                    {"id": "h2", "district": 2, "candidates": []}],
        senateRaces=[{"id": "s", "candidates": []}], nextSenateElection=None,
        statewideRaces=[{"office": f"o{i}", "nominees": []} for i in range(3)],
        stateLegRaces=[{"label": "State Senate", "districts": [1, 2]},
                       {"label": "State House", "districts": [1]}],
        judicialCoverage={"status": "covered", "ballotList": True},
        judicialRaces=[{"court": "supreme", "seats": []}],
        measures=[{"status": "certified"}] * 2,
        measureCoverage={"status": "covered"},
    )
    boxes = ballot_boxes(b)
    assert boxes["contestCount"] == 1 + 1 + 3 + 2 + 1 + 2
    assert boxes["contests"]["stateleg"]["figure"]["value"] == 3
    assert boxes["contests"]["judicial"]["dek"] == "1 court on the ballot."
    assert "no-senate" not in boxes["contests"]
    unread = _ballot(houseRaces=[], statewideCoverage={"status": "not_yet_covered"})
    assert contest_count(unread) == 0


def test_filer_lists_are_never_worded_as_the_ballot():
    # Before a state certifies its list nothing may say who is "on the ballot".
    boxes = ballot_boxes(_ballot(ballotBasis={"basis": "filers"}, statewideRaces=[],
                                 statewideCoverage={"status": "not_yet_covered"}))
    for box in boxes["summary"]:
        assert "on the ballot" not in box["title"] and "'s ballot" not in box["title"]
