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


# ── CA direct-PDF path (replaces Vote Smart for this one state) ────────


@pytest.mark.asyncio
async def test_sync_ca_measures_upserts_directly_from_one_pdf_pass(monkeypatch, db_session):
    """CA's fetch already returns the full raw+detail shape in one PDF
    pass (unlike Vote Smart's list-then-per-item-detail calls) — confirm
    _sync_ca_measures upserts straight from it and marks coverage."""
    from app.pipeline.fetch import ballot_measures_ca

    async def fake_fetch(client, db, year, election_date):
        assert year == 2026
        assert election_date == "2026-11-03"
        return [ballot_measures_ca._to_measure(
            {"number": "2", "title": "T", "origin": "the Legislature",
             "official_summary": "S", "fiscal_impact": "F",
             "yes_means": "Y", "no_means": "N"},
            election_date, "https://vig.cdn.sos.ca.gov/2026/general/pdf/complete-vig.pdf",
        )]

    monkeypatch.setattr(ballot_measures_ca, "fetch_ca_measures", fake_fetch)
    synced, failed, marked_removed = await election_pipeline._sync_ca_measures(
        db_session, None, "2026-11-03",
    )
    assert (synced, failed, marked_removed) == (1, 0, 0)

    m = db_session.query(BallotMeasure).filter(BallotMeasure.state == "CA").one()
    assert m.id == "CA-2026-11-03-2"
    assert m.yes_means == "Y"
    assert m.source_name == ballot_measures_ca.SOURCE_NAME

    coverage = db_session.query(MeasureCoverage).filter(
        MeasureCoverage.state == "CA", MeasureCoverage.election_date == "2026-11-03",
    ).one()
    assert coverage.status == MeasureCoverage.COVERED
    assert coverage.source_name == ballot_measures_ca.SOURCE_NAME


@pytest.mark.asyncio
async def test_sync_ca_measures_marks_ingest_failed_on_fetch_failure(monkeypatch, db_session):
    from app.pipeline.fetch import ballot_measures_ca

    async def fake_fetch(client, db, year, election_date):
        return None

    monkeypatch.setattr(ballot_measures_ca, "fetch_ca_measures", fake_fetch)
    result = await election_pipeline._sync_ca_measures(db_session, None, "2026-11-03")
    assert result == (0, 1, 0)

    coverage = db_session.query(MeasureCoverage).filter(
        MeasureCoverage.state == "CA", MeasureCoverage.election_date == "2026-11-03",
    ).one()
    assert coverage.status == MeasureCoverage.INGEST_FAILED


@pytest.mark.asyncio
async def test_sync_ballot_measures_runs_ca_even_without_a_votesmart_key(monkeypatch, db_session):
    """The whole point: CA must not depend on VOTESMART_API_KEY at all."""
    from app.pipeline.fetch import ballot_measures, ballot_measures_ca

    monkeypatch.setattr(ballot_measures.settings, "VOTESMART_API_KEY", "")

    async def fake_fetch(client, db, year, election_date):
        return [ballot_measures_ca._to_measure(
            {"number": "1", "title": "T", "origin": None, "official_summary": "S",
             "fiscal_impact": None, "yes_means": None, "no_means": None},
            election_date, "https://example.com/vig.pdf",
        )]

    monkeypatch.setattr(ballot_measures_ca, "fetch_ca_measures", fake_fetch)
    result = await election_pipeline._sync_ballot_measures(db_session, None, 2026)
    assert result["skipped_other_states"] is True
    assert result["synced"] == 1
    assert db_session.query(BallotMeasure).filter(BallotMeasure.state == "CA").count() == 1
