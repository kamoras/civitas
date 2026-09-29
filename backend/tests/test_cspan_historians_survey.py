"""Regression test for cspan_historians_survey.py's table parser.

Covers the edge cases that took a real fix against live C-SPAN HTML to
get right: the page embeds 11 near-identical tables (the aggregate score
plus one per category) with no distinguishing table attributes — only
div#rgtoverall scopes to the right one. Also covers a president with two
terms (rated once by C-SPAN, applied to each term ended by the edition's
year, read from the presidents table) and the Garfield middle-initial
mismatch against NAME_TO_ID.
"""

from app.pipeline.fetch.cspan_historians_survey import _parse_survey_table

# The presidents table's (id, name, term end) for the rows below.
_TERMS = [
    ("washington-1", "George Washington", "1797-03-04"),
    ("lincoln-16", "Abraham Lincoln", "1865-04-15"),
    ("garfield-20", "James A. Garfield", "1881-09-19"),
    ("cleveland-22", "Grover Cleveland", "1889-03-04"),
    ("cleveland-24", "Grover Cleveland", "1897-03-04"),
    ("trump-45", "Donald J. Trump", "2021-01-20"),
    ("trump-47", "Donald J. Trump", None),
]

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
    data = _parse_survey_table(_FIXTURE_HTML, 2021, _TERMS)

    # The category table's row (FDR, 94.8) must not appear at all.
    assert "fdr-32" not in data

    assert data["lincoln-16"] == 897
    assert data["washington-1"] == 851
    assert data["garfield-20"] == 506


def test_cleveland_single_rating_applies_to_both_terms():
    data = _parse_survey_table(_FIXTURE_HTML, 2021, _TERMS)
    assert data["cleveland-22"] == 523
    assert data["cleveland-24"] == 523


def test_trump_maps_only_to_first_term():
    data = _parse_survey_table(_FIXTURE_HTML, 2021, _TERMS)
    assert data["trump-45"] == 312
    assert "trump-47" not in data


def test_a_later_edition_rates_every_term_ended_by_then():
    # No per-name rule: an edition after the second term ended covers both.
    terms = [t if t[0] != "trump-47" else ("trump-47", "Donald J. Trump", "2029-01-20") for t in _TERMS]
    data = _parse_survey_table(_FIXTURE_HTML, 2029, terms)
    assert data["trump-45"] == data["trump-47"] == 312


async def test_the_newest_edition_that_parses_wins(monkeypatch):
    from types import SimpleNamespace

    from app.pipeline.fetch import cspan_historians_survey as cs

    rows = "".join(
        f'<tr class="result"><td class="name">{n}</td><td class="score">{500 + i}</td></tr>'
        for i, n in enumerate(["George Washington"] * 1)
    )
    good = f'<div id="rgtoverall"><table>{rows}</table></div>'
    asked = []

    async def fake_fetch(client, limiter, method, url, **kw):
        asked.append(url)
        return SimpleNamespace(status_code=200 if "2021" in url else 404, text=good)

    monkeypatch.setattr(cs, "fetch_with_retry", fake_fetch)
    monkeypatch.setattr(cs, "api_cache_get", lambda *a, **k: None)
    monkeypatch.setattr(cs, "api_cache_set", lambda *a, **k: None)
    monkeypatch.setattr(cs, "rated_terms", lambda name, edition, terms: [f"p{i}" for i in range(44)])

    class DB:
        def query(self, model):
            return SimpleNamespace(all=lambda: [])

    data = await cs.fetch_cspan_historians_survey(None, DB())
    assert len(data) == 44
    assert asked[0] == cs.edition_url(cs.utcnow().year) and asked[-1] == cs.edition_url(2021)


