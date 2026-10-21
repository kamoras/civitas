"""What a chart's readout says about each of its marks.

Every chart on the site names its marks on hover, tap and the keyboard
(frontend `components/shared/ChartReadout.tsx`). The words and figures it
shows are written here, beside the data, so the page does no arithmetic
(AGENTS.md: the backend owns calculations). A mark is
{id, label, detail, shown, context, href}: what it is, a second line
(party and seat, a date), its value as printed with its unit, its place
among the rest (a rank, a share, a period), and its page where one exists.

Read path only: no model, no fetch.
"""
from datetime import date


def mark(id: str, label: str, shown: str, *, detail: str | None = None,
         context: str | None = None, href: str | None = None) -> dict:
    return {"id": id, "label": label, "detail": detail, "shown": shown,
            "context": context, "href": href}


def _count(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def vote_page(v) -> str:
    """A roll call's page, at its members' list: who voted each way. `v` is a
    RollCall or its dict (chamber, congress, session, number)."""
    get = v.get if isinstance(v, dict) else lambda k: getattr(v, k)
    return f"/congress/votes/{get('chamber')}/{get('congress')}/{get('session')}/{get('number')}#members"


def split_marks(yeas: int | None, nays: int | None, href: str | None = None) -> list[dict]:
    """A yea-nay bar's two sides: each one's votes and share of those voting,
    and the vote's page (who voted each way) where there is one."""
    yeas, nays = yeas or 0, nays or 0
    total = yeas + nays

    def side(id: str, label: str, n: int) -> dict:
        share = f"{round(100 * n / total)}% of the {total:,} voting" if total else None
        return mark(id, label, _count(n, "vote", "votes"), context=share, href=href)

    return [side("yea", "Yea", yeas), side("nay", "Nay", nays)]


def _short(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d:%b} {d.day}, {d.year}"


def period_marks(opened: str, closes: str, total_days: int, days_left: int) -> list[dict]:
    """A comment period's bar: how many of its days have gone, and its dates."""
    gone = total_days - days_left
    return [mark("elapsed", "Comment period",
                 f"{gone:,} of {_count(total_days, 'day', 'days')} gone",
                 context=f"opened {_short(opened)}, closes {_short(closes)}")]
