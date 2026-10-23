"""Where a president stands among the other presidencies, figure by figure.

A president's page shows no score (2026-10). It shows each figure the
retired Presidential Score was computed from (approval, its change over the
term, jobs, growth, unemployment, inflation, the historians' points) among
every other presidency with the same figure, the way a member's page shows
"Where they stand": the figure, every peer's figure, the president's
percentile, a tick for the figure expected (the peer median, or what
presidents starting from the same level saw), and a word from the members'
fixed rule (`peer_comparison.peer_word`). The stored figures are the ones the
president pipeline wrote; nothing here is fitted.

A figure that exists only from some year is compared within that era, and the
row says so: approval polls from Truman, payroll jobs from 1939, growth
against peer economies and the unemployment and inflation series from 1947.

Outside app/pipeline/ on purpose, like peer_comparison: a display rule must
not move the analysis-code fingerprint. Read path only: ORM queries and the
persisted reference, no model.
"""

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.models import President
from app.ordinals import rank_shown
from app.peer_comparison import RULE as MEMBER_RULE, percentile, peer_word
from app.pipeline.analyze.president_scorer import (
    FULL_TERM_DAYS,
    _approval_reference,
    _macro_reference,
    jobs_per_attributed_year,
    peer_comparable,
    peer_relative,
    term_days,
)
from app.pipeline.fetch.cspan_historians_survey import edition_url, stored_edition
from app.pipeline.fetch.presidential_approval import dated_approvals, stored_approval_history
from app.pipeline.fetch.peer_gdp import PEER_COUNTRIES
from app.time_utils import utcnow

# The word's cut points are the members' (peer_comparison.RULE), served with
# every comparison so the page can state them.
RULE_CUTS = {k: MEMBER_RULE[k] for k in ("wellOutside", "outside", "wellGapSd")}
# Fewest presidencies with a figure before a word is drawn: the fewest at
# which the rule's outer cut can be crossed at all. A presidency's mid-rank
# percentile among n others is at least 50 / (n + 1), so a "well below"
# below the 10th percentile needs n + 1 > 50 / 10. Derived from the cut, not
# typed (it was 10, the old reference floor).
MIN_PEERS = int(50 / RULE_CUTS["wellOutside"][0])
RULE = {**RULE_CUTS, "minPeers": MIN_PEERS}

_APPROVAL_SOURCE = {"name": "American Presidency Project",
                    "url": "https://www.presidency.ucsb.edu/statistics/data/presidential-job-approval"}
_JOBS_SOURCE = {"name": "BLS payrolls", "url": "https://data.bls.gov/timeseries/CES0000000001"}
_PEER_GDP_SOURCE = {"name": "Maddison Project and World Bank",
                    "url": "https://www.rug.nl/ggdc/historicaldevelopment/maddison/"}
_GDP_SOURCE = {"name": "MeasuringWorth", "url": "https://www.measuringworth.com/datasets/usgdp/"}
_UNEMPLOYMENT_SOURCE = {"name": "BLS via FRED", "url": "https://fred.stlouisfed.org/series/UNRATE"}
_PRICES_SOURCE = {"name": "BLS via FRED", "url": "https://fred.stlouisfed.org/series/CPIAUCSL"}
_MARGIN_SOURCE = {"name": "American Presidency Project",
                  "url": "https://www.presidency.ucsb.edu/statistics/data/presidential-election-mandates"}


def _years(p: President) -> float:
    days = term_days(p.term_start, p.term_end or utcnow().date().isoformat()) or 0
    return max(days / 365.25, 0.1)


def _start_year(p: President) -> int | None:
    return int(p.term_start[:4]) if p.term_start else None


def term_years(p: President) -> str | None:
    """The term's years as a hover label prints them: "1933–1945", "since 2025"."""
    start = _start_year(p)
    if start is None:
        return None
    if p.is_current or not p.term_end:
        return f"since {start}"
    return f"{start}–{p.term_end[:4]}"


def _full_term(p: President) -> bool:
    return not p.is_current and (term_days(p.term_start, p.term_end) or FULL_TERM_DAYS) >= FULL_TERM_DAYS


def _jobs(p: President) -> float | None:
    if p.jobs_created_millions is None:
        return None
    return jobs_per_attributed_year(p.jobs_created_millions, _years(p))


def _pct(v: float) -> str:
    return f"{v:.1f}%"


def _signed(v: float) -> str:
    return f"{v:+.1f} points"


def _moved(v: float) -> str:
    """"rose 2.1 points" / "fell 0.4 points"."""
    return f"{'rose' if v >= 0 else 'fell'} {abs(v):.1f} points"


_ECONOMY = {"jobs", "growthVsPeers", "growth", "unemployment", "inflation"}


def _millions(v: float) -> str:
    return f"{v:.2f} million a year"


@dataclass(frozen=True)
class _Figure:
    key: str
    label: str
    question: str
    why: str
    measured: str
    era: str
    source: dict
    value: Callable[[President], float | None]
    # Who the president is set beside: everyone else this is true of.
    peer: Callable[[President], bool]
    shown: Callable[[float], str]
    fact: str          # "{shown}", or "{moved}" ("rose 2.1 points")
    more: str          # what the presidencies above did
    less: str


_FIGURES = [
    _Figure(
        "approval", "Average approval", "How did the public rate the term?",
        "How much of the public approved of the job the president was doing, on average.",
        "The average of every approval poll over the term. A sitting or cut-short presidency is set beside "
        "every other presidency over the same number of days from its first poll, since approval tends to fall "
        "as a term goes on; presidencies polled for fewer days are left out.",
        "presidencies with approval polls (from 1945)", _APPROVAL_SOURCE,
        lambda p: p.avg_approval, lambda p: p.avg_approval is not None and _full_term(p),
        _pct, "Approval averaged {shown} during the term.", "averaged higher", "averaged lower",
    ),
    _Figure(
        "approvalChange", "Approval change", "How did the public rate the term?",
        "How far approval moved from the start of the term to the end.",
        "The last quarter of the term's polls minus the first quarter. The tick is what presidents who "
        "started at the same level went on to see: the higher a term starts, the further it tends to fall. "
        "A sitting or cut-short presidency is set beside every other presidency over the same number of days "
        "from its first poll.",
        "presidencies with approval polls (from 1945)", _APPROVAL_SOURCE,
        lambda p: p.approval_trend, lambda p: p.approval_trend is not None and _full_term(p),
        _signed, "Approval {moved} from the start of the term to the end.",
        "moved up more", "moved down more",
    ),
    _Figure(
        "electionMargin", "Election margin", "How did the public rate the term?",
        "Before polling, the margin of victory is the only record of how the public voted.",
        "The average popular-vote margin in the president's own election wins; before 1824, estimated "
        "from the electoral vote.",
        "presidents who won a presidential election", _MARGIN_SOURCE,
        lambda p: p.election_margin if p.avg_approval is None else None,
        lambda p: p.election_margin is not None,
        _signed, "Won by an average of {shown}.", "won by more", "won by less",
    ),
    _Figure(
        "jobs", "Jobs added", "What happened to the economy during the term?",
        "How many payroll jobs were added each year, leaving out the first year.",
        "Payroll jobs added from the end of the term's first year to its end, per year. The first year is "
        "left out, since it mostly reflects the previous administration. Counted in jobs, not percent, "
        "because percent growth falls with the labor force's own slowing growth.",
        "presidencies since 1939, when the payroll series starts", _JOBS_SOURCE,
        _jobs, lambda p: _jobs(p) is not None and not p.is_current,
        _millions, "Jobs grew by {shown} during the term.", "added more", "added fewer",
    ),
    _Figure(
        "growthVsPeers", "Growth against peer economies", "What happened to the economy during the term?",
        "Growth that was particular to the US, after the shocks every rich economy shared.",
        f"Growth per person a year, leaving out the first year, minus the median of {len(PEER_COUNTRIES)} peer economies over "
        "the same years, with the part of the gap their catching up with US incomes predicts set aside.",
        "presidencies since 1947, when the peer series starts", _PEER_GDP_SOURCE,
        lambda p: peer_relative(_start_year(p), p.gdp_growth_relative),
        lambda p: peer_relative(_start_year(p), p.gdp_growth_relative) is not None and not p.is_current,
        _signed, "US growth ran {shown} a year against peer economies during the term.",
        "ran further ahead", "ran further behind",
    ),
    _Figure(
        "growth", "Economic growth", "What happened to the economy during the term?",
        "How fast the economy grew each year, leaving out the first year.",
        "Average annual real growth, leaving out the first year, measured from the last peak when a term "
        "began in a downturn. No peer-economy series reaches these years.",
        "presidencies before 1947", _GDP_SOURCE,
        lambda p: p.gdp_growth_avg if not peer_comparable(_start_year(p)) else None,
        lambda p: p.gdp_growth_avg is not None and not peer_comparable(_start_year(p)),
        _pct, "The economy grew {shown} a year during the term.", "grew faster", "grew slower",
    ),
    _Figure(
        "unemployment", "Unemployment change", "What happened to the economy during the term?",
        "How far the unemployment rate moved over the term.",
        "The unemployment rate in the last credited year minus the rate the year the term began. The tick "
        "is what presidents starting at the same rate saw: a term that starts near a recession's peak "
        "usually sees it fall a long way.",
        "presidencies since 1947, when the series starts", _UNEMPLOYMENT_SOURCE,
        lambda p: p.unemployment_change, lambda p: p.unemployment_change is not None and not p.is_current,
        _signed, "Unemployment {moved} during the term.", "rose more", "fell more",
    ),
    _Figure(
        "inflation", "Inflation", "What happened to the economy during the term?",
        "How fast prices rose each year, on average.",
        "Average yearly consumer-price inflation over the credited years. The tick is what presidents "
        "starting at the same rate averaged, since inherited inflation tends to linger.",
        "presidencies since 1947, when the series starts", _PRICES_SOURCE,
        lambda p: p.inflation_average, lambda p: p.inflation_average is not None and not p.is_current,
        _pct, "Prices rose {shown} a year during the term.", "saw faster rises", "saw slower rises",
    ),
    _Figure(
        "historians", "Historians' points", "How do historians rate the presidency?",
        "How historians rate the presidency: their own survey, not a Civitas figure.",
        "C-SPAN's Presidential Historians Survey: historians rate ten areas of leadership such as crisis "
        "leadership, economic management and moral authority, summed to a total. It rates only completed "
        "terms, and historians as a field tend to favor presidents who expanded federal power.",
        "presidencies the survey rates", {"name": "C-SPAN survey", "url": "https://www.c-span.org"},
        lambda p: p.historical_legacy_score, lambda p: p.historical_legacy_score is not None,
        lambda v: f"{int(v)} points", "Historians gave the presidency {shown}.",
        "were rated higher", "were rated lower",
    ),
]


# A presidency shorter than a full term is ranked among every other
# presidency's figure over the same number of days from its first poll
# (president_scorer.window_reference's "values"): approval falls as a term
# goes on, so part of a term beside whole terms flatters or penalises by
# length alone. The field of each value, and the fact worded for the window.
_WINDOW_FIELD = {"approval": "avg", "approvalChange": "trend"}
_WINDOW_FACT = {"approval": "Approval averaged {shown} over the term's first {days} days.",
                "approvalChange": "Approval {moved} over the term's first {days} days."}


def _same_window(fig: _Figure, p: President) -> tuple[dict[str, float], int] | None:
    """Each other presidency's figure over this one's days, by id, and the
    days; None for a full term, another figure, or a reference written
    before the values were kept (the row then compares with full terms
    until the next president run writes them)."""
    if fig.key not in _WINDOW_FIELD or _full_term(p):
        return None
    window = _approval_reference(None, bool(p.is_current), p.id)
    values, days = window.get("values"), window.get("days")
    if not values or days is None:
        return None
    field = _WINDOW_FIELD[fig.key]
    return {pid: v[field] for pid, v in values.items() if v.get(field) is not None}, int(days)


# UCSB's label for a row it filled in between polls: not a reading anyone
# took, so never a presidency's lowest or highest single poll.
_INTERPOLATED = "interpolated"

# Each presidency's polls as (date, approve %), date order, the rows UCSB
# interpolated left out, beside the date of its first row of any kind (the
# day a same-window figure counts from, as approval_window does).
Polls = dict[str, tuple[date, list[tuple[date, float]]]]


def _polls(db: Session, presidents: list[President]) -> Polls:
    out: Polls = {}
    for o in presidents:
        if o.avg_approval is None:
            continue
        history = stored_approval_history(db, o.id) or []
        every = dated_approvals(history)
        if every:
            out[o.id] = (every[0][0], dated_approvals([q for q in history if q.source != _INTERPOLATED]))
    return out


def _poll(reading: tuple[date, float]) -> str:
    """"34% (Mar 2026)"."""
    return f"{reading[1]:.0f}% ({reading[0].strftime('%b %Y')})"


def _extremes(polls: Polls, pid: str, days: int | None) -> tuple[str, str] | None:
    """The presidency's lowest and highest single poll, shown, over the polls
    its figure on the row averages (its first `days` days, or the term);
    the earliest of equal readings. None without polls."""
    first, series = polls.get(pid, (None, []))
    within = [r for r in series if days is None or (r[0] - first).days <= days]
    if not within:
        return None
    return _poll(min(within, key=lambda r: r[1])), _poll(max(within, key=lambda r: r[1]))


def _approval_details(p: President, pairs: list, polls: Polls, days: int | None) -> dict:
    """The approval row's single-poll extremes: a line for the presidency
    ("Lowest single poll 34% (Mar 2026) · highest 47% (Jan 2025)", "so far"
    for a term under way) saying the strip ranks averages, and each dot's
    hover detail with its years and its own extremes. Readers took "lower"
    to mean a lower single reading; the row ranks averages."""
    within = f" in the first {days:,} days" if days is not None else ""
    so_far = " so far" if p.is_current else ""

    def detail(o: President, when: str) -> str | None:
        ext = _extremes(polls, o.id, days)
        span = f"single polls{when} from {ext[0]} to {ext[1]}" if ext else None
        return " · ".join(x for x in (term_years(o), span) if x) or None

    own = _extremes(polls, p.id, days)
    # The window is this presidency's own polled days, so its own extremes
    # cover its whole record.
    line = f"Lowest single poll{so_far} {own[0]} · highest{so_far} {own[1]}. " if own else ""
    ranks = f"averages over each presidency's first {days:,} days" if days is not None else "term averages"
    return {
        "aside": f"{line}The strip ranks {ranks}, not single polls.",
        "detail": detail(p, so_far),
        "distributionDetails": [detail(o, within) for o, _ in pairs],
    }


def _expected(fig: _Figure, p: President, median: float | None) -> tuple[float | None, str]:
    """The tick: what presidents starting from the same level saw, where the
    reference fits one, else the peer median."""
    if fig.key == "approval":
        stat = _approval_reference(None, bool(p.is_current), p.id).get("avg_approval")
        if stat and not _full_term(p):
            return stat["mean"], "same stretch"
    elif fig.key == "approvalChange":
        fit = _approval_reference(None, bool(p.is_current), p.id).get("approval_trend_fit")
        if fit and p.approval_start is not None:
            return fit["intercept"] + fit["slope"] * p.approval_start, "same start"
    elif fig.key in ("unemployment", "inflation"):
        macro = _macro_reference(None, p.id)
        fit = macro.get("unemployment_fit" if fig.key == "unemployment" else "inflation_fit")
        start = p.unemployment_start if fig.key == "unemployment" else p.inflation_start
        if fit and start is not None:
            return fit["intercept"] + fit["slope"] * start, "same start"
    return median, "peer median"


_TICK = {"peer median": "median {}", "same start": "{} for the same start",
         "same stretch": "{} over the same stretch"}


def _measured(fig: _Figure, p: President) -> str:
    """The figure's method as the row shows it. A term still running has no
    later year than its first to credit until its second calendar year is
    over and published, and peer_gdp.peer_relative_growth then uses the
    first year alone: said so, rather than "leaving out the first year".
    Once a later year can exist the run may have one or not yet (the World
    Bank publishes months after a year ends), and the text says either."""
    if fig.key != "growthVsPeers" or not p.is_current or _start_year(p) is None:
        return fig.measured
    if utcnow().year <= _start_year(p) + 1:
        return ("First year only; no later year published yet. Growth per person in the term's first year, "
                f"minus the median of {len(PEER_COUNTRIES)} peer economies that year, with the part of the gap "
                "their catching up with US incomes predicts set aside. Finished terms leave the first year out, "
                "since it mostly reflects the previous administration.")
    return ("For a term still running: growth per person a year from the term's second year through the last "
            "year published, or the first year alone until a later one is, minus the median of "
            f"{len(PEER_COUNTRIES)} peer economies over the same years, with the part of the gap their catching up "
            "with US incomes predicts set aside.")


def _row(fig: _Figure, p: President, everyone: list[President], polls: Polls | None = None) -> dict | None:
    value = fig.value(p)
    if value is None:
        return None
    window = _same_window(fig, p)
    if window:
        by_id, days = window
        pairs = sorted(((o, by_id[o.id]) for o in everyone if o.id != p.id and o.id in by_id),
                       key=lambda x: x[1])
        era = f"presidencies polled for at least {days:,} days, each over its first {days:,} days"
        over = f" over their first {days:,} days"
        fact = _WINDOW_FACT[fig.key]
    else:
        pairs = sorted(((o, fig.value(o) if fig.key != "electionMargin" else o.election_margin)
                        for o in everyone if o.id != p.id and fig.peer(o)), key=lambda x: x[1])
        era, over, fact, days = fig.era, "", fig.fact, None
    others = [v for _, v in pairs]
    values = sorted(others + [value])
    median = statistics.median(others) if others else None
    expected, basis = _expected(fig, p, median)
    # A sitting president's economy covers part of a term: drawn, never worded.
    thin = len(others) < MIN_PEERS or (p.is_current and fig.key in _ECONOMY)
    pct = percentile(value, values)
    spread = statistics.pstdev(values) if len(values) > 1 else 0
    gap = (value - expected) / spread if spread and expected is not None else None
    more, less = sum(1 for v in others if v > value), sum(1 for v in others if v < value)
    shown = fig.shown(value)
    n_side, verb = (more, fig.more) if pct <= 50 else (less, fig.less)
    return {
        "key": fig.key, "label": fig.label, "question": fig.question, "why": fig.why,
        "measured": _measured(fig, p), "source": fig.source,
        "value": value, "shown": shown,
        "expected": expected, "expectedBasis": basis,
        "expectedShown": None if expected is None else _TICK[basis].format(fig.shown(expected)),
        "peerGroup": {"label": era, "n": len(others)},
        "distribution": others,
        "distributionShown": [fig.shown(v) for v in others],
        "distributionIds": [o.id for o, _ in pairs],
        "distributionNames": [o.name for o, _ in pairs],
        # For the strip's hover label: each presidency's years and its place
        # among every presidency on the row, this one included.
        "distributionYears": [term_years(o) for o, _ in pairs],
        "distributionRanks": [rank_shown(v, values) for v in others],
        "rank": rank_shown(value, values),
        "percentile": round(pct, 1),
        "word": None if thin else peer_word(pct, gap).replace("peers", "other presidencies"),
        "sentence": f"{fact.format(shown=shown, moved=_moved(value), days=f'{days or 0:,}')} {n_side:,} of the other {len(others):,} presidencies {verb}{over}.",
        "thin": thin,
        **(_approval_details(p, pairs, polls or {}, days) if fig.key == "approval" else {}),
    }


_STANDS_OUT_WHY = ("Picked by rule: of the figures here, this one is furthest from the middle of the other "
                   "presidencies.")
_NONE_STANDS_OUT = "Close to the other presidencies on every figure here."
_NONE_STANDS_OUT_WHY = ("A figure is picked only when it is in the top or bottom tenth of the other presidencies "
                        "and far from the figure expected. None is.")


def president_peers(db: Session, president_id: str) -> dict | None:
    """The president's rows, one per figure the presidency has, each among
    the other presidencies with that figure; the historians' own rank; the
    line above the stack and why it was picked; and the plain reasons for
    each record the presidency has none of. None when there is no such
    president."""
    p = db.query(President).filter(President.id == president_id).first()
    if p is None:
        return None
    everyone = db.query(President).all()
    polls = _polls(db, everyone) if p.avg_approval is not None else {}
    rows = [r for r in (_row(f, p, everyone, polls) for f in _FIGURES) if r]
    strong = [r for r in rows if (r["word"] or "").startswith("well")]
    if strong:
        top = max(strong, key=lambda r: abs(r["percentile"] - 50))
        stands_out, why = f"{top['label']}: {top['sentence']}", f"{_STANDS_OUT_WHY} {top['why']}"
    else:
        stands_out, why = _NONE_STANDS_OUT, _NONE_STANDS_OUT_WHY

    rated = [o.historical_legacy_score for o in everyone if o.historical_legacy_score is not None]
    points = p.historical_legacy_score
    edition = stored_edition(db)
    missing = []
    if p.avg_approval is None and p.election_margin is None:
        missing.append("No approval polls or presidential election wins on record: the presidency predates "
                       "polling and came by succession, not by winning a presidential election.")
    if points is None:
        missing.append("Not rated by historians: C-SPAN's survey rates only completed terms, and the newest "
                       "edition published does not rate this presidency.")
    if not any(r["key"] in _ECONOMY for r in rows):
        missing.append("No economic figures for this term yet: each is counted from the term's second year."
                       if p.is_current else "No economic series reaches this term.")
    survey = edition_url(edition).split("?")[0] if edition else None
    for r in rows:
        if r["key"] == "historians" and survey:
            r["source"] = {"name": f"C-SPAN survey, {edition}", "url": survey}
    return {
        "rule": RULE,
        "years": term_years(p),
        "measures": rows,
        "standsOut": stands_out,
        "standsOutWhy": why,
        "missing": missing,
        "historians": {
            "rank": (1 + sum(1 for v in rated if v > points)) if points is not None else None,
            "rated": len(rated),
            "points": points,
            "surveyYear": edition,
            "surveyUrl": survey,
        },
        "recentApproval": p.recent_avg_approval,
        "otherTerms": [
            {"id": o.id, "number": o.number, "points": o.historical_legacy_score}
            for o in sorted(everyone, key=lambda o: o.number)
            if o.name == p.name and o.id != p.id and o.historical_legacy_score is not None
        ],
    }
