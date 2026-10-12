"""Principal actor of an Action Center story and the admin's weekly
concentration (story_actor, action_center._principal_actor,
label_principal_actors, admin_actor_concentration)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import numpy as np
import pytest

from app.api.admin import admin_actor_concentration
from app.models import ActionIssue, President, Senator
from app.pipeline.analyze import action_center as ac
from app.pipeline.analyze import story_actor as sa

PRESIDENT = {"id": "p1", "name": "Pat Example", "branch": "president", "match_reason": "named in coverage"}
SENATOR = {"id": "S001", "name": "Dana Sample", "branch": "senate", "match_reason": "named in coverage"}


class TestHeadlineOfficial:
    def test_earliest_named_official_in_the_headline_leads(self):
        title = "Sample presses Example on tariffs"
        assert sa.headline_official(title, [PRESIDENT, SENATOR]) == "senate:S001"
        assert sa.headline_official("Example signs order Sample opposed", [PRESIDENT, SENATOR]) == sa.PRESIDENT

    def test_the_presidents_institutional_forms_count_as_him(self):
        assert sa.headline_official("The White House bars three outlets", [PRESIDENT]) == sa.PRESIDENT

    def test_a_name_inside_another_word_is_not_a_mention(self):
        assert sa.headline_official("Examples abound of agency delays", [PRESIDENT]) is None

    def test_an_official_named_only_below_the_headline_does_not_lead(self):
        # The headline is what a reader sees; the lede's officials don't lead.
        assert sa.headline_official("Senate blocks war powers resolution", [PRESIDENT]) is None


class TestStoryClass:
    reference = np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0], [0.1, 0.9]])
    reference = reference / np.linalg.norm(reference, axis=1, keepdims=True)
    categories = ["business", "business", "us_courts", "us_courts"]

    def test_weighted_vote_of_the_nearest_examples(self):
        assert sa.story_class(np.array([0.0, 1.0]), self.reference, self.categories, k=3) == "class:us_courts"
        assert sa.story_class(np.array([1.0, 0.0]), self.reference, self.categories, k=3) == "class:business"

    def test_principal_actor_falls_back_to_the_class(self):
        with patch.object(ac, "_embed_texts_sim", return_value=np.array([[1.0, 0.0]])), \
                patch.object(ac, "_relevance_reference", return_value=self.reference), \
                patch.object(ac, "relevance_examples", return_value=[{"category": c} for c in self.categories]):
            label = ac._principal_actor("Chipmaker posts record earnings", "", [PRESIDENT])
            assert ac._principal_actor("Example signs order", "", [PRESIDENT]) == sa.PRESIDENT
        assert label == "class:business"
        assert not sa.has_government_actor(label)
        assert sa.has_government_actor("class:us_courts") and sa.has_government_actor(sa.PRESIDENT)


class TestHerfindahl:
    def test_bounds(self):
        assert sa.herfindahl([]) is None
        assert sa.herfindahl(["a", "a", "a"]) == 1.0
        assert sa.herfindahl(["a", "b", "c", "d"]) == pytest.approx(0.25)
        assert sa.herfindahl(["a", "a", "b", "c"]) == pytest.approx(0.375)


class TestLabelEarlierIssues:
    def test_unlabelled_news_issues_get_an_actor_and_drafts_are_left(self, db_session):
        db_session.add(President(id="p1", name="Pat Example", party="R", number=99, term_start="2025-01-20", is_current=True))
        db_session.add_all([
            ActionIssue(date="2026-10-01", rank=1, title="Example signs order", summary="", facts="[]"),
            ActionIssue(date="2026-10-01", rank=2, title="Chipmaker posts record earnings", summary="", facts="[]"),
            ActionIssue(date="2026-10-01", rank=3, title="House vote on H.R. 1", summary="",
                        source_type="house_roll_call_vote"),
        ])
        db_session.commit()
        reference = np.array([[1.0, 0.0]])
        with patch.object(ac, "_embed_texts_sim", return_value=np.array([[1.0, 0.0], [1.0, 0.0]])), \
                patch.object(ac, "_relevance_reference", return_value=reference), \
                patch.object(ac, "relevance_examples", return_value=[{"category": "business"}]):
            assert ac.label_principal_actors(db_session) == 2
            assert ac.label_principal_actors(db_session) == 0  # nothing left to label
        got = {i.title: i.principal_actor for i in db_session.query(ActionIssue).all()}
        assert got == {
            "Example signs order": sa.PRESIDENT,
            "Chipmaker posts record earnings": "class:business",
            "House vote on H.R. 1": None,
        }


class TestAdminConcentration:
    def test_weekly_hhi_top_actor_and_counts(self, db_session):
        today = datetime.now(UTC).date()
        monday = today - timedelta(days=today.weekday())
        db_session.add(President(id="p1", name="Pat Example", party="R", number=99, term_start="2025-01-20", is_current=True))
        db_session.add(Senator(id="S001", name="Dana Sample", state="ZZ", party="D"))
        for n, actor in enumerate([sa.PRESIDENT, sa.PRESIDENT, "senate:S001", "class:business"]):
            db_session.add(ActionIssue(date=monday.isoformat(), rank=n + 1, title=f"t{n}", principal_actor=actor))
        # Left out: a primary-source draft, an unlabelled row, a week outside the window.
        db_session.add(ActionIssue(date=monday.isoformat(), rank=9, title="d", principal_actor=sa.PRESIDENT,
                                   source_type="senate_roll_call_vote"))
        db_session.add(ActionIssue(date=monday.isoformat(), rank=10, title="u"))
        db_session.add(ActionIssue(date=(monday - timedelta(weeks=5)).isoformat(), rank=1, title="old",
                                   principal_actor=sa.PRESIDENT))
        db_session.commit()

        result = admin_actor_concentration(weeks=2, db=db_session)

        last_week, this_week = result["weeks"]
        assert last_week == {"weekStart": (monday - timedelta(weeks=1)).isoformat(), "issues": 0, "hhi": None,
                             "top": None, "president": 0, "noGovernment": 0}
        assert this_week["issues"] == 4
        assert this_week["hhi"] == pytest.approx(0.375)
        assert this_week["top"] == {"label": sa.PRESIDENT, "name": "President Pat Example", "issues": 2}
        assert (this_week["president"], this_week["noGovernment"]) == (2, 1)
