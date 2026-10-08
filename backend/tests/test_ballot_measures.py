"""Tests for statewide ballot-measure ingestion and the state ballot API.

The distinction under test throughout is the one the feature exists to
preserve: "this state has no measures" and "we don't know this state's
measures" are different claims, and a bug that collapses them tells a
voter in a state with 17 amendments that there is nothing to research.
"""

import json
import logging
import threading
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from app import ops_alerts
from app.api import elections
from app.models import BallotMeasure, MeasureCoverage, Race
from app.pipeline import election_pipeline
from app.pipeline.fetch import ballot_measure_pdf_sources, ballot_measures_pdf
from app.time_utils import utcnow


def _body(response):
    return json.loads(response.body)


def _measure(db, mid, state="GA", date="2026-11-03", number="Amendment 1", **kw):
    m = BallotMeasure(id=mid, state=state, election_date=date, number=number, **kw)
    db.add(m)
    return m


# ── upsert + reconciliation ───────────────────────────────────────────


def test_upsert_skips_measure_without_an_election_date(db_session):
    """A measure we can't date is a measure we can't say is on WHICH ballot."""
    election_pipeline._upsert_measure(
        db_session,
        {"id": "old-1", "state": "GA", "number": "A1", "title": "t"},
        {"official_title": "x"},
        "Earlier Source",
    )
    db_session.commit()
    assert db_session.query(BallotMeasure).count() == 0


def test_upsert_stores_verbatim_fields_and_clears_removed_status(db_session):
    _measure(db_session, "old-1", status="removed")
    db_session.commit()

    election_pipeline._upsert_measure(
        db_session,
        {"id": "old-1", "state": "GA", "number": "Amendment 1", "title": "T",
         "election_date": "2026-11-03"},
        {"official_title": "Official", "yes_means": "keeps the law",
         "no_means": "repeals it", "fiscal_impact": "$1"},
        "Earlier Source",
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
        {"id": "old-1", "state": "GA", "number": "", "title": "First",
         "election_date": "2026-11-03"},
        {}, "Earlier Source",
    )
    db_session.commit()
    election_pipeline._upsert_measure(
        db_session,
        {"id": "old-2", "state": "GA", "number": "", "title": "Second",
         "election_date": "2026-11-03"},
        {}, "Earlier Source",
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
        {"id": "old-2", "state": "WA", "number": "R-101", "title": "Referendum",
         "election_date": "2026-11-03"},
        {"official_title": "Approved retains the law; rejected repeals it"},
        "Earlier Source",
    )
    db_session.commit()
    m = db_session.query(BallotMeasure).one()
    assert m.yes_means is None
    assert m.no_means is None


def test_reconcile_marks_unseen_measures_removed_not_deleted(db_session):
    _measure(db_session, "old-1")
    _measure(db_session, "old-2", number="Amendment 2")
    db_session.commit()

    marked = election_pipeline._reconcile_state_measures(
        db_session, "GA", {"2026-11-03"}, {"old-1"},
    )
    db_session.commit()

    assert marked == 1
    assert db_session.query(BallotMeasure).count() == 2
    gone = db_session.query(BallotMeasure).filter(BallotMeasure.id == "old-2").one()
    assert gone.status == "removed"


def test_reconcile_deletes_only_after_the_grace_window(db_session):
    stale = _measure(db_session, "old-old", number="Amendment 9")
    stale.last_seen_at = utcnow() - timedelta(
        days=election_pipeline.MEASURE_REMOVAL_GRACE_DAYS + 1,
    )
    db_session.commit()

    election_pipeline._reconcile_state_measures(db_session, "GA", {"2026-11-03"}, {"old-1"})
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
        db_session, "GA", elections.active_election(db_session).election_day.isoformat(),
        MeasureCoverage.CONFIRMED_NONE, 0, source_name="Earlier Source",
    )
    db_session.commit()

    data = _body(elections.state_ballot("GA", db=db_session))
    assert data["measures"] == []
    assert data["measureCoverage"]["status"] == MeasureCoverage.CONFIRMED_NONE
    assert data["measureCoverage"]["sourceName"] == "Earlier Source"


def test_state_ballot_returns_measures_and_races(db_session, freeze_utcnow):
    freeze_utcnow(datetime(2026, 9, 30, 12, 0))  # a 2026-cycle fixture
    db_session.add(Race(
        id="2026-SEN-GA", cycle_year=election_pipeline.current_election_cycle(db_session),
        office="S", state="GA", district=None,
    ))
    db_session.add(Race(
        id="2026-HOUSE-GA-7", cycle_year=election_pipeline.current_election_cycle(db_session),
        office="H", state="GA", district=7,
    ))
    _measure(db_session, "old-1", official_title="Official title",
             yes_means="yes does this", source_name="Earlier Source")
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
    # Executive contests are published but no legislative seats are: the
    # seats must still be named missing. One flag opts a state in to both,
    # but coverage is claimed per section, from what is actually there.
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


def test_a_covered_state_names_what_its_executive_section_leaves_out(db_session, monkeypatch):
    """A state office on the ballot that the adapter does not read (Louisiana's
    and Montana's district-elected Public Service Commissions) is named by
    the entry's statewide_omits. Once the Governor is covered the general
    omission goes, so the entry's own list has to say it -- and only once
    covered, since before that the general line already does."""
    from app.pipeline.fetch.state_candidates import _sync_statewide_nominees

    source = {"strategy": "nh_results", "source_name": "NH SoS", "statewide_offices": True,
              "statewide_omits": ["Public Service Commission districts"]}
    monkeypatch.setattr(elections, "source_for_state", lambda state: source)

    before = _body(elections.state_ballot("NH", db=db_session))
    assert "Public Service Commission districts" not in before["omits"]
    assert any("Governor" in item for item in before["omits"])

    _sync_statewide_nominees(db_session, before["cycleYear"], "NH", source, [
        {"office": "governor", "district": None, "party": "R", "last_name": "Kelly Ayotte"},
    ])
    after = _body(elections.state_ballot("NH", db=db_session))
    assert "Public Service Commission districts" in after["omits"]
    assert not any("Governor" in item for item in after["omits"])
    assert after["statewideRaces"][0]["termYears"] == 2  # New Hampshire: two-year governors


def test_state_ballot_lookup_falls_back_when_no_verified_link(db_session):
    """An unverified per-state URL is never handed to a user — a dead link
    on "see your real ballot" is the worst failure this feature has."""
    data = _body(elections.state_ballot("GA", db=db_session))
    assert data["officialLookup"]["isStateSpecific"] is False
    assert data["officialLookup"]["url"].startswith("https://")


def test_removed_measures_are_still_returned(db_session, freeze_utcnow):
    """Rendered as removed, never silently dropped."""
    freeze_utcnow(datetime(2026, 9, 30, 12, 0))  # a 2026-cycle fixture
    _measure(db_session, "old-1", status="removed")
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


def _link_files(monkeypatch, tmp_path, states, volume_states=None):
    """Point the link check at a bundled file holding `states` (and, when
    given, a volume copy from an earlier run holding `volume_states`)."""
    from app.pipeline.fetch import ballot_lookup

    bundled, volume = tmp_path / "bundled.json", tmp_path / "volume.json"
    fallback = {"url": "https://nat.example", "label": "N", "source_name": "S"}
    bundled.write_text(json.dumps({"national_fallback": fallback, "states": states}))
    if volume_states is not None:
        volume.write_text(json.dumps({"national_fallback": fallback, "states": volume_states}))
    monkeypatch.setattr(ballot_lookup, "_BUNDLED_PATH", str(bundled))
    monkeypatch.setattr(ballot_lookup, "_VOLUME_PATH", str(volume))
    monkeypatch.setattr(ballot_lookup, "_cache", None)
    monkeypatch.setattr(ballot_lookup, "_cache_stamp", None)
    return ballot_lookup, volume


def _answering(**by_url):
    """A client whose GET of each URL answers `by_url[url]` (status, final
    URL, headers, body)."""
    import httpx

    class _Client:
        async def get(self, url, **kwargs):
            status, final, headers, body = by_url[url]
            return httpx.Response(
                status, headers=headers, content=body,
                request=httpx.Request("GET", final or url),
            )

    return _Client()


@pytest.mark.asyncio
async def test_link_verification_clears_a_link_that_stops_resolving(monkeypatch, tmp_path):
    """A link that rots between runs must stop being shown, not keep
    riding a check that passed weeks ago — and the state's page falls back
    to the national directory."""
    ballot_lookup, volume = _link_files(
        monkeypatch, tmp_path,
        {"GA": {"url": "https://ga.example/lookup", "verified_at": None}},
        volume_states={"GA": {"url": "https://ga.example/lookup", "verified_at": "2026-01-01T00:00:00"}},
    )
    assert ballot_lookup.lookup_for_state("GA")["isStateSpecific"] is True

    client = _answering(**{"https://ga.example/lookup": (404, None, {}, b"")})
    result = await ballot_lookup.refresh_link_verification(client)
    assert result["failed"] == 1
    assert json.loads(volume.read_text())["states"]["GA"]["verified_at"] is None
    shown = ballot_lookup.lookup_for_state("GA")
    assert shown["isStateSpecific"] is False
    assert shown["url"] == "https://nat.example"


@pytest.mark.asyncio
async def test_link_verification_shows_a_link_that_resolves(monkeypatch, tmp_path):
    ballot_lookup, _ = _link_files(
        monkeypatch, tmp_path, {"GA": {"url": "https://ga.example/lookup", "label": "GA lookup"}},
    )
    client = _answering(**{"https://ga.example/lookup": (200, None, {}, b"<title>Lookup</title>")})
    assert (await ballot_lookup.refresh_link_verification(client))["verified"] == 1
    shown = ballot_lookup.lookup_for_state("GA")
    assert shown["isStateSpecific"] is True
    assert shown["url"] == "https://ga.example/lookup"


@pytest.mark.asyncio
async def test_link_verification_does_not_pass_a_bot_challenge(monkeypatch, tmp_path):
    """Imperva serves its challenge with a 200. A wall that refuses the
    check leaves the state unverified (AGENTS.md section 7), not shown."""
    ballot_lookup, _ = _link_files(
        monkeypatch, tmp_path, {"MA": {"url": "https://ma.example/lookup"}},
    )
    challenge = (200, None, {"x-iinfo": "1-2-3"}, b"<script src='/_Incapsula_Resource?x'></script>")
    client = _answering(**{"https://ma.example/lookup": challenge})
    assert (await ballot_lookup.refresh_link_verification(client))["failed"] == 1
    assert ballot_lookup.lookup_for_state("MA")["isStateSpecific"] is False


@pytest.mark.asyncio
async def test_link_verification_does_not_pass_a_redirect_to_the_homepage(monkeypatch, tmp_path):
    """A retired lookup redirected to its site's homepage resolves, but is
    no longer the lookup. A redirect to the lookup's new address (another
    host, or a deeper page) is fine."""
    ballot_lookup, _ = _link_files(monkeypatch, tmp_path, {
        "OH": {"url": "https://oh.example/voterlookup.aspx"},
        "MS": {"url": "https://ms.example/elections/locator"},
        "WA": {"url": "https://wa.example/WhereToVote.aspx"},
    })
    client = _answering(**{
        "https://oh.example/voterlookup.aspx": (200, "https://oh.example/", {}, b""),
        "https://ms.example/elections/locator": (200, "https://myelectionday.ms.example/", {}, b""),
        "https://wa.example/WhereToVote.aspx": (200, "https://wa.example/portal/login.aspx", {}, b""),
    })
    result = await ballot_lookup.refresh_link_verification(client)
    assert (result["verified"], result["failed"]) == (2, 1)
    assert ballot_lookup.lookup_for_state("OH")["isStateSpecific"] is False
    assert ballot_lookup.lookup_for_state("MS")["isStateSpecific"] is True
    assert ballot_lookup.lookup_for_state("WA")["isStateSpecific"] is True


@pytest.mark.asyncio
async def test_link_verification_checks_the_bundled_urls_not_last_runs(monkeypatch, tmp_path):
    """The volume copy is the check's own output; reading candidates from
    it would keep checking a URL app/data has since corrected."""
    ballot_lookup, volume = _link_files(
        monkeypatch, tmp_path,
        {"GA": {"url": "https://ga.example/new"}},
        volume_states={"GA": {"url": "https://ga.example/old", "verified_at": "2026-01-01T00:00:00"}},
    )
    client = _answering(**{"https://ga.example/new": (200, None, {}, b"")})
    await ballot_lookup.refresh_link_verification(client)
    assert json.loads(volume.read_text())["states"]["GA"]["url"] == "https://ga.example/new"
    assert ballot_lookup.lookup_for_state("GA")["url"] == "https://ga.example/new"


def test_bundled_ballot_lookups_cover_every_state_from_its_own_authority():
    """Every state and D.C. has an official lookup, none of them shown
    before the link check has passed it, none from an aggregator."""
    from urllib.parse import urlsplit

    from app.pipeline.fetch import ballot_lookup
    from app.state_names import STATE_NAMES

    data = json.loads(open(ballot_lookup._BUNDLED_PATH, encoding="utf-8").read())
    states = data["states"]
    assert set(states) == set(STATE_NAMES)
    for code, entry in states.items():
        assert entry["verified_at"] is None, code  # only the link check sets it
        assert urlsplit(entry["url"]).scheme == "https", code
        host = urlsplit(entry["url"]).hostname
        assert not any(host == d or host.endswith("." + d) for d in (
            "vote.org", "ballotpedia.org", "usa.gov", "vote.gov",
        )), code
        assert entry["label"] and entry["source_name"] and entry["_checked"], code


# ── direct path: each registered state read from its own office ────


def _fake_pdf_source(state="CA", source_name="Example Elections Office"):
    return {"url_pattern": "https://example.com/{year}/ballot.pdf",
            "source_name": source_name, "strategy": "fake"}


@pytest.mark.asyncio
async def test_sync_pdf_measures_upserts_directly_from_one_pdf_pass(monkeypatch, db_session):
    """A registered state's fetch already returns the full raw+detail
    shape in one pass — confirm _sync_pdf_measures upserts straight from it and
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
async def test_sync_pdf_measures_records_not_yet_published_without_failing(monkeypatch, db_session, freeze_utcnow):
    """Maine's guide appears weeks before November. Until then the state
    is not yet covered — not ingest_failed (nothing is broken, so nothing
    should page anyone) and never confirmed_none."""
    freeze_utcnow(datetime(2026, 9, 30, 12, 0))  # a 2026-cycle fixture
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
async def test_sync_ballot_measures_reads_registered_states_directly(monkeypatch, db_session):
    """The state's own office is the only source: a registered state is
    read and written with no other key or service involved."""
    from app.pipeline.fetch import ballot_measure_pdf_sources, ballot_measures_pdf

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
    assert result["synced"] == 1
    assert db_session.query(BallotMeasure).filter(BallotMeasure.state == "CA").count() == 1


async def test_a_town_ballot_that_failed_to_load_is_never_stored(monkeypatch, db_session):
    """A fetch failure is this request's, not the town's: cached (nginx
    caches whatever the backend marks public) it would tell every reader
    the ballot couldn't be read until it expired."""
    from app.api import elections

    monkeypatch.setattr(elections.ballot_pdf, "is_configured", lambda town: False)
    monkeypatch.setattr(elections, "civic_is_configured", lambda: True)
    monkeypatch.setattr(elections, "address_for_town", lambda state, town: "1 Main St")

    async def failed(*_a, **_k):
        return None

    monkeypatch.setattr(elections, "fetch_town_ballot", failed)
    resp = await elections.town_ballot(None, "MA", "Somerville", db=db_session)
    assert resp.headers["Cache-Control"] == "public, max-age=30"
    assert b"ingest_failed" in resp.body


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
    """The regression: the direct path had no MEASURE_SHRINK_FLOOR
    guard — a read that returned 1 of 6 known
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
async def test_switching_a_state_to_direct_sourcing_retires_the_old_sources_rows(monkeypatch, db_session):
    """The regression: a state moved from another source to its own office
    kept the old source's rows — reconciled to "no longer on the ballot" for 45 days
    beside the same measure's new card, or (dated differently) left as a
    certified duplicate. They are superseded, so they are deleted — and
    only by a successful read."""
    _measure(db_session, "old-100", state="CA", number="Prop 1", source_name="Earlier Source")
    _measure(db_session, "old-101", state="CA", number="Prop 2", source_name="Earlier Source")
    # Another election in the same year (a primary) and an earlier
    # cycle's record are left alone: only the election read is replaced.
    _measure(db_session, "old-090", state="CA", date="2026-06-02", number="Prop 1", source_name="Earlier Source")
    _measure(db_session, "old-050", state="CA", date="2024-11-05", number="Prop 9", source_name="Earlier Source")
    db_session.commit()

    _direct_source(monkeypatch, [None, ["1", "2"]])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    # A failed read retires nothing.
    assert db_session.query(BallotMeasure).filter(BallotMeasure.id.like("old-%")).count() == 4

    _, failed, marked = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert (failed, marked) == (0, 0)
    ids = sorted(m.id for m in db_session.query(BallotMeasure).all())
    assert ids == ["CA-2026-11-03-1", "CA-2026-11-03-2", "old-050", "old-090"]
    assert {m.status for m in db_session.query(BallotMeasure).all()} == {"certified"}


@pytest.mark.asyncio
async def test_a_confirmed_none_also_retires_the_superseded_source(monkeypatch, db_session):
    _measure(db_session, "old-100", state="CA", number="Prop 1", source_name="Earlier Source")
    db_session.commit()
    _direct_source(monkeypatch, [[]])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert db_session.query(BallotMeasure).count() == 0
    assert _coverage(db_session).status == MeasureCoverage.CONFIRMED_NONE


@pytest.mark.asyncio
async def test_direct_source_failures_alert(monkeypatch, db_session):
    """A broken direct reader pages: _sync_ballot_measures must reach its
    alert whatever else the run found."""
    import app.ops_alerts as ops_alerts

    _direct_source(monkeypatch, [None])
    sent = []
    monkeypatch.setattr(ops_alerts, "send_ops_alert", lambda *a, **kw: sent.append(a))
    result = await election_pipeline._sync_ballot_measures(db_session, None, 2026)
    assert result["failed_states"] == 1
    assert len(sent) == 1


# ── round 2: the write can't lose both answers; scope; freshness ─────


@pytest.mark.asyncio
async def test_a_direct_row_sharing_another_sources_number_replaces_it(monkeypatch, db_session):
    """The regression: the direct row with the old source's number tripped
    uq_ballot_measure_state_date_number and rolled back, but was still
    counted as seen, so the old source's row was then deleted — an empty
    table under coverage "covered, 1"."""
    _measure(db_session, "old-100", state="CA", number="1", source_name="Earlier Source")
    db_session.commit()
    _direct_source(monkeypatch, [["1"]])
    synced, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert (synced, failed) == (1, 0)
    [row] = db_session.query(BallotMeasure).all()
    assert row.id == "CA-2026-11-03-1" and row.status == "certified"
    assert _coverage(db_session).measure_count == 1


@pytest.mark.asyncio
async def test_a_failed_write_keeps_the_old_rows_and_counts_nothing(monkeypatch, db_session):
    _measure(db_session, "old-100", state="CA", number="Prop 1", source_name="Earlier Source")
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
    assert [m.id for m in db_session.query(BallotMeasure).all()] == ["old-100"]
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
    """The shrink guard compares against this election's rows only: four
    2024 measures on file must not hold back a real one-measure 2026 list."""
    monkeypatch.setattr(election_pipeline, "_prune_past_measures", lambda db: 0)
    for i in range(4):
        _measure(db_session, f"CA-2024-11-05-{i}", state="CA", date="2024-11-05", number=str(i),
                 source_name="Example Elections Office")
    db_session.commit()
    _direct_source(monkeypatch, [["1"]])
    _, failed, marked = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert (failed, marked) == (0, 0)
    assert _coverage(db_session).status == MeasureCoverage.COVERED


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
    # The page's election (api/elections -> active_election); the test
    # database holds no counts, so no results window keeps an earlier one.
    from app.election_phase import election_today, resolve_active_election

    return resolve_active_election(election_today(), lambda day: None).election_day.isoformat()


def test_state_ballot_shows_only_the_pages_own_election(db_session):
    """The regression (pre-existing): every BallotMeasure the state had
    ever had was listed, so an earlier cycle's measures — or a primary's —
    rendered under this election's heading."""
    current = _page_election()
    _measure(db_session, "old-now", date=current, number="Amendment 1", source_name="Earlier Source")
    _measure(db_session, "old-old", date="2024-11-05", number="Amendment 1", source_name="Earlier Source")
    _measure(db_session, "old-primary", date=f"{current[:4]}-05-19", number="Amendment 9", source_name="Earlier Source")
    db_session.commit()
    data = _body(elections.state_ballot("GA", db=db_session))
    assert [m["id"] for m in data["measures"]] == ["old-now"]


def test_a_removed_measure_of_this_election_still_renders_as_removed(db_session):
    _measure(db_session, "old-struck", date=_page_election(), status="removed", source_name="Earlier Source")
    _measure(db_session, "old-old-struck", date="2024-11-05", status="removed", source_name="Earlier Source")
    db_session.commit()
    data = _body(elections.state_ballot("GA", db=db_session))
    assert [(m["id"], m["status"]) for m in data["measures"]] == [("old-struck", "removed")]


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
    _measure(db_session, "old-old", date="2024-11-05", number="Amendment 1")
    _measure(db_session, "old-now", date=_page_election(), number="Amendment 1")
    db_session.commit()
    assert election_pipeline._prune_past_measures(db_session) == 1
    assert [m.id for m in db_session.query(BallotMeasure).all()] == ["old-now"]



def test_the_election_still_on_the_page_is_never_pruned(db_session, monkeypatch):
    """A slow count keeps the state page on the election just held for up
    to two months (election_phase.active_election) -- past the 45-day
    grace window. Its measures stay while the page still shows it."""
    from datetime import date as _date

    from app.election_phase import ActiveElection

    held = _date(2026, 11, 3)
    monkeypatch.setattr(
        election_pipeline, "utcnow",
        lambda: utcnow().replace(year=2026, month=12, day=28),
    )
    monkeypatch.setattr(
        election_pipeline, "active_election",
        lambda db=None: ActiveElection(
            election_day=held, phase="results", results_until=_date(2027, 1, 3), last_result_change=None,
        ),
    )
    _measure(db_session, "held", date=held.isoformat(), number="Amendment 1")
    _measure(db_session, "older", date="2024-11-05", number="Amendment 1")
    db_session.commit()
    assert election_pipeline._prune_past_measures(db_session) == 1
    assert [m.id for m in db_session.query(BallotMeasure).all()] == ["held"]

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
    the previous source's."""
    from app.pipeline.fetch.ballot_measure_text import NotYetPublished

    election_pipeline._set_coverage(
        db_session, "CA", "2026-11-03", MeasureCoverage.CONFIRMED_NONE, source_name="Earlier Source",
    )
    db_session.commit()
    _direct_source(monkeypatch, [NotYetPublished("guide", deadline_applies=False)])
    _, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert failed == 0
    assert _coverage(db_session).status == MeasureCoverage.NOT_YET_COVERED
    # With none of the previous source's rows on file, its name goes too
    # (contrast test_the_previous_sources_coverage_keeps_its_name_until_this_one_answers).
    assert _coverage(db_session).source_name == "Example Elections Office"


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
    monkeypatch.setattr(ops_alerts, "send_ops_alert", lambda subject, body, dedupe_key=None, condition=None: sent.append(dedupe_key))
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
    election_date as an ISO string, so both plausible shapes a source
    might give are normalised and anything else is skipped, never stored
    under a date no query matches."""
    assert election_pipeline._iso_election_date("2026-11-03") == "2026-11-03"
    assert election_pipeline._iso_election_date("2026-11-03T00:00:00") == "2026-11-03"
    assert election_pipeline._iso_election_date("11/03/2026") == "2026-11-03"
    assert election_pipeline._iso_election_date("11/3/2026") == "2026-11-03"
    assert election_pipeline._iso_election_date("Nov 3") is None
    for mid, given in (("old-us", "11/03/2026"), ("old-bad", "November 2026")):
        election_pipeline._upsert_measure(
            db_session, {"id": mid, "state": "GA", "number": mid, "title": "t", "election_date": given},
            {}, "Earlier Source",
        )
    db_session.commit()
    assert [(m.id, m.election_date) for m in db_session.query(BallotMeasure).all()] == [("old-us", "2026-11-03")]


# ── round 4 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_ingest_alert_fires_every_night_a_state_fails(monkeypatch, db_session):
    """The regression: dedupe_key was the constant "ballot-measure-ingest",
    and send_ops_alert dedupes for as long as it remembers a key — so the
    first failure ever was the last one anyone heard about."""
    import app.ops_alerts as ops_alerts

    keys = []
    monkeypatch.setattr(ops_alerts, "send_ops_alert", lambda s, b, dedupe_key=None, condition=None: keys.append(dedupe_key))
    election_pipeline._alert_ingest_failures(db_session, ["CA"], "2026-11-03")
    election_pipeline._alert_ingest_failures(db_session, ["CA", "MI"], "2026-11-03")
    assert len(set(keys)) == 2
    assert all(k.startswith(f"ballot-measure-ingest-2026-11-03-{utcnow().date().isoformat()}-") for k in keys)
    from datetime import datetime as dt

    monkeypatch.setattr(election_pipeline, "utcnow", lambda: dt(2026, 10, 30))
    election_pipeline._alert_ingest_failures(db_session, ["CA"], "2026-11-03")
    assert keys[-1] != keys[0]  # the next night, the same failure alerts again
    election_pipeline._alert_ingest_failures(db_session, [], "2026-11-03")
    assert len(keys) == 3


@pytest.mark.asyncio
async def test_a_failed_read_records_and_alerts_the_readers_own_reason(monkeypatch, db_session):
    """The coverage row said only "fetch failed" while the log knew it was
    a 403 (Georgia, 2026-10-01): the reason the reader logs is the one the
    row and the alert carry. A warning another thread logs meanwhile is
    not this state's reason."""
    monkeypatch.setattr(ballot_measure_pdf_sources, "configured_states", lambda: {"GA"})
    monkeypatch.setattr(ballot_measure_pdf_sources, "source_for_state", lambda state: _fake_pdf_source())
    reader_log = logging.getLogger("app.pipeline.fetch.http_utils")

    async def fake_fetch(client, db, state, year, election_date):
        elsewhere = threading.Thread(target=lambda: reader_log.warning("Failed to fetch feed Alaska Beacon"))
        elsewhere.start()
        elsewhere.join()
        reader_log.error("GA proposed constitutional amendments failed after 3 attempts: HTTP 403")
        return None

    monkeypatch.setattr(ballot_measures_pdf, "fetch_state_measures_pdf", fake_fetch)
    failing: list[str] = []
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03", failing)

    coverage = db_session.query(MeasureCoverage).filter(MeasureCoverage.state == "GA").one()
    assert coverage.status == MeasureCoverage.INGEST_FAILED
    assert coverage.error_detail == "GA proposed constitutional amendments failed after 3 attempts: HTTP 403"

    bodies = []
    monkeypatch.setattr(ops_alerts, "send_ops_alert", lambda s, b, dedupe_key=None, condition=None: bodies.append(b))
    election_pipeline._alert_ingest_failures(db_session, failing, "2026-11-03")
    assert "- GA: GA proposed constitutional amendments failed after 3 attempts: HTTP 403" in bodies[0]


@pytest.mark.asyncio
async def test_a_measure_the_source_reports_gone_is_removed_not_a_lost_document(monkeypatch, db_session):
    """Oklahoma: the only listed State Question is no longer dated for this
    election. The reader reports it removed with its NotYetPublished; the
    row is marked removed and the state reads not yet covered — not the
    nightly lost-document alarm, with the struck question still shown."""
    from app.pipeline.fetch.ballot_measure_text import NotYetPublished

    gone = NotYetPublished("a State Question", deadline_applies=False, removed=[
        {"number": "1", "title": "T", "origin": None, "official_summary": None, "fiscal_impact": None,
         "yes_means": None, "no_means": None, "removed": True},
    ])
    _direct_source(monkeypatch, [["1"], gone, gone])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    for _ in range(2):
        _, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
        assert failed == 0
        assert db_session.query(BallotMeasure).one().status == "removed"
        assert _coverage(db_session).status == MeasureCoverage.NOT_YET_COVERED


@pytest.mark.asyncio
async def test_an_operator_can_accept_a_states_absence(monkeypatch, db_session):
    """Michigan's document disappears when its only proposal is struck, and
    the reader can only say "not published": the operator path marks the
    rows removed, records confirmed none with the note, and the nightly
    sync leaves that standing — until the reader answers again."""
    from app.api.admin import admin_accept_measure_absence
    from app.pipeline.fetch import ballot_measure_pdf_sources
    from app.pipeline.fetch.ballot_measure_text import NotYetPublished

    _direct_source(monkeypatch, [["1"], NotYetPublished("doc", deadline_applies=False),
                                  NotYetPublished("doc", deadline_applies=False), ["2"]])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    _, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert failed == 1  # the honest nightly alarm, before anyone checks

    with pytest.raises(HTTPException):
        admin_accept_measure_absence("ZZ", "2026-11-03", note="checked the SOS release", force=False, db=db_session)
    with pytest.raises(HTTPException):
        admin_accept_measure_absence("CA", "11/03/2026", note="checked the SOS release", force=False, db=db_session)
    monkeypatch.setattr(ballot_measure_pdf_sources, "source_for_state", lambda st: _fake_pdf_source())
    result = admin_accept_measure_absence(
        "CA", "2026-11-03", note="SOS release 2026-10-02: measure 1 struck by court order", force=False,
        db=db_session,
    )
    assert result["markedRemoved"] == 1
    row = _coverage(db_session)
    assert row.status == MeasureCoverage.CONFIRMED_NONE
    assert "struck by court order" in row.error_detail
    assert db_session.query(BallotMeasure).one().status == "removed"

    _, failed, _ = await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    assert failed == 0
    assert _coverage(db_session).status == MeasureCoverage.CONFIRMED_NONE

    # The reader answers again: its answer replaces the operator's.
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    row = _coverage(db_session)
    assert row.status == MeasureCoverage.COVERED and row.operator_note is None


@pytest.mark.asyncio
async def test_the_previous_sources_coverage_keeps_its_name_until_this_one_answers(monkeypatch, db_session):
    """Round 4: on a state just moved to its own office, "not yet
    published" re-labelled the coverage row with the state office while
    the cards on the page — and the "last successful read" date — were
    still the previous source's."""
    from app.pipeline.fetch.ballot_measure_text import NotYetPublished

    _measure(db_session, "old-1", state="CA", source_name="Earlier Source")
    election_pipeline._set_coverage(db_session, "CA", "2026-11-03", MeasureCoverage.COVERED, 1, source_name="Earlier Source")
    db_session.commit()
    _direct_source(monkeypatch, [NotYetPublished("guide", deadline_applies=False)])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    row = _coverage(db_session)
    assert row.status == MeasureCoverage.NOT_YET_COVERED
    assert row.source_name == "Earlier Source"



# ── round 5 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_accepted_absence_survives_a_failed_night(monkeypatch, db_session):
    """The regression: one failed fetch after accept-absence left the state
    in ingest_failed for good — every later "document absent" night kept
    it there with nothing paged. The failure itself still alerts that
    night; the next absent night restores the operator's answer."""
    from app.pipeline.fetch.ballot_measure_text import NotYetPublished

    nyp = NotYetPublished("doc", deadline_applies=False)
    _direct_source(monkeypatch, [["1"], None, nyp, nyp])
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    election_pipeline.accept_state_absence(
        db_session, "CA", "2026-11-03", "Example Elections Office", "SOS release: struck", force=True,
    )
    failing: list[str] = []
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03", failing)
    assert failing == ["CA"] and _coverage(db_session).status == MeasureCoverage.INGEST_FAILED
    assert _coverage(db_session).operator_note == "SOS release: struck"
    for _ in range(2):
        failing = []
        await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03", failing)
        assert failing == []
        row = _coverage(db_session)
        assert row.status == MeasureCoverage.CONFIRMED_NONE
        assert "SOS release: struck" in row.error_detail


def test_accept_absence_refuses_a_freshly_covered_state_without_force(db_session):
    """The regression: accept-absence marked a freshly read, certified
    measure removed (e.g. the wrong state typed)."""
    import json

    _measure(db_session, "FL-2026-11-03-1", state="FL", number="1", source_name="Florida DOS")
    election_pipeline._set_coverage(db_session, "FL", "2026-11-03", MeasureCoverage.COVERED, 1, source_name="Florida DOS")
    db_session.commit()
    with pytest.raises(election_pipeline.AbsenceRefused):
        election_pipeline.accept_state_absence(db_session, "FL", "2026-11-03", "Florida DOS", "wrong state?")
    assert db_session.query(BallotMeasure).one().status == "certified"
    election_pipeline.accept_state_absence(
        db_session, "FL", "2026-11-03", "Florida DOS", "court order, checked", force=True,
    )
    row = _coverage(db_session, "FL")
    [action] = json.loads(row.operator_actions)
    assert action["note"] == "court order, checked" and action["force"] is True and action["marked"] == 1


def test_the_admin_endpoint_answers_409_without_force(monkeypatch, db_session):
    from app.api.admin import admin_accept_measure_absence
    from app.pipeline.fetch import ballot_measure_pdf_sources

    monkeypatch.setattr(ballot_measure_pdf_sources, "source_for_state", lambda st: _fake_pdf_source())
    election_pipeline._set_coverage(
        db_session, "CA", "2026-11-03", MeasureCoverage.COVERED, 1, source_name="Example Elections Office",
    )
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        admin_accept_measure_absence("CA", "2026-11-03", note="checked the SOS release", force=False, db=db_session)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_the_audit_trail_survives_the_readers_next_answer(monkeypatch, db_session):
    import json

    _direct_source(monkeypatch, [["1"]])
    election_pipeline.accept_state_absence(db_session, "CA", "2026-11-03", "Example Elections Office", "checked")
    await election_pipeline._sync_pdf_measures(db_session, None, "2026-11-03")
    row = _coverage(db_session)
    assert row.status == MeasureCoverage.COVERED and row.operator_note is None
    assert len(json.loads(row.operator_actions)) == 1


def test_accept_absence_marks_every_sources_rows(db_session):
    """The regression: only the registry source's rows were marked, so a
    row another source left on file rendered as current under "none"."""
    _measure(db_session, "old-1", state="CA", source_name="Earlier Source")
    _measure(db_session, "old-other-election", state="CA", date="2026-06-02", source_name="Earlier Source")
    db_session.commit()
    marked = election_pipeline.accept_state_absence(
        db_session, "CA", "2026-11-03", "Example Elections Office", "checked the release",
    )
    assert marked == 1
    status = {m.id: m.status for m in db_session.query(BallotMeasure).all()}
    assert status == {"old-1": "removed", "old-other-election": "certified"}


def test_the_api_says_whose_determination_a_none_is(db_session):
    election = _page_election()
    election_pipeline._set_coverage(db_session, "GA", election, MeasureCoverage.CONFIRMED_NONE, source_name="GA SoS")
    db_session.commit()
    assert _body(elections.state_ballot("GA", db=db_session))["measureCoverage"]["basis"] == "source"
    election_pipeline.accept_state_absence(db_session, "GA", election, "GA SoS", "private operator note")
    cov = _body(elections.state_ballot("GA", db=db_session))["measureCoverage"]
    assert cov["basis"] == "operator"
    assert "private operator note" not in json.dumps(cov)


# ── the retired Vote Smart source ────────────────────────────────────


def test_the_retired_sources_rows_are_purged_once_and_idempotently(db_session):
    """Rows the Vote Smart integration left behind were never re-checked
    against the state: they are deleted outright — never left current,
    and never marked "removed" (which would claim the state struck them)."""
    _measure(db_session, "vs-1", source_name="Vote Smart")
    _measure(db_session, "vs-2", number="Amendment 2", status="removed", source_name="Vote Smart")
    _measure(db_session, "vs-3", number="Amendment 3", source_name=None)
    _measure(db_session, "GA-2026-11-03-9", number="9", source_name="Georgia Secretary of State")
    db_session.commit()

    assert election_pipeline._purge_retired_source(db_session) == 3
    assert [m.id for m in db_session.query(BallotMeasure).all()] == ["GA-2026-11-03-9"]
    assert election_pipeline._purge_retired_source(db_session) == 0
    assert db_session.query(BallotMeasure).count() == 1


@pytest.mark.asyncio
async def test_the_nightly_sync_purges_the_retired_sources_rows(monkeypatch, db_session):
    _direct_source(monkeypatch, [["1"]])
    _measure(db_session, "vs-1", state="GA", date=_page_election(), source_name="Vote Smart")
    db_session.commit()
    await election_pipeline._sync_ballot_measures(db_session, None, 2026)
    assert db_session.query(BallotMeasure).filter(BallotMeasure.state == "GA").count() == 0


@pytest.mark.asyncio
async def test_a_state_with_no_direct_source_is_not_yet_covered(monkeypatch, db_session):
    """No registered reader means nothing was checked: NOT_YET_COVERED,
    never CONFIRMED_NONE — including over a none some earlier source
    recorded — and no alert, since nothing broke. DC is included."""
    import app.ops_alerts as ops_alerts

    election = _page_election()
    _direct_source(monkeypatch, [["1"]])
    monkeypatch.setattr(election_pipeline, "federal_states", lambda: frozenset({"CA", "GA", "OR"}))
    election_pipeline._set_coverage(
        db_session, "GA", election, MeasureCoverage.CONFIRMED_NONE, source_name="Vote Smart",
    )
    db_session.commit()
    sent = []
    monkeypatch.setattr(ops_alerts, "send_ops_alert", lambda *a, **kw: sent.append(a))

    result = await election_pipeline._sync_ballot_measures(db_session, None, 2026)

    assert result["not_read_states"] == 3 and result["failed_states"] == 0
    assert sent == []
    for state in ("GA", "OR", "DC"):
        row = db_session.query(MeasureCoverage).filter(
            MeasureCoverage.state == state, MeasureCoverage.election_date == election,
        ).one()
        assert row.status == MeasureCoverage.NOT_YET_COVERED
        assert "no direct source" in row.error_detail
        assert row.last_success_at is None
    page = _body(elections.state_ballot("GA", db=db_session))
    assert page["measureCoverage"]["status"] == MeasureCoverage.NOT_YET_COVERED
    assert page["officialLookup"]["url"]


def test_the_admin_endpoint_refuses_a_state_with_no_direct_source(monkeypatch, db_session):
    """Accepting an absence needs a source whose answer it stands in for;
    an unread state would have "none" reverted to not-yet-covered nightly."""
    from app.api.admin import admin_accept_measure_absence
    from app.pipeline.fetch import ballot_measure_pdf_sources

    monkeypatch.setattr(ballot_measure_pdf_sources, "source_for_state", lambda st: None)
    with pytest.raises(HTTPException) as exc:
        admin_accept_measure_absence("GA", "2026-11-03", note="checked the SOS release", force=True, db=db_session)
    assert exc.value.status_code == 400
    assert db_session.query(MeasureCoverage).count() == 0


def test_a_leftover_votesmart_key_in_env_does_not_break_startup(tmp_path, monkeypatch):
    """Production .env files still set VOTESMART_API_KEY; Settings forbids
    unknown keys, so the retired one is dropped rather than fatal. Any
    other unknown key still fails loudly."""
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.setenv("VOTESMART_API_KEY", "from-the-environment")
    env = tmp_path / ".env"
    env.write_text("VOTESMART_API_KEY=abc123\n")
    settings = Settings(_env_file=env)
    assert not hasattr(settings, "VOTESMART_API_KEY")

    env.write_text("VOTESMART_API_KEY=abc123\nNOT_A_REAL_SETTING=1\n")
    with pytest.raises(ValidationError):
        Settings(_env_file=env)


def test_the_purge_resets_coverage_the_retired_source_recorded(db_session):
    """A Vote Smart "none" (or "covered") with a last-read date would
    otherwise date a check this site no longer makes, above an empty page."""
    election = _page_election()
    election_pipeline._set_coverage(db_session, "GA", election, MeasureCoverage.CONFIRMED_NONE, source_name="Vote Smart")
    election_pipeline._set_coverage(db_session, "FL", election, MeasureCoverage.COVERED, 2, source_name="Florida DOS")
    db_session.commit()
    election_pipeline._purge_retired_source(db_session)
    ga = db_session.query(MeasureCoverage).filter(MeasureCoverage.state == "GA").one()
    assert ga.status == MeasureCoverage.NOT_YET_COVERED
    assert ga.source_name is None and ga.last_success_at is None
    fl = db_session.query(MeasureCoverage).filter(MeasureCoverage.state == "FL").one()
    assert fl.status == MeasureCoverage.COVERED and fl.last_success_at is not None


@pytest.mark.asyncio
async def test_an_unregistered_state_keeps_the_date_of_measures_still_on_file(monkeypatch, db_session):
    """A state taken out of the registry keeps its last read's measures
    until the election passes; the date that read happened still dates them."""
    election = _page_election()
    _direct_source(monkeypatch, [["1"]])
    monkeypatch.setattr(election_pipeline, "federal_states", lambda: frozenset({"CA", "OR"}))
    _measure(db_session, f"OR-{election}-1", state="OR", date=election, number="1", source_name="Oregon SoS")
    election_pipeline._set_coverage(db_session, "OR", election, MeasureCoverage.COVERED, 1, source_name="Oregon SoS")
    db_session.commit()
    await election_pipeline._sync_ballot_measures(db_session, None, 2026)
    row = db_session.query(MeasureCoverage).filter(MeasureCoverage.state == "OR").one()
    assert row.status == MeasureCoverage.NOT_YET_COVERED
    assert row.last_success_at is not None
    # The rows' own source stays named, so the page can date them as that
    # source's last read. They stay certified: marking them removed would
    # claim the state struck them, which nobody checked.
    assert row.source_name == "Oregon SoS"
    assert db_session.query(BallotMeasure).filter(BallotMeasure.state == "OR").one().status == "certified"


def test_every_unread_state_has_a_reason_and_no_read_state_claims_one():
    """Every state the sync leaves unread gets its honest reason on its
    page; a state that becomes registered must leave 'unread' (a stale
    reason would contradict the measures shown)."""
    from app.pipeline.fetch import ballot_measure_pdf_sources as sources

    sources.invalidate_cache()
    registry = sources._load()
    unread = set(registry["unread"])
    expected = (election_pipeline.federal_states() | {"DC"}) - sources.configured_states()
    assert unread == expected
    assert not unread & sources.configured_states()
    for state, entry in registry["unread"].items():
        assert set(entry) == {"reason", "dev_note"}, state
        assert len(entry["reason"]) > 20 and entry["dev_note"], state


def test_unread_reasons_make_no_claim_about_network_access():
    """A reason is shown on the production page, which runs on a
    different network than the machine that checked the state: "blocks
    automated access" can be false there. Only facts about what the state
    publishes, or that Civitas doesn't read it yet, are true everywhere.
    The technical finding lives in dev_note, which is never served."""
    from app.pipeline.fetch import ballot_measure_pdf_sources as sources

    sources.invalidate_cache()
    network_words = ("block", "cloudflare", "imperva", "akamai", "captcha", "firewall",
                     "our server", "ip ", "refused", "reach", "automated access", "can't be read")
    for state, entry in sources._load()["unread"].items():
        reason = entry["reason"].lower()
        assert not [w for w in network_words if w in reason], (state, reason)
        assert ("does not read" in reason and "automatically yet" in reason) or "publish" in reason or "posted" in reason, state


def test_unread_reasons_are_true_in_any_cycle():
    """A reason stays on the page election after election: a date, a year
    or "this election" in it goes stale (and contradicts the next cycle).
    When something was found belongs in dev_note."""
    import re

    from app.pipeline.fetch import ballot_measure_pdf_sources as sources

    sources.invalidate_cache()
    months = r"january|february|march|april|may|june|july|august|september|october|november|december"
    for state, entry in sources._load()["unread"].items():
        reason = entry["reason"].lower()
        assert not re.search(r"\b(19|20)\d{2}\b", reason), (state, reason)
        assert not re.search(rf"\b({months})\b", reason), (state, reason)
        assert "this election" not in reason and "as of" not in reason, (state, reason)


def test_the_page_gives_an_unread_states_reason(db_session):
    from app.pipeline.fetch import ballot_measure_pdf_sources as sources

    sources.invalidate_cache()
    ny = _body(elections.state_ballot("NY", db=db_session))["measureCoverage"]
    assert ny["status"] == MeasureCoverage.NOT_YET_COVERED
    assert ny["unreadReason"] == "Civitas does not read New York's official measure list automatically yet."
    assert "dev_note" not in json.dumps(ny) and "Cloudflare" not in json.dumps(ny)
    de = _body(elections.state_ballot("DE", db=db_session))["measureCoverage"]
    assert de["unreadReason"].startswith("Delaware publishes no statewide list")
    assert _body(elections.state_ballot("CA", db=db_session))["measureCoverage"]["unreadReason"] is None


@pytest.mark.asyncio
async def test_an_unread_states_coverage_records_its_reason(monkeypatch, db_session):
    _direct_source(monkeypatch, [["1"]])
    monkeypatch.setattr(election_pipeline, "federal_states", lambda: frozenset({"CA", "MS"}))
    from app.pipeline.fetch import ballot_measure_pdf_sources as sources

    monkeypatch.setattr(sources, "unread_reason", lambda st: "Mississippi publishes no list." if st == "MS" else None)
    await election_pipeline._sync_ballot_measures(db_session, None, 2026)
    row = db_session.query(MeasureCoverage).filter(MeasureCoverage.state == "MS").one()
    assert row.error_detail == "no direct source: Mississippi publishes no list."
