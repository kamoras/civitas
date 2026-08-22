"""Tests for scripts/retire_duplicate_current_issues.py — the one-time
cleanup for ActionIssue rows created before dedupe_near_identical_issues
existed (see action_center.py). The clustering logic itself is unit-tested
in test_action_center.py; this file covers the script's own read-filter-
retire-commit wiring end to end, against the real embedding model."""

import json
from datetime import datetime

from app.models import ActionIssue


def _issue(id, title, facts, created_at):
    row = ActionIssue(date="2026-08-21", rank=1, title=title, facts=json.dumps(facts), is_current=True)
    row.id = id
    row.created_at = created_at
    return row


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
