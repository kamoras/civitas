"""The president page's charts: the term against the other presidencies.

Three, each built only from series the president pipeline already stores:

- Approval through the term: every approval poll (American Presidency
  Project) averaged by month of the term, this president's line over every
  other polling-era presidency's, with the middle half of the others at
  each month.
- Jobs since the inauguration: total nonfarm payrolls (BLS via FRED,
  annual averages, from 1939), jobs added since the inauguration year, by
  year of the term, against the other presidencies since 1939.
- Growth in each year of the term: real GDP (MeasuringWorth), each
  calendar year's growth, against the middle half of every other
  presidency's years in the same era (before or since 1947).

Every number, label, caption and table row is built here; the page draws
them. The wording says what happened during the term, never why.

Outside app/pipeline/ like president_peers: display only, no fingerprint.
Read path only: stored series (the cache the pipeline fills, read whatever
its age) and ORM rows; nothing is fetched here.
"""

import statistics
from datetime import date

from sqlalchemy.orm import Session

from app.models import President
from app.pipeline.analyze.president_scorer import peer_comparable
from app.pipeline.fetch.historical_gdp import stored_real_gdp
from app.pipeline.fetch.macro_series import PAYROLLS, stored_annual_series
from app.pipeline.fetch.presidential_approval import dated_approvals, stored_approval_history
from app.time_utils import utcnow

# Fewest other presidencies at a point before the middle half is drawn
# there: below it, a quartile is one or two presidencies' own figures. A
# convention, stated on the page; late in a second term few have served.
MIN_BAND = 5
_MONTH_DAYS = 30.4375


def _quartiles(values: list[float]) -> tuple[float, float, float]:
    q = statistics.quantiles(values, n=4, method="inclusive")
    return q[0], q[1], q[2]


def _end(p: President) -> date:
    return date.fromisoformat(p.term_end) if p.term_end else utcnow().date()


def _line_chart(me: President, mine: list[tuple[int, float]], others: dict[President, list[tuple[int, float]]],
                fmt, x_min_span: int) -> dict:
    """The shared line-and-band shape: this term's points, every other
    term's points over the same span, and the others' middle half at each
    point where at least MIN_BAND of them reach it."""
    x_max = max([x for x, _ in mine] + [x_min_span])
    others = {o: [(x, y) for x, y in pts if x <= x_max] for o, pts in others.items()}
    others = {o: pts for o, pts in others.items() if pts}
    band = []
    for x in range(x_max + 1):
        at = [y for pts in others.values() for px, y in pts if px == x]
        if len(at) >= MIN_BAND:
            lo, mid, hi = _quartiles(at)
            band.append({"x": x, "low": lo, "median": mid, "high": hi, "n": len(at)})
    ys = [y for _, y in mine] + [y for pts in others.values() for _, y in pts]
    return {
        "series": {"id": me.id, "name": me.name, "points": [[x, y] for x, y in mine]},
        "others": [{"id": o.id, "name": o.name, "points": [[x, y] for x, y in pts]}
                   for o, pts in sorted(others.items(), key=lambda kv: kv[0].number)],
        "band": band,
        "xMax": x_max,
        "yMin": min(ys),
        "yMax": max(ys),
        # The axis ends as printed: the page formats nothing.
        "yTop": fmt(max(ys)),
        "yBottom": fmt(min(ys)),
        "n": len(others),
        "table": [{"x": x, "value": fmt(y),
                   "band": next((f"{fmt(b['low'])} to {fmt(b['high'])} (n={b['n']})" for b in band if b["x"] == x),
                                None)}
                  for x, y in mine],
    }


def _monthly_approval(p: President, db: Session) -> list[tuple[int, float]]:
    polls = stored_approval_history(db, p.id)
    if not polls:
        return []
    start, end = date.fromisoformat(p.term_start), _end(p)
    # A poll in the term's last days stays in its last month.
    last = int(max((end - start).days - 1, 0) // _MONTH_DAYS)
    months: dict[int, list[float]] = {}
    for day, value in dated_approvals(polls):
        if start <= day <= end:
            months.setdefault(min(int((day - start).days // _MONTH_DAYS), last), []).append(value)
    return sorted((m, statistics.mean(v)) for m, v in months.items())


def _pct(v: float) -> str:
    return f"{v:.0f}%"


def _approval_chart(p: President, everyone: list[President], db: Session) -> dict | None:
    mine = _monthly_approval(p, db)
    if not mine:
        return None
    others = {o: pts for o in everyone if o.id != p.id and (pts := _monthly_approval(o, db))}
    chart = _line_chart(p, mine, others, _pct, 47)
    last_x, last_y = mine[-1]
    band = next((b for b in chart["band"] if b["x"] == last_x), None)
    caption = f"Approval in month {last_x + 1} of the term: {_pct(last_y)}."
    if band:
        caption += (f" At the same point, the middle half of {band['n']} other presidencies ran from "
                    f"{_pct(band['low'])} to {_pct(band['high'])}.")
    return {
        "key": "approval", "title": "Approval through the term",
        "why": "How the public's approval moved over the term, beside other presidencies at the same point.",
        "xLabel": "Month of the term", "xUnit": "month", "yLabel": "Approve",
        "window": "Polls from the start of the term to its end, averaged by month of the term; polling began in 1945.",
        "source": {"name": "American Presidency Project",
                   "url": "https://www.presidency.ucsb.edu/statistics/data/presidential-job-approval"},
        "caption": caption, "kind": "line", "yFormat": "pct", **chart,
    }


def _jobs_points(p: President, payrolls: dict[int, float]) -> list[tuple[int, float]]:
    start = int(p.term_start[:4])
    if start not in payrolls:
        return []
    # The year the term ends in belongs to the successor, as for growth.
    last = min(_end(p).year - (1 if p.term_end else 0), max(payrolls))
    # PAYEMS is in thousands of jobs: millions added since the inauguration year.
    return [(y - start, (payrolls[y] - payrolls[start]) / 1000) for y in range(start, last + 1) if y in payrolls]


def _millions(v: float) -> str:
    return f"{v:+.1f}M"


def _jobs_chart(p: President, everyone: list[President], db: Session) -> dict | None:
    payrolls = stored_annual_series(db, PAYROLLS)
    if not payrolls:
        return None
    mine = _jobs_points(p, payrolls)
    if len(mine) < 2:
        return None
    others = {o: pts for o in everyone if o.id != p.id and len(pts := _jobs_points(o, payrolls)) >= 2}
    chart = _line_chart(p, mine, others, _millions, 4)
    last_x, last_y = mine[-1]
    band = next((b for b in chart["band"] if b["x"] == last_x), None)
    caption = f"Jobs added since the year of the inauguration, by year {last_x + 1} of the term: {_millions(last_y)}."
    if band:
        caption += (f" At the same point, the middle half of {band['n']} other presidencies since 1939 ran from "
                    f"{_millions(band['low'])} to {_millions(band['high'])}.")
    return {
        "key": "jobs", "title": "Jobs since the inauguration",
        "why": "How many payroll jobs were added during the term, beside other presidencies at the same point.",
        "xLabel": "Year of the term", "xUnit": "year", "yLabel": "Jobs added",
        "window": ("Annual average payrolls, each year against the inauguration year's; the series starts in "
                   "1939, so the comparison covers presidencies since then. Only complete years are counted."),
        "source": {"name": "BLS payrolls via FRED", "url": "https://fred.stlouisfed.org/series/PAYEMS"},
        "caption": caption, "kind": "line", "yFormat": "millions", **chart,
    }


def _term_growth(p: President, gdp: dict[int, float]) -> list[tuple[int, float]]:
    start, last = int(p.term_start[:4]), _end(p).year
    if p.term_end:
        last -= 1  # the year the term ends in belongs to the successor
    return [(y, (gdp[y] / gdp[y - 1] - 1) * 100) for y in range(start, last + 1) if y in gdp and y - 1 in gdp]


def _growth_chart(p: President, everyone: list[President], db: Session) -> dict | None:
    gdp = stored_real_gdp(db)
    if not gdp:
        return None
    mine = _term_growth(p, gdp)
    if not mine:
        return None
    postwar = peer_comparable(int(p.term_start[:4]))
    era_others = [o for o in everyone if o.id != p.id and peer_comparable(int(o.term_start[:4])) == postwar]
    years = [g for o in era_others for _, g in _term_growth(o, gdp)]
    era = "since 1947" if postwar else "before 1947"
    low, mid, high = _quartiles(years) if len(years) >= MIN_BAND else (None, None, None)
    lo_g, hi_g = min(g for _, g in mine), max(g for _, g in mine)
    caption = (f"Real growth in the one complete year of the term on record: {lo_g:.1f}%." if len(mine) == 1
               else f"Real growth in each year of the term ran from {lo_g:.1f}% to {hi_g:.1f}%.")
    if low is not None:
        caption += (f" Across the {len(years)} years of {len(era_others)} other presidencies {era}, the middle "
                    f"half ran from {low:.1f}% to {high:.1f}%, with a median of {mid:.1f}%.")
    return {
        "key": "growth", "title": "Growth in each year of the term",
        "why": "How fast the economy grew each calendar year of the term, beside the years of other presidencies.",
        "xLabel": "Year", "xUnit": "calendar year", "yLabel": "Real GDP growth",
        "window": (f"Calendar years from the inauguration year to the year before the term ended, each against "
                   f"the year before; compared with presidencies {era}. The first year mostly reflects the "
                   f"previous administration."),
        "source": {"name": "MeasuringWorth", "url": "https://www.measuringworth.com/datasets/usgdp/"},
        "caption": caption, "kind": "bars", "yFormat": "pct1",
        "bars": [{"label": str(y), "value": g, "shown": f"{g:.1f}%", "first": i == 0}
                 for i, (y, g) in enumerate(mine)],
        "eraBand": None if low is None else {"low": low, "median": mid, "high": high,
                                             "years": len(years), "presidencies": len(era_others)},
        "yMin": (y_min := min([g for _, g in mine] + ([low] if low is not None else []) + [0])),
        "yMax": (y_max := max([g for _, g in mine] + ([high] if high is not None else []) + [0])),
        "yTop": f"{y_max:.1f}%",
        "yBottom": f"{y_min:.1f}%",
        "bandShown": None if low is None else f"middle half {low:.1f}% to {high:.1f}%, median {mid:.1f}%",
        "n": len(era_others),
    }


def president_charts(db: Session, president_id: str) -> dict | None:
    """The charts a presidency has the series for, in page order; None for
    no such president."""
    p = db.query(President).filter(President.id == president_id).first()
    if p is None:
        return None
    everyone = db.query(President).all()
    charts = [c for c in (_approval_chart(p, everyone, db), _jobs_chart(p, everyone, db),
                          _growth_chart(p, everyone, db)) if c]
    return {"charts": charts, "minBand": MIN_BAND}
