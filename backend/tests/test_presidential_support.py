"""Votes with the president's stated position: reading OMB's statements,
deciding which roll calls they apply to, the per-member tallies, the
expected rate and its interval (analyze/presidential_support.py)."""

import json
import math
import pathlib
from types import SimpleNamespace

import numpy as np
import pytest

from app.models import (
    PolicyStatement, President, PresidentialSupport, Representative, RollCall, RollCallPosition, Senator, StatementRollCall,
)
from app.pipeline.analyze import presidential_support as ps
from app.pipeline.analyze import sap_position
from app.pipeline.fetch import omb_statements
from app.services.presidential_support_service import chamber_view, member_view, summary

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "omb"


# ── The index and the PDF ─────────────────────────────────────────

def test_index_lists_each_pdf_once_in_page_order():
    entries = omb_statements.parse_index((FIXTURES / "index.html").read_text())
    assert [e["url"].rsplit("/", 1)[-1] for e in entries] == [
        "SAP-HR9340.pdf", "SAP-S.Res_.-852.pdf", "SAP-S.J.-Res.-197.pdf",
    ]
    assert entries[0]["title"].startswith("H.R. 9340")


def test_a_real_statement_reads_into_its_document_fields():
    parsed = omb_statements.read_statement((FIXTURES / "SAP-HR1968.pdf").read_bytes())
    assert parsed["issued"] == "2025-03-11"
    assert parsed["chamber"] == "house"
    assert parsed["measures"] == ["HR.1968"]
    assert parsed["amendments"] == []
    assert "strongly supports passage of H.R. 1968" in parsed["opening"]
    assert parsed["closing"].startswith("If this bill were presented to the President")


def _line(i, text, x=72.0, gap=14.0, page=0):
    top = 100.0 + i * gap
    return (page, x, top, top + 12.0, text)


def test_title_block_citations_and_paragraphs_from_line_geometry():
    """The heading split across lines, a centred multi-measure title with an
    amendment number, and body paragraphs separated by a wider gap."""
    body = "x" * 45
    lines = [
        (0, 400.0, 10.0, 22.0, "July 30, 2026"),
        (0, 400.0, 24.0, 36.0, "(Senate)"),
        (0, 100.0, 40.0, 60.0, "S"), (0, 110.0, 42.0, 58.0, "TATEMENT OF"),
        (0, 250.0, 40.0, 60.0, "A"), (0, 260.0, 42.0, 58.0, "DMINISTRATION"),
        (0, 430.0, 40.0, 60.0, "P"), (0, 440.0, 42.0, 58.0, "OLICY"),
        (0, 90.0, 62.0, 74.0, "S. Amendment 12 to H.R. 5 and S.J. Res. 7 – A Long Title That Runs Wide Here"),
        (0, 200.0, 76.0, 88.0, "(Sen. Roe, D-VA, and 3 cosponsors)"),
        # Three paragraphs of three lines, 2pt between lines, 24pt between paragraphs.
        *[(0, 72.0, 100.0 + 14 * i, 112.0 + 14 * i, t) for i, t in
          enumerate([f"The Administration opposes passage. {body}", body, body])],
        *[(0, 72.0, 164.0 + 14 * i, 176.0 + 14 * i, t) for i, t in
          enumerate([f"Second paragraph {body}", body, body])],
        *[(0, 72.0, 228.0 + 14 * i, 240.0 + 14 * i, t) for i, t in
          enumerate(["If it were presented to the President, his advisors would", "recommend a veto.", "2"])],
        (0, 300.0, 280.0, 292.0, "* * * * * * *"),
    ]
    parsed = omb_statements.parse_statement(lines)
    assert parsed["chamber"] == "senate" and parsed["issued"] == "2026-07-30"
    assert parsed["measures"] == ["HR.5", "SJRES.7"]
    assert parsed["amendments"] == ["12"]
    assert parsed["opening"].startswith("The Administration opposes passage.") and body in parsed["opening"]
    assert parsed["closing"] == "If it were presented to the President, his advisors would recommend a veto."


def test_a_file_that_is_not_a_statement_reads_as_none():
    assert omb_statements.parse_statement([_line(0, "Some other document entirely, with no heading at all")]) is None
    assert omb_statements.read_statement(b"not a pdf") is None


# ── Reading the position ──────────────────────────────────────────

def test_sentences_keep_citations_whole():
    text = ("The Administration strongly opposes passage of H. Con. Res. 89, a resolution. "
            "U.S. forces remain in the region today. Too short.")
    assert sap_position.sentences(text) == [
        "The Administration strongly opposes passage of H. Con. Res. 89, a resolution.",
        "U.S. forces remain in the region today.",
    ]


def test_decision_rule():
    """The most position-like sentence decides; nearer 'other' means no
    position on passage."""
    sims = {"support": np.array([0.70, 0.90]), "oppose": np.array([0.71, 0.85]), "other": np.array([0.80, 0.80])}
    assert sap_position.decide(sims) == ("support", 1)
    sims["other"] = np.array([0.80, 0.95])
    assert sap_position.decide(sims) == ("none", 1)
    sims = {"support": np.array([0.80]), "oppose": np.array([0.86]), "other": np.array([0.70])}
    assert sap_position.decide(sims) == ("oppose", 0)


@pytest.mark.slow
def test_the_fixture_statement_reads_as_support():
    parsed = omb_statements.read_statement((FIXTURES / "SAP-HR1968.pdf").read_bytes())
    position, sentence = sap_position.classify(parsed["opening"], parsed["closing"])
    assert position == "support"
    assert sentence in parsed["opening"] + " " + parsed["closing"]


# ── Which roll calls a statement applies to ───────────────────────

def _s(sid, chamber, issued, measures, amendments=()):
    return {"id": sid, "chamber": chamber, "issued": issued, "measures": list(measures), "amendments": list(amendments)}


def _rc(chamber, bill, day, question, title=""):
    return {"chamber": chamber, "bill_id": bill, "date": day, "question": question, "title": title}


def test_final_vote_after_a_statement_to_that_chamber_counts():
    statements = [_s(1, "house", "2026-05-01", ["HR.1"])]
    assert ps.applicable_statement(_rc("house", "HR.1", "2026-05-02", "On Passage"), statements)["id"] == 1
    # Same day counts; before it doesn't; the other chamber doesn't.
    assert ps.applicable_statement(_rc("house", "HR.1", "2026-05-01", "On Passage"), statements)["id"] == 1
    assert ps.applicable_statement(_rc("house", "HR.1", "2026-04-30", "On Passage"), statements) is None
    assert ps.applicable_statement(_rc("senate", "HR.1", "2026-05-03", "On Passage of the Bill H.R. 1"), statements) is None


@pytest.mark.parametrize("question", [
    "On Motion to Recommit", "On Ordering the Previous Question", "On Agreeing to the Amendment",
])
def test_procedural_votes_and_unnamed_amendments_do_not_count(question):
    statements = [_s(1, "house", "2026-05-01", ["HR.1"])]
    assert ps.applicable_statement(_rc("house", "HR.1", "2026-05-02", question), statements) is None


def test_senate_questions_carry_the_document():
    statements = [_s(1, "senate", "2026-05-01", ["SJRES.7"])]
    assert ps.applicable_statement(_rc("senate", "SJRES.7", "2026-05-02", "On the Joint Resolution S.J.Res. 7"), statements)
    assert ps.applicable_statement(_rc("senate", "SJRES.7", "2026-05-02", "On the Motion to Discharge S.J.Res. 7"),
                                   statements) is None
    assert ps.applicable_statement(_rc("senate", "SJRES.7", "2026-05-02", "On Cloture on the Motion to Proceed S.J.Res. 7"),
                                   statements) is None


def test_a_later_statement_on_the_measure_in_either_chamber_closes_the_window():
    statements = [_s(1, "house", "2026-05-01", ["HR.1"]), _s(2, "senate", "2026-06-28", ["HR.1"])]
    assert ps.applicable_statement(_rc("house", "HR.1", "2026-05-22", "On Passage"), statements)["id"] == 1
    concur = _rc("house", "HR.1", "2026-07-03", "On Motion to Concur in the Senate Amendment")
    assert ps.applicable_statement(concur, statements) is None
    senate = _rc("senate", "HR.1", "2026-07-01", "On Passage of the Bill H.R. 1")
    assert ps.applicable_statement(senate, statements)["id"] == 2


def test_an_amendment_statement_applies_to_that_amendment_only():
    statements = [_s(1, "senate", "2026-07-28", ["HR.5"]), _s(2, "senate", "2026-08-07", ["HR.5"], ["6715"])]
    amendment = _rc("senate", "HR.5", "2026-08-07", "On the Amendment S.Amdt. 6715 to S.Amdt. 6711 to H.R. 5")
    assert ps.applicable_statement(amendment, statements)["id"] == 2
    other = _rc("senate", "HR.5", "2026-08-07", "On the Amendment S.Amdt. 6716 to H.R. 5")
    assert ps.applicable_statement(other, statements) is None
    # An amendment's statement does not close the measure's window.
    passage = _rc("senate", "HR.5", "2026-08-07", "On Passage of the Bill H.R. 5")
    assert ps.applicable_statement(passage, statements)["id"] == 1


@pytest.mark.parametrize("position,stance,expected", [
    ("Yea", "support", True), ("Aye", "support", True), ("No", "support", False),
    ("Nay", "oppose", True), ("Yea", "oppose", False), ("Not Voting", "support", None), ("Present", "oppose", None),
])
def test_which_side_a_recorded_position_is(position, stance, expected):
    assert ps.with_president(position, stance) is expected


# ── The numbers ───────────────────────────────────────────────────

def test_wilson_interval_matches_published_values():
    # Agresti & Coull (1998) use these as worked cases: x=0 keeps a real
    # upper end where the Wald interval collapses to zero width.
    low, high = ps.wilson_interval(0, 10)
    assert low == 0.0 and math.isclose(high, 0.2775, abs_tol=1e-4)
    low, high = ps.wilson_interval(5, 10)
    assert math.isclose(low, 0.2366, abs_tol=1e-4) and math.isclose(high, 0.7634, abs_tol=1e-4)
    assert ps.wilson_interval(0, 0) is None


def test_fit_recovers_a_known_curve_and_needs_enough_members():
    rng = np.random.default_rng(7)
    rows = []
    for party, a, b in (("D", -1.5, -1.0), ("R", 2.0, 1.2)):
        for al in np.linspace(-0.4, 1.0, 40):
            n = 200
            p = 1 / (1 + math.exp(-(a + b * al)))
            rows.append((party, float(al), int(rng.binomial(n, p)), n))
    fits = ps.fit_expectation(rows)
    assert math.isclose(fits["D"]["a"], -1.5, abs_tol=0.25) and math.isclose(fits["R"]["b"], 1.2, abs_tol=0.4)
    assert fits["D"]["members"] == 40 and fits["D"]["votes"] == 8000
    assert ps.fit_expectation(rows[:10]) == {}


def test_summary_says_which_side_of_the_expectation_the_interval_sits():
    row = PresidentialSupport(chamber="house", member_id="x", congress=119, party="D",
                              with_president=30, votes=100, expected=0.1)
    out = summary(row)
    assert out["rate"] == 0.3 and out["comparedWithExpected"] == "more"
    assert out["differenceLow"] == round(out["rateLow"] - 0.1, 4)
    row.expected = 0.3
    assert summary(row)["comparedWithExpected"] == "within"
    row.expected = None
    assert summary(row)["difference"] is None and summary(row)["comparedWithExpected"] is None


# ── End to end on the stored record ───────────────────────────────

def _house_fixture(db):
    """Two parties of 20+ members so the fit runs; one supported bill passed
    and one opposed resolution, one procedural vote that must not count."""
    statements = [
        PolicyStatement(url="https://example.gov/a.pdf", title="H.R. 10 – A", readable=True, issued="2026-03-01",
                        chamber="house", measures='["HR.10"]', amendments="[]", position="support",
                        position_sentence="The Administration supports passage of H.R. 10."),
        PolicyStatement(url="https://example.gov/b.pdf", title="H.J. Res. 3 – B", readable=True, issued="2026-03-01",
                        chamber="house", measures='["HJRES.3"]', amendments="[]", position="oppose",
                        position_sentence="The Administration opposes passage of H.J. Res. 3."),
        PolicyStatement(url="https://example.gov/c.pdf", title="H.R. 11 – C", readable=True, issued="2026-03-01",
                        chamber="house", measures='["HR.11"]', amendments="[]", position="none",
                        position_sentence="The Administration looks forward to working with Congress."),
    ]
    db.add_all(statements)
    rolls = [
        RollCall(chamber="house", congress=119, session=2, number=1, date="2026-03-02", question="On Passage",
                 title="A", bill_id="HR.10", result="Passed", source_url="https://clerk.house.gov/evs/2026/roll001.xml"),
        RollCall(chamber="house", congress=119, session=2, number=2, date="2026-03-02",
                 question="On Agreeing to the Resolution", title="B", bill_id="HJRES.3", result="Failed",
                 source_url="https://clerk.house.gov/evs/2026/roll002.xml"),
        RollCall(chamber="house", congress=119, session=2, number=3, date="2026-03-02", question="On Motion to Recommit",
                 title="A", bill_id="HR.10", result="Failed", source_url="https://clerk.house.gov/evs/2026/roll003.xml"),
        RollCall(chamber="house", congress=119, session=2, number=4, date="2026-03-02", question="On Passage",
                 title="C", bill_id="HR.11", result="Passed", source_url="https://clerk.house.gov/evs/2026/roll004.xml"),
    ]
    db.add_all(rolls)
    db.flush()
    for i in range(24):
        for party in ("D", "R"):
            bio = f"{party}{i:06d}"
            db.add(Representative(id=f"rep-{bio.lower()}", bioguide_id=bio, name=f"Member {bio}", state="OH",
                                  district=i + 1, party=party))
            # R: with the president on both; D: against on both, except a few.
            loyal = party == "R" or i < 3
            db.add(RollCallPosition(roll_call_id=rolls[0].id, member_id=bio, last_name=bio, party=party, state="OH",
                                    position="Yea" if loyal else "Nay"))
            db.add(RollCallPosition(roll_call_id=rolls[1].id, member_id=bio, last_name=bio, party=party, state="OH",
                                    position=("Nay" if loyal else "Yea") if i != 5 else "Not Voting"))
            for rc in (rolls[2], rolls[3]):
                db.add(RollCallPosition(roll_call_id=rc.id, member_id=bio, last_name=bio, party=party, state="OH",
                                        position="Yea"))
    db.commit()
    return statements, rolls


def test_compute_chamber_counts_only_final_votes_with_a_position(db_session):
    _house_fixture(db_session)
    out = ps.compute_chamber(db_session, "house", 119)
    assert out["rollCalls"] == 2 and out["matched"] == 3 and out["members"] == 48
    assert db_session.query(StatementRollCall).count() == 3
    rows = {r.member_id: r for r in db_session.query(PresidentialSupport)}
    assert (rows["rep-r000000"].with_president, rows["rep-r000000"].votes) == (2, 2)
    assert (rows["rep-d000010"].with_president, rows["rep-d000010"].votes) == (0, 2)
    assert rows["rep-d000005"].votes == 1  # not voting is not a vote
    assert rows["rep-d000001"].with_president == 2
    # Rerunning replaces, never duplicates.
    ps.compute_chamber(db_session, "house", 119)
    assert db_session.query(PresidentialSupport).count() == 48


def test_read_side_views(db_session):
    _house_fixture(db_session)
    ps.compute_chamber(db_session, "house", 119)
    view = chamber_view(db_session, "house")
    assert len(view["members"]) == 48 and [r["rollCall"] for r in view["rollCalls"]] == [2, 1]
    assert view["rollCalls"][0]["presidentSide"] == "Nay"
    assert view["rollCalls"][0]["statement"]["sentence"].startswith("The Administration opposes")
    member = member_view(db_session, "rep-d000005")
    assert [v["memberVote"] for v in member["rollCalls"]] == ["Not Voting", "Nay"]
    assert [v["withPresident"] for v in member["rollCalls"]] == [None, False]
    assert member_view(db_session, "nobody") is None
    assert chamber_view(db_session, "senate") is None


def test_senate_positions_resolve_by_name_and_state(db_session):
    db_session.add(PolicyStatement(url="https://example.gov/s.pdf", title="S. 2", readable=True, issued="2026-03-01",
                                   chamber="senate", measures='["S.2"]', amendments="[]", position="support",
                                   position_sentence="The Administration supports passage of S. 2."))
    rc = RollCall(chamber="senate", congress=119, session=2, number=9, date="2026-03-04",
                  question="On Passage of the Bill S. 2", title="S. 2", bill_id="S.2", result="Bill Passed")
    db_session.add(rc)
    db_session.add(Senator(id="jane-doe", bioguide_id="D000001", name="Jane Doe", state="GA", party="D"))
    db_session.flush()
    db_session.add(RollCallPosition(roll_call_id=rc.id, member_id="S999", last_name="Doe", first_name="Jane",
                                    party="D", state="GA", position="Yea"))
    db_session.commit()
    ps.compute_chamber(db_session, "senate", 119)
    (row,) = db_session.query(PresidentialSupport).all()
    assert (row.member_id, row.with_president, row.votes, row.expected) == ("jane-doe", 1, 1, None)
    assert member_view(db_session, "jane-doe")["rollCalls"][0]["memberVote"] == "Yea"


# ── Reading new statements ────────────────────────────────────────

async def test_refresh_reads_new_statements_and_keeps_going_past_a_failed_fetch(db_session, monkeypatch):
    raw = (FIXTURES / "SAP-HR1968.pdf").read_bytes()
    entries = [{"url": "https://example.gov/ok.pdf", "title": "H.R. 1968"},
               {"url": "https://example.gov/down.pdf", "title": "Down"},
               {"url": "https://example.gov/junk.pdf", "title": "Junk"}]

    async def index(_client):
        return entries

    async def pdf(_client, url):
        return {"ok": raw, "down": None, "junk": b"not a pdf"}[url.rsplit("/", 1)[-1][:-4]]

    monkeypatch.setattr(omb_statements, "fetch_index", index)
    monkeypatch.setattr(omb_statements, "fetch_statement_pdf", pdf)
    monkeypatch.setattr(sap_position, "classify", lambda opening, closing: ("support", "sentence"))
    assert await ps.refresh_statements(None, db_session) == {"status": "ok", "read": 2}
    stored = {s.url.rsplit("/", 1)[-1]: s for s in db_session.query(PolicyStatement)}
    assert set(stored) == {"ok.pdf", "junk.pdf"}  # a failed fetch is retried, never stored
    assert stored["ok.pdf"].readable and json.loads(stored["ok.pdf"].measures) == ["HR.1968"]
    assert not stored["junk.pdf"].readable
    # Nothing new: nothing read again.
    assert (await ps.refresh_statements(None, db_session))["read"] == 0  # the failed fetch is tried again, and fails again

    async def outage(_client):
        return None

    monkeypatch.setattr(omb_statements, "fetch_index", outage)
    assert await ps.refresh_statements(None, db_session) == {"status": "index unavailable", "read": 0}
    assert db_session.query(PolicyStatement).count() == 2


def test_a_statement_from_another_congress_never_applies(db_session):
    """H.R. 10 of the 120th is not the 119th's: a 2025 statement must not
    count a 2027 vote on the same number."""
    db_session.add(PolicyStatement(url="https://example.gov/old.pdf", title="H.R. 10", readable=True,
                                   issued="2025-03-01", chamber="house", measures='["HR.10"]', amendments="[]",
                                   position="support", position_sentence="Supports."))
    db_session.add(RollCall(chamber="house", congress=120, session=1, number=1, date="2027-02-01",
                            question="On Passage", title="H.R. 10", bill_id="HR.10", result="Passed"))
    db_session.commit()
    assert ps.match_roll_calls(db_session, "house", 120) == 0


async def test_reading_stops_at_its_budget(db_session, monkeypatch):
    """A first run reads ~200 statements; past the budget the rest wait for
    the next run instead of holding the pipeline."""
    raw = (FIXTURES / "SAP-HR1968.pdf").read_bytes()

    async def index(_client):
        return [{"url": f"https://example.gov/{i}.pdf", "title": "H.R. 1968"} for i in range(3)]

    async def pdf(_client, url):
        return raw

    ticks = iter([0.0, 0.0, 10.0])
    monkeypatch.setattr(omb_statements, "fetch_index", index)
    monkeypatch.setattr(omb_statements, "fetch_statement_pdf", pdf)
    monkeypatch.setattr(sap_position, "classify", lambda opening, closing: ("support", "sentence"))
    monkeypatch.setattr(ps, "READ_BUDGET_S", 5)
    monkeypatch.setattr(ps, "time", SimpleNamespace(monotonic=lambda: next(ticks, 10.0)))
    assert await ps.refresh_statements(None, db_session) == {"status": "budget spent", "read": 1}


def test_a_congress_spanning_an_inauguration_counts_only_the_sitting_president(db_session):
    """The 121st Congress convenes on Jan 3, 2029, under the outgoing
    president; from Jan 20 "the president" is the new one. An outgoing
    statement must not count a later vote as the new president's position,
    and votes before the new term are not the new president's either."""
    db_session.add_all([
        President(id="outgoing-47", name="Outgoing", party="R", number=47, term_start="2025-01-20",
                  term_end="2029-01-20"),
        President(id="incoming-48", name="Incoming", party="D", number=48, term_start="2029-01-20", is_current=True),
        PolicyStatement(url="https://example.gov/outgoing.pdf", title="H.R. 5", readable=True, issued="2029-01-10",
                        chamber="house", measures='["HR.5"]', amendments="[]", position="support",
                        position_sentence="Supports."),
        PolicyStatement(url="https://example.gov/incoming.pdf", title="H.R. 6", readable=True, issued="2029-02-01",
                        chamber="house", measures='["HR.6"]', amendments="[]", position="oppose",
                        position_sentence="Opposes."),
        RollCall(chamber="house", congress=121, session=1, number=1, date="2029-01-15", question="On Passage",
                 title="H.R. 5", bill_id="HR.5", result="Passed"),
        RollCall(chamber="house", congress=121, session=1, number=2, date="2029-01-25", question="On Passage",
                 title="H.R. 5", bill_id="HR.5", result="Passed"),
        RollCall(chamber="house", congress=121, session=1, number=3, date="2029-02-05", question="On Passage",
                 title="H.R. 6", bill_id="HR.6", result="Failed"),
    ])
    db_session.commit()
    assert ps.match_roll_calls(db_session, "house", 121) == 1
    (only,) = db_session.query(StatementRollCall).all()
    assert db_session.get(RollCall, only.roll_call_id).number == 3
