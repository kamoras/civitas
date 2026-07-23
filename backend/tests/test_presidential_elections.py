"""Tests for presidential_elections' margin-scale consistency (2026-07,
#218 review S3).

Three inputs used to land on one z-scored axis at three different scales:
genuine popular margins (single digits), raw electoral-college margins
(tens of points — the "nd"-popular fallback), and an ad-hoc `pct - 55`
heuristic for pre-1824 electoral shares. Both non-popular inputs are now
rescaled onto the popular-margin scale via relationships fit on the 50
mandates-table elections where both figures exist (see the module's
calibration constants).
"""

from app.pipeline.fetch.presidential_elections import (
    _ELECTORAL_SHARE_TO_POPULAR_INTERCEPT,
    _ELECTORAL_SHARE_TO_POPULAR_SLOPE,
    _ELECTORAL_TO_POPULAR_MARGIN_SLOPE,
    _parse_mandates_table,
)

_TABLE_HTML = """
<html><body><table><tbody>
<tr><td>Abraham Lincoln</td><td>1860</td><td>39.7%</td><td>10.1</td><td>59.4%</td><td>18.9</td><td>19.7</td></tr>
<tr><td>Andrew Jackson</td><td>1828</td><td>56.0%</td><td>12.4</td><td>68.2%</td><td>36.4</td><td>12.2</td></tr>
<tr><td>John Quincy Adams</td><td>1824</td><td>nd</td><td>nd</td><td>32.2%</td><td>-6.1</td><td>nd</td></tr>
</tbody></table></body></html>
"""


class TestMarginScaleConsistency:
    def test_popular_margin_used_directly_when_present(self):
        result = _parse_mandates_table(_TABLE_HTML)
        assert result["lincoln-16"] == [10.1]
        assert result["jackson-7"] == [12.4]

    def test_nd_popular_falls_back_to_rescaled_electoral_margin(self):
        # J.Q. Adams 1824: popular is "nd", electoral margin -6.1 — the
        # fallback must be RESCALED (x0.211), not the raw electoral value
        # that sits on a ~5x larger scale.
        result = _parse_mandates_table(_TABLE_HTML)
        assert result["jqadams-6"] == [-6.1 * _ELECTORAL_TO_POPULAR_MARGIN_SLOPE]

    def test_share_rescale_maps_landslides_into_popular_range(self):
        # Monroe's 1820 98.3% electoral share: the old `pct - 55` heuristic
        # produced +43.3 — double any popular margin ever recorded. The
        # fitted line keeps it a strong-but-on-scale +19.9.
        rescaled = (
            _ELECTORAL_SHARE_TO_POPULAR_SLOPE * 98.3
            + _ELECTORAL_SHARE_TO_POPULAR_INTERCEPT
        )
        assert 15.0 < rescaled < 27.0  # inside the observed popular range (-3..26.2)

    def test_share_rescale_near_even_election_maps_near_zero(self):
        rescaled = (
            _ELECTORAL_SHARE_TO_POPULAR_SLOPE * 50.0
            + _ELECTORAL_SHARE_TO_POPULAR_INTERCEPT
        )
        assert -3.0 < rescaled < 4.0
