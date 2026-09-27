"""Period summaries across New Year: December is complete only once
January has begun, and the week spanning it has days in both years."""

import json

from app.models import MonthSummary, TimelineEntry, WeekSummary
from app.pipeline.analyze import action_center as ac


def _entries(db, dates):
    for d in dates:
        db.add(TimelineEntry(date=d, title=f"Story {d}", summary="", policy_areas=json.dumps(["Economy"])))
    db.commit()


def test_december_is_summarized_in_january(db_session, monkeypatch):
    labels = []

    def fake(label, entries, cache_key, db):
        labels.append((label, len(entries)))
        return {"summary": "", "topAreas": []}

    monkeypatch.setattr(ac, "_generate_period_summary", fake)
    _entries(db_session, ["2025-12-10", "2025-12-29", "2025-12-31", "2026-01-02", "2026-01-03"])
    ac.generate_period_summaries("2026-01-06", db_session)

    december = db_session.query(MonthSummary).filter_by(year=2025, month=12).one()
    assert december.entry_count == 3
    assert ("December 2025", 3) in labels
    # January is still under way.
    assert db_session.query(MonthSummary).filter_by(year=2026, month=1).count() == 0

    # 2025-12-29 .. 2026-01-04 is ISO week 1 of 2026, written from all its days.
    week = db_session.query(WeekSummary).filter_by(year=2026, week_num=1).one()
    assert (week.start_date, week.end_date, week.entry_count) == ("2025-12-29", "2026-01-04", 4)

    # A second pass writes nothing new.
    labels.clear()
    ac.generate_period_summaries("2026-01-06", db_session)
    assert labels == []
