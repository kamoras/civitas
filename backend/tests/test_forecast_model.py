"""The 2026 election map's method (app/forecast): the fit reproduces from
its checked-in inputs, the poll parser's gates hold, the output carries no
win language and its licence, and the map hides from election day on."""

import asyncio
import json
import math
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import district_lean
from app.api import election_map as api
from app.election_phase import CAMPAIGN, ELECTION_DAY, ActiveElection
from app.forecast import build, model
from app.forecast import polls as wiki
from app.models import Candidate, Race

DATA = Path(__file__).resolve().parents[1] / "app" / "data"
TRAINING = json.loads((DATA / "forecast_training.json").read_text())
FITTED = json.loads((DATA / "forecast_model.json").read_text())
HOUSE_TRAINING = json.loads((DATA / "forecast_house_training.json").read_text())
HOUSE_FITTED = json.loads((DATA / "forecast_house_model.json").read_text())
HOUSE_LEAN = json.loads((DATA / "forecast_house_lean.json").read_text())


def assert_close(got, want, rel=1e-9, abs_tol=0.0, path="$"):
    """Equal structure, numbers within tolerance: the file holds fitted values
    at write precision (fit_forecast_model.WRITE_DIGITS), and a least-squares
    refit on another platform's BLAS differs in its last bits."""
    if isinstance(want, dict):
        assert isinstance(got, dict) and set(got) == set(want), path
        for k in want:
            assert_close(got[k], want[k], rel, abs_tol, f"{path}.{k}")
    elif isinstance(want, list):
        assert isinstance(got, (list, tuple)) and len(got) == len(want), path
        for i, (g, w) in enumerate(zip(got, want)):
            assert_close(g, w, rel, abs_tol, f"{path}[{i}]")
    elif isinstance(want, float) or isinstance(got, float):
        assert math.isclose(got, want, rel_tol=rel, abs_tol=abs_tol), f"{path}: {got} != {want}"
    else:
        assert got == want, f"{path}: {got} != {want}"


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("horizon", model.HORIZONS)
def test_fit_reproduces_from_the_training_file(horizon):
    refit = model.fit(TRAINING["races"], TRAINING["generic"], horizon)
    assert_close(refit, FITTED["fits"][str(horizon)])


def test_held_out_evaluation_reproduces():
    # One horizon is enough to prove the file is the code's output; the
    # fit script writes all three the same way.
    again = model.leave_one_cycle_out(TRAINING["races"], TRAINING["generic"], 14)
    # The evaluation is written already rounded for display (3-4 decimals),
    # so a last-bit difference in a fit can move a figure by one unit there.
    assert_close(again, FITTED["evaluation"]["14"], abs_tol=1e-3)


@pytest.mark.parametrize("horizon", model.HORIZONS)
def test_house_fit_reproduces_from_its_training_file(horizon):
    # The House borrows the Senate fit's national coefficient (model.fit).
    coef = model.fit(TRAINING["races"], TRAINING["generic"], horizon)["beta"][3]
    refit = model.fit(HOUSE_TRAINING["races"], HOUSE_TRAINING["generic"], horizon, coef)
    assert_close(refit, HOUSE_FITTED["fits"][str(horizon)])


def test_house_held_out_evaluation_reproduces():
    def coef_for(year):  # the Senate's, refitted without the held-out cycle
        return model.fit([r for r in TRAINING["races"] if r["year"] != year],
                         {y: v for y, v in TRAINING["generic"].items() if int(y) != year}, 14)["beta"][3]

    again = model.leave_one_cycle_out(HOUSE_TRAINING["races"], HOUSE_TRAINING["generic"], 14, coef_for)
    assert_close(again, HOUSE_FITTED["evaluation"]["14"], abs_tol=1e-3)


def test_house_training_leans_are_the_history_files_own():
    """Each House race's lean is the one the history file holds for that
    cycle's lines, which uses only presidential votes cast before it."""
    for r in HOUSE_TRAINING["races"]:
        assert r["lean"] == HOUSE_LEAN["leans"][str(r["year"])][r["state"]]
    assert {r["year"] for r in HOUSE_TRAINING["races"]} == {2018, 2020, 2022}
    assert not any(r["year"] == 2020 and r["state"].startswith("NC-") for r in HOUSE_TRAINING["races"])
    assert all("2024" not in m for m in HOUSE_LEAN["_method"].values() if "2016" in m)


def test_house_history_lean_recomputes_from_its_counts():
    nation = HOUSE_LEAN["nation"]
    for cycle, congress in (("2018", "116"), ("2022", "118")):
        for district, (d16, r16, d20, r20) in list(HOUSE_LEAN["lines"][congress].items())[:40]:
            if cycle == "2018":
                want = -100 * (d16 / (d16 + r16) - nation["2016"]["D"] / sum(nation["2016"].values()))
            else:
                want = model.two_party_lean({"D": d16, "R": r16}, {"D": d20, "R": r20}, nation["2016"], nation["2020"])
            # Stored at two decimals; a rounding edge may land either way.
            assert math.isclose(HOUSE_LEAN["leans"][cycle][district], want, abs_tol=0.006)


def test_training_rows_are_every_contested_cycle():
    years = {r["year"] for r in TRAINING["races"]}
    assert years == set(range(1998, 2023, 2))
    assert all(r["inc"] in (-1, 0, 1) for r in TRAINING["races"])


@pytest.mark.parametrize("fitted", [FITTED, HOUSE_FITTED], ids=["senate", "house"])
def test_ranges_hold_about_their_stated_share_on_held_out_cycles(fitted):
    for h in model.HORIZONS:
        ev = fitted["evaluation"][str(h)]
        assert 0.72 <= ev["coverage80"] <= 0.88, ev["coverage80"]
        assert 0.90 <= ev["coverage95"] <= 0.99, ev["coverage95"]
        # Polls add information: the combined method beats both halves.
        polled = ev["polled"]
        assert polled["combined"]["brier"] < polled["fundamentals"]["brier"]
        assert polled["combined"]["brier"] < polled["pollsOnly"]["brier"]


def test_lean_is_the_state_pvi_files_own_numbers():
    leans, _ = build._lean_table()
    published = json.loads((DATA / "state_pvi.json").read_text())["states"]
    assert {st: round(v) for st, v in leans.items()} == published


# ---------------------------------------------------------------------------
# The poll parser and its gates
# ---------------------------------------------------------------------------

HEADER = """{| class="wikitable"
|- valign=bottom
! Poll source
! Date(s)<br />administered
! Sample<br />size
! Margin<br />{{nowrap|of error}}
! Alex<br />Smith (D)
! Jordan<br />Lee (R)
! Other
! Undecided
"""


def article(rows: str, header: str = HEADER) -> str:
    return ("==General election==\n===Polling===\n" + header + rows + "|}\n"
            "===Results===\n<ref name=\"cited\">{{cite web |url=https://example.org/named |title=x}}</ref>\n")


ROW = """|-
|style="text-align:left;"|Example Polling (R)<ref>{{cite web |url=https://example.org/poll |title=t}}</ref>
|October 7–9, 2026
|1,101 (LV)
|±{{nbsp}}2.9%
|{{party shading/Democratic}}|'''51%'''
|43%
|–
|6%
"""


def parse(text: str, as_of=date(2026, 10, 15)):
    return wiki.poll_table(text, "Alex Smith", "Jordan Lee", as_of, wiki.named_refs(text))


def test_a_poll_row_parses_with_its_source_and_partisan_mark():
    (poll,) = parse(article(ROW)).polls
    assert (poll.pollster, poll.end, poll.sample, poll.population, poll.moe) == \
        ("Example Polling", "2026-10-09", 1101, "LV", 2.9)
    assert (poll.dem, poll.rep, poll.partisan, poll.url) == (51.0, 43.0, "R", "https://example.org/poll")


def test_shares_over_100_fail_the_gate():
    with pytest.raises(wiki.GateFailure, match="sum"):
        parse(article(ROW.replace("|6%", "|16%")))


def test_fieldwork_after_the_revision_fails_the_gate():
    with pytest.raises(wiki.GateFailure, match="outside"):
        parse(article(ROW), as_of=date(2026, 10, 8))


def test_a_cell_spanning_two_columns_counts_once():
    (poll,) = parse(article(ROW.replace("|–\n|6%\n", '|colspan="2"|6%\n'))).polls
    assert (poll.dem, poll.rep) == (51.0, 43.0)


def test_a_poll_listed_twice_fails_the_gate():
    with pytest.raises(wiki.GateFailure, match="twice"):
        parse(article(ROW + ROW))


def test_a_dividing_line_is_not_a_poll():
    divider = '|-\n|style="background:#eee"|\n|June 16, 2026\n|colspan="6"|Primary runoff\n'
    assert len(parse(article(ROW + divider)).polls) == 1


def test_a_rowspan_without_its_separator_still_spans():
    two = """|-
|rowspan="2"|Two Question Polling<ref name="cited"/>
|rowspan="2"|October 1–3, 2026
|rowspan="2"|600 (LV)
|rowspan="2" {{sdash}}
|48%
|44%
|–
|8%
|-
|50%
|46%
|–
|4%
"""
    polls = parse(article(two)).polls
    assert [(p.dem, p.rep) for p in polls] == [(48.0, 44.0), (50.0, 46.0)]
    assert polls[0].url == "https://example.org/named"


def test_a_bipartisan_pair_carries_no_sponsor_flag():
    (poll,) = parse(article(ROW.replace("Example Polling (R)", "First Firm (R)/<br/>Second Firm (D)"))).polls
    assert poll.partisan == ""


def test_a_table_for_other_candidates_is_not_read():
    text = article(ROW, HEADER.replace("Alex<br />Smith (D)", "Casey<br />Hill (D)"))
    assert parse(text) is None


def test_a_question_without_a_nominee_is_skipped():
    other = ROW.replace("|43%", "|–").replace("Example Polling (R)", "Other Polling")
    assert [p.pollster for p in parse(article(ROW + other)).polls] == ["Example Polling"]


# ---------------------------------------------------------------------------
# The build, the API, and what they never say
# ---------------------------------------------------------------------------


def _seed(db):
    db.add(Race(id="2026-SEN-OH", cycle_year=2026, office="S", state="OH", is_special=False))
    db.add(Candidate(id="S1", race_id="2026-SEN-OH", name="SMITH, ALEX", ballot_name="Alex Smith", party="DEM",
                     incumbent_challenge="C", confirmed_general=True))
    db.add(Candidate(id="S2", race_id="2026-SEN-OH", name="LEE, JORDAN", ballot_name="Jordan Lee", party="REP",
                     incumbent_challenge="I", confirmed_general=True))
    db.add(Race(id="2026-HOUSE-OH-9", cycle_year=2026, office="H", state="OH", district=9, is_special=False))
    db.add(Candidate(id="H1", race_id="2026-HOUSE-OH-9", name="SMITH, ALEX", ballot_name="Alex Smith", party="DEM",
                     incumbent_challenge="I", confirmed_general=True))
    db.add(Candidate(id="H2", race_id="2026-HOUSE-OH-9", name="LEE, JORDAN", ballot_name="Jordan Lee", party="REP",
                     incumbent_challenge="C", confirmed_general=True))
    db.add(Race(id="2026-SEN-WY", cycle_year=2026, office="S", state="WY", is_special=False))
    db.add(Candidate(id="S3", race_id="2026-SEN-WY", name="ROE, PAT", party="REP", incumbent_challenge="O",
                     confirmed_general=True))
    db.commit()


GENERIC = """==Generic congressional ballot aggregate polls==
<section begin="GenericBallotAgg"/>
{|class="wikitable"
!Source of poll<br>aggregation
!Dates<br>administered
!Dates<br>updated
!Republicans
!Democrats
!Other/<br>Undecided
!Margin
|-
|Example Average<ref>{{cite web |url=https://example.org/avg |title=a}}</ref>
|January 9, 2025 – October 13, 2026
|October 14, 2026
|44.0%
|{{Party shading/Democratic}} |'''48.0%'''
|8.0%
|Democrats +4.0%
|}<section end="GenericBallotAgg" />
"""


def _revisions():
    rev = {"revid": 7, "timestamp": "2026-10-15T00:00:00Z"}
    return {
        "2026 United States Senate election in Ohio": dict(rev, title="2026 United States Senate election in Ohio",
                                                           content=article(ROW)),
        build.GENERIC_ARTICLE: dict(rev, title=build.GENERIC_ARTICLE, content=GENERIC),
        HOUSE_OHIO: dict(rev, title=HOUSE_OHIO, content="== District 8 ==\nnothing\n== District 9 ==\n"
                         + article(ROW).replace("==General election==", "===General election===")
                         .replace("===Polling===", "====Polling====").replace("===Results===", "====Results====")),
    }


HOUSE_OHIO = "2026 United States House of Representatives elections in Ohio"


def _built(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 15, 16, 0))
    _seed(db_session)
    return build.build(db_session, _revisions(), house_titles=[HOUSE_OHIO])


def _seats(data):
    return [s for c in data["chambers"].values() for s in c["seats"]]


def test_build_describes_ranges_and_never_chances(db_session, freeze_utcnow):
    data = _built(db_session, freeze_utcnow)
    oh = next(s for s in _seats(data) if s["id"] == "2026-SEN-OH")
    assert oh["status"] == "range" and oh["range80"][0] < oh["mean"] < oh["range80"][1]
    assert oh["polls"]["used"] == 1 and oh["polls"]["list"][0]["usedInAverage"]
    assert oh["polls"]["all"] == oh["polls"]["list"]
    wy = next(s for s in _seats(data) if s["id"] == "2026-SEN-WY")
    assert wy["status"] == "not_contested" and "Democratic" in wy["reason"]


def test_a_race_with_no_confirmed_ballot_says_so_not_uncontested(db_session, freeze_utcnow):
    db_session.add(Race(id="2026-SEN-NY", cycle_year=2026, office="S", state="NY", is_special=False))
    db_session.add(Candidate(id="F1", race_id="2026-SEN-NY", name="FILER, ONE", party="DEM",
                             confirmed_general=False))
    data = _built(db_session, freeze_utcnow)
    ny = next(s for s in _seats(data) if s["id"] == "2026-SEN-NY")
    assert ny["status"] == "unconfirmed" and "not yet confirmed" in ny["reason"]
    assert data["national"]["margin"] == -4.0
    # The seats and the national line are what the map shows; the
    # calibration block is the method page's evidence (how often the
    # method's implied chances came true on held-out cycles).
    text = json.dumps({"seats": _seats(data), "national": data["national"]}).lower()
    for word in ("wins", "winner", "favored", "favoured", "projected", "probability", "pdem", "chance"):
        assert word not in text, word


def test_a_house_seat_reads_its_district_section(db_session, freeze_utcnow):
    data = _built(db_session, freeze_utcnow)
    (oh9,) = data["chambers"]["house"]["seats"]
    assert oh9["status"] == "range" and oh9["polls"]["used"] == 1
    assert oh9["article"]["title"] == HOUSE_OHIO and oh9["district"] == 9
    # Its lean is the 2026-lines computed lean the site publishes.
    assert oh9["lean"] == district_lean.lean_for("OH", 9, build.HOUSE_LINES)


@pytest.mark.parametrize("chamber,fitted", [("senate", FITTED), ("house", HOUSE_FITTED)])
def test_calibration_shown_is_the_fitted_evaluation(db_session, freeze_utcnow, chamber, fitted):
    data = _built(db_session, freeze_utcnow)
    ev = fitted["evaluation"][str(data["horizon"])]
    shown = data["chambers"][chamber]["calibration"]
    assert shown["table"] == ev["calibration"]
    assert shown["coverage80"] == ev["coverage80"]
    assert data["horizon"] == 14  # 19 days out: the 14-day fit


def test_labels_and_colours_are_decided_together_on_the_written_value():
    # 4.6 is written R+5, so it takes the 5-15 colour, never the "<5" one.
    assert build.lean_label(4.6) == "R+5" and build.bin_of(4.6) == "R_usually"
    assert build.bin_of(0.4) == "close" and build.lean_label(0.4) == "EVEN"
    # A range whose low edge is written EVEN reaches even: grey, not R.
    assert build.range_close(0.3, 9.0) and build.range_bin(0.3, 9.0) == "close"
    assert build.range_label(-8.7, 5.3) == "D+9 to R+5"


def test_a_rejected_table_and_an_outage_are_named_not_read_as_no_poll(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 10, 15, 16, 0))
    _seed(db_session)
    revs = _revisions()
    revs["2026 United States Senate election in Ohio"]["content"] = article(ROW.replace("|6%", "|16%"))
    oh = next(s for s in _seats(build.build(db_session, revs, house_titles=[HOUSE_OHIO]))
              if s["id"] == "2026-SEN-OH")
    assert oh["status"] == "range" and oh["pollSource"]["state"] == "rejected"
    assert "sum" in oh["pollSource"]["detail"]
    with pytest.raises(wiki.GateFailure):
        build.build(db_session, None)  # no national figure to keep either: refuses to build blind


def test_an_outage_keeps_the_last_good_polls_and_dates_the_national_figure(db_session, freeze_utcnow, tmp_path,
                                                                            monkeypatch):
    freeze_utcnow(datetime(2026, 10, 15, 16, 0))
    _seed(db_session)
    path = tmp_path / "election_map.json"
    monkeypatch.setattr(build, "map_path", lambda: str(path))
    path.write_text(json.dumps(build.build(db_session, _revisions(), house_titles=[HOUSE_OHIO])))
    again = build.build(db_session, None)
    oh = next(s for s in _seats(again) if s["id"] == "2026-SEN-OH")
    assert oh["pollSource"]["state"] == "unreachable" and oh["pollSource"]["keptFrom"] == "2026-10-15"
    assert oh["polls"]["used"] == 1
    assert again["national"]["status"] == "kept" and again["national"]["updated"] == "2026-10-14"


def test_a_seat_with_no_lean_says_so(db_session, freeze_utcnow, monkeypatch):
    real = build._lean_table

    def without_ohio():
        leans, meta = real()
        return {k: v for k, v in leans.items() if k != "OH"}, meta

    monkeypatch.setattr(build, "_lean_table", without_ohio)
    oh = next(s for s in _seats(_built(db_session, freeze_utcnow)) if s["id"] == "2026-SEN-OH")
    assert oh["status"] == "no_lean" and oh["leanLabel"] is None


def test_an_unreachable_wikipedia_raises_an_ops_alert(db_session, freeze_utcnow, monkeypatch, tmp_path):
    freeze_utcnow(datetime(2026, 10, 15, 16, 0))
    _seed(db_session)
    alerts = []
    monkeypatch.setattr(build, "send_ops_alert", lambda subject, body, **kw: alerts.append(subject))
    monkeypatch.setattr(build, "map_path", lambda: str(tmp_path / "m.json"))

    async def down(titles):
        return None

    monkeypatch.setattr(build.wiki, "fetch_current", down)
    with pytest.raises(wiki.GateFailure):
        asyncio.run(build.refresh(db_session))
    assert alerts == ["Election map: Wikipedia unreachable"]


def test_api_serves_one_chamber_when_asked(db_session, freeze_utcnow, monkeypatch):
    data = _built(db_session, freeze_utcnow)
    body = _client(monkeypatch, data).get("/api/elections/map?chamber=house").json()
    assert list(body["chambers"]) == ["house"] and body["chambersAvailable"] == ["senate", "house"]


def test_api_marks_an_old_map_stale(db_session, freeze_utcnow, monkeypatch):
    data = _built(db_session, freeze_utcnow)
    client = _client(monkeypatch, data)
    assert client.get("/api/elections/map").json()["stale"] is False
    freeze_utcnow(datetime(2026, 10, 16, 6, 0))
    assert client.get("/api/elections/map").json()["stale"] is True


def test_build_stops_on_election_day(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 11, 3, 15, 0))
    _seed(db_session)
    assert build.build(db_session, _revisions()) is None


def _client(monkeypatch, data, phase=CAMPAIGN):
    monkeypatch.setattr(api, "_load", lambda: data)
    monkeypatch.setattr(api, "active_election", lambda: ActiveElection(date(2026, 11, 3), phase, None, None))
    app = FastAPI()
    app.include_router(api.router, prefix="/api")
    return TestClient(app)


def test_api_serves_the_map_with_its_licence(db_session, freeze_utcnow, monkeypatch):
    data = _built(db_session, freeze_utcnow)
    client = _client(monkeypatch, data)
    body = client.get("/api/elections/map").json()
    assert all("all" not in s.get("polls", {}) for c in body["chambers"].values() for s in c["seats"])
    assert body["sources"]["polls"]["license"] == "CC BY-SA 4.0"
    assert body["sources"]["polls"]["attribution"] == "Wikipedia contributors"
    csv_text = client.get("/api/elections/map.csv").text
    assert "Wikipedia contributors, CC BY-SA 4.0" in csv_text and "Not a race call." in csv_text
    assert "\nhouse,2026-HOUSE-OH-9,OH,9," in csv_text


def test_api_hides_the_map_from_election_day(db_session, freeze_utcnow, monkeypatch):
    data = _built(db_session, freeze_utcnow)
    client = _client(monkeypatch, data, phase=ELECTION_DAY)
    assert client.get("/api/elections/map").status_code == 404
    assert client.get("/api/elections/map.csv").status_code == 404


def test_the_method_reads_nothing_from_scoring():
    root = Path(__file__).resolve().parents[1] / "app" / "forecast"
    for path in root.glob("*.py"):
        imports = [line for line in path.read_text().splitlines() if line.startswith(("from ", "import "))]
        assert not [line for line in imports if "app.pipeline.analyze" in line or "config_definitions" in line]
