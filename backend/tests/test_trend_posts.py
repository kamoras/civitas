"""Trend posts ("On the record"): analyze/trend_poster.py and its parts.

No model and no network: trend scores, embeddings and the getTrends read
are replaced per test; the record matching, templates, caps, holds, audit
and publishing run for real against an in-memory database.
"""

import dataclasses
import json
import re
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
import numpy as np
import pytest
from atproto_client.models.blob_ref import BlobRef

from app import broadcast
from app.api.feed import _content_html
from app.models import (
    ActionIssue,
    BroadcastPost,
    CongressEvent,
    RollCall,
    RollCallPosition,
    Senator,
    SponsoredBill,
    TrendCandidate,
    TrendObservation,
)
from app.pipeline.analyze import trend_evidence, trend_poster, trend_relevance
from app.pipeline.analyze.bluesky_utils import BSKY_MAX_IMAGE_BYTES, PostImage, publish_post
from app.pipeline.analyze.trend_relevance import TrendScores
from app.pipeline.fetch import trending
from app.pipeline.fetch.trending import BlueskyTrend
from app.services import record_cards

NOW = datetime(2026, 10, 14, 18, 0)  # 14:00 Eastern, inside the posting window
RELEVANT = TrendScores(relevance=0.9, tragedy=0.0, political_violence=0.0, loaded=0.0)


def trend(name, topic="t1", rank=0, description="", started=NOW - timedelta(hours=3), category="politics"):
    return BlueskyTrend(topic=topic, display_name=name, description=description, rank=rank,
                        status="trending", category=category, started_at=started, post_count=100)


def fake_embed(texts):
    """A unit vector per text, keyed on the first word the fixtures share
    on purpose ("tariff", "harbor", ...), so related texts score 1.0 and
    unrelated ones 0."""
    keys = ["tariff", "harbor", "orchard", "nominee", "canal"]
    out = np.zeros((len(texts), len(keys) + 1), dtype=np.float32)
    for i, t in enumerate(texts):
        low = t.lower()
        hit = next((j for j, k in enumerate(keys) if k in low), len(keys))
        out[i, hit] = 1.0
    return out


@pytest.fixture()
def world(db_session, monkeypatch, freeze_utcnow):
    """A Senate with a sponsor, a bill, a vote on it, a day's record and an
    issue, and the trend poster's model and network calls replaced."""
    freeze_utcnow(NOW)
    db = db_session
    db.add_all([
        Senator(id="S1", name="Pat Quill", state="OR", party="D"),
        Senator(id="S2", name="Lee Marsh", state="TX", party="R"),
    ])
    db.add(SponsoredBill(senator_id="S1", bill_id="S.100", title="Example Tariff Relief Act", congress=119,
                         introduced_date="2026-09-01", latest_action="Placed on the calendar.",
                         latest_action_date="2026-10-01"))
    db.add(SponsoredBill(senator_id="S2", bill_id="S.200", title="Harbor Dredging Act", congress=119,
                         introduced_date="2026-09-02", latest_action="Referred to committee.",
                         latest_action_date="2026-10-03"))
    rc = RollCall(chamber="senate", congress=119, session=2, number=301, date="2026-10-13",
                  question="On Passage of the Bill", title="Example Tariff Relief Act",
                  result="Bill Passed", yeas=2, nays=1, bill_id="S.100", fetched_at=NOW)
    db.add(rc)
    db.flush()
    db.add_all([
        RollCallPosition(roll_call_id=rc.id, member_id="X1", last_name="Quill", party="D", state="OR", position="Yea"),
        RollCallPosition(roll_call_id=rc.id, member_id="X2", last_name="Marsh", party="R", state="TX", position="Yea"),
        RollCallPosition(roll_call_id=rc.id, member_id="X3", last_name="Doe", party="R", state="OH", position="Nay"),
    ])
    db.add(CongressEvent(chamber="senate", date="2026-10-12", kind="committee", seq=1,
                         name="Committee on Orchards", text="Committee on Orchards held a hearing on orchard pests.",
                         source="digest"))
    db.add(ActionIssue(date="2026-10-14", rank=1, title="Canal toll plan advances", related_bill_ids="[]",
                       related_explore_ids="[]", related_senators=json.dumps([{"party": "R"}])))
    db.commit()

    monkeypatch.setattr(trend_poster, "_embed_texts_sim", fake_embed)
    monkeypatch.setattr(trend_evidence, "_embed_texts_sim", fake_embed)
    monkeypatch.setattr(trend_poster, "loaded_votes", lambda texts: [0.0 for _ in texts])
    monkeypatch.setattr(trend_poster, "check_source_freshness", lambda *a, **k: None)
    alerts = []
    monkeypatch.setattr(trend_poster, "send_ops_alert", lambda *a, **k: alerts.append((a, k)))
    monkeypatch.setattr(trend_poster, "resolve_ops_alert", lambda *a, **k: None)
    state = {"trends": [], "scores": {}}
    monkeypatch.setattr(trend_poster, "fetch_bluesky_trends", lambda: state["trends"])
    monkeypatch.setattr(trend_poster, "score_trends", lambda triples: [
        state["scores"].get(n, dataclasses.replace(RELEVANT, relevance=1.0 if c == "politics" else 0.0))
        for n, _, c in triples])
    state["db"], state["alerts"] = db, alerts
    return state


def run(world, at=NOW):
    return trend_poster.run_trend_posts(world["db"], now=at)


def posts(db):
    return db.query(BroadcastPost).filter_by(kind="trend").order_by(BroadcastPost.id).all()


# -- posting on a pinned match -----------------------------------------------

def test_an_act_title_match_posts_the_vote_in_its_own_words(world, bluesky_configured):
    world["trends"] = [trend("Example Tariff Relief Act")]
    assert run(world)["posted"] == 1
    (post,) = posts(world["db"])
    assert post.subject == "trend-record:vote:senate/119/2/301"
    assert post.url == "https://civitas-research.org/congress/votes/senate/119/2/301"
    assert post.text == (
        "Trending on Bluesky: “Example Tariff Relief Act”. On the record: Senate roll call 301, "
        "119th Congress, October 13, 2026: On Passage of the Bill: Example Tariff Relief Act. "
        "Result: Bill Passed. Yea 2. Nay 1."
    )
    assert post.image_url == "https://civitas-research.org/api/og?vote=senate/119/2/301"
    row = world["db"].query(TrendCandidate).one()
    assert (row.decision, row.match_type, row.record_kind) == ("posted", "act", "vote")
    assert row.broadcast_post_id == post.id


def test_every_word_is_template_a_record_field_or_the_quoted_trend_name(world, bluesky_configured):
    world["trends"] = [trend("Example Tariff Relief Act")]
    run(world)
    (post,) = posts(world["db"])
    card = record_cards.card_for_key(world["db"], "vote:senate/119/2/301")
    template = {"Trending", "on", "Bluesky", "On", "the", "record", "Yea", "Nay", "Result"}
    fields = " ".join([card.eyebrow, card.headline, *card.lines, record_cards.long_date(card.date),
                       "Example Tariff Relief Act"])
    for word in re.findall(r"[A-Za-z0-9]+", post.text):
        assert word in template or word in fields, word


def test_an_issue_match_needs_cosine_of_at_least_half_and_links_the_issue(world, bluesky_configured):
    world["trends"] = [trend("Canal tolls", description="The canal toll plan advances in the state legislature.")]
    run(world)
    (post,) = posts(world["db"])
    assert post.text.startswith("Trending on Bluesky: “Canal tolls”. On the record: Civitas Action Center")
    assert post.url.startswith("https://civitas-research.org/issue/")
    assert post.image_url is None and post.embed_kind == "card"


def test_a_member_named_alone_is_not_a_match(world):
    world["trends"] = [trend("Lee Marsh speech")]  # names a member; no bill of theirs is about it
    run(world)
    row = world["db"].query(TrendCandidate).one()
    assert (row.decision, row.reason) == ("ineligible", "no pinned record")
    assert json.loads(row.named_parties) == ["R"]


def test_a_named_member_with_their_own_bill_on_the_topic_matches(world):
    world["trends"] = [trend("Lee Marsh harbor push", description="Lee Marsh presses his harbor dredging bill.")]
    run(world)
    row = world["db"].query(TrendCandidate).one()
    assert (row.match_type, row.record_key) == ("member", "bill:119/S.200")
    assert json.loads(row.sponsor_parties) == ["R"]


def test_a_record_dated_after_the_trend_plus_two_days_is_not_evidence(world):
    world["trends"] = [trend("Example Tariff Relief Act", started=datetime(2026, 9, 1, 12))]
    run(world)
    row = world["db"].query(TrendCandidate).one()
    assert row.record_key == "bill:119/S.100" or row.decision == "ineligible"  # the vote (Oct 13) is too late
    assert row.record_kind != "vote"


def test_a_trend_bluesky_files_outside_politics_is_never_matched(world):
    world["trends"] = [trend("Example Tariff Relief Act", category="sports")]
    run(world)
    row = world["db"].query(TrendCandidate).one()
    assert row.decision == "ineligible" and row.reason.startswith("Bluesky category sports")
    assert row.record_key is None


def test_relevance_is_bluesky_politics_category(monkeypatch):
    """Whether a trend is a policy story is Bluesky's filing, not a vote."""
    monkeypatch.setattr(trend_relevance, "_embed_texts_sim", lambda texts: np.zeros((len(texts), 3)))
    monkeypatch.setattr(trend_relevance, "gate_votes", lambda gate, emb: np.zeros(len(emb)))
    scores = trend_relevance.score_trends([("A", "", "politics"), ("B", None, "Politics "), ("C", "", "sports"),
                                           ("D", "", None)])
    assert [s.relevant for s in scores] == [True, True, False, False]
    assert "relevance" not in trend_relevance.GATES


# -- the neutrality gate ------------------------------------------------------

@pytest.mark.parametrize("name", ["VOTE PAT QUILL", "Vote for Pat Quill now"])
def test_an_electioneering_trend_name_is_skipped(world, name):
    world["trends"] = [trend(name)]
    run(world)
    row = world["db"].query(TrendCandidate).one()
    assert row.decision == "skipped" and "electioneering" in row.reason
    assert posts(world["db"]) == []


def test_a_loaded_trend_name_is_skipped(world):
    world["trends"] = [trend("Example Tariff Relief Act")]
    world["scores"]["Example Tariff Relief Act"] = TrendScores(0.9, 0, 0, loaded=0.99)
    run(world)
    row = world["db"].query(TrendCandidate).one()
    assert row.decision == "skipped" and "loaded language" in row.reason


def test_a_name_announcing_what_its_description_only_urges_is_skipped(world):
    world["trends"] = [trend("Tariff war ends", description="Senators urge the President to end the tariff war.")]
    run(world)
    row = world["db"].query(TrendCandidate).one()
    assert row.decision == "skipped" and "advocacy stated as fact" in row.reason


def test_a_loaded_bill_title_is_left_out_of_the_post_and_the_image(world, monkeypatch, bluesky_configured):
    monkeypatch.setattr(trend_poster, "loaded_votes", lambda texts: [0.99 for _ in texts])
    world["trends"] = [trend("Example Tariff Relief Act")]
    run(world)
    (post,) = posts(world["db"])
    assert "On Passage of the Bill: S. 100." in post.text
    assert post.text.count("Example Tariff Relief Act") == 1  # the quoted trend name only
    assert post.image_url.endswith("&title=0")
    assert "Example Tariff Relief Act" not in post.alt_text


# -- tragedies -----------------------------------------------------------------

def test_a_tragedy_with_no_record_is_never_posted(world):
    world["trends"] = [trend("Shooting at a fairground")]
    world["scores"]["Shooting at a fairground"] = TrendScores(0.9, tragedy=0.95, political_violence=0.95, loaded=0)
    run(world)
    row = world["db"].query(TrendCandidate).one()
    assert row.decision == "ineligible" and posts(world["db"]) == []


def test_a_tragedy_without_a_political_reading_is_skipped_even_with_a_record(world):
    world["trends"] = [trend("Example Tariff Relief Act")]
    world["scores"]["Example Tariff Relief Act"] = TrendScores(0.9, tragedy=0.95, political_violence=0.1, loaded=0)
    run(world)
    row = world["db"].query(TrendCandidate).one()
    assert row.decision == "skipped" and "tragedy" in row.reason and posts(world["db"]) == []


def test_a_political_tragedy_needs_an_official_record_not_an_issue(world):
    world["trends"] = [trend("Canal tolls", description="The canal toll plan advances in the state legislature.")]
    world["scores"]["Canal tolls"] = TrendScores(0.9, tragedy=0.95, political_violence=0.95, loaded=0)
    run(world)
    row = world["db"].query(TrendCandidate).one()
    assert row.decision == "skipped" and "official record" in row.reason


def test_a_political_tragedy_with_an_official_record_posts_only_the_record(world, bluesky_configured):
    world["trends"] = [trend("Example Tariff Relief Act")]
    world["scores"]["Example Tariff Relief Act"] = TrendScores(0.9, tragedy=0.95, political_violence=0.95, loaded=0)
    run(world)
    (post,) = posts(world["db"])
    assert post.text.split("On the record: ", 1)[1].startswith("Senate roll call 301")


# -- caps, dedupe, holds ---------------------------------------------------------

def test_one_post_a_run_two_a_day_four_hours_apart(world, freeze_utcnow):
    db = world["db"]
    world["trends"] = [trend("Example Tariff Relief Act", "a"), trend("Lee Marsh harbor push", "b", rank=1, description="Lee Marsh presses his harbor dredging bill."),
                       trend("Canal tolls", "c", rank=2, description="The canal toll plan advances in the state legislature.")]
    run(world)
    assert len(posts(db)) == 1
    freeze_utcnow(NOW + timedelta(hours=1))
    run(world, NOW + timedelta(hours=1))
    assert len(posts(db)) == 1
    assert "under 4 hours" in db.query(TrendCandidate).filter_by(topic="b").one().reason
    freeze_utcnow(NOW + timedelta(hours=4))
    run(world, NOW + timedelta(hours=4))
    assert len(posts(db)) == 2
    freeze_utcnow(NOW + timedelta(hours=8))
    run(world, NOW + timedelta(hours=8))
    assert len(posts(db)) == 2  # the day's two
    assert "2 trend posts already today" in db.query(TrendCandidate).filter_by(topic="c").one().reason


def test_the_same_record_is_not_posted_twice_in_fourteen_days(world, freeze_utcnow):
    db = world["db"]
    world["trends"] = [trend("Example Tariff Relief Act", "a")]
    run(world)
    later = NOW + timedelta(days=3)
    freeze_utcnow(later)
    world["trends"] = [trend("Example Tariff Relief Act vote", "z", started=later)]
    run(world, later)
    assert len(posts(db)) == 1
    row = db.query(TrendCandidate).filter_by(topic="z").one()
    assert row.decision == "skipped" and "posted in the last 14 days" in row.reason


def test_a_record_an_issue_post_carried_is_skipped(world):
    db = world["db"]
    db.add(ActionIssue(date="2026-10-14", rank=2, title="Unrelated headline",
                       related_bill_ids=json.dumps([{"id": "S.100", "congress": 119}]),
                       bsky_posted_at=NOW - timedelta(hours=2)))
    db.commit()
    world["trends"] = [trend("Example Tariff Relief Act")]
    run(world)
    row = db.query(TrendCandidate).one()
    assert row.decision == "skipped" and "issue post already covers it" in row.reason


def test_outside_the_window_a_trend_is_held_then_dropped_once_it_leaves_the_list(world, freeze_utcnow):
    db = world["db"]
    night = datetime(2026, 10, 15, 8, 0)  # 04:00 Eastern
    with patch.object(broadcast, "_windows_cache", [(10, 1)]):
        freeze_utcnow(night)
        world["trends"] = [trend("Example Tariff Relief Act", "a"), trend("Canal tolls", "c", rank=1, description="The canal toll plan advances in the state legislature.")]
        run(world, night)
        assert {r.topic: r.decision for r in db.query(TrendCandidate)} == {"a": "held", "c": "held"}
        assert posts(db) == []
        morning = datetime(2026, 10, 15, 14, 0)  # 10:00 Eastern
        freeze_utcnow(morning)
        world["trends"] = [trend("Canal tolls", "c", rank=0, description="The canal toll plan advances in the state legislature.")]
        run(world, morning)
    rows = {r.topic: r for r in db.query(TrendCandidate)}
    assert rows["a"].decision == "dropped" and "no longer trending" in rows["a"].reason
    assert rows["c"].decision == "posted"


def test_an_outage_of_the_trends_endpoint_is_not_no_trends(world, monkeypatch):
    db = world["db"]
    db.add(TrendCandidate(topic="a", display_name="x", rank=0, relevance=1, tragedy=0, political_violence=0,
                          loaded=0, decision="held", first_seen_at=NOW, last_seen_at=NOW, decided_at=NOW))
    db.commit()
    monkeypatch.setattr(trend_poster, "fetch_bluesky_trends", lambda: None)
    assert run(world) == {"outage": 1}
    assert db.query(TrendCandidate).one().decision == "held"  # not dropped
    assert db.query(TrendObservation).count() == 0  # no empty hour recorded


def test_observations_are_kept_per_listing(world):
    world["trends"] = [trend("Example Tariff Relief Act", "a"), trend("Canal tolls", "c", rank=1, description="The canal toll plan advances in the state legislature.")]
    run(world)
    assert sorted((o.topic, o.rank) for o in world["db"].query(TrendObservation)) == [("a", 0), ("c", 1)]


# -- party-blind selection and the audit -------------------------------------------

def _swap_parties(db):
    swap = {"D": "R", "R": "D"}
    for s in db.query(Senator):
        s.party = swap.get(s.party, s.party)
    for p in db.query(RollCallPosition):
        p.party = swap.get(p.party, p.party)
    for i in db.query(ActionIssue):
        i.related_senators = json.dumps([{**m, "party": swap.get(m.get("party"), m.get("party"))}
                                         for m in json.loads(i.related_senators or "[]")])
    db.commit()


@pytest.mark.parametrize("relabel", [False, True])
def test_trend_selection_is_party_blind(world, relabel):
    db = world["db"]
    if relabel:
        _swap_parties(db)
    world["trends"] = [trend("Lee Marsh harbor push", "b", rank=0, description="Lee Marsh presses his harbor dredging bill."), trend("Example Tariff Relief Act", "a", rank=3),
                       trend("Canal tolls", "c", rank=1, description="The canal toll plan advances in the state legislature.")]
    run(world)
    chosen = db.query(TrendCandidate).filter_by(decision="posted").one()
    scores = {r.topic: r.selection_score for r in db.query(TrendCandidate)}
    # The same trend and scores with every party label swapped: the
    # selection reads rank and match strength only.
    assert chosen.topic == "b"
    assert scores == {"b": pytest.approx(1.0), "a": pytest.approx(22 / 25), "c": pytest.approx(24 / 25)}


def test_pick_ignores_everything_but_score_and_rank():
    a = TrendCandidate(topic="a", rank=2, decision="eligible", selection_score=0.5, named_parties='["D"]')
    b = TrendCandidate(topic="b", rank=1, decision="eligible", selection_score=0.5, named_parties='["R"]')
    assert trend_poster.pick([a, b]).topic == "b"
    a.named_parties, b.named_parties = b.named_parties, a.named_parties
    assert trend_poster.pick([a, b]).topic == "b"


def test_the_binomial_test_is_exact():
    assert trend_poster.binomial_two_sided(5, 10, 0.5) == pytest.approx(1.0)
    assert trend_poster.binomial_two_sided(10, 10, 0.5) == pytest.approx(2 / 1024)
    assert trend_poster.binomial_two_sided(0, 0, 0.5) == 1.0


def test_the_party_audit_alarms_on_a_divergence(world):
    db = world["db"]
    for i in range(30):
        posted = i < 12
        db.add(TrendCandidate(topic=f"t{i}", display_name="x", rank=0, relevance=1, tragedy=0,
                              political_violence=0, loaded=0, selection_score=0.5,
                              decision="posted" if posted else "eligible",
                              named_parties='["D"]' if posted else '["R"]',
                              first_seen_at=NOW, last_seen_at=NOW, decided_at=NOW))
    db.commit()
    audit = trend_poster.party_audit(db)
    assert audit["posted"]["D"] == 12 and audit["diverges"]
    trend_poster.check_party_balance(db)
    assert world["alerts"]


def test_party_lean_counts_named_members_and_sponsors():
    assert trend_poster.party_lean(["D", "R", "R"]) == "R"
    assert trend_poster.party_lean(["D", "R"]) is None
    assert trend_poster.party_lean([]) is None


# -- hashtags, the image, the trial ---------------------------------------------

def test_no_hashtags_reach_a_post(world, bluesky_configured):
    world["trends"] = [trend("#Example Tariff Relief Act")]
    run(world)
    (post,) = posts(world["db"])
    assert "#" not in post.text and "#" not in post.title


def test_the_trial_alternates_image_and_card_by_eastern_day_then_keeps_the_image(world):
    db = world["db"]
    even = datetime(2026, 10, 14, 18) if datetime(2026, 10, 14).toordinal() % 2 == 0 else datetime(2026, 10, 15, 18)
    assert trend_poster.embed_kind_for(db, even) == "image"
    assert trend_poster.embed_kind_for(db, even + timedelta(days=1)) == "card"
    db.add(BroadcastPost(kind="trend", subject="s", title="t", text="x", url="u", published_at=even))
    db.commit()
    assert trend_poster.embed_kind_for(db, even + timedelta(days=29)) == "image"
    assert trend_poster.embed_kind_for(db, even + timedelta(days=30)) == "image"


def test_the_image_goes_to_bluesky_with_its_alt_text(world, bluesky_configured, monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (1200).to_bytes(4, "big") + (630).to_bytes(4, "big") + b"\x00" * 50
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        return MagicMock(content=png, raise_for_status=lambda: None)

    def send(text, url, **kw):
        seen["image"] = kw.get("image")
        return True

    monkeypatch.setattr(broadcast.httpx, "get", fake_get)
    monkeypatch.setattr(broadcast, "publish_post", send)
    monkeypatch.setattr(trend_poster, "embed_kind_for", lambda db, now: "image")
    world["trends"] = [trend("Example Tariff Relief Act")]
    run(world)
    assert seen["url"] == "http://frontend:3000/api/og?vote=senate/119/2/301"
    image = seen["image"]
    assert (image.width, image.height) == (1200, 630)
    assert image.alt == posts(world["db"])[0].alt_text


def test_an_unreadable_image_falls_back_to_the_card_and_never_holds_the_post(world, bluesky_configured, monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectError("down")

    sent = {}
    monkeypatch.setattr(broadcast.httpx, "get", boom)
    monkeypatch.setattr(broadcast, "publish_post", lambda text, url, **kw: sent.setdefault("kw", kw) or True)
    monkeypatch.setattr(trend_poster, "embed_kind_for", lambda db, now: "image")
    world["trends"] = [trend("Example Tariff Relief Act")]
    run(world)
    assert "image" not in sent["kw"]
    assert posts(world["db"])[0].bsky_status == "sent"


def test_publish_post_sends_an_oversize_image_as_the_link_card():
    big = PostImage(data=b"x" * (BSKY_MAX_IMAGE_BYTES + 1), alt="alt", width=1200, height=630)
    with patch("app.pipeline.analyze.bluesky_utils.settings") as s, \
         patch("app.pipeline.analyze.bluesky_utils.build_link_card", return_value="CARD") as card, \
         patch("atproto.Client") as client_cls:
        s.BSKY_HANDLE, s.BSKY_APP_PASSWORD = "h", "p"
        client = MagicMock()
        client.send_post.return_value = MagicMock(uri="at://x/post/1")
        client_cls.return_value = client
        sent = publish_post("text", "https://civitas-research.org/x", success_msg="ok", error_context="t", image=big)
    assert sent.embed_kind == "card" and sent.uri == "at://x/post/1"
    card.assert_called_once()
    assert client.send_post.call_args.kwargs["embed"] == "CARD"


def test_publish_post_attaches_a_usable_image_with_its_size():
    img = PostImage(data=b"png", alt="Alt text", width=1200, height=630)
    with patch("app.pipeline.analyze.bluesky_utils.settings") as s, \
         patch("app.pipeline.analyze.bluesky_utils.build_link_card") as card, \
         patch("atproto.Client") as client_cls:
        s.BSKY_HANDLE, s.BSKY_APP_PASSWORD = "h", "p"
        client = MagicMock()
        client.upload_blob.return_value = MagicMock(blob=BlobRef(
            mime_type="image/png", size=3,
            ref={"$link": "bafkreibme22gw2h7y2h7tg2fhqotaqjucnbc24deqo72b6mkl2egezxhvy"}))
        client_cls.return_value = client
        sent = publish_post("text", "https://civitas-research.org/x", success_msg="ok", error_context="t", image=img)
    assert sent.embed_kind == "image"
    card.assert_not_called()
    embed = client.send_post.call_args.kwargs["embed"]
    assert embed.images[0].alt == "Alt text"
    assert (embed.images[0].aspect_ratio.width, embed.images[0].aspect_ratio.height) == (1200, 630)


def test_the_feed_entry_shows_the_posts_own_image():
    post = BroadcastPost(kind="trend", subject="s", title="t", text="words", url="https://civitas-research.org/x",
                         image_url="https://civitas-research.org/api/og?vote=senate/119/2/1", alt_text="Alt words",
                         card_image="https://civitas-research.org/og.png")
    html = _content_html(post)
    assert "api/og?vote=senate/119/2/1" in html and 'alt="Alt words"' in html and "og.png" not in html


def test_the_trend_feed_and_kind_exist():
    assert "trend" in broadcast.KINDS and broadcast.FEEDS["trends"].kinds == ("trend",)
    assert "trend" in broadcast.FEEDS["all"].kinds
    assert "trend" not in broadcast.SCHEDULED_KINDS  # held by trend_poster itself, re-checked before posting


# -- record cards: one data object for the image and its alt text -----------------

def test_a_vote_cards_alt_text_states_every_figure_the_image_shows(world):
    card = record_cards.card_for_key(world["db"], "vote:senate/119/2/301")
    alt = card.alt
    for text in (card.eyebrow, card.headline, *card.lines, card.source):
        assert text.rstrip(".") in alt
    for row in card.tally:
        assert f"{row['label']} {row['total']}" in alt
        for p in row["parties"]:
            if p["count"]:
                assert f"{p['name']} {p['count']}" in alt
    assert card.source == ("Source: U.S. Senate roll-call vote record · civitas-research.org/congress/votes/"
                           "senate/119/2/301 · data as of October 14, 2026")
    assert [r["total"] for r in card.tally] == [2, 1, 0]


@pytest.mark.parametrize("key", ["bill:119/S.100", "event:1"])
def test_every_card_has_a_source_line_and_a_nonempty_alt(world, key):
    card = record_cards.card_for_key(world["db"], key)
    assert re.fullmatch(r"Source: .+ · civitas-research\.org/[\w/.-]+ · data as of .+", card.source), card.source
    assert card.alt.strip() and card.source in card.alt


def test_card_keys_that_dont_resolve_are_none(world):
    for key in ("vote:moon/119/2/1", "bill:abc/S.1", "event:999", "nope:1", "vote:senate/119/2"):
        assert record_cards.card_for_key(world["db"], key) is None


def test_the_cards_api_serves_the_card(world):
    from app.api.cards import record_card

    resp = record_card("vote:senate/119/2/301", title=0, db=world["db"])
    body = json.loads(resp.body)
    assert body["headline"] == "On Passage of the Bill: S. 100" and body["alt"] and body["source"]


# -- the getTrends reader ---------------------------------------------------------

def test_get_trends_reads_rank_status_and_start(monkeypatch):
    payload = {"trends": [
        {"topic": "abc", "displayName": "First", "description": "d", "startedAt": "2026-10-14T12:00:00Z",
         "status": "hot", "category": "politics", "postCount": 7},
        {"topic": "", "displayName": "no topic"},
        {"topic": "def", "displayName": "Second"},
    ]}
    monkeypatch.setattr(trending.httpx, "get", lambda *a, **k: MagicMock(json=lambda: payload, raise_for_status=lambda: None))
    got = trending.fetch_bluesky_trends()
    assert [(t.topic, t.rank) for t in got] == [("abc", 0), ("def", 2)]
    assert got[0].started_at == datetime(2026, 10, 14, 12) and got[0].post_count == 7


def test_get_trends_failure_is_none_and_empty_is_empty(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(trending.httpx, "get", boom)
    assert trending.fetch_bluesky_trends() is None
    monkeypatch.setattr(trending.httpx, "get", lambda *a, **k: MagicMock(json=lambda: {"oops": 1}, raise_for_status=lambda: None))
    assert trending.fetch_bluesky_trends() is None
    monkeypatch.setattr(trending.httpx, "get", lambda *a, **k: MagicMock(json=lambda: {"trends": []}, raise_for_status=lambda: None))
    assert trending.fetch_bluesky_trends() == []


# -- calibration ----------------------------------------------------------------

def test_trend_gates_calibration_matches_examples():
    cal = trend_relevance.calibration()
    for gate in trend_relevance.GATES:
        assert cal[gate]["examples_hash"] == trend_relevance.gate_examples_hash(gate), (
            f"{gate}: rerun backend/scripts/calibrate_trend_gates.py")
        assert cal[gate]["examples"] == len(trend_relevance.gate_examples(gate))
    assert "relevance" not in cal
    assert cal["_source"]


def test_every_labelled_set_documents_its_source_and_rubric():
    for path in {g[0] for g in trend_relevance.GATES.values()}:
        data = json.loads(path.read_text())
        assert data["_source"] and data["_rubric"] and data["examples"]


def test_act_phrases_take_the_whole_run_never_a_tail():
    assert trend_evidence.act_phrases("Abolish DHS. Illinois Democrat Jane Roe's Safe Streets Act") == [
        "illinois democrat jane roe's safe streets act"]
    assert trend_evidence.act_phrases("The Act") == []


def test_a_shorter_title_inside_a_longer_one_is_another_act(world):
    world["trends"] = [trend("Relief Act", description="The relief bill.")]  # inside "Example Tariff Relief Act"
    run(world)
    assert world["db"].query(TrendCandidate).one().decision == "ineligible"


def test_a_qualifier_in_front_of_a_title_is_another_jurisdictions_act(world):
    world["trends"] = [trend("EU Example Tariff Relief Act", description="The EU adopted its own law.")]
    run(world)
    assert world["db"].query(TrendCandidate).one().decision == "ineligible"


def test_a_short_title_matches_with_the_bill_number(world):
    world["trends"] = [trend("Relief Act S. 100", description="Senators debate S. 100.")]
    run(world)
    assert world["db"].query(TrendCandidate).one().record_key == "vote:senate/119/2/301"


def _nomination(db, date="2026-10-10"):
    db.add(RollCall(chamber="senate", congress=119, session=2, number=302, date=date,
                    question="On the Nomination PN9", title="Jordan Vale, of Ohio, to be Secretary of Canals",
                    result="Nomination Confirmed", yeas=51, nays=48, fetched_at=NOW))
    db.commit()


def test_a_nominee_named_in_a_story_is_not_an_anchor(world):
    _nomination(world["db"])
    world["trends"] = [trend("Jordan Vale letter", description="Jordan Vale sent a letter about fees.")]
    run(world)
    assert world["db"].query(TrendCandidate).one().decision == "ineligible"


def test_a_trend_about_the_nomination_anchors_on_the_vote(world):
    _nomination(world["db"])
    name = "Jordan Vale confirmation"
    world["trends"] = [trend(name, description="The Senate confirmed Jordan Vale.")]
    world["scores"][name] = TrendScores(0.9, 0, 0, 0, appointment=0.95)
    run(world)
    row = world["db"].query(TrendCandidate).one()
    assert (row.match_type, row.record_key) == ("nominee", "vote:senate/119/2/302")


def test_an_old_confirmation_is_not_a_record_of_new_news(world):
    _nomination(world["db"], date="2026-03-01")
    name = "Jordan Vale confirmation"
    world["trends"] = [trend(name, description="The Senate confirmed Jordan Vale.")]
    world["scores"][name] = TrendScores(0.9, 0, 0, 0, appointment=0.95)
    run(world)
    assert world["db"].query(TrendCandidate).one().decision == "ineligible"


def test_an_old_act_record_counts_only_when_the_trend_name_names_it(world):
    later = NOW + timedelta(days=60)
    world["trends"] = [trend("IRS data delay", description="IRS holds back data on the Example Tariff Relief Act.",
                             started=later)]
    run(world, later)
    assert world["db"].query(TrendCandidate).one().decision == "ineligible"
    world["trends"] = [trend("Example Tariff Relief Act", "t2", started=later)]
    run(world, later)
    assert world["db"].query(TrendCandidate).filter_by(topic="t2").one().record_kind == "vote"


def test_a_vote_with_no_title_and_no_bill_is_never_a_record(world):
    db = world["db"]
    db.add(RollCall(chamber="house", congress=119, session=2, number=9, date="2026-10-12", question="On Passage",
                    title="", result="Passed", yeas=1, nays=0, fetched_at=NOW))
    db.commit()
    assert record_cards.card_for_key(db, "vote:house/119/2/9") is None
    index = trend_evidence.RecordIndex(db, NOW)
    assert all(rc.number != 9 for rc in index.votes)


def test_an_issue_match_needs_a_description_and_the_trend_names_subject(world):
    db = world["db"]
    # Enough stored issues for a document frequency to mean something.
    db.add_all([ActionIssue(date="2026-09-01", rank=1, title=f"Unrelated story {i}") for i in range(120)])
    db.commit()
    world["trends"] = [trend("Canal tolls", "a", description=""),
                       trend("Fee politics", "b", description="Canal toll plan debated.")]
    run(world)
    rows = {r.topic: r for r in world["db"].query(TrendCandidate)}
    assert rows["a"].decision == "ineligible"  # no description
    assert rows["b"].decision == "ineligible"  # "canal" only in the description
