"""What an Action Center issue offers a reader beyond its named members: the
committee that handles it and the rules about it still open for comment
(action_center._build_actions_from_data, _open_comment_docs), and the
reader counts that rank the Today tab's comment periods
(_refresh_open_comment_views)."""

from unittest.mock import patch

import numpy as np

from app.models import ApiCache, ExploreDocument
from app.pipeline.analyze import action_center as ac

FINANCE = {"code": "SSFI", "name": "Senate Committee on Finance", "chamber": "senate",
           "url": "https://www.finance.senate.gov/"}


def _doc(id_, title, doc_type="Proposed Rule", close="2026-10-20", comment_url="https://www.regulations.gov/x",
         ext=None):
    return ExploreDocument(
        id=id_, doc_type=doc_type, source="Federal Register", title=title, summary="", body="b",
        date="2026-09-20", chamber="Regulatory", comment_url=comment_url, comments_close_on=close,
        external_id=ext or f"fr-reg-2026-{id_}",
    )


class TestBuildActions:
    def test_comment_period_and_committee_are_template_text_around_stored_data(self):
        actions = ac._build_actions_from_data(
            "A story", [], [], [], [], policy_areas=["HEALTH"],
            committees=[FINANCE],
            comment_docs=[{"id": 7, "title": "A rule", "comments_close_on": "2026-10-20"}],
        )
        assert actions == [
            {"text": "Comment by October 20", "type": "comment_period", "url": "/explore/7#comment",
             "docId": 7, "closesOn": "2026-10-20"},
            {"text": "Contact the Senate Committee on Finance", "type": "contact_committee",
             "url": "https://www.finance.senate.gov/",
             "committee": {"code": "SSFI", "name": "Senate Committee on Finance", "chamber": "senate"}},
        ]

    def test_generic_contact_only_when_nothing_specific_exists(self):
        def generic(**kw):
            return any(a["type"] == "contact_senator"
                       for a in ac._build_actions_from_data("t", [], [], [], [], policy_areas=["HEALTH"], **kw))

        assert generic()
        assert not generic(committees=[FINANCE])


class TestOpenCommentDocs:
    def _embed(self, mapping):
        return lambda texts: np.array([mapping[t] for t in texts])

    def test_open_related_documents_then_similar_open_rules(self, db_session):
        db_session.add_all([
            _doc(1, "Related open notice", doc_type="Notice"),
            _doc(2, "Drug pricing rule"),
            _doc(3, "Drug pricing notice", doc_type="Notice"),
            _doc(4, "Closed drug pricing rule", close="2026-09-01"),
            _doc(5, "Unrelated rule"),
        ])
        db_session.commit()
        vectors = {"Drug pricing": [1.0, 0.0], "Drug pricing rule": [0.9, np.sqrt(1 - 0.81)],
                   "Unrelated rule": [0.0, 1.0], "Closed drug pricing rule": [1.0, 0.0]}
        ac._open_rule_title_embs.clear()
        with patch.object(ac, "comment_period_today", return_value="2026-10-11"), \
                patch.object(ac, "_embed_texts_sim", self._embed(vectors)):
            got = ac._open_comment_docs("Drug pricing", [{"id": 1}], db_session)
        # The related notice is kept (the issue's own link); a notice found
        # only by title is not; a closed rule never is.
        assert [d["id"] for d in got] == [1, 2]
        assert got[1] == {"id": 2, "title": "Drug pricing rule", "comments_close_on": "2026-10-20"}

    def test_a_rule_below_the_title_bar_is_not_offered(self, db_session):
        db_session.add(_doc(2, "Fisheries quota rule"))
        db_session.commit()
        vectors = {"Drug pricing": [1.0, 0.0], "Fisheries quota rule": [0.3, np.sqrt(1 - 0.09)]}
        ac._open_rule_title_embs.clear()
        with patch.object(ac, "comment_period_today", return_value="2026-10-11"), \
                patch.object(ac, "_embed_texts_sim", self._embed(vectors)):
            assert ac._open_comment_docs("Drug pricing", [], db_session) == []


class TestRefreshOpenCommentViews:
    def test_stores_reader_counts_once_a_day(self, db_session):
        db_session.add_all([_doc(1, "Open rule", ext="fr-reg-2026-001"), _doc(2, "Closed", close="2026-09-01",
                                                                             ext="fr-reg-2026-002")])
        db_session.commit()
        asked = []

        async def fetch(client, numbers, fields):
            asked.append((sorted(numbers), fields))
            return {"2026-001": {"document_number": "2026-001", "page_views": {"count": 321}}}, set()

        with patch.object(ac, "comment_period_today", return_value="2026-10-11"), \
                patch.object(ac, "fetch_fr_metadata", fetch):
            assert ac._refresh_open_comment_views(db_session) == 1
            assert ac._refresh_open_comment_views(db_session) == 0  # within the day
        assert asked == [(["2026-001"], ["document_number", "page_views"])]
        assert db_session.get(ExploreDocument, 1).fr_page_views == 321

    def test_a_failed_request_changes_nothing_and_retries_next_hour(self, db_session):
        db_session.add(_doc(1, "Open rule", ext="fr-reg-2026-001"))
        db_session.commit()

        async def fetch(client, numbers, fields):
            return None

        with patch.object(ac, "comment_period_today", return_value="2026-10-11"), \
                patch.object(ac, "fetch_fr_metadata", fetch):
            assert ac._refresh_open_comment_views(db_session) == 0
        assert db_session.get(ExploreDocument, 1).fr_page_views is None
        assert db_session.query(ApiCache).filter(ApiCache.cache_key == ac._FR_VIEWS_CACHE_KEY).count() == 0
