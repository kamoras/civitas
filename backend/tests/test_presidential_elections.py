"""Tests for presidential_elections' margin-scale consistency (2026-07,
#218 review S3).

Three inputs used to land on one z-scored axis at three different scales:
genuine popular margins (single digits), raw electoral-college margins
(tens of points — the "nd"-popular fallback), and an ad-hoc `pct - 55`
heuristic for pre-1824 electoral shares. Both non-popular inputs are
rescaled onto the popular-margin scale via least-squares fits on the
mandates-table elections where both figures exist — re-estimated on every
fetch since v6.13 (_fit_scales) rather than hand-typed from a one-off fit.
"""

from app.pipeline.fetch.presidential_elections import (
    _MIN_FIT_ROWS,
    _fit_scales,
    _mandates_rows,
    _parse_mandates_table,
)


def _row(name, year, popular_margin, electoral_pct, electoral_margin):
    pm = "nd" if popular_margin is None else f"{popular_margin}"
    return (
        f"<tr><td>{name}</td><td>{year}</td><td>50.0%</td><td>{pm}</td>"
        f"<td>{electoral_pct}%</td><td>{electoral_margin}</td><td>0</td></tr>"
    )


def _table(rows):
    return "<html><body><table><tbody>" + "".join(rows) + "</tbody></table></body></html>"


def _paired_rows(n):
    # popular_margin = 0.2 x electoral_margin and = 0.4 x share - 20 exactly,
    # so both fits are recoverable. Listed under a continuation name cell
    # (blank) after Lincoln, which is how the real table lists re-elections.
    rows = [_row("Abraham Lincoln", 1860, 10.0, 75.0, 50.0)]
    for i in range(1, n):
        share = 55.0 + i
        rows.append(_row("", 1860 + 4 * i, round(0.4 * share - 20, 4), share, round(2 * share - 100, 4)))
    return rows


class TestMarginScaleConsistency:
    def test_fits_are_estimated_from_the_table(self):
        fits = _fit_scales(_mandates_rows(_table(_paired_rows(25))))
        assert abs(fits["margin_slope"] - 0.2) < 1e-9
        assert abs(fits["share_slope"] - 0.4) < 1e-9
        assert abs(fits["share_intercept"] + 20) < 1e-9
        assert fits["n"] == 25

    def test_popular_margin_used_directly_when_present(self):
        margins, _ = _parse_mandates_table(_table(_paired_rows(25)))
        assert margins["lincoln-16"][0] == 10.0

    def test_nd_popular_falls_back_to_rescaled_electoral_margin(self):
        # J.Q. Adams 1824: popular is "nd", electoral margin -6.1 — rescaled
        # by the fitted slope, not the raw value on a ~5x larger scale.
        rows = _paired_rows(25) + [_row("John Quincy Adams", 1824, None, 32.2, -6.1)]
        margins, fits = _parse_mandates_table(_table(rows))
        assert margins["jqadams-6"] == [-6.1 * fits["margin_slope"]]

    def test_too_few_paired_rows_skips_rescaling_instead_of_guessing(self):
        rows = _paired_rows(_MIN_FIT_ROWS - 1) + [_row("John Quincy Adams", 1824, None, 32.2, -6.1)]
        margins, fits = _parse_mandates_table(_table(rows))
        assert fits is None
        assert "jqadams-6" not in margins
        assert margins["lincoln-16"][0] == 10.0  # popular margins still used
