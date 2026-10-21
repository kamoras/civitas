"""No code is about this cycle, this Congress or this year (AGENTS.md §6a).

A scan of every module under app/ for a Congress number or a calendar year
written into the code: an integer literal from 1700 to 2099, a Congress
number (100 to 130) on a line that speaks of a Congress, and a year inside
a string that is not a docstring or a log message. Each one that is here on
purpose is listed below with why: a historical fact (the 1st Congress
convened in 1789), a fixed date the site states (an API sunset), or a
number that only looks like a year (a 2,048-token context). Anything else
fails: derive it from the clock (`settings.CURRENT_CONGRESS`,
`election_phase.active_election()`, `congress_first_year`) or read it from
a data file, never type it.

An entry is keyed by file and value, so moving a line doesn't break it,
and a stale entry (nothing left to excuse) fails too, so the list can only
shrink with the code.
"""

import ast
import re
from collections import Counter
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# Not after "u" or "x": "\u2019" in a raw string is an escape, not a year.
_YEAR_IN_TEXT = re.compile(r"(?<![\d.\-/ux])(1[789]\d\d|20\d\d)(?!\d)")
_CONGRESS_LINE = re.compile(r"congress|_cong\b", re.I)
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}

# (file under app/, value): why it is not a cycle- or Congress-specific literal.
ALLOWED: dict[tuple[str, int], str] = {
    # The 1st Congress convened in 1789: every Congress's dates follow from it.
    ("api/congress.py", 1789): "first year Congress met",
    ("pipeline/analyze/president_scorer.py", 1789): "first Congress, for a term's Congresses",
    ("pipeline/analyze/score_calculator.py", 1789): "first Congress",
    ("pipeline/fetch/congress.py", 1789): "first Congress (congress_first_year, congress_for_year)",
    ("pipeline/fetch/district_pvi.py", 1789): "first Congress",
    ("services/_scorecard_common.py", 1789): "first Congress",
    ("time_utils.py", 1789): "first Congress",
    ("district_lines.py", 1786): "the Nth Congress is elected in 1786 + 2N",
    ("pipeline/fetch/presidential_elections.py", 1789): "pre-1824 election, historical table",
    ("pipeline/fetch/presidential_elections.py", 1792): "pre-1824 election, historical table",
    ("pipeline/fetch/presidential_elections.py", 1796): "pre-1824 election, historical table",
    ("pipeline/fetch/presidential_elections.py", 1800): "pre-1824 election, historical table",
    ("pipeline/fetch/presidential_elections.py", 1804): "pre-1824 election, historical table",
    ("pipeline/fetch/presidential_elections.py", 1808): "pre-1824 election, historical table",
    ("pipeline/fetch/presidential_elections.py", 1812): "pre-1824 election, historical table",
    ("pipeline/fetch/presidential_elections.py", 1816): "pre-1824 election, historical table",
    ("pipeline/fetch/presidential_elections.py", 1820): "pre-1824 election, historical table",
    # The Senate's three classes, by the year each was first elected in the
    # modern rotation: a constitutional cycle (art. I, sec. 3), every class
    # recurring every six years from it.
    ("election_calendar.py", 2018): "Senate class 1 rotation base",
    ("election_calendar.py", 2020): "Senate class 2 rotation base",
    ("election_calendar.py", 2022): "Senate class 3 rotation base",
    # Where a data series begins, a historical fact about the source.
    ("pipeline/analyze/president_scorer.py", 1947): "first year of the postwar peer and price series",
    ("pipeline/fetch/peer_gdp.py", 1946): "first bundled Maddison level",
    ("pipeline/president_pipeline.py", 1939): "BLS payroll series begins",
    ("pipeline/president_pipeline.py", 1790): "real GDP series begins",
    ("pipeline/fetch/cspan_historians_survey.py", 2021): "the first edition the parser was verified on",
    ("pipeline/justice_pipeline.py", 2014): "last term in Epstein & Posner's bundled coding",
    ("services/explore_coverage.py", 1935): "Federal Register volume 1 is 1936",
    ("pipeline/analyze/document_authority.py", 1854): "Congressional Globe/Record volume arithmetic",
    ("pipeline/fetch/electoral_college.py", 1937): "20th Amendment moved inauguration to Jan 20",
    ("forecast/training_polls.py", 1899): "Excel's date epoch",
    # The site's own history: dates already past that the site states.
    ("pipeline/congress_activity.py", 2025): "the floor archive's first day (the 119th Congress convened)",
    ("api/public.py", 2026): "the date public API v1's deprecation was announced",
    # Numbers in this range that are not years.
    ("api/visits.py", 2000): "a load-time histogram bucket, ms",
    ("data_refresh.py", 1800): "a timeout, seconds",
    ("issue_ids.py", 2000): "a self-check's sample size",
    ("pipeline/analyze/action_center.py", 2048): "LLM context, tokens",
    ("pipeline/analyze/election_bluesky.py", 2048): "LLM context, tokens",
    ("pipeline/analyze/ollama_client.py", 2048): "LLM tokens / bytes",
    ("pipeline/analyze/lobbying_records.py", 2000): "a document-frequency cap",
    ("pipeline/analyze/post_composer.py", 2000): "calls between refits",
    ("pipeline/analyze/prompts.py", 2000): "characters of a document",
    ("pipeline/analyze/race_relevance.py", 2000): "a row limit",
    ("pipeline/explore_pipeline.py", 2000): "rows per run / characters",
    ("pipeline/fetch/ballot_measures_ms.py", 2000): "two-digit year to four",
    ("pipeline/fetch/ptr_common.py", 2000): "two-digit year to four",
    ("pipeline/fetch/regulations_gov.py", 2000): "characters of a comment",
    ("pipeline/fetch/sec_tickers.py", 1799): "an SEC SIC code range",
    ("pipeline/fetch/sec_tickers.py", 2000): "an SEC SIC code range",
    ("pipeline/fetch/sec_tickers.py", 2099): "an SEC SIC code range",
}

# Years inside strings: citations and source-series facts in shown text.
ALLOWED_IN_TEXT: dict[tuple[str, int], str] = {
    ("api/feed.py", 2026): "the feed's fixed epoch and RFC 4151 tag date",
    ("entity_feeds.py", 2026): "RFC 4151 tag date",
    ("api/public.py", 1939): "BLS payroll series begins",
    ("api/redistricting.py", 2020): "the 2020 census's residents, the column the file reports",
    ("pipeline/analyze/president_scorer.py", 1824): "first national popular vote",
    ("pipeline/fetch/face_detection.py", 2023): "the vendored model's file name",
    ("pipeline/fetch/voteview.py", 2002): "Canes-Wrone, Brady & Cogan (2002), a citation",
    ("pipeline/fetch/voteview.py", 2010): "Bafumi & Herron (2010), a citation",
    ("pipeline/analyze/president_scorer.py", 1943): "FDR's approval polls end",
    ("president_charts.py", 1804): "the 12th Amendment changed electoral voting",
    ("schemas.py", 1786): "the Nth Congress is elected in 1786 + 2N",
    ("api/visits.py", 2026): "the first whole day visits were confirmed (CONFIRMED_VISITS_SINCE)",
    ("issue_ids.py", 2000): "a self-check's sample size",
    ("pipeline/justice_pipeline.py", 1937): "the bundled file's name",
    ("pipeline/justice_pipeline.py", 2014): "the bundled file's name",
    ("president_charts.py", 1945): "approval polling begins",
    ("president_charts.py", 1947): "postwar series begin",
    ("president_charts.py", 1939): "payroll series begins",
    ("president_peers.py", 1945): "approval polling begins",
    ("president_peers.py", 1824): "first national popular vote",
    ("president_peers.py", 1939): "payroll series begins",
    ("president_peers.py", 1947): "postwar series begin",
    ("schemas.py", 2026): "the dates features were removed, and API v1's sunset",
    ("schemas.py", 2027): "API v1's sunset date",
    ("services/presidential_support_service.py", 1996): "Papke & Wooldridge (1996)",
    ("services/presidential_support_service.py", 1927): "Wilson (1927)",
    ("services/presidential_support_service.py", 1998): "Agresti & Coull (1998)",
    ("services/presidential_support_service.py", 2019): "Yin, Hay & Roth (2019)",
    ("pipeline/analyze/president_scorer.py", 1941): "FDR's approval polls begin",
    ("pipeline/fetch/peer_gdp.py", 2000): "the World Bank API's per_page",
    ("api/feedback.py", 2022): "GitHub's API version header",
    ("pipeline/analyze/action_center.py", 1970): "an empty-date sentinel",
}


def _docstrings_and_logs(tree: ast.AST) -> set[int]:
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            skip.add(id(node.value))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LOG_METHODS):
            skip.update(id(c) for c in ast.walk(node) if isinstance(c, ast.Constant))
    return skip


def _literals() -> tuple[Counter, Counter]:
    numbers, texts = Counter(), Counter()
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        source = path.read_text()
        lines = source.splitlines()
        tree = ast.parse(source)
        skip = _docstrings_and_logs(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or id(node) in skip or isinstance(node.value, bool):
                continue
            if isinstance(node.value, int):
                if 1700 <= node.value <= 2099:
                    numbers[(rel, node.value)] += 1
                elif 100 <= node.value <= 130 and _CONGRESS_LINE.search(lines[node.lineno - 1]):
                    numbers[(rel, node.value)] += 1
            elif isinstance(node.value, str):
                for year in {int(y) for y in _YEAR_IN_TEXT.findall(node.value)}:
                    texts[(rel, year)] += 1
    return numbers, texts


def test_no_congress_or_year_is_written_into_the_code():
    numbers, texts = _literals()
    new = sorted(set(numbers) - set(ALLOWED)) + sorted(set(texts) - set(ALLOWED_IN_TEXT))
    assert not new, (
        "A year or Congress number is written into the code (AGENTS.md §6a). Derive it from the clock "
        "or a data file; if it is a historical fact, add it to ALLOWED with why: " + repr(new))


def test_every_allowance_still_excuses_something():
    numbers, texts = _literals()
    stale = sorted(set(ALLOWED) - set(numbers)) + sorted(set(ALLOWED_IN_TEXT) - set(texts))
    assert not stale, f"Nothing left to excuse; remove from the allowlist: {stale}"
