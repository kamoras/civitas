"""What a justice's page draws: each figure on the record beside the same
figure for every sitting justice, and who appointed the justice.

Records and measurements only, never a score or a rank (justice v3,
docs/research/justice-scores.md). Every figure is read from what the justice
pipeline stored (the voting record over the terms it keeps, the opinions
written, the Martin-Quinn positions, the appointer estimate) or counted from
the stored votes; the frontend only places and draws them. The peers are the
sitting justices, because the stored record covers only them: the same terms
for everyone, so the figures compare like with like.

Outside app/pipeline/ on purpose, like peer_comparison: a display rule must
not move the analysis-code fingerprint. Read path only: ORM queries, no model.
"""

import re
import statistics
from datetime import date

from sqlalchemy.orm import Session, selectinload

from app.models import Justice, JusticeAppointment, President
from app.ordinals import HIGHEST, RIGHTMOST, rank_shown
from app.photos import justice_photo
from app.schemas import (
    JusticeAppointedBySchema,
    JusticeAppointerSchema,
    JusticeFiguresSchema,
    JusticeIntervalSchema,
    JusticeMeasureSchema,
    JusticePeerPointSchema,
    ReadoutMarkSchema,
)
from app.services.court_service import _position, _positions
from app.services.justice_service import CI_Z
from app.time_utils import utcnow

_SCDB = {"name": "Supreme Court Database and Oyez", "url": "http://scdb.la.psu.edu"}
_MQ = {"name": "Martin-Quinn scores", "url": "https://mqscores.wustl.edu"}
_FJC = {"name": "Federal Judicial Center and Oyez", "url": "https://www.fjc.gov/history/judges"}
_DAYS_PER_YEAR = 365.25
_MINUS = "−"


def _pct(share: float) -> str:
    return f"{round(share * 100)}%"


def _signed(x: float, digits: int) -> str:
    return f"{'+' if x >= 0 else _MINUS}{abs(x):.{digits}f}"


def _points(share: float) -> str:
    """A share as signed points with a true minus: 0.063 is "+6.3"."""
    return _signed(share * 100, 1)


def _years(years: float) -> str:
    whole = int(years)
    return f"{whole} year{'' if whole == 1 else 's'}"


def _one_vote(j: Justice) -> tuple[int, int]:
    """(cases decided by one vote the justice took part in, those where the
    justice was in the majority). In a one-vote case each vote in the
    majority decided it."""
    close = [v for v in j.votes if v.majority_votes - v.minority_votes == 1]
    return len(close), sum(1 for v in close if v.vote == "majority")


def _tenure(j: Justice, today: date) -> float | None:
    try:
        start = date.fromisoformat((j.date_start or "")[:10])
    except ValueError:
        return None
    end = today
    if not j.is_active and j.date_end:
        try:
            end = date.fromisoformat(j.date_end[:10])
        except ValueError:
            pass
    return max(0.0, (end - start).days / _DAYS_PER_YEAR)


def _measure(
    key: str, question: str, label: str, why: str, unit: str, source: dict,
    subject: Justice, values: dict[str, tuple[float, str] | None], names: dict[str, str],
    record: int | None, sentence: str, median_fmt,
    words: tuple[str, str, str] = ("Higher than", "Highest", "Lowest"),
) -> JusticeMeasureSchema:
    """One figure for `subject` beside every peer's: the peers ascending,
    the median, and how many of the others sit below the subject."""
    have = {jid: v for jid, v in values.items() if v is not None}
    ascending = sorted(v for v, _ in have.values())
    # The hover label's place: left-right for a position, else highest.
    rank_words = RIGHTMOST if unit == "position" else HIGHEST
    peers = sorted(
        (JusticePeerPointSchema(id=jid, name=names[jid], value=v, shown=s,
                                rank=rank_shown(v, ascending, rank_words))
         for jid, (v, s) in have.items()),
        key=lambda p: (p.value, p.name),
    )
    mine = have.get(subject.id)
    median = statistics.median(p.value for p in peers) if peers else None
    below = sum(1 for p in peers if mine is not None and p.id != subject.id and p.value < mine[0])
    others = len(peers) - (1 if mine is not None else 0)
    above = sum(1 for p in peers if mine is not None and p.id != subject.id and p.value > mine[0])
    than, top, bottom = words
    if mine is not None and median is not None and others:
        everyone = f"of the {others + 1} sitting justices"
        place = (f"{top} {everyone}" if below == others
                 else f"{bottom} {everyone}" if above == others
                 else f"Tied for {top.lower()} {everyone}" if above == 0
                 else f"Tied for {bottom.lower()} {everyone}" if below == 0
                 else f"{than} {below} of the other {others} sitting justices")
        sentence = f"{sentence} {place}; median {median_fmt(median)}."
    return JusticeMeasureSchema(
        key=key, question=question, label=label, why=why, unit=unit,
        value=mine[0] if mine else None, shown=mine[1] if mine else None, record=record,
        peers=peers, median=median, median_shown=median_fmt(median) if median is not None else None,
        below=below if mine else None, sentence=sentence, source=source,
    )


def _in_term(p: President, day: str | None) -> bool:
    return bool(day) and p.term_start <= day[:10] and (p.term_end is None or day[:10] < p.term_end)


def _appointing(db: Session, j: Justice) -> President | None:
    """The presidents-table row behind the justice's appointing president:
    the one of that name in office the day the justice took the seat (the
    pipeline's own rule, resolve_appointment), so a president with two
    nonconsecutive terms links the right one; the only one of that name
    when the date is unknown."""
    rows = db.query(President).filter(President.name == j.appointing_president).all()
    return next((p for p in rows if _in_term(p, j.date_start)), rows[0] if len(rows) == 1 else None)


def _appointed_by(db: Session, j: Justice) -> JusticeAppointedBySchema | None:
    if not j.appointing_president:
        return None
    president = _appointing(db, j)
    year = int(j.date_start[:4]) if j.date_start and j.date_start[:4].isdigit() else None
    return JusticeAppointedBySchema(
        id=president.id if president else None, name=j.appointing_president,
        party=j.appointing_party or None, year=year,
    )


def _interval(p: Justice) -> JusticeIntervalSchema:
    low = round(p.appointer_effect - CI_Z * p.appointer_effect_se, 4)
    high = round(p.appointer_effect + CI_Z * p.appointer_effect_se, 4)
    return JusticeIntervalSchema(
        id=p.id, name=p.name, estimate=p.appointer_effect, ci_low=low, ci_high=high,
        estimate_shown=_points(p.appointer_effect), interval_shown=f"{_points(low)} to {_points(high)}",
    )


def _appointer(subject: Justice, peers: list[Justice]) -> JusticeAppointerSchema | None:
    """Every sitting justice's appointer estimate with its 95% interval,
    widest effect last; None when the subject has none."""
    if subject.appointer_effect is None or subject.appointer_effect_se is None:
        return None
    rows = sorted(
        (_interval(p) for p in peers if p.appointer_effect is not None and p.appointer_effect_se is not None),
        key=lambda r: (r.estimate, r.name),
    )
    me = next(r for r in rows if r.id == subject.id)
    votes = (subject.loyalty_votes_in or 0) + (subject.loyalty_votes_out or 0)
    word = "more" if me.estimate >= 0 else "less"
    return JusticeAppointerSchema(
        rows=rows,
        sentence=(f"With the government's side held fixed, {abs(me.estimate) * 100:.1f} points {word} often "
                  f"for the federal government under the appointing president than under others "
                  f"(95% interval {_points(me.ci_low)} to {_points(me.ci_high)}), across {votes} votes."),
        through_term=subject.loyalty_through_term,
        marks=[ReadoutMarkSchema(id=r.id, label=r.name, shown=f"{r.estimate_shown} points",
                                 context=f"95% interval {r.interval_shown}", href=f"/politicians/{r.id}")
               for r in rows],
    )


def justice_figures(db: Session, justice_id: str) -> JusticeFiguresSchema | None:
    subject = db.query(Justice).options(selectinload(Justice.votes)).filter(Justice.id == justice_id).first()
    if subject is None:
        return None
    peers = (db.query(Justice).options(selectinload(Justice.votes))
             .filter(Justice.is_active.is_(True)).all())
    if subject.id not in {p.id for p in peers}:
        peers.append(subject)
    names = {p.id: p.name for p in peers}
    today = utcnow().date()
    terms = sorted({int(v.case_term) for p in peers for v in p.votes if v.case_term.isdigit()})

    def recorded(fn):
        return {p.id: (fn(p) if p.cases_decided else None) for p in peers}

    n = subject.cases_decided
    measures: list[JusticeMeasureSchema] = []
    measures.append(_measure(
        "majority", "How often on the winning side?", "In the majority",
        "A justice often in dissent is outvoted more than most; one rarely in dissent is usually with the Court.",
        "share", _SCDB, subject,
        recorded(lambda p: (p.majority_pct / 100, _pct(p.majority_pct / 100))), names, n,
        f"In the majority in {_pct(subject.majority_pct / 100)} of {n} cases, "
        f"in dissent in {_pct(subject.dissent_pct / 100)}.",
        _pct,
    ))

    def one_vote(p: Justice):
        total, won = _one_vote(p)
        return (won / total, f"{won} of {total}") if total else None

    total, won = _one_vote(subject)
    measures.append(_measure(
        "decisive", "How often on the winning side?", "Majority in one-vote cases",
        "In a case decided by one vote, each vote in the majority decided it: this shows who close cases turn on.",
        "share", _SCDB, subject, {p.id: one_vote(p) for p in peers}, names, total,
        f"In the majority in {won} of the {total} cases decided by one vote." if total
        else "No case decided by one vote in the record.",
        _pct,
    ))
    for key, label, noun, why, attr in (
        ("authoredMajority", "Opinions for the Court", "opinions for the Court",
         "The opinion for the Court states the rule lower courts must follow.", "authored_majority"),
        ("authoredDissent", "Dissents written", "dissents",
         "A dissent sets out the losing side's case, and later Courts sometimes adopt it.", "authored_dissent"),
        ("authoredConcurrence", "Concurrences written", "concurrences",
         "A concurrence agrees with the result but gives other reasons, often narrowing the rule.",
         "authored_concurrence"),
    ):
        k = getattr(subject, attr)
        measures.append(_measure(
            key, "What opinions has the justice written?", label, why, "count", _SCDB, subject,
            recorded(lambda p, a=attr: (float(getattr(p, a)), str(getattr(p, a)))), names, n,
            f"{k} {noun} in {n} cases.", lambda x: f"{x:g}", words=("More than", "Most", "Fewest"),
        ))

    def served(p: Justice):
        years = _tenure(p, today)
        return (years, _years(years)) if years is not None else None

    tenure = {p.id: served(p) for p in peers}
    mine = tenure.get(subject.id)
    since = (subject.date_start or "")[:4]
    measures.append(_measure(
        "tenure", "How long on the Court?", "Years on the Court",
        "Seniority sets the order in which justices speak and vote in conference, and a long tenure shapes more terms of law.",
        "years", _FJC, subject, tenure, names, None,
        f"On the Court since {since}: {mine[1]}." if mine and since else "Start of service not on record.",
        _years, words=("Longer than", "Longest", "Shortest"),
    ))

    mq_term = max((pt[0] for p in peers for pt in _positions(p)), default=None)

    def position(p: Justice):
        pos = _position(p, mq_term)
        return (pos["position"], _signed(pos["position"], 2)) if pos else None

    pos = position(subject)
    measures.append(_measure(
        "position", "Where on the left-right line?", "Martin-Quinn position",
        "Estimated from each vote beside the other justices' votes: negative is liberal, positive conservative.",
        "position", _MQ, subject, {p.id: position(p) for p in peers}, names, None,
        f"{pos[1]} in the {mq_term} term." if pos else "No position for the latest term yet.",
        lambda x: _signed(x, 2), words=("To the right of", "Furthest right", "Furthest left"),
    ))

    return JusticeFiguresSchema(
        appointed_by=_appointed_by(db, subject),
        peers=len([p for p in peers if p.is_active]),
        terms=terms,
        measures=measures,
        appointer=_appointer(subject, peers),
    )


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def _page_for(a: JusticeAppointment, justices: list[Justice]) -> Justice | None:
    """The stored justice an appointment belongs to, by the pipeline's own
    rule (justice_pipeline._appointers): the surname ends the justice's
    name and the first initial begins it, and the justice was serving when
    the appointment began or after (an associate later made chief has one
    page for both)."""
    for j in justices:
        start = (j.date_start or "")[:10]
        ended = (j.date_end or "")[:10]
        # The surname as stored, suffix dropped ("Doe, Jr." is "Doe"); the
        # full name ends with the suffix, so it can't be matched on.
        surname = _name_key((j.last_name or j.name).split(",")[0])
        if (surname.endswith(a.last_key) and j.name[:1].upper() == a.first_initial
                and (not ended or ended >= a.served_from) and (not start or a.served_until is None
                                                               or start <= a.served_until)):
            return j
    return None


def justices_appointed_by(db: Session, president_id: str) -> list[dict] | None:
    """Every Supreme Court appointment the president made, by date, from the
    Federal Judicial Center's directory (JusticeAppointment, stored by the
    justice pipeline), each linked to the justice's page where the site
    holds one; None when there is no such president."""
    president = db.query(President).filter(President.id == president_id).first()
    if president is None:
        return None
    justices = db.query(Justice).all()
    rows = (db.query(JusticeAppointment).filter(JusticeAppointment.president_id == president_id)
            .order_by(JusticeAppointment.served_from).all())
    out = []
    for a in rows:
        page = _page_for(a, justices)
        out.append({"id": page.id if page else None, "name": page.name if page else a.name, "seat": a.seat,
                    "dateStart": a.served_from, "dateEnd": a.served_until,
                    "isActive": a.served_until is None, "photo": justice_photo(page) if page else None})
    return out
