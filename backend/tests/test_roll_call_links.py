"""Related votes in Congress (app/roll_call_links.py): its calibration, its
arithmetic, the hourly matching and what the API serves from it."""

import ast
import json
import pathlib

import numpy as np
from fastapi import Response

from app import roll_call_links as rcl
from app.api.action import _build_issue_response, get_issue_members, get_monitor, issue_roll_calls
from app.issue_ids import to_public_id
from app.models import ActionIssue, ApiCache, MonitorUpdate, NationalMonitor, Representative, RollCall, RollCallPosition


def test_calibration_matches_the_examples():
    """Rerun scripts/calibrate_roll_call_links.py when the examples change."""
    cal = rcl.calibration()
    assert cal["data_hash"] == rcl.data_hash()
    assert cal["window_days"] == list(rcl.WINDOW)
    assert 0.0 < cal["threshold"] < 1.0


def test_the_threshold_is_where_the_precision_rule_holds():
    """The threshold is where precision's exact lower bound peaks, and that
    bound is at least 0.80; the held-out blocks, each fitted without its own
    dates, meet the bar too."""
    cal = rcl.calibration()
    assert cal["precision_rule_met"] is True
    curve = cal["curve"]
    peak = max(curve, key=lambda row: (row["precision_exact_lower"], -row["threshold"]))
    assert cal["threshold"] == peak["threshold"]
    assert cal["all_pairs"]["precision_exact_lower"] >= 0.8
    assert cal["held_out"]["precision_exact_lower"] >= 0.8


def test_every_label_is_one_of_its_own_candidates():
    ex = rcl.examples()
    for item in ex["items"]:
        assert set(item["related"]) <= set(item["candidates"]), item["id"]
        for ref in item["candidates"]:
            assert rcl.in_window(item["date"], ex["roll_calls"][ref]["date"]), (item["id"], ref)


def test_an_amendment_vote_is_linked_only_by_its_stated_purpose():
    assert not rcl.linkable("On Agreeing to the Amendment")
    assert not rcl.linkable("On the Amendment S.Amdt. 6715 to S.Amdt. 6711 to H.R. 5334", "")
    assert rcl.linkable("On the Amendment S.Amdt. 6760", "To limit price increases for college athletics tickets.")
    assert rcl.roll_call_text("On the Amendment S.Amdt. 6760", "Cruz Amdt. No. 6760", "To limit ticket prices.") == (
        "To limit ticket prices.")
    assert rcl.linkable("On the Motion to Discharge S.J.Res. 185")
    assert rcl.linkable("On Passage of the Bill H.R. 5334")


def test_a_vote_is_embedded_by_its_title_and_a_story_by_its_headline():
    assert rcl.roll_call_text("On the Nomination PN1078", "Todd Blanche, of Florida, to be Attorney General") == (
        "Todd Blanche, of Florida, to be Attorney General")
    assert rcl.roll_call_text("Call of the House", "") == "Call of the House"
    assert rcl.story_text(" A headline ") == "A headline"


def test_refs_round_trip_and_bad_ones_are_dropped():
    assert rcl.parse_ref(rcl.roll_call_ref("senate", 119, 2, 244)) == ("senate", 119, 2, 244)
    assert rcl.parse_ref("senate-119-2") is None
    assert rcl.parse_ref("assembly-1-2-3") is None
    assert rcl.stored_refs('["house-119-2-170", "nope", 3]') == ["house-119-2-170"]
    assert rcl.stored_refs(None) == [] and rcl.stored_refs("{not json") == []


# Two-dimensional stand-ins for both models: "iran" and "budget" point
# different ways, so only a like pair clears the threshold.
_VECS = {"iran": [1.0, 0.0], "budget": [0.0, 1.0]}


def _encode(texts):
    return np.array([_VECS["iran" if "Iran" in t else "budget"] for t in texts])


_FAKE = {m: _encode for m in rcl.MODELS}
_CAL = {"threshold": 0.5, "data_hash": "test"}


def test_a_story_links_the_like_votes_in_its_window_only():
    votes = [
        ("senate-119-2-244", "2026-09-24", "Remove Armed Forces from hostilities with Iran"),
        ("house-119-2-281", "2026-09-24", "Establishing the congressional budget"),
        ("senate-119-2-200", "2026-09-10", "Remove Armed Forces from hostilities with Iran"),
    ]
    out = rcl.link([("2026-09-24", "Senate GOP blocks effort to end Iran war")], votes, encode=_FAKE, cal=_CAL)
    assert out == [["senate-119-2-244"]]


def test_nothing_is_linked_when_the_calibration_met_no_threshold():
    votes = [("senate-119-2-244", "2026-09-24", "Iran")]
    assert rcl.link([("2026-09-24", "Iran")], votes, encode=_FAKE, cal={**_CAL, "threshold": None}) == [[]]


def test_the_window_runs_from_three_days_before_to_two_after():
    assert rcl.in_window("2026-09-24", "2026-09-21") and rcl.in_window("2026-09-24", "2026-09-26")
    assert not rcl.in_window("2026-09-24", "2026-09-20") and not rcl.in_window("2026-09-24", "2026-09-27")
    assert rcl.window_dates("2026-09-24") == ("2026-09-21", "2026-09-26")


def _vote(db, number, day, title, chamber="senate", question="On the Joint Resolution", yeas=49, nays=50):
    rc = RollCall(chamber=chamber, congress=119, session=2, number=number, date=day, question=question,
                  title=title, result="Rejected", rejected=yeas < nays, yeas=yeas, nays=nays)
    db.add(rc)
    db.flush()
    return rc


def test_the_hourly_refresh_stores_links_for_issues_and_monitor_updates(db_session):
    _vote(db_session, 244, "2026-09-24", "Remove Armed Forces from hostilities with Iran")
    _vote(db_session, 245, "2026-09-24", "Establishing the congressional budget")
    _vote(db_session, 246, "2026-09-24", "Iran Amdt. No. 1", question="On the Amendment S.Amdt. 1")
    issue = ActionIssue(date="2026-09-24", rank=1, title="Senate GOP blocks effort to end Iran war", summary="s")
    monitor = NationalMonitor(slug="us-iran-conflict", title="U.S.-Iran Conflict")
    db_session.add_all([issue, monitor])
    db_session.flush()
    update = MonitorUpdate(monitor_id=monitor.id, date="2026-09-25", summary="s", source_url="https://x",
                           article_title="Iran war vote fails")
    db_session.add(update)
    db_session.flush()

    assert rcl.refresh_links(db_session, "2026-09-26", encode=_FAKE, cal=_CAL) == 2

    assert json.loads(issue.related_roll_call_refs) == ["senate-119-2-244"]
    assert json.loads(update.roll_call_refs) == ["senate-119-2-244"]


def test_unmatched_rows_are_backfilled_in_bounded_batches(db_session, monkeypatch):
    monkeypatch.setattr(rcl, "BACKFILL_PER_RUN", 1)
    _vote(db_session, 10, "2026-06-01", "Remove Armed Forces from hostilities with Iran")
    old = [ActionIssue(date=d, rank=1, title="Iran war", summary="s") for d in ("2026-06-01", "2026-06-02")]
    db_session.add_all(old)
    db_session.flush()

    rcl.refresh_links(db_session, "2026-10-16", encode=_FAKE, cal=_CAL)
    assert [i.related_roll_call_refs is None for i in old] == [True, False]  # newest first
    rcl.refresh_links(db_session, "2026-10-16", encode=_FAKE, cal=_CAL)
    assert all(json.loads(i.related_roll_call_refs) == ["senate-119-2-10"] for i in old)


def test_a_new_calibration_resets_every_stored_link(db_session, monkeypatch):
    monkeypatch.setattr(rcl, "BACKFILL_PER_RUN", 0)
    issue = ActionIssue(date="2026-06-01", rank=1, title="Iran war", summary="s", related_roll_call_refs="[]")
    db_session.add(issue)
    db_session.flush()
    rcl.refresh_links(db_session, "2026-10-16", encode=_FAKE, cal=_CAL)
    assert issue.related_roll_call_refs == "[]"

    rcl.refresh_links(db_session, "2026-10-16", encode=_FAKE, cal={**_CAL, "threshold": 0.6})
    db_session.refresh(issue)
    assert issue.related_roll_call_refs is None
    assert db_session.query(ApiCache).filter(ApiCache.tier == "roll-call-links").count() == 1


def test_an_issue_serves_its_linked_votes_and_each_members_vote_on_them(db_session):
    rc = _vote(db_session, 170, "2026-05-14", "Remove Armed Forces from hostilities with Iran",
               chamber="house", yeas=212, nays=212)
    db_session.add(Representative(id="bo-birch", name="Bo Birch", state="VT", party="I", district=0,
                                  is_current=True, bioguide_id="B000001"))
    db_session.add(RollCallPosition(roll_call_id=rc.id, member_id="B000001", last_name="Birch", state="VT",
                                    position="Aye"))
    issue = ActionIssue(date="2026-05-14", rank=1, title="House ties on Iran", summary="s",
                        related_roll_call_refs=json.dumps(["house-119-2-170"]))
    db_session.add(issue)
    db_session.commit()

    assert [r.number for r in issue_roll_calls(db_session, issue)] == [170]
    body = _build_issue_response(issue, db_session)
    assert body["voteCount"] == 1
    (vote,) = body["relatedVotes"]
    assert (vote["title"], vote["yeas"], vote["nays"], vote["margin"]) == (
        "Remove Armed Forces from hostilities with Iran", 212, 212, 0)
    members = get_issue_members(to_public_id(issue.id), Response(), state="VT", db=db_session)
    assert members.members[0].positions == {"house-119-2-170": "Aye"}


async def test_a_monitor_serves_its_votes_on_the_record_oldest_first(db_session):
    _vote(db_session, 244, "2026-09-24", "Iran resolution")
    _vote(db_session, 170, "2026-05-14", "Iran resolution", chamber="house", yeas=212, nays=212)
    monitor = NationalMonitor(slug="us-iran-conflict", title="U.S.-Iran Conflict")
    db_session.add(monitor)
    db_session.flush()
    db_session.add_all([
        MonitorUpdate(monitor_id=monitor.id, date="2026-09-25", summary="a", source_url="https://a",
                      roll_call_refs=json.dumps(["senate-119-2-244"])),
        MonitorUpdate(monitor_id=monitor.id, date="2026-05-14", summary="b", source_url="https://b",
                      roll_call_refs=json.dumps(["house-119-2-170", "senate-119-2-244"])),
        MonitorUpdate(monitor_id=monitor.id, date="2026-05-15", summary="c", source_url="https://c"),
    ])
    db_session.commit()

    body = await get_monitor(Response(), "us-iran-conflict", db=db_session)

    assert [(v["chamber"], v["number"], v["margin"]) for v in body["onTheRecord"]] == [
        ("house", 170, 0), ("senate", 244, -1)]
    assert len(body["updates"]) == 3


def test_matching_lives_outside_the_fingerprinted_code():
    """app/roll_call_links.py is not under app/pipeline/, so the
    analysis-code hash never reads it, and no hashed module imports it."""
    from app.pipeline import senate_pipeline

    app_dir = pathlib.Path(senate_pipeline.__file__).resolve().parent.parent
    assert pathlib.Path(rcl.__file__).resolve().parent == app_dir
    hashed = [
        py for py in [*(app_dir / "pipeline").rglob("*.py"), app_dir / "config_definitions.py"]
        if "/fetch/" not in str(py)
        and py.relative_to(app_dir).as_posix() not in senate_pipeline._NOT_ANALYSIS_PATHS
    ]
    for py in hashed:
        for node in ast.walk(ast.parse(py.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "app.roll_call_links", py
                assert not (node.module == "app" and any(a.name == "roll_call_links" for a in node.names)), py


def test_names_are_masked_in_both_texts_and_left_out_of_what_is_compared():
    mask = rcl.NameMask(["Jane Q. Doe", "ROE, RICHARD A", "Ann Example"], president="Donald J. Trump")
    assert mask("Jane Doe confirmed; Richard Roe objects") == "[name] confirmed; [name] objects"
    assert mask("Jane Q. Doe, of Ohio, to be Attorney General") == "[name], of Ohio, to be Attorney General"
    assert mask("Trump's plan") == "[name]'s plan"
    # A bare surname other than the sitting president's is a word or someone else.
    assert mask("Doe and Example") == "Doe and Example"
    assert rcl.story_text("[name] confirmed as attorney general") == "confirmed as attorney general"
    assert rcl.roll_call_text("On the Nomination PN1", "[name], of Ohio, to be Attorney General") == (
        ", of Ohio, to be Attorney General")


def test_no_checked_in_example_names_a_person_the_site_records(db_session):
    """The examples are masked (AGENTS.md: no named politicians in checked-in
    files); every name NameMask would mask is already a mask."""
    ex = rcl.examples()
    mask = rcl.NameMask(["Donald J. Trump"], president="Donald J. Trump")
    texts = [t for i in ex["items"] for t in (i["title"], i["summary"])]
    texts += [t for v in ex["roll_calls"].values() for t in (v["title"], v.get("purpose") or "")]
    assert all(mask(t) == t for t in texts)
    assert sum("[name]" in t for t in texts) > 100


def test_each_newly_matched_story_with_a_vote_in_its_window_is_counted_for_drift(db_session, monkeypatch):
    seen = []
    monkeypatch.setattr(rcl.calibration_drift, "observe", lambda gate, scores, t: seen.append((gate, list(scores), t)))
    _vote(db_session, 244, "2026-09-24", "Remove Armed Forces from hostilities with Iran")
    db_session.add_all([
        ActionIssue(date="2026-09-24", rank=1, title="Iran war vote", summary="s"),
        ActionIssue(date="2026-06-01", rank=1, title="No votes that week", summary="s"),
    ])
    db_session.flush()
    rcl.refresh_links(db_session, "2026-09-26", encode=_FAKE, cal=_CAL)
    assert seen == [("roll_call_links", [1.0], 0.5)]
    # The hourly recheck of an already matched story is not counted again.
    rcl.refresh_links(db_session, "2026-09-26", encode=_FAKE, cal=_CAL)
    assert seen[-1] == ("roll_call_links", [], 0.5)
    assert "roll_call_links" in rcl.calibration_drift.GATES


def test_a_structural_link_is_kept_as_a_data_supplied_label(db_session):
    from app.models import RollCallLinkLabel

    _vote(db_session, 250, "2026-09-28", "A bill to protect student athletes")
    db_session.query(RollCall).filter_by(number=250).update({"bill_id": "S.4668"})
    _vote(db_session, 251, "2026-09-28", "Establishing the congressional budget")
    issue = ActionIssue(date="2026-09-29", rank=1, title="Senate passes the college sports bill", summary="s",
                        related_bill_ids=json.dumps([{"id": "S.4668", "congress": 119, "name": "S. 4668"}]))
    plain = ActionIssue(date="2026-09-29", rank=1, title="Another story", summary="s")
    db_session.add_all([issue, plain])
    db_session.flush()

    rcl.refresh_links(db_session, "2026-09-30", encode=_FAKE, cal=_CAL)

    (label,) = db_session.query(RollCallLinkLabel).all()
    assert label.issue_id == issue.id and label.source == "bill_named"
    assert json.loads(label.related) == ["senate-119-2-250"]
    assert set(json.loads(label.candidates)) == {"senate-119-2-250", "senate-119-2-251"}
