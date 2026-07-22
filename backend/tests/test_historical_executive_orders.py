"""Regression test for historical_executive_orders.py's EO-table parser.

Covers party-tag extraction (used by presidential_roster.py for identity
data) across the party-abbreviation variants UCSB's table actually uses:
F(ederalist), D-R (Democratic-Republican), D, R.

Fixture rows are copied verbatim (structure) from a live fetch of
https://www.presidency.ucsb.edu/statistics/data/executive-orders, 2026-07.
"""

from app.pipeline.fetch.historical_executive_orders import _parse_eo_table

_FIXTURE_HTML = """
<html><body><table><tbody>
<tr>
<td><strong>George Washington (F)</strong></td>
<td><strong>Total</strong></td>
<td style="text-align: center;"><strong>8</strong></td>
<td style="text-align: center;"><strong>1</strong></td>
<td style="text-align: center;"><strong>7.85</strong></td>
<td><em>unnumbered</em></td>
</tr>
<tr>
<td><strong>Thomas Jefferson (D-R)</strong></td>
<td><strong>Total</strong></td>
<td style="text-align: center;"><strong>4</strong></td>
<td style="text-align: center;"><strong>0.50</strong></td>
<td style="text-align: center;"><strong>8.00</strong></td>
<td><em>unnumbered</em></td>
</tr>
<tr>
<td><strong>Barack Obama (D)</strong></td>
<td><strong>Total</strong></td>
<td style="text-align: center;"><strong>276</strong></td>
<td style="text-align: center;"><strong>35</strong></td>
<td style="text-align: center;"><strong>8.00</strong></td>
<td><em>13489 - 13764</em></td>
</tr>
<tr>
<td><strong>Donald J. Trump - I (R)</strong></td>
<td><strong>Total</strong></td>
<td style="text-align: center;"><strong>220</strong></td>
<td style="text-align: center;"><strong>55</strong></td>
<td style="text-align: center;"><strong>4.00</strong></td>
<td><em>13765 - 13984</em></td>
</tr>
</tbody></table></body></html>
"""


def test_eo_table_parser_extracts_party():
    data = _parse_eo_table(_FIXTURE_HTML)

    assert data["washington-1"]["party"] == "F"
    assert data["jefferson-3"]["party"] == "D-R"
    assert data["obama-44"]["party"] == "D"
    assert data["trump-45"]["party"] == "R"

    assert data["obama-44"]["total_orders"] == 276
    assert data["obama-44"]["avg_per_year"] == 35.0
    assert data["obama-44"]["years_in_office"] == 8.0


if __name__ == "__main__":
    test_eo_table_parser_extracts_party()
    print("OK")
