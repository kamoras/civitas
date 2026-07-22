"""Regression test for cspan_historians_survey.py's table parser.

Covers the edge cases that took a real fix against live C-SPAN HTML to
get right: the page embeds 11 near-identical tables (the aggregate score
plus one per category) with no distinguishing table attributes — only
div#rgtoverall scopes to the right one. Also covers Grover Cleveland
(rated once by C-SPAN, applied to both of this platform's per-term ids)
and the Garfield/Arthur/Nixon/Bush-41 middle-initial mismatches against
NAME_TO_ID.
"""

from app.pipeline.fetch.cspan_historians_survey import _parse_survey_table

# Two tables: #rgtoverall (the real "Final Score" aggregate) and a
# second, differently-scored "category" table using the identical
# tr.result/td.name/td.score structure — verifies the parser scopes to
# the right one rather than picking up both. Row content copied verbatim
# (structure) from a live fetch of c-span.org/presidentsurvey2021, 2026-07.
_FIXTURE_HTML = """
<html><body>
<div id="rgtoverall"><section class="right-column overall"><table><tbody>
<tr class="result">
  <td class="name"><a href="./?personid=34702">Abraham Lincoln</a></td>
  <td class="score">897</td>
  <td class="rank">1</td>
</tr>
<tr class="result">
  <td class="name"><a href="./?personid=39784">George Washington</a></td>
  <td class="score">851</td>
  <td class="rank">2</td>
</tr>
<tr class="result">
  <td class="name"><a href="./?personid=1">Grover Cleveland</a></td>
  <td class="score">523</td>
  <td class="rank">25</td>
</tr>
<tr class="result">
  <td class="name"><a href="./?personid=2">Donald J. Trump</a></td>
  <td class="score">312</td>
  <td class="rank">41</td>
</tr>
<tr class="result">
  <td class="name"><a href="./?personid=3">James A. Garfield</a></td>
  <td class="score">506</td>
  <td class="rank">27</td>
</tr>
</tbody></table></section></div>
<div id="rgteconomic"><section class="right-column economic"><table><tbody>
<tr class="result">
  <td class="name"><a href="./?personid=5157">Franklin D. Roosevelt</a></td>
  <td class="score">94.8</td>
  <td class="rank">1</td>
</tr>
</tbody></table></section></div>
</body></html>
"""


def test_survey_parser_scopes_to_overall_table_only():
    data = _parse_survey_table(_FIXTURE_HTML)

    # The category table's row (FDR, 94.8) must not appear at all.
    assert "fdr-32" not in data

    assert data["lincoln-16"] == 897
    assert data["washington-1"] == 851
    assert data["garfield-20"] == 506


def test_cleveland_single_rating_applies_to_both_terms():
    data = _parse_survey_table(_FIXTURE_HTML)
    assert data["cleveland-22"] == 523
    assert data["cleveland-24"] == 523


def test_trump_maps_only_to_first_term():
    data = _parse_survey_table(_FIXTURE_HTML)
    assert data["trump-45"] == 312
    assert "trump-47" not in data


if __name__ == "__main__":
    test_survey_parser_scopes_to_overall_table_only()
    test_cleveland_single_rating_applies_to_both_terms()
    test_trump_maps_only_to_first_term()
    print("OK")
