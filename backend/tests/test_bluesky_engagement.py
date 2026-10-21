"""The outlet-repost gate's threshold is fitted on labels the posts' own
links supply (scripts/calibrate_engagement.py, refitted daily on the posts
the gate records), never typed, and runs at the unmeasured value until a
fit meets the precision rule."""

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx

from app.models import ActionIssue, OutletPostLink
from app.pipeline.analyze import bluesky_engagement as be
from app.precision_rule import LOWEST_PASSING, PRECISION_LOWER_BOUND

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "calibrate_engagement.py"
_spec = importlib.util.spec_from_file_location("calibrate_engagement", _SCRIPT)
calibrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(calibrate)


def test_the_bundled_calibration_says_where_it_came_from():
    data = json.loads(be.CALIBRATION_PATH.read_text())
    assert "calibrate_engagement.py" in data["_source"]
    assert data["labels_sha256"] == be.labels_sha256(data["labels"])
    assert data["labelled_posts"] == len(data["labels"])
    # The file names the precision-rule variant it was fitted under, the one the gate's refit uses.
    assert data["precision_rule"] == be.PRECISION_VARIANT == LOWEST_PASSING
    t = data["threshold"]
    assert t is None or data["at_threshold"]["precision_exact_lower"] >= PRECISION_LOWER_BOUND
    # What the gate runs at, and whether anyone measured it, is stated beside the fit.
    assert data["in_use"]["measured"] is (t is not None)
    assert data["in_use"]["threshold"] == (t if t is not None else be.UNMEASURED_THRESHOLD)
    # Each label carries what the refit needs: the matched issue, the live
    # issues citing the article, and the gate's score.
    for uri, article, matched, citing, sim in data["labels"]:
        assert uri and article and citing and 0 < sim <= 1


def test_without_a_fit_the_gate_runs_at_the_unmeasured_value():
    assert be.engage_threshold({"threshold": None}) == be.UNMEASURED_THRESHOLD
    assert be.engage_threshold({"threshold": 0.85}) == 0.85


def test_an_article_is_keyed_without_scheme_query_or_trailing_slash():
    assert calibrate.article_key("https://www.npr.org/2026/10/01/x/?utm=1") == "npr.org/2026/10/01/x"
    assert calibrate.article_key(None) is None


def test_a_post_is_labelled_only_when_a_live_issue_cites_its_article():
    assert be.link_label(1, [1, 2]) is True
    assert be.link_label(1, [2]) is False  # another story's coverage
    assert be.link_label(1, []) is None  # nothing says what it is about


def test_a_rate_limit_or_server_error_leaves_a_link_unresolved():
    assert be.resolved(200) and be.resolved(403)  # an article page refusing a bot is still the article
    assert not be.resolved(429) and not be.resolved(503)


def test_the_post_link_is_read_from_the_api_and_from_atproto_models():
    assert be.post_link({"embed": {"external": {"uri": "https://n.pr/x"}}}) == "https://n.pr/x"
    model = SimpleNamespace(embed=SimpleNamespace(external=SimpleNamespace(uri="https://apne.ws/y")))
    assert be.post_link(model) == "https://apne.ws/y"
    assert be.post_link({"embed": None}) is None


def _issue(db, iid, urls):
    db.add(ActionIssue(id=iid, date="2026-10-20", rank=iid, title=f"issue {iid}", summary="", source_urls=json.dumps(urls)))


def _record(db, uri, link, sim, matched, live):
    be.record_post(db, uri=uri, outlet="npr.org", posted_at=datetime(2026, 10, 20), link=link, sim=sim,
                   matched=matched, live=live)


def test_recorded_links_are_resolved_and_retried(db_session):
    _record(db_session, "at://a", "https://n.pr/a", 0.9, 1, [1])
    _record(db_session, "at://b", "https://n.pr/b", 0.9, 1, [1])
    db_session.commit()

    def answer(request):
        if request.url.path == "/b":
            return httpx.Response(429)
        return httpx.Response(200)
    client = httpx.Client(transport=httpx.MockTransport(answer))
    assert be.resolve_pending(db_session, client) == 1
    a, b = db_session.get(OutletPostLink, "at://a"), db_session.get(OutletPostLink, "at://b")
    assert a.article == "n.pr/a" and a.resolve_tries == 1
    assert b.article is None and b.resolve_tries == 1  # tried again next run
    for _ in range(be.RESOLVE_TRIES):
        be.resolve_pending(db_session, client)
    assert db_session.get(OutletPostLink, "at://b").resolve_tries == be.RESOLVE_TRIES


def test_the_refit_pools_the_bundled_labels_with_recorded_posts(db_session, monkeypatch):
    monkeypatch.setattr(be, "bundled_fit", lambda: {"labels": [["at://old", "x.org/o", 5, [5], 0.95]]})
    _issue(db_session, 1, ["https://www.npr.org/a"])
    _issue(db_session, 2, ["https://www.npr.org/b"])
    rows = [("at://right", "npr.org/a", 0.9, 1), ("at://wrong", "npr.org/b", 0.8, 1),
            ("at://none", "npr.org/c", 0.99, 1)]
    for uri, article, sim, matched in rows:
        _record(db_session, uri, "https://n.pr/" + uri, sim, matched, [1, 2])
        db_session.flush()
        db_session.get(OutletPostLink, uri).article = article
    db_session.commit()
    fit = be.refit(db_session)
    assert fit["labelled_posts"] == 3 and fit["recorded_labelled"] == 2
    assert fit["precision_rule"] == be.PRECISION_VARIANT
    # Three labelled posts can't give a bound of 0.80: the gate stays unmeasured.
    assert fit["threshold"] is None
    assert be.engage_threshold(fit) == be.UNMEASURED_THRESHOLD


def test_the_refit_is_made_once_a_day_and_a_failure_keeps_the_fit_in_use(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(be, "refit", lambda db: calls.append(1) or {"date": be.utcnow().date().isoformat(),
                                                                     "threshold": 0.86})
    assert be.current_fit(db_session)["threshold"] == 0.86
    assert be.current_fit(db_session)["threshold"] == 0.86
    assert calls == [1]

    def broken(db):
        raise RuntimeError("no")
    monkeypatch.setattr(be, "refit", broken)
    monkeypatch.setattr(be, "utcnow", lambda: datetime(2099, 1, 1))
    assert be.current_fit(db_session)["threshold"] == 0.86  # the stored refit stands
