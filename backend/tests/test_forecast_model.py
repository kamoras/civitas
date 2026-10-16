"""The election map's method (app/forecast): the fit reproduces from
its checked-in inputs, the poll parser's gates hold, the output carries no
win language and its licence, and the map hides from election day on."""

import asyncio
import json
import math
from datetime import date, datetime, timedelta
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
from app.pipeline.fetch.district_pvi import congress_for_election

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
    written = {k: v for k, v in FITTED["evaluation"]["14"].items() if k != "forward"}
    assert_close(again, written, abs_tol=1e-3)


def test_the_forward_check_reproduces():
    # The newest cycle, fitted on the cycles before it only.
    again = model.forward(TRAINING["races"], TRAINING["generic"], 14)
    assert_close(again, FITTED["evaluation"]["14"]["forward"], abs_tol=1e-3)
    assert again["cycle"] == max(r["year"] for r in TRAINING["races"])
    assert again["trainedOn"][1] < again["cycle"]


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
    written = {k: v for k, v in HOUSE_FITTED["evaluation"]["14"].items() if k != "forward"}
    assert_close(again, written, abs_tol=1e-3)


def test_house_training_leans_are_the_history_files_own():
    """Each House race's lean is the one the history file holds for that
    cycle's lines, which uses only presidential votes cast before it."""
    for r in HOUSE_TRAINING["races"]:
        assert r["lean"] == HOUSE_LEAN["leans"][str(r["year"])][r["state"]]
    # Every cycle the FEC has published a compilation for and the lean
    # history and the generic-ballot polls reach.
    cov = HOUSE_TRAINING["_coverage"]
    assert {r["year"] for r in HOUSE_TRAINING["races"]} == set(cov["results"]) & set(cov["lean"]) & set(cov["polls"])
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
    # Every cycle both the training polls and MEDSL's results reach.
    years = {r["year"] for r in TRAINING["races"]}
    cov = TRAINING["_coverage"]
    assert years == set(cov["polls"]) & set(cov["results"])
    assert all(r["inc"] in (-1, 0, 1) for r in TRAINING["races"])
    # A race whose two sources disagree is left out, never fitted on either.
    left_out = {(e["year"], e["state"], e["special"]) for e in TRAINING["_excluded"]}
    assert not left_out & {(r["year"], r["state"], r["special"]) for r in TRAINING["races"]}


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


def parse(text: str, as_of=date(2026, 10, 15), cycle: int = 2026):
    return wiki.poll_table(text, "Alex Smith", "Jordan Lee", as_of, wiki.named_refs(text), cycle)


def in_cycle(text: str, cycle: int) -> str:
    """A fixture written for the 2026 cycle, moved to `cycle` (its dates
    and titles), so the same article tests any election."""
    return text.replace("2026", str(cycle)).replace("2025", str(cycle - 1))


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


def _seed(db, cycle: int = 2026):
    c = cycle
    db.add(Race(id=f"{c}-SEN-OH", cycle_year=c, office="S", state="OH", is_special=False))
    db.add(Candidate(id=f"S1-{c}", race_id=f"{c}-SEN-OH", name="SMITH, ALEX", ballot_name="Alex Smith", party="DEM",
                     incumbent_challenge="C", confirmed_general=True))
    db.add(Candidate(id=f"S2-{c}", race_id=f"{c}-SEN-OH", name="LEE, JORDAN", ballot_name="Jordan Lee", party="REP",
                     incumbent_challenge="I", confirmed_general=True))
    db.add(Race(id=f"{c}-HOUSE-OH-9", cycle_year=c, office="H", state="OH", district=9, is_special=False))
    db.add(Candidate(id=f"H1-{c}", race_id=f"{c}-HOUSE-OH-9", name="SMITH, ALEX", ballot_name="Alex Smith",
                     party="DEM", incumbent_challenge="I", confirmed_general=True))
    db.add(Candidate(id=f"H2-{c}", race_id=f"{c}-HOUSE-OH-9", name="LEE, JORDAN", ballot_name="Jordan Lee",
                     party="REP", incumbent_challenge="C", confirmed_general=True))
    db.add(Race(id=f"{c}-SEN-WY", cycle_year=c, office="S", state="WY", is_special=False))
    db.add(Candidate(id=f"S3-{c}", race_id=f"{c}-SEN-WY", name="ROE, PAT", party="REP", incumbent_challenge="O",
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


def senate_ohio(cycle: int = 2026) -> str:
    return f"{cycle} United States Senate election in Ohio"


def house_ohio(cycle: int = 2026) -> str:
    return f"{cycle} United States House of Representatives elections in Ohio"


HOUSE_OHIO = house_ohio()


def _revisions(cycle: int = 2026):
    rev = {"revid": 7, "timestamp": f"{cycle}-10-15T00:00:00Z"}
    row = in_cycle(ROW, cycle)
    generic = build.generic_article(cycle)
    return {
        senate_ohio(cycle): dict(rev, title=senate_ohio(cycle), content=article(row)),
        generic: dict(rev, title=generic, content=in_cycle(GENERIC, cycle)),
        house_ohio(cycle): dict(rev, title=house_ohio(cycle), content="== District 8 ==\nnothing\n== District 9 ==\n"
                                + article(row).replace("==General election==", "===General election===")
                                .replace("===Polling===", "====Polling====")
                                .replace("===Results===", "====Results====")),
    }


def _built(db_session, freeze_utcnow, cycle: int = 2026):
    freeze_utcnow(datetime(cycle, 10, 15, 16, 0))
    _seed(db_session, cycle)
    return build.build(db_session, _revisions(cycle), house_titles=[house_ohio(cycle)])


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
    # Its lean is the computed lean on the lines the cycle seats, the one
    # the race page shows.
    assert data["sources"]["lean"]["house"]["congress"] == congress_for_election(2026)
    assert oh9["lean"] == district_lean.lean_for("OH", 9, congress_for_election(2026))


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
    revs[senate_ohio()]["content"] = article(ROW.replace("|6%", "|16%"))
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
    day = date.fromisoformat(data["electionDay"])
    monkeypatch.setattr(api, "active_election", lambda: ActiveElection(day, phase, None, None))
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


# ---------------------------------------------------------------------------
# Any cycle, no code edit: the cycle comes from active_election()
# ---------------------------------------------------------------------------


def test_the_fieldwork_floor_is_the_cycles_own():
    assert wiki.earliest_fieldwork(2026) == date(2025, 1, 1)
    assert wiki.earliest_fieldwork(2028) == date(2027, 1, 1)
    # A 2028 article's row dated in the 2026 cycle is the last cycle's poll.
    with pytest.raises(wiki.GateFailure, match="2027-01-01"):
        parse(article(ROW), as_of=date(2028, 9, 15), cycle=2028)
    (poll,) = parse(article(in_cycle(ROW, 2028).replace("2028", "2027")), as_of=date(2028, 9, 15), cycle=2028).polls
    assert poll.end == "2027-10-09"
    # The generic-ballot averages have the same floor.
    with pytest.raises(wiki.GateFailure, match="date or share gate"):
        wiki.generic_ballot(GENERIC, date(2028, 9, 15), 2028)


def test_a_future_campaign_reads_its_own_articles_and_lines(db_session, freeze_utcnow, monkeypatch, tmp_path):
    """2028's campaign: its articles, its fieldwork floor and the lines the
    election seats (the 121st Congress's), with no edit to the code."""
    monkeypatch.setattr(build, "map_path", lambda: str(tmp_path / "m.json"))
    assert build.generic_article(2028) == "2028 United States House of Representatives elections"
    data = _built(db_session, freeze_utcnow, 2028)  # 2028-10-15, 23 days out
    assert data["cycle"] == 2028 and data["electionDay"] == "2028-11-07"
    assert data["national"]["article"] == build.generic_article(2028) and data["national"]["margin"] == -4.0
    oh = next(s for s in _seats(data) if s["id"] == "2028-SEN-OH")
    # Its poll is read and listed, but 23 days out is beyond the training
    # polls' reach, so the range is the structural line's alone.
    assert oh["article"]["title"] == senate_ohio(2028) and oh["polls"]["count"] == 1
    assert oh["fundamentalsOnly"] and oh["polls"]["used"] == 0 and len(oh["polls"]["list"]) == 1
    assert data["sources"]["lean"]["house"]["congress"] == congress_for_election(2028) == 121
    (oh9,) = data["chambers"]["house"]["seats"]
    # No computed lean for the 121st Congress's lines yet: the seat says so
    # (the race page shows no usual vote either), never the 120th's figure.
    expected = district_lean.lean_for("OH", 9, 121)
    if expected is None:
        assert oh9["status"] == "no_lean" and oh9["lean"] is None
    else:
        assert oh9["lean"] == expected


def test_beyond_the_poll_window_the_range_is_the_structural_lines_alone(db_session, freeze_utcnow):
    """Further out than the training polls reach, the method was never
    checked with polls: the range is the fundamentals' (with their own
    calibrated spread), labelled so, and the seat's polls are listed beside it."""
    near = _built(db_session, freeze_utcnow)  # 19 days out
    window = near["pollWindowDays"]
    assert near["pollsInRange"] and window == build.data_refresh.read("forecast_model.json")["fits"]["14"]["windowDays"]
    freeze_utcnow(datetime(2026, 11, 3) - timedelta(days=window + 1, hours=-16))
    revisions = _revisions()
    gen = revisions[build.generic_article(2026)]
    gen["content"] = gen["content"].replace("October 13, 2026", "October 1, 2026").replace("October 14, 2026",
                                                                                          "October 2, 2026")
    far = build.build(db_session, revisions, house_titles=[house_ohio()])
    assert far["daysToElection"] == window + 1 and not far["pollsInRange"]
    oh = next(s for s in _seats(far) if s["id"] == "2026-SEN-OH")
    assert oh["status"] == "range" and oh["fundamentalsOnly"]
    assert oh["mean"] == oh["prior"] and oh["pollAverage"] is None and oh["pollShare"] == 0
    assert oh["polls"]["list"] and not any(p["usedInAverage"] for p in oh["polls"]["list"])
    params = build.data_refresh.read("forecast_model.json")["fits"]["14"]
    half = model.Z80 * params["priorScale"] * params["priorSd"]
    assert abs(oh["range80"][1] - oh["range80"][0] - 2 * half) < 0.2


def test_a_new_cycle_never_keeps_the_last_cycles_figures(db_session, freeze_utcnow, monkeypatch, tmp_path):
    path = tmp_path / "election_map.json"
    monkeypatch.setattr(build, "map_path", lambda: str(path))
    path.write_text(json.dumps(_built(db_session, freeze_utcnow)))
    freeze_utcnow(datetime(2028, 9, 15, 16, 0))
    _seed(db_session, 2028)
    # Wikipedia down on the 2028 map's first build: no 2026 national figure
    # to fall back on, so it refuses rather than showing last cycle's.
    with pytest.raises(wiki.GateFailure):
        build.build(db_session, None)


def test_the_map_stops_on_a_future_election_night(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2028, 11, 8, 2, 0))  # 9 p.m. Eastern, Nov 7, 2028
    _seed(db_session, 2028)
    assert build.build(db_session, _revisions(2028)) is None


def test_a_missing_house_table_raises_an_ops_alert(db_session, freeze_utcnow, monkeypatch, tmp_path):
    monkeypatch.setattr(build, "map_path", lambda: str(tmp_path / "m.json"))
    monkeypatch.setattr(build.district_lean, "computed_lean", lambda congress: {})
    sent, resolved = [], []
    monkeypatch.setattr(build, "send_ops_alert", lambda subject, body, **kw: sent.append(kw["dedupe_key"]))
    monkeypatch.setattr(build, "resolve_ops_alert", resolved.append)
    data = _built(db_session, freeze_utcnow, 2028)
    build._alert_missing_house_lines(data, 2028)
    assert sent == [f"{build.HOUSE_LINES_CONDITION}-121"]
    data["chambers"]["house"]["seats"][0]["status"] = "range"
    build._alert_missing_house_lines(data, 2028)
    assert resolved == [build.HOUSE_LINES_CONDITION]


def test_api_names_the_cycle_and_its_lines(db_session, freeze_utcnow, monkeypatch, tmp_path):
    monkeypatch.setattr(build, "map_path", lambda: str(tmp_path / "m.json"))
    data = _built(db_session, freeze_utcnow, 2028)
    client = _client(monkeypatch, data)
    assert client.get("/api/elections/map").json()["cycle"] == 2028
    res = client.get("/api/elections/map.csv")
    assert res.text.startswith("# Civitas 2028 general-election map")
    assert "121st Congress lines" in res.text
    assert 'filename="civitas-2028-election-map.csv"' in res.headers["content-disposition"]


@pytest.mark.parametrize("today, due", [
    (date(2025, 1, 2), 2022),   # the 2024 winners not yet sworn in
    (date(2025, 1, 3), 2024),
    (date(2026, 10, 16), 2024),
    (date(2028, 11, 8), 2026),  # election night: 2028's count is not certified
    (date(2029, 1, 3), 2028),
])
def test_the_last_certified_general(today, due):
    assert build.last_certified_general(today) == due


def test_fits_a_cycle_behind_raise_an_ops_alert_until_refitted(freeze_utcnow, monkeypatch):
    sent, resolved = [], []
    monkeypatch.setattr(build, "send_ops_alert", lambda subject, body, **kw: sent.append((kw["dedupe_key"], body)))
    monkeypatch.setattr(build, "resolve_ops_alert", resolved.append)
    newest = max(r["year"] for r in TRAINING["races"])
    house_newest = max(r["year"] for r in HOUSE_TRAINING["races"])
    # The day the next general after the fits' newest is certified.
    freeze_utcnow(datetime(min(newest, house_newest) + 3, 1, 3, 12, 0))
    build.check_training_staleness()
    assert sent and sent[0][0] == f"{build.TRAINING_CONDITION}-{min(newest, house_newest) + 2}"
    # Refitted on that election: resolved.
    due = min(newest, house_newest) + 2
    monkeypatch.setattr(build.data_refresh, "read", lambda name: {"races": [{"year": due}]})
    build.check_training_staleness()
    assert resolved == [build.TRAINING_CONDITION]


def _staleness_alert(monkeypatch, freeze_utcnow, docs: dict, due: int) -> str:
    sent = []
    monkeypatch.setattr(build, "send_ops_alert", lambda subject, body, **kw: sent.append(body))
    monkeypatch.setattr(build, "resolve_ops_alert", lambda c: None)
    monkeypatch.setattr(build.data_refresh, "read", lambda name: docs.get(name, {}))
    freeze_utcnow(datetime(due + 1, 1, 3, 12, 0))
    build.check_training_staleness()
    (body,) = sent
    return body


def test_the_staleness_alert_names_each_missing_input(freeze_utcnow, monkeypatch):
    """Each chamber says which input stops it, never "add it to the script"."""
    docs = {
        "forecast_training.json": {"races": [{"year": 2026}], "_coverage": {"polls": [2026, 2028], "results": [2026]}},
        "forecast_house_training.json": {"races": [{"year": 2022}],
                                         "_coverage": {"polls": [2024, 2028], "results": [2022], "lean": [2022, 2024]}},
        "forecast_polls.json": {"retrieved": "2029-01-02"},
    }
    body = _staleness_alert(monkeypatch, freeze_utcnow, docs, 2028)
    assert "The 2028 general election's certified results are official" in body
    assert "Senate (fit stops at 2026): MEDSL's U.S. Senate file (doi:10.7910/DVN/PEJ5QU) has no 2028 results yet" \
        in body
    assert ("House (fit stops at 2022): the FEC has not published its Federal Elections 2028 compilation (US House "
            "Results by State); MEDSL's U.S. House file is behind a sign-in form Civitas does not answer; "
            "app/data/forecast_house_lean.json has no 2028 district lean") in body
    assert "Silver Bulletin" not in body  # both chambers' polls reach 2028
    assert "fit_forecast_model.py and refit" not in body


def test_the_staleness_alert_names_missing_polls(freeze_utcnow, monkeypatch):
    docs = {
        "forecast_training.json": {"races": [{"year": 2024}], "_coverage": {"polls": [2024], "results": [2024, 2026]}},
        "forecast_house_training.json": {"races": [{"year": 2026}], "_coverage": {}},
        "forecast_polls.json": {"retrieved": "2027-01-10"},
    }
    body = _staleness_alert(monkeypatch, freeze_utcnow, docs, 2026)
    assert ("Senate (fit stops at 2024): Silver Bulletin's raw-poll file (app/data/forecast_polls.json, retrieved "
            "2027-01-10) has no 2026 Senate polls yet.") in body
    assert "House (fit" not in body


def test_api_gives_a_states_seats_and_one_seat_in_a_line_each(db_session, freeze_utcnow, monkeypatch):
    data = _built(db_session, freeze_utcnow)
    client = _client(monkeypatch, data)
    body = client.get("/api/elections/map/seats?state=OH").json()
    by_id = {s["id"]: s for s in body["seats"]}
    oh = by_id["2026-SEN-OH"]
    full = next(s for s in _seats(data) if s["id"] == "2026-SEN-OH")
    # The build's own labels, never re-derived.
    assert oh["chamber"] == "senate" and oh["leanLabel"] == full["leanLabel"]
    assert oh["range80Label"] == full.get("range80Label") and set(by_id) <= {s["id"] for s in _seats(data)}
    assert all(s["state"] == "OH" for s in body["seats"])
    assert body["pollSource"]["license"] == "CC BY-SA 4.0"
    one = client.get("/api/elections/map/seats?seat=2026-HOUSE-OH-9").json()["seats"]
    assert [s["id"] for s in one] == ["2026-HOUSE-OH-9"] and one[0]["chamber"] == "house"
    assert client.get("/api/elections/map/seats").status_code == 422


def test_api_hides_the_seat_lines_from_election_day(db_session, freeze_utcnow, monkeypatch):
    data = _built(db_session, freeze_utcnow)
    assert _client(monkeypatch, data, phase=ELECTION_DAY).get("/api/elections/map/seats?state=OH").status_code == 404
