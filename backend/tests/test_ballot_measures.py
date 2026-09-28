"""Tests for statewide ballot-measure ingestion and the state ballot API.

The distinction under test throughout is the one the feature exists to
preserve: "this state has no measures" and "we don't know this state's
measures" are different claims, and a bug that collapses them tells a
voter in a state with 17 amendments that there is nothing to research.
"""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import elections
from app.models import BallotMeasure, MeasureCoverage, Race
from app.pipeline import election_pipeline
from app.pipeline.fetch import ballot_measures
from app.time_utils import utcnow


def _body(response):
    return json.loads(response.body)


def _measure(db, mid, state="GA", date="2026-11-03", number="Amendment 1", **kw):
    m = BallotMeasure(id=mid, state=state, election_date=date, number=number, **kw)
    db.add(m)
    return m


# ── fetch layer: the None-vs-[] contract ──────────────────────────────


def test_parse_measure_list_returns_none_on_unexpected_shape():
    """A shape we don't recognize is a FAILURE, not an empty ballot."""
    assert ballot_measures._parse_measure_list({}, "GA") is None
    assert ballot_measures._parse_measure_list({"measures": {}}, "GA") is None


def test_parse_measure_list_handles_single_object():
    """Vote Smart collapses one-element lists to a bare object."""
    payload = {"measures": {"measure": {"measureId": "1234", "title": "T", "measureCode": "A1"}}}
    parsed = ballot_measures._parse_measure_list(payload, "ga")
    assert len(parsed) == 1
    assert parsed[0]["id"] == "vs-1234"
    assert parsed[0]["state"] == "GA"


def test_parse_measure_list_skips_rows_without_an_id():
    payload = {"measures": {"measure": [{"title": "no id"}, {"measureId": "9", "title": "ok"}]}}
    parsed = ballot_measures._parse_measure_list(payload, "GA")
    assert [m["id"] for m in parsed] == ["vs-9"]


def test_text_treats_non_string_values_as_absent():
    """A nested object must not be coerced to the literal "{}" and rendered."""
    assert ballot_measures._text({"a": {}}, "a") is None
    assert ballot_measures._text({"a": "", "b": " x "}, "a", "b") == "x"


def test_is_configured_false_without_key(monkeypatch):
    monkeypatch.setattr(ballot_measures.settings, "VOTESMART_API_KEY", "")
    assert ballot_measures.is_configured() is False


@pytest.mark.asyncio
async def test_fetch_returns_none_when_unconfigured(monkeypatch, db_session):
    """No key must yield "unknown", never "no measures"."""
    monkeypatch.setattr(ballot_measures.settings, "VOTESMART_API_KEY", "")
    result = await ballot_measures.fetch_state_measures(None, db_session, "GA", 2026)
    assert result is None


# ── upsert + reconciliation ───────────────────────────────────────────


def test_upsert_skips_measure_without_an_election_date(db_session):
    """A measure we can't date is a measure we can't say is on WHICH ballot."""
    election_pipeline._upsert_measure(
        db_session,
        {"id": "vs-1", "state": "GA", "number": "A1", "title": "t"},
        {"official_title": "x"},
        "Vote Smart",
    )
    db_session.commit()
    assert db_session.query(BallotMeasure).count() == 0


def test_upsert_stores_verbatim_fields_and_clears_removed_status(db_session):
    _measure(db_session, "vs-1", status="removed")
    db_session.commit()

    election_pipeline._upsert_measure(
        db_session,
        {"id": "vs-1", "state": "GA", "number": "Amendment 1", "title": "T",
         "election_date": "2026-11-03"},
        {"official_title": "Official", "yes_means": "keeps the law",
         "no_means": "repeals it", "fiscal_impact": "$1"},
        "Vote Smart",
    )
    db_session.commit()

    m = db_session.query(BallotMeasure).one()
    assert m.official_title == "Official"
    assert m.yes_means == "keeps the law"
    assert m.no_means == "repeals it"
    # Back in the feed => certified again; reconciliation is the only
    # writer of "removed".
    assert m.status == "certified"


def test_two_unnumbered_measures_same_state_and_date_both_persist(db_session):
    """`number` defaults to "" whenever a source hasn't assigned one yet
    (ballot_measures._text's fallback), and a state can have more than one
    such measure at once early in a cycle. The state/date/number index is
    partial (WHERE number != '') for exactly this reason — a plain
    UniqueConstraint here collides on the second blank-numbered measure
    and the per-measure try/except in _sync_ballot_measures silently
    drops it, which is real data loss on the one dataset this feature
    can't afford to lose rows from."""
    election_pipeline._upsert_measure(
        db_session,
        {"id": "vs-1", "state": "GA", "number": "", "title": "First",
         "election_date": "2026-11-03"},
        {}, "Vote Smart",
    )
    db_session.commit()
    election_pipeline._upsert_measure(
        db_session,
        {"id": "vs-2", "state": "GA", "number": "", "title": "Second",
         "election_date": "2026-11-03"},
        {}, "Vote Smart",
    )
    db_session.commit()

    assert db_session.query(BallotMeasure).filter(
        BallotMeasure.state == "GA", BallotMeasure.election_date == "2026-11-03",
    ).count() == 2


def test_missing_yes_no_framing_stays_null(db_session):
    """Never inferred — the intuitive inference is inverted on a veto
    referendum, where "approved" RETAINS the law under challenge."""
    election_pipeline._upsert_measure(
        db_session,
        {"id": "vs-2", "state": "WA", "number": "R-101", "title": "Referendum",
         "election_date": "2026-11-03"},
        {"official_title": "Approved retains the law; rejected repeals it"},
        "Vote Smart",
    )
    db_session.commit()
    m = db_session.query(BallotMeasure).one()
    assert m.yes_means is None
    assert m.no_means is None


def test_reconcile_marks_unseen_measures_removed_not_deleted(db_session):
    _measure(db_session, "vs-1")
    _measure(db_session, "vs-2", number="Amendment 2")
    db_session.commit()

    marked = election_pipeline._reconcile_state_measures(
        db_session, "GA", {"2026-11-03"}, {"vs-1"},
    )
    db_session.commit()

    assert marked == 1
    assert db_session.query(BallotMeasure).count() == 2
    gone = db_session.query(BallotMeasure).filter(BallotMeasure.id == "vs-2").one()
    assert gone.status == "removed"


def test_reconcile_deletes_only_after_the_grace_window(db_session):
    stale = _measure(db_session, "vs-old", number="Amendment 9")
    stale.last_seen_at = utcnow() - timedelta(
        days=election_pipeline.MEASURE_REMOVAL_GRACE_DAYS + 1,
    )
    db_session.commit()

    election_pipeline._reconcile_state_measures(db_session, "GA", {"2026-11-03"}, {"vs-1"})
    db_session.commit()
    assert db_session.query(BallotMeasure).count() == 0


def test_coverage_row_is_upserted_not_duplicated(db_session):
    election_pipeline._set_coverage(db_session, "GA", "2026-11-03", MeasureCoverage.COVERED, 3)
    election_pipeline._set_coverage(
        db_session, "GA", "2026-11-03", MeasureCoverage.INGEST_FAILED, 0, error="boom",
    )
    db_session.commit()

    rows = db_session.query(MeasureCoverage).all()
    assert len(rows) == 1
    assert rows[0].status == MeasureCoverage.INGEST_FAILED
    assert rows[0].error_detail == "boom"


# ── API ───────────────────────────────────────────────────────────────


def test_state_ballot_404s_on_unknown_state(db_session):
    with pytest.raises(HTTPException) as exc:
        elections.state_ballot("ZZ", db=db_session)
    assert exc.value.status_code == 404


def test_state_ballot_allows_dc(db_session):
    """The map renders DC as a clickable region, so this route must not
    404 on a link the site itself produces."""
    data = _body(elections.state_ballot("dc", db=db_session))
    assert data["state"] == "DC"
    assert data["stateName"] == "District of Columbia"
    assert any("Delegate" in item for item in data["omits"])


def test_state_ballot_defaults_to_not_yet_covered(db_session):
    """A state we've never synced must never read as "no measures"."""
    data = _body(elections.state_ballot("GA", db=db_session))
    assert data["measures"] == []
    assert data["measureCoverage"]["status"] == MeasureCoverage.NOT_YET_COVERED


def test_state_ballot_distinguishes_confirmed_none(db_session):
    election_pipeline._set_coverage(
        db_session, "GA", elections.next_election_day(elections.utcnow().date()).isoformat(),
        MeasureCoverage.CONFIRMED_NONE, 0, source_name="Vote Smart",
    )
    db_session.commit()

    data = _body(elections.state_ballot("GA", db=db_session))
    assert data["measures"] == []
    assert data["measureCoverage"]["status"] == MeasureCoverage.CONFIRMED_NONE
    assert data["measureCoverage"]["sourceName"] == "Vote Smart"


def test_state_ballot_returns_measures_and_races(db_session):
    db_session.add(Race(
        id="2026-SEN-GA", cycle_year=election_pipeline.current_election_cycle(),
        office="S", state="GA", district=None,
    ))
    db_session.add(Race(
        id="2026-HOUSE-GA-7", cycle_year=election_pipeline.current_election_cycle(),
        office="H", state="GA", district=7,
    ))
    _measure(db_session, "vs-1", official_title="Official title",
             yes_means="yes does this", source_name="Vote Smart")
    db_session.commit()

    data = _body(elections.state_ballot("GA", db=db_session))
    assert len(data["senateRaces"]) == 1
    assert len(data["houseRaces"]) == 1
    assert len(data["measures"]) == 1
    measure = data["measures"][0]
    assert measure["officialTitle"] == "Official title"
    assert measure["yesMeans"] == "yes does this"
    assert measure["noMeans"] is None
    # No model-generated field exists on this payload, by design.
    assert "plainSummary" not in measure


def test_state_ballot_names_the_election_and_its_omissions(db_session):
    data = _body(elections.state_ballot("GA", db=db_session))
    assert data["electionType"] == "general"
    assert data["electionDate"].endswith(("-11-03", "-11-08", "-11-02", "-11-05", "-11-07"))
    assert any("Governor" in item for item in data["omits"])
    assert any("Primary" in item for item in data["omits"])
    assert data["officialLookup"]["url"]


def test_state_ballot_drops_the_governor_omission_once_that_state_is_covered(db_session):
    """`omits` has to shrink as the gaps actually close. A list that keeps
    disclaiming something the page now shows stops describing the page and
    becomes boilerplate a reader learns to skip past — including past the
    entries that are still true."""
    from app.pipeline.fetch.state_candidates import _sync_statewide_nominees

    before = _body(elections.state_ballot("GA", db=db_session))
    assert any("Governor" in item for item in before["omits"])
    assert before["statewideCoverage"]["status"] == "not_yet_covered"

    _sync_statewide_nominees(
        db_session, before["cycleYear"], "GA",
        {"strategy": "tabular", "source_name": "GA SoS", "statewide_offices": True},
        [{"office": "governor", "district": None, "party": "D", "last_name": "Real Person"}],
    )
    after = _body(elections.state_ballot("GA", db=db_session))
    assert not any("Governor" in item for item in after["omits"])
    # ... and the omissions that are still true are still there.
    assert any("Primary" in item for item in after["omits"])
    assert any("State legislative" in item for item in after["omits"])
    assert after["statewideRaces"][0]["nominees"][0]["name"] == "Real Person"
    # The office's term, from data/office_terms.json.
    assert after["statewideRaces"][0]["termYears"] == 4


def test_state_ballot_drops_the_governor_omission_when_coverage_is_confirmed_none(db_session):
    """A checked state that genuinely elects no executive officers this
    cycle must NOT keep the omission either: "this page omits Governor
    contests" implies there is one being withheld. Having looked and
    found none is knowledge, not a gap, and the section says so in its
    own words (confirmed_none) rather than through a scope disclaimer."""
    from app.pipeline.fetch.state_candidates import _sync_statewide_nominees

    cycle = _body(elections.state_ballot("GA", db=db_session))["cycleYear"]
    _sync_statewide_nominees(
        db_session, cycle, "GA",
        {"strategy": "tabular", "source_name": "GA SoS", "statewide_offices": True}, [],
    )
    data = _body(elections.state_ballot("GA", db=db_session))
    assert data["statewideCoverage"]["status"] == "confirmed_none"
    assert not any("Governor" in item for item in data["omits"])


def test_state_ballot_drops_the_legislature_omission_once_seats_are_covered(db_session):
    """The second omission to come off the list, by the same rule as the
    first: it describes the page, so it goes when it stops being true."""
    from app.pipeline.fetch.state_candidates import (
        _sync_state_leg_nominees,
        _sync_statewide_nominees,
    )

    before = _body(elections.state_ballot("GA", db=db_session))
    assert any("State legislative" in item for item in before["omits"])
    assert before["stateLegRaces"] == []

    cycle = before["cycleYear"]
    source = {"strategy": "tabular", "source_name": "GA SoS", "statewide_offices": True}
    _sync_statewide_nominees(db_session, cycle, "GA", source, [])
    _sync_state_leg_nominees(db_session, cycle, "GA", source, [
        {"office": "lower", "district": "3", "party": "D", "last_name": "Real Person"},
    ])

    after = _body(elections.state_ballot("GA", db=db_session))
    assert not any("State legislative" in item for item in after["omits"])
    assert after["stateLegRaces"][0]["chamber"] == "lower"
    assert after["stateLegRaces"][0]["districts"][0]["nominees"][0]["name"] == "Real Person"
    assert after["stateLegRaces"][0]["termYears"] == 2  # Georgia's House: two-year terms
    # The omissions that are still true stay.
    assert any("County and municipal" in item for item in after["omits"])
    assert any("Judicial" in item for item in after["omits"])


def test_state_ballot_keeps_the_legislature_omission_when_only_executives_are_covered(db_session):
    """A state whose executive contests are published but whose seats
    are not must still say the seats are missing. One flag opts a state
    in to both, but coverage is claimed per section, from what is
    actually there."""
    from app.pipeline.fetch.state_candidates import _sync_statewide_nominees

    cycle = _body(elections.state_ballot("GA", db=db_session))["cycleYear"]
    _sync_statewide_nominees(
        db_session, cycle, "GA",
        {"strategy": "tabular", "source_name": "GA SoS", "statewide_offices": True},
        [{"office": "governor", "district": None, "party": "D", "last_name": "A Governor"}],
    )
    data = _body(elections.state_ballot("GA", db=db_session))
    assert not any("Governor" in item for item in data["omits"])
    assert any("State legislative" in item for item in data["omits"])


def test_state_ballot_lookup_falls_back_when_no_verified_link(db_session):
    """An unverified per-state URL is never handed to a user — a dead link
    on "see your real ballot" is the worst failure this feature has."""
    data = _body(elections.state_ballot("GA", db=db_session))
    assert data["officialLookup"]["isStateSpecific"] is False
    assert data["officialLookup"]["url"].startswith("https://")


def test_removed_measures_are_still_returned(db_session):
    """Rendered as removed, never silently dropped."""
    _measure(db_session, "vs-1", status="removed")
    db_session.commit()
    data = _body(elections.state_ballot("GA", db=db_session))
    assert [m["status"] for m in data["measures"]] == ["removed"]


# ── official-ballot link gating ───────────────────────────────────────


def test_lookup_hides_unverified_state_entry(monkeypatch):
    from app.pipeline.fetch import ballot_lookup

    monkeypatch.setattr(ballot_lookup, "_cache", {
        "national_fallback": {"url": "https://nat.example", "label": "N", "source_name": "S"},
        "states": {"GA": {"url": "https://ga.example", "verified_at": None}},
    })
    result = ballot_lookup.lookup_for_state("GA")
    assert result["url"] == "https://nat.example"
    assert result["isStateSpecific"] is False


def test_lookup_uses_verified_state_entry(monkeypatch):
    from app.pipeline.fetch import ballot_lookup

    monkeypatch.setattr(ballot_lookup, "_cache", {
        "national_fallback": {"url": "https://nat.example", "label": "N", "source_name": "S"},
        "states": {"GA": {
            "url": "https://ga.example", "label": "GA lookup",
            "source_name": "GA SoS", "verified_at": "2026-08-01T00:00:00",
        }},
    })
    result = ballot_lookup.lookup_for_state("GA")
    assert result["url"] == "https://ga.example"
    assert result["isStateSpecific"] is True


@pytest.mark.asyncio
async def test_link_verification_clears_a_link_that_stops_resolving(monkeypatch, tmp_path):
    """A link that rots between runs must stop being shown, not keep
    riding a check that passed weeks ago."""
    from app.pipeline.fetch import ballot_lookup

    monkeypatch.setattr(ballot_lookup, "_VOLUME_PATH", str(tmp_path / "lookup.json"))
    monkeypatch.setattr(ballot_lookup, "_cache", {
        "national_fallback": {"url": "https://nat.example"},
        "states": {"GA": {"url": "https://ga.example", "verified_at": "2026-01-01T00:00:00"}},
    })

    class _Client:
        async def get(self, url, **kwargs):
            return SimpleNamespace(status_code=404)

    result = await ballot_lookup.refresh_link_verification(_Client())
    assert result["failed"] == 1
    saved = json.loads((tmp_path / "lookup.json").read_text())
    assert saved["states"]["GA"]["verified_at"] is None


# ── generic direct-PDF path (replaces Vote Smart per registered state) ─


def _fake_pdf_source(state="CA", source_name="Example Elections Office"):
    return {"url_pattern": "https://example.com/{year}/ballot.pdf",
            "source_name": source_name, "strategy": "fake"}


@pytest.mark.asyncio
async def test_sync_pdf_measures_upserts_directly_from_one_pdf_pass(monkeypatch, db_session):
    """A registered state's fetch already returns the full raw+detail
    shape in one PDF pass (unlike Vote Smart's list-then-per-item-detail
    calls) — confirm _sync_pdf_measures upserts straight from it and
    marks coverage."""
    from app.pipeline.fetch import ballot_measure_pdf_sources, ballot_measures_pdf

    source = _fake_pdf_source()
    monkeypatch.setattr(ballot_measure_pdf_sources, "configured_states", lambda: {"CA"})
    monkeypatch.setattr(ballot_measure_pdf_sources, "source_for_state", lambda state: source)

    async def fake_fetch(client, db, state, year, election_date):
        assert state == "CA"
        assert year == 2026
        assert election_date == "2026-11-03"
        return [ballot_measures_pdf._to_measure(
            "CA",
            {"number": "2", "title": "T", "origin": "the Legislature",
             "official_summary": "S", "fiscal_impact": "F",
             "yes_means": "Y", "no_means": "N"},
            election_date, "https://example.com/2026/ballot.pdf",
        )]

    monkeypatch.setattr(ballot_measures_pdf, "fetch_state_measures_pdf", fake_fetch)
    synced, failed, marked_removed = await election_pipeline._sync_pdf_measures(
        db_session, None, "2026-11-03",
    )
    assert (synced, failed, marked_removed) == (1, 0, 0)

    m = db_session.query(BallotMeasure).filter(BallotMeasure.state == "CA").one()
    assert m.id == "CA-2026-11-03-2"
    assert m.yes_means == "Y"
    assert m.source_name == source["source_name"]

    coverage = db_session.query(MeasureCoverage).filter(
        MeasureCoverage.state == "CA", MeasureCoverage.election_date == "2026-11-03",
    ).one()
    assert coverage.status == MeasureCoverage.COVERED
    assert coverage.source_name == source["source_name"]


@pytest.mark.asyncio
async def test_sync_pdf_measures_records_not_yet_published_without_failing(monkeypatch, db_session):
    """Maine's guide appears weeks before November. Until then the state
    is not yet covered — not ingest_failed (nothing is broken, so nothing
    should page anyone) and never confirmed_none."""
    from app.pipeline.fetch import ballot_measure_pdf_sources, ballot_measures_pdf
    from app.pipeline.fetch.ballot_measure_text import NotYetPublished

    monkeypatch.setattr(ballot_measure_pdf_sources, "configured_states", lambda: {"CA"})
    monkeypatch.setattr(ballot_measure_pdf_sources, "source_for_state", lambda state: _fake_pdf_source())

    async def fake_fetch(client, db, state, year, election_date):
        raise NotYetPublished("guide for 2026")

    monkeypatch.setattr(ballot_measures_pdf, "fetch_state_measures_pdf", fake_fetch)
    result = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert result == (0, 0, 0)

    coverage = db_session.query(MeasureCoverage).filter(
        MeasureCoverage.state == "CA", MeasureCoverage.election_date == "2026-11-03",
    ).one()
    assert coverage.status == MeasureCoverage.NOT_YET_COVERED
    assert "not yet published" in coverage.error_detail


@pytest.mark.asyncio
async def test_sync_pdf_measures_marks_ingest_failed_on_fetch_failure(monkeypatch, db_session):
    from app.pipeline.fetch import ballot_measure_pdf_sources, ballot_measures_pdf

    monkeypatch.setattr(ballot_measure_pdf_sources, "configured_states", lambda: {"CA"})
    monkeypatch.setattr(ballot_measure_pdf_sources, "source_for_state", lambda state: _fake_pdf_source())

    async def fake_fetch(client, db, state, year, election_date):
        return None

    monkeypatch.setattr(ballot_measures_pdf, "fetch_state_measures_pdf", fake_fetch)
    result = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert result == (0, 1, 0)

    coverage = db_session.query(MeasureCoverage).filter(
        MeasureCoverage.state == "CA", MeasureCoverage.election_date == "2026-11-03",
    ).one()
    assert coverage.status == MeasureCoverage.INGEST_FAILED


@pytest.mark.asyncio
async def test_sync_ballot_measures_runs_pdf_states_even_without_a_votesmart_key(monkeypatch, db_session):
    """The whole point: a state with a registered PDF source must not
    depend on VOTESMART_API_KEY at all."""
    from app.pipeline.fetch import ballot_measure_pdf_sources, ballot_measures, ballot_measures_pdf

    monkeypatch.setattr(ballot_measures.settings, "VOTESMART_API_KEY", "")
    monkeypatch.setattr(ballot_measure_pdf_sources, "configured_states", lambda: {"CA"})
    monkeypatch.setattr(ballot_measure_pdf_sources, "source_for_state", lambda state: _fake_pdf_source())

    async def fake_fetch(client, db, state, year, election_date):
        return [ballot_measures_pdf._to_measure(
            "CA",
            {"number": "1", "title": "T", "origin": None, "official_summary": "S",
             "fiscal_impact": None, "yes_means": None, "no_means": None},
            election_date, "https://example.com/2026/ballot.pdf",
        )]

    monkeypatch.setattr(ballot_measures_pdf, "fetch_state_measures_pdf", fake_fetch)
    result = await election_pipeline._sync_ballot_measures(db_session, None, 2026)
    assert result["skipped_other_states"] is True
    assert result["synced"] == 1
    assert db_session.query(BallotMeasure).filter(BallotMeasure.state == "CA").count() == 1


def test_upsert_stores_the_named_drafters(db_session):
    # The columns existed and the API served them, but nothing wrote them.
    election_pipeline._upsert_measure(
        db_session,
        {"id": "SD-2026-11-03-I", "state": "SD", "number": "I", "title": "Constitutional Amendment I",
         "election_date": "2026-11-03"},
        {"official_title": "An Amendment ...", "title_authority": "South Dakota Attorney General",
         "fiscal_authority": "South Dakota Legislative Research Council"},
        "South Dakota Secretary of State",
    )
    db_session.commit()
    m = db_session.query(BallotMeasure).one()
    assert m.title_authority == "South Dakota Attorney General"
    assert m.fiscal_authority == "South Dakota Legislative Research Council"


# ── direct-source sync: shrink guard, source switch, confirmed none ───


def _direct_source(monkeypatch, listed_by_call, source_name="Example Elections Office", state="CA"):
    """Register one direct-sourced state whose successive fetches return
    the given answers in order."""
    from app.pipeline.fetch import ballot_measure_pdf_sources, ballot_measures_pdf

    source = _fake_pdf_source(source_name=source_name)
    monkeypatch.setattr(ballot_measure_pdf_sources, "configured_states", lambda: {state})
    monkeypatch.setattr(ballot_measure_pdf_sources, "source_for_state", lambda st: source)
    answers = iter(listed_by_call)

    async def fake_fetch(client, db, st, year, election_date):
        answer = next(answers)
        if answer is None:
            return None
        if isinstance(answer, Exception):
            raise answer
        # "3-" is measure 3 reported by the state as struck (removed).
        return [
            ballot_measures_pdf._to_measure(
                st,
                {"number": n.rstrip("-"), "title": f"T{n}", "origin": None, "official_summary": "S",
                 "fiscal_impact": None, "yes_means": None, "no_means": None, "removed": n.endswith("-")},
                election_date, "https://example.com/ballot.pdf",
            )
            for n in answer
        ]

    monkeypatch.setattr(ballot_measures_pdf, "fetch_state_measures_pdf", fake_fetch)
    return source


def _coverage(db, state="CA"):
    return db.query(MeasureCoverage).filter(
        MeasureCoverage.state == state, MeasureCoverage.election_date == "2026-11-03",
    ).one()


@pytest.mark.asyncio
async def test_direct_sync_refuses_an_implausible_shrink(monkeypatch, db_session):
    """The regression: the Vote Smart path had the MEASURE_SHRINK_FLOOR
    guard and the direct path didn't — a read that returned 1 of 6 known
    measures marked the other five "no longer on the ballot"."""
    _direct_source(monkeypatch, [["1", "2", "3", "4", "5", "6"], ["1"]])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    synced, failed, marked = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert (failed, marked) == (1, 0)
    assert {m.status for m in db_session.query(BallotMeasure).all()} == {"certified"}
    coverage = _coverage(db_session)
    assert coverage.status == MeasureCoverage.INGEST_FAILED
    assert "implausible shrink" in coverage.error_detail


@pytest.mark.asyncio
async def test_direct_sync_reconciles_a_real_removal(monkeypatch, db_session):
    _direct_source(monkeypatch, [["1", "2", "3"], ["1", "2"]])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    _, failed, marked = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert (failed, marked) == (0, 1)
    gone = db_session.query(BallotMeasure).filter(BallotMeasure.id == "CA-2026-11-03-3").one()
    assert gone.status == "removed"


@pytest.mark.asyncio
async def test_a_confirmed_none_reconciles_earlier_rows(monkeypatch, db_session):
    """The regression: [] wrote CONFIRMED_NONE and returned before
    reconciling, so a measure listed earlier stayed "certified" on a page
    whose coverage said there were none."""
    runs = election_pipeline.MEASURE_SHRINK_CONFIRM_RUNS
    _direct_source(monkeypatch, [["1"]] + [[]] * runs)
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    # 1 -> 0 is a shrink past the floor: held back until it repeats ...
    for _ in range(runs - 1):
        await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
        assert db_session.query(BallotMeasure).one().status == "certified"
    # ... and then accepted: removed, never left "certified" under a none.
    _, failed, marked = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert (failed, marked) == (0, 1)
    assert db_session.query(BallotMeasure).one().status == "removed"
    assert _coverage(db_session).status == MeasureCoverage.CONFIRMED_NONE


@pytest.mark.asyncio
async def test_switching_a_state_to_direct_sourcing_retires_its_vote_smart_rows(monkeypatch, db_session):
    """The regression: a state moved from Vote Smart to its own office kept
    its vs-... rows — reconciled to "no longer on the ballot" for 45 days
    beside the same measure's new card, or (dated differently) left as a
    certified duplicate. They are superseded, so they are deleted — and
    only by a successful read."""
    _measure(db_session, "vs-100", state="CA", number="Prop 1", source_name="Vote Smart")
    _measure(db_session, "vs-101", state="CA", number="Prop 2", source_name="Vote Smart")
    # Another election in the same year (a primary) and an earlier
    # cycle's record are left alone: only the election read is replaced.
    _measure(db_session, "vs-090", state="CA", date="2026-06-02", number="Prop 1", source_name="Vote Smart")
    _measure(db_session, "vs-050", state="CA", date="2024-11-05", number="Prop 9", source_name="Vote Smart")
    db_session.commit()

    _direct_source(monkeypatch, [None, ["1", "2"]])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    # A failed read retires nothing.
    assert db_session.query(BallotMeasure).filter(BallotMeasure.id.like("vs-%")).count() == 4

    _, failed, marked = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert (failed, marked) == (0, 0)
    ids = sorted(m.id for m in db_session.query(BallotMeasure).all())
    assert ids == ["CA-2026-11-03-1", "CA-2026-11-03-2", "vs-050", "vs-090"]
    assert {m.status for m in db_session.query(BallotMeasure).all()} == {"certified"}


@pytest.mark.asyncio
async def test_a_confirmed_none_also_retires_the_superseded_source(monkeypatch, db_session):
    _measure(db_session, "vs-100", state="CA", number="Prop 1", source_name="Vote Smart")
    db_session.commit()
    _direct_source(monkeypatch, [[]])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert db_session.query(BallotMeasure).count() == 0
    assert _coverage(db_session).status == MeasureCoverage.CONFIRMED_NONE


@pytest.mark.asyncio
async def test_vote_smart_confirmed_none_reconciles_earlier_rows(monkeypatch, db_session):
    """Same rule on the Vote Smart path: CONFIRMED_NONE never sits beside a
    "certified" row for the same election."""
    from app.pipeline.fetch import ballot_measure_pdf_sources

    monkeypatch.setattr(ballot_measures.settings, "VOTESMART_API_KEY", "k")
    monkeypatch.setattr(ballot_measure_pdf_sources, "configured_states", lambda: set())
    monkeypatch.setattr(election_pipeline, "STATES_WITH_FEDERAL_RACES", {"GA"})
    monkeypatch.setattr(election_pipeline, "next_election_day", lambda d: __import__("datetime").date(2026, 11, 3))
    monkeypatch.setattr(election_pipeline, "MEASURE_SHRINK_CONFIRM_RUNS", 1)
    _measure(db_session, "vs-1", source_name="Vote Smart")
    db_session.commit()

    async def none_listed(client, db, state, cycle):
        return []

    monkeypatch.setattr(ballot_measures, "fetch_state_measures", none_listed)
    result = await election_pipeline._sync_ballot_measures(db_session, None, 2026)
    assert result["marked_removed"] == 1
    assert db_session.query(BallotMeasure).one().status == "removed"


@pytest.mark.asyncio
async def test_direct_source_failures_alert_without_a_votesmart_key(monkeypatch, db_session):
    """The regression: with VOTESMART_API_KEY unset, _sync_ballot_measures
    returned before its alert, so a broken direct reader never paged."""
    import app.ops_alerts as ops_alerts

    monkeypatch.setattr(ballot_measures.settings, "VOTESMART_API_KEY", "")
    _direct_source(monkeypatch, [None])
    sent = []
    monkeypatch.setattr(ops_alerts, "send_ops_alert", lambda *a, **kw: sent.append(a))
    result = await election_pipeline._sync_ballot_measures(db_session, None, 2026)
    assert result["failed_states"] == 1
    assert len(sent) == 1


# ── round 2: the write can't lose both answers; scope; freshness ─────


@pytest.mark.asyncio
async def test_a_direct_row_sharing_a_vote_smart_number_replaces_it(monkeypatch, db_session):
    """The regression: the direct row with Vote Smart's number tripped
    uq_ballot_measure_state_date_number and rolled back, but was still
    counted as seen, so the Vote Smart row was then deleted — an empty
    table under coverage "covered, 1"."""
    _measure(db_session, "vs-100", state="CA", number="1", source_name="Vote Smart")
    db_session.commit()
    _direct_source(monkeypatch, [["1"]])
    synced, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert (synced, failed) == (1, 0)
    [row] = db_session.query(BallotMeasure).all()
    assert row.id == "CA-2026-11-03-1" and row.status == "certified"
    assert _coverage(db_session).measure_count == 1


@pytest.mark.asyncio
async def test_a_failed_write_keeps_the_old_rows_and_counts_nothing(monkeypatch, db_session):
    _measure(db_session, "vs-100", state="CA", number="Prop 1", source_name="Vote Smart")
    db_session.commit()
    _direct_source(monkeypatch, [["1", "2"]])
    real_upsert = election_pipeline._upsert_measure

    def failing_upsert(db, raw, detail, source_name):
        if raw["number"] == "2":
            raise RuntimeError("constraint")
        real_upsert(db, raw, detail, source_name)

    monkeypatch.setattr(election_pipeline, "_upsert_measure", failing_upsert)
    synced, failed, marked = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert (synced, failed, marked) == (0, 1, 0)
    # The whole write rolled back: the superseded row is still there, and
    # nothing half-written is.
    assert [m.id for m in db_session.query(BallotMeasure).all()] == ["vs-100"]
    assert _coverage(db_session).status == MeasureCoverage.INGEST_FAILED


@pytest.mark.asyncio
async def test_a_reported_removal_explains_a_shrink(monkeypatch, db_session):
    """Florida reports a struck amendment (Status "Removed"); a shorter list
    the state itself explains is written at once, not held as suspicious."""
    _direct_source(monkeypatch, [["1", "2", "3", "4"], ["1", "2-", "3-", "4-"]])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    _, failed, marked = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert (failed, marked) == (0, 3)
    status = {m.number: m.status for m in db_session.query(BallotMeasure).all()}
    assert status == {"1": "certified", "2": "removed", "3": "removed", "4": "removed"}
    assert _coverage(db_session).measure_count == 1


@pytest.mark.asyncio
async def test_a_repeated_shorter_list_is_eventually_accepted(monkeypatch, db_session):
    runs = election_pipeline.MEASURE_SHRINK_CONFIRM_RUNS
    _direct_source(monkeypatch, [["1", "2", "3", "4"]] + [["1"]] * runs)
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    for _ in range(runs - 1):
        _, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
        assert failed == 1
    _, failed, marked = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert (failed, marked) == (0, 3)
    assert _coverage(db_session).status == MeasureCoverage.COVERED


@pytest.mark.asyncio
async def test_a_different_shorter_list_restarts_the_streak(monkeypatch, db_session):
    runs = election_pipeline.MEASURE_SHRINK_CONFIRM_RUNS
    answers = [["1", "2", "3", "4"]] + [["1"] if i % 2 else ["2"] for i in range(runs + 1)]
    _direct_source(monkeypatch, answers)
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    for _ in range(runs + 1):
        _, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
        assert failed == 1
    assert {m.status for m in db_session.query(BallotMeasure).all()} == {"certified"}


@pytest.mark.asyncio
async def test_earlier_cycles_do_not_inflate_the_shrink_baseline(monkeypatch, db_session):
    """The regression: the Vote Smart path counted every row the state had
    ever had, so 2024's measures held a real, shorter 2026 list back."""
    from app.pipeline.fetch import ballot_measure_pdf_sources

    monkeypatch.setattr(ballot_measures.settings, "VOTESMART_API_KEY", "k")
    monkeypatch.setattr(ballot_measure_pdf_sources, "configured_states", lambda: set())
    monkeypatch.setattr(election_pipeline, "STATES_WITH_FEDERAL_RACES", {"GA"})
    monkeypatch.setattr(election_pipeline, "next_election_day", lambda d: __import__("datetime").date(2026, 11, 3))
    monkeypatch.setattr(election_pipeline, "_prune_past_measures", lambda db: 0)
    for i in range(4):
        _measure(db_session, f"vs-old{i}", date="2024-11-05", number=f"Amendment {i}", source_name="Vote Smart")
    db_session.commit()

    async def one_listed(client, db, state, cycle):
        return [{"id": "vs-new", "source_measure_id": "new", "state": "GA", "number": "Amendment 1",
                 "title": "T", "election_date": "2026-11-03"}]

    async def detail(client, db, mid):
        return {"election_date": "2026-11-03"}

    monkeypatch.setattr(ballot_measures, "fetch_state_measures", one_listed)
    monkeypatch.setattr(ballot_measures, "fetch_measure_detail", detail)
    result = await election_pipeline._sync_ballot_measures(db_session, None, 2026)
    assert result["failed_states"] == 0
    assert _coverage(db_session, "GA").status == MeasureCoverage.COVERED


@pytest.mark.asyncio
async def test_a_failed_read_does_not_refresh_the_last_successful_check(monkeypatch, db_session):
    _direct_source(monkeypatch, [["1"], None])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    good = _coverage(db_session).last_success_at
    assert good is not None
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    row = _coverage(db_session)
    assert row.status == MeasureCoverage.INGEST_FAILED
    assert row.last_success_at == good
    assert row.checked_at >= good


@pytest.mark.asyncio
async def test_a_document_that_disappears_after_coverage_is_a_failure(monkeypatch, db_session):
    """The regression: a state covered for this election whose document
    later read as not published flipped quietly to not_yet_covered."""
    from app.pipeline.fetch.ballot_measure_text import NotYetPublished

    nights = 3
    _direct_source(monkeypatch, [["1"]] + [NotYetPublished("guide", deadline_applies=False)] * nights)
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    good = _coverage(db_session).last_success_at
    # Not just the first night: the alarm holds every night the document
    # stays missing (round 3 — night 2 used to fall back to
    # not_yet_covered, silently, because night 1 had set ingest_failed).
    for _ in range(nights):
        _, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
        assert failed == 1
        row = _coverage(db_session)
        assert row.status == MeasureCoverage.INGEST_FAILED
        assert "read successfully for this election before" in row.error_detail
        assert row.last_success_at == good
    assert db_session.query(BallotMeasure).one().status == "certified"


@pytest.mark.asyncio
async def test_not_published_past_the_expected_by_cutoff_is_a_failure(monkeypatch, db_session):
    """A document the state publishes for every general (a sample ballot, a
    guide with a mail-by date) that is still missing after the cutoff is a
    moved file or a renamed link, not a wait — it alerts. One that exists
    only in a year with a measure never hits the cutoff."""
    from app.pipeline.fetch.ballot_measure_text import NotYetPublished

    monkeypatch.setattr(election_pipeline, "_past_expected_by", lambda src, day: False)
    _direct_source(monkeypatch, [NotYetPublished("sample ballot")])
    _, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert failed == 0
    assert _coverage(db_session).status == MeasureCoverage.NOT_YET_COVERED

    monkeypatch.setattr(election_pipeline, "_past_expected_by", lambda src, day: True)
    _direct_source(monkeypatch, [NotYetPublished("sample ballot"), NotYetPublished("notice", deadline_applies=False)])
    _, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert failed == 1
    assert _coverage(db_session).status == MeasureCoverage.INGEST_FAILED
    assert "expected by now" in _coverage(db_session).error_detail

    db_session.query(MeasureCoverage).delete()
    db_session.commit()
    _, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert failed == 0
    assert _coverage(db_session).status == MeasureCoverage.NOT_YET_COVERED


def test_the_expected_by_cutoff_counts_back_from_election_day(monkeypatch):
    from datetime import datetime

    election = "2026-11-03"
    monkeypatch.setattr(election_pipeline, "utcnow", lambda: datetime(2026, 10, 13))
    assert election_pipeline._past_expected_by({"expected_by_days_before": 21}, election)
    assert not election_pipeline._past_expected_by({"expected_by_days_before": 21}, "2026-11-10")
    assert not election_pipeline._past_expected_by({}, election)  # default: 14 days -> Oct 20
    monkeypatch.setattr(election_pipeline, "utcnow", lambda: datetime(2026, 10, 12))
    assert not election_pipeline._past_expected_by({"expected_by_days_before": 21}, election)
    monkeypatch.setattr(election_pipeline, "utcnow", lambda: datetime(2026, 10, 20))
    assert election_pipeline._past_expected_by({}, election)


# ── the state page shows its own election only ───────────────────────


def _page_election():
    return elections.next_election_day(elections.utcnow().date()).isoformat()


def test_state_ballot_shows_only_the_pages_own_election(db_session):
    """The regression (pre-existing): every BallotMeasure the state had
    ever had was listed, so an earlier cycle's measures — or a primary's —
    rendered under this election's heading."""
    current = _page_election()
    _measure(db_session, "vs-now", date=current, number="Amendment 1", source_name="Vote Smart")
    _measure(db_session, "vs-old", date="2024-11-05", number="Amendment 1", source_name="Vote Smart")
    _measure(db_session, "vs-primary", date=f"{current[:4]}-05-19", number="Amendment 9", source_name="Vote Smart")
    db_session.commit()
    data = _body(elections.state_ballot("GA", db=db_session))
    assert [m["id"] for m in data["measures"]] == ["vs-now"]


def test_a_removed_measure_of_this_election_still_renders_as_removed(db_session):
    _measure(db_session, "vs-struck", date=_page_election(), status="removed", source_name="Vote Smart")
    _measure(db_session, "vs-old-struck", date="2024-11-05", status="removed", source_name="Vote Smart")
    db_session.commit()
    data = _body(elections.state_ballot("GA", db=db_session))
    assert [(m["id"], m["status"]) for m in data["measures"]] == [("vs-struck", "removed")]


def test_checked_at_is_the_last_successful_check(db_session):
    """After a failed read the page still shows the measures on file; the
    date it prints for them must be when they were last read, not when
    the failure happened."""
    election = _page_election()
    election_pipeline._set_coverage(db_session, "GA", election, MeasureCoverage.COVERED, 1, source_name="S")
    db_session.commit()
    row = db_session.query(MeasureCoverage).one()
    row.last_success_at = row.checked_at = utcnow() - timedelta(days=3)
    db_session.commit()
    election_pipeline._set_coverage(db_session, "GA", election, MeasureCoverage.INGEST_FAILED, 1, source_name="S")
    db_session.commit()
    cov = _body(elections.state_ballot("GA", db=db_session))["measureCoverage"]
    assert cov["status"] == MeasureCoverage.INGEST_FAILED
    assert cov["checkedAt"][:10] == (utcnow() - timedelta(days=3)).date().isoformat()
    assert cov["lastAttemptAt"][:10] == utcnow().date().isoformat()


def test_past_elections_measures_are_pruned(db_session):
    _measure(db_session, "vs-old", date="2024-11-05", number="Amendment 1")
    _measure(db_session, "vs-now", date=_page_election(), number="Amendment 1")
    db_session.commit()
    assert election_pipeline._prune_past_measures(db_session) == 1
    assert [m.id for m in db_session.query(BallotMeasure).all()] == ["vs-now"]


# ── round 3 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_cached_truncated_read_never_counts_toward_the_shrink_streak(monkeypatch, db_session):
    """The regression: a non-empty list is cached for 72h, so ONE truncated
    read came back from the cache on nights 2 and 3 and reached
    MEASURE_SHRINK_CONFIRM_RUNS — measures struck on the strength of one
    bad response. Only a fresh read counts, and a held-back list's cache
    entry is dropped so the next night asks the state again."""
    from datetime import timedelta as td

    from app.models import ApiCache
    from app.pipeline.fetch import ballot_measure_pdf_sources, ballot_measures_pdf as bmp

    source = _fake_pdf_source()
    source["strategy"] = "fake_multi"
    monkeypatch.setattr(ballot_measure_pdf_sources, "configured_states", lambda: {"CA"})
    monkeypatch.setattr(ballot_measure_pdf_sources, "source_for_state", lambda st: source)
    monkeypatch.setattr(bmp, "source_for_state", lambda st: source)
    calls = []

    def item(n):
        return ({"number": str(n), "title": f"T{n}", "origin": None, "official_summary": "S",
                 "fiscal_impact": None, "yes_means": None, "no_means": None}, "https://x")

    async def reader(client, year):
        calls.append(year)
        return [item(1), item(2), item(3), item(4)] if len(calls) == 1 else [item(1)]

    monkeypatch.setitem(bmp.MULTI_DOCUMENT_STRATEGIES, "fake_multi", reader)
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    for e in db_session.query(ApiCache).all():
        e.cached_at = utcnow() - td(hours=100)
    db_session.commit()
    runs = election_pipeline.MEASURE_SHRINK_CONFIRM_RUNS
    for night in range(1, runs + 1):
        _, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
        # Every night asked the state again: nothing was replayed.
        assert len(calls) == night + 1
        if night < runs:
            assert failed == 1
            assert {m.status for m in db_session.query(BallotMeasure).all()} == {"certified"}
    # Three FRESH identical reads are the state's answer.
    assert _coverage(db_session).status == MeasureCoverage.COVERED


def test_a_cached_list_does_not_advance_the_streak(db_session):
    _measure(db_session, "CA-2026-11-03-1", state="CA", number="1", source_name="S")
    _measure(db_session, "CA-2026-11-03-2", state="CA", number="2", source_name="S")
    _measure(db_session, "CA-2026-11-03-3", state="CA", number="3", source_name="S")
    db_session.commit()
    for _ in range(5):
        assert election_pipeline._shrink_held_back(
            db_session, "CA", "2026-11-03", ["CA-2026-11-03-1"], 1, 0, 3, fresh=False,
        )
    assert (_coverage(db_session).shrink_streak or 0) == 0


@pytest.mark.asyncio
async def test_coverage_another_source_left_is_not_this_sources_success(monkeypatch, db_session):
    """The regression: Maine's first night on its own office read "was
    confirmed_none, now reads as not published" — the confirmed none was
    Vote Smart's."""
    from app.pipeline.fetch.ballot_measure_text import NotYetPublished

    election_pipeline._set_coverage(
        db_session, "CA", "2026-11-03", MeasureCoverage.CONFIRMED_NONE, source_name="Vote Smart",
    )
    db_session.commit()
    _direct_source(monkeypatch, [NotYetPublished("guide", deadline_applies=False)])
    _, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert failed == 0
    assert _coverage(db_session).status == MeasureCoverage.NOT_YET_COVERED


@pytest.mark.asyncio
async def test_not_yet_covered_never_claims_a_successful_read(monkeypatch, db_session):
    from app.pipeline.fetch.ballot_measure_text import NotYetPublished

    _direct_source(monkeypatch, [NotYetPublished("guide", deadline_applies=False)])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    row = _coverage(db_session)
    assert row.status == MeasureCoverage.NOT_YET_COVERED
    assert row.last_success_at is None


def test_a_held_back_shrink_with_no_coverage_row_claims_no_success(db_session):
    for n in range(4):
        _measure(db_session, f"CA-2026-11-03-{n}", state="CA", number=str(n), source_name="S")
    db_session.commit()
    assert election_pipeline._shrink_held_back(
        db_session, "CA", "2026-11-03", ["CA-2026-11-03-0"], 1, 0, 4,
    )
    row = _coverage(db_session)
    assert row.last_success_at is None
    assert row.shrink_streak == 1


@pytest.mark.asyncio
async def test_only_struck_measures_on_file_explain_a_drop(monkeypatch, db_session):
    """The regression: explained = every Removed row the state reported, so
    Removed rows never on file padded a real shrink past the floor."""
    _direct_source(monkeypatch, [["1", "2", "3", "4", "5"], ["1", "8-", "9-"]])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    _, failed, marked = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert (failed, marked) == (1, 0)
    assert {m.status for m in db_session.query(BallotMeasure).all()} == {"certified"}


@pytest.mark.asyncio
async def test_a_late_cycle_notice_for_documents_that_may_never_come(monkeypatch, db_session):
    """A reader whose document exists only in a year with a measure can sit
    in not_yet_covered to election day if it broke before reading anything.
    At the default expected-by date it sends one low-severity notice (the
    page stays "not yet covered"), deduped per state and election."""
    import app.ops_alerts as ops_alerts
    from app.pipeline.fetch.ballot_measure_text import NotYetPublished

    sent = []
    monkeypatch.setattr(ops_alerts, "send_ops_alert", lambda subject, body, dedupe_key=None: sent.append(dedupe_key))
    monkeypatch.setattr(election_pipeline, "_past_expected_by", lambda src, day: True)
    _direct_source(monkeypatch, [NotYetPublished("guide", deadline_applies=False)])
    _, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert failed == 0
    assert _coverage(db_session).status == MeasureCoverage.NOT_YET_COVERED
    assert sent == ["ballot-measure-late-CA-2026-11-03"]

    sent.clear()
    monkeypatch.setattr(election_pipeline, "_past_expected_by", lambda src, day: False)
    _direct_source(monkeypatch, [NotYetPublished("guide", deadline_applies=False)])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert sent == []


def test_election_dates_are_stored_as_iso_or_not_at_all(db_session):
    """The state page, reconciliation and pruning all compare
    election_date as an ISO string. Vote Smart's format isn't verified
    against a real payload (its API answers "Authorization failed" with no
    key, and none is on file), so both plausible shapes are normalised and
    anything else is skipped, never stored under a date no query matches."""
    assert election_pipeline._iso_election_date("2026-11-03") == "2026-11-03"
    assert election_pipeline._iso_election_date("2026-11-03T00:00:00") == "2026-11-03"
    assert election_pipeline._iso_election_date("11/03/2026") == "2026-11-03"
    assert election_pipeline._iso_election_date("11/3/2026") == "2026-11-03"
    assert election_pipeline._iso_election_date("Nov 3") is None
    for mid, given in (("vs-us", "11/03/2026"), ("vs-bad", "November 2026")):
        election_pipeline._upsert_measure(
            db_session, {"id": mid, "state": "GA", "number": mid, "title": "t", "election_date": given},
            {}, "Vote Smart",
        )
    db_session.commit()
    assert [(m.id, m.election_date) for m in db_session.query(BallotMeasure).all()] == [("vs-us", "2026-11-03")]
