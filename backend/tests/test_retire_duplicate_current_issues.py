"""Tests for scripts/retire_duplicate_current_issues.py's clustering logic
— the one-time cleanup for ActionIssue rows created before
_NEAR_IDENTICAL_TITLE_THRESHOLD existed (see action_center.py)."""

import json
from datetime import datetime

from app.models import ActionIssue
from scripts.retire_duplicate_current_issues import _is_duplicate


def _issue(id, title, facts, created_at):
    row = ActionIssue(date="2026-08-21", rank=1, title=title, facts=json.dumps(facts), is_current=True)
    row.id = id
    row.created_at = created_at
    return row


class TestIsDuplicate:
    def test_near_identical_title_is_a_duplicate(self):
        a = _issue(603, "Trump defends beef import plan amid GOP criticism",
                   ["GOP lawmakers voiced alarm over the policy's effect on U.S. beef producers."],
                   datetime(2026, 8, 21, 23, 17))
        b = _issue(604, "Trump defends beef import plan amid GOP criticism",
                   ["GOP lawmakers voiced alarm over the policy's effect on U.S. cattle producers."],
                   datetime(2026, 8, 22, 0, 17))
        assert _is_duplicate(a, b, sim=0.988) is True

    def test_distinct_development_in_the_same_saga_is_not_a_duplicate(self):
        a = _issue(535, "Senate passes funding bill to avert October shutdown",
                   ["Senator Grassley led the floor vote ahead of the October deadline."],
                   datetime(2026, 8, 18))
        b = _issue(495, "Senate approves funding bill to avoid shutdown",
                   ["Senator Collins negotiated the final procedural agreement with House leaders."],
                   datetime(2026, 8, 19))
        assert _is_duplicate(a, b, sim=0.795) is False


class TestClustersAndKeepsFreshest:
    def test_a_three_way_near_identical_cluster_collapses_to_the_newest(self, db_session):
        from scripts import retire_duplicate_current_issues as script

        db_session.add(_issue(603, "Trump defends beef import plan amid GOP criticism",
                               ["Trump said the decision was driven by public pressure to lower beef prices.",
                                "GOP lawmakers voiced alarm over the policy's effect on U.S. beef producers."],
                               datetime(2026, 8, 21, 23, 17)))
        db_session.add(_issue(604, "Trump defends beef import plan amid GOP criticism",
                               ["Trump said the decision was driven by public pressure to lower beef prices.",
                                "GOP lawmakers voiced alarm over the policy's effect on U.S. cattle producers."],
                               datetime(2026, 8, 22, 0, 17)))
        db_session.add(_issue(605, "Trump defends beef import plan after GOP criticism",
                               ["Trump stated the suspension of higher tariffs would lower ground beef prices."],
                               datetime(2026, 8, 22, 2, 18)))
        db_session.add(_issue(999, "Senate confirms new EPA administrator",
                               ["The Senate voted 54-46 to confirm the nominee."],
                               datetime(2026, 8, 21, 10, 0)))
        db_session.commit()

        script.SessionLocal = lambda: db_session
        db_session.close = lambda: None
        script.main()

        rows = {r.id: r.is_current for r in db_session.query(ActionIssue).all()}
        assert rows == {603: False, 604: False, 605: True, 999: True}
