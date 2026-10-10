"""The week label the Action Center's weekly summary is written from."""
from datetime import date

from app.pipeline.analyze.action_center import week_span_label


def test_a_week_inside_one_month():
    assert week_span_label(date(2026, 9, 21), date(2026, 9, 27)) == "September 21–27, 2026"


def test_a_week_across_a_month_names_both_months():
    assert week_span_label(date(2026, 9, 28), date(2026, 10, 4)) == "September 28–October 4, 2026"


def test_a_week_across_a_year_names_both_years():
    assert week_span_label(date(2026, 12, 28), date(2027, 1, 3)) == "December 28, 2026–January 3, 2027"
