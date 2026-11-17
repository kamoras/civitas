"""The whole president run (president_pipeline.run_president_pipeline), on a
test database, from every source it reads to the rows it stores.

Every source is stubbed with a small, made-up but well-formed series: the
roster (six presidencies, one before polling, one sitting), executive-order
counts, real GDP, unemployment and consumer prices, BLS jobs, approval polls
with the by-party columns, House polarization, election margins and the
historians' survey. The World Bank answers nothing new, so the peer
comparison runs on the bundled Maddison file. Six presidencies are fewer
than the bundled president_reference.json was measured on, so its stats
stand where the run's own would be thinner (keep_fuller): a change to that
file moves the golden too. The rows the run stores are pinned in
fixtures/president_run_golden.json.
"""

import math
import pathlib
from datetime import date, datetime, timedelta

import pytest

from app.models import President
from app.pipeline import president_pipeline as pp
from app.pipeline.fetch.macro_series import CONSUMER_PRICES, PAYROLLS, UNEMPLOYMENT
from app.pipeline.fetch.presidential_approval import PRESIDENT_APPROVAL_SLUGS, ApprovalPoll
from app.pipeline.fetch.presidential_roster import RosterEntry
from tests.pipeline_run_helpers import matches_golden, stored

GOLDEN = pathlib.Path(__file__).parent / "fixtures" / "president_run_golden.json"
NOW = datetime(2026, 10, 15, 16, 0)

ROSTER = [
    RosterEntry("lincoln-16", "Abraham Lincoln", "1861-03-04", "1865-04-15", 16),
    RosterEntry("truman-33", "Harry S. Truman", "1945-04-12", "1953-01-20", 33),
    RosterEntry("eisenhower-34", "Dwight D. Eisenhower", "1953-01-20", "1961-01-20", 34),
    RosterEntry("obama-44", "Barack Obama", "2009-01-20", "2017-01-20", 44),
    RosterEntry("biden-46", "Joseph R. Biden", "2021-01-20", "2025-01-20", 46),
    RosterEntry("trump-47", "Donald J. Trump", "2025-01-20", None, 47),
]
PARTY = {"lincoln-16": "R", "truman-33": "D", "eisenhower-34": "R", "obama-44": "D",
         "biden-46": "D", "trump-47": "R"}


def _wave(year: int, scale: float) -> float:
    return scale * math.sin(year * 0.7)


def _polls(entry: RosterEntry) -> list[ApprovalPoll]:
    """A poll every 90 days of the term, approval drifting with its number."""
    start = date.fromisoformat(entry.term_start)
    end = date.fromisoformat(entry.term_end) if entry.term_end else NOW.date()
    polls, day, i = [], start + timedelta(days=10), 0
    while day < end:
        approve = 50 + (entry.number % 7) - i * 0.4 + _wave(day.year, 3)
        own, other = (85 - i * 0.3, 15 + i * 0.1)
        dem, rep = (own, other) if PARTY[entry.id] == "D" else (other, own)
        polls.append(ApprovalPoll(
            day.strftime("%m/%d/%Y"), (day + timedelta(days=3)).strftime("%m/%d/%Y"),
            round(approve, 1), round(95 - approve, 1), 5, "gallup",
            dem=round(dem, 1), ind=round(approve - 3, 1), rep=round(rep, 1),
        ))
        day += timedelta(days=90)
        i += 1
    return polls


SOURCES = {
    "eo": {e.id: {"total_orders": 40 + 9 * e.number, "party": PARTY[e.id]} for e in ROSTER},
    "gdp": {y: 1000 * 1.032 ** (y - 1790) * (1 + _wave(y, 0.01)) for y in range(1790, NOW.year)},
    UNEMPLOYMENT: {y: 5.5 + _wave(y, 1.5) for y in range(1948, NOW.year)},
    CONSUMER_PRICES: {y: 24 * 1.035 ** (y - 1947) * (1 + _wave(y, 0.004)) for y in range(1947, NOW.year)},
    PAYROLLS: {y: 40000 + 1500 * (y - 1939) for y in range(1939, NOW.year)},
    "polls": {e.id: _polls(e) for e in ROSTER if e.number >= 33},
    "polarization": {c: round(0.5 + 0.006 * (c - 79), 4) for c in range(79, 120)},
    "margins": {"lincoln-16": 10.1, "truman-33": 4.5, "eisenhower-34": 13.0, "obama-44": 7.2,
                "biden-46": 4.5, "trump-47": 1.5},
    "historians": {"lincoln-16": 897, "truman-33": 713, "eisenhower-34": 734, "obama-44": 664,
                   "biden-46": 520},
}


@pytest.fixture()
def president_run(db_session, monkeypatch, freeze_utcnow):
    """The run with every source answering from SOURCES; `down` turns them
    all off for the next run, as a night every source is unreachable."""
    freeze_utcnow(NOW)
    down = {"on": False}

    def answer(value, empty):
        return empty if down["on"] else value

    async def eo(db):
        return answer(SOURCES["eo"], {})

    async def roster(db):
        return answer(list(ROSTER), [])

    async def gdp(client, db, start, end):
        return answer(SOURCES["gdp"], {})

    async def series(client, db, name):
        return answer(SOURCES[name], None)

    async def jobs(client, start, end):
        return answer(round(0.12 * (int(end[:4]) if end else 2029) - 0.11 * int(start[:4]), 3), None)

    async def slugs(db, presidents):
        return dict(PRESIDENT_APPROVAL_SLUGS)

    async def polls(db, pid, slug):
        return answer(SOURCES["polls"].get(pid, []), [])

    async def polarization(db, congresses, sitting):
        return answer({c: d for c, d in SOURCES["polarization"].items() if c in congresses}, {})

    async def margins(db):
        return answer(SOURCES["margins"], {})

    async def historians(client, db):
        return answer(SOURCES["historians"], {})

    async def world_bank(client, db, first, last):
        return answer({}, None)

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(pp, "fetch_historical_eo_counts", eo)
    monkeypatch.setattr(pp, "fetch_presidential_roster", roster)
    monkeypatch.setattr(pp, "fetch_historical_real_gdp", gdp)
    monkeypatch.setattr(pp, "fetch_annual_series", series)
    monkeypatch.setattr(pp, "fetch_jobs_for_president", jobs)
    monkeypatch.setattr(pp, "approval_slugs", slugs)
    monkeypatch.setattr(pp, "fetch_president_approval_history", polls)
    monkeypatch.setattr(pp, "fetch_house_party_distance", polarization)
    monkeypatch.setattr(pp, "fetch_election_margins", margins)
    monkeypatch.setattr(pp, "fetch_cspan_historians_survey", historians)
    monkeypatch.setattr(pp, "fetch_world_bank_per_capita", world_bank)
    monkeypatch.setattr(pp, "fetch_president_portraits", nothing)
    monkeypatch.setattr(pp, "fetch_elections", nothing)
    return db_session, down


async def test_a_run_scores_every_president_from_its_sources(president_run):
    db, _ = president_run
    result = await pp.run_president_pipeline(db)
    assert (result["updated"], result["failed"]) == (6, 0)
    by_id = {p.id: p for p in db.query(President)}
    # The president before polling is scored on election margin, the
    # others on approval; the sitting president has a recent average.
    assert by_id["lincoln-16"].election_margin == 10.1 and by_id["lincoln-16"].avg_approval is None
    assert by_id["obama-44"].election_margin is None and by_id["obama-44"].avg_approval is not None
    assert by_id["trump-47"].recent_avg_approval is not None
    assert all(p.score_public_mandate is not None for p in by_id.values())
    assert matches_golden(db, GOLDEN)


async def test_a_night_every_source_is_down_keeps_what_was_stored(president_run):
    """No figure a source supplied is lost to a night it can't be read: the
    stored inputs, and the scores computed from them, stand."""
    db, down = president_run
    await pp.run_president_pipeline(db)
    before = stored(db)["presidents"]
    down["on"] = True
    result = await pp.run_president_pipeline(db)
    assert (result["updated"], result["failed"]) == (6, 0)
    assert stored(db)["presidents"] == before
