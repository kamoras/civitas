"""Where a member stands among peers, measure by measure.

The member page's "Where they stand" stack and the measures page read this
instead of the composite Representation Score: for each measure (small
gifts, PAC money, breaks with party, bills moved), the member's plain
figure, the figure expected for the seat (the scorer's own seat-adjusted
reference where it has one, else the peer median, and the row says which),
every peer's figure, the member's percentile among them, and a word from a
fixed rule. Nothing here is fitted or learned: the figures are the facts
the scorer already stored beside each member's scores (score_breakdown), and
the word is a percentile rule whose cut points are conventions, stated in
`RULE` and served with every comparison so the methods text can quote them.

The direction words are neutral ("above", "below"), never "better" or
"worse": each measure says which way is which in `direction`, and the word
is earned by distance from peers, not by a view of what is good.

Outside app/pipeline/ on purpose, like score_display: a display rule must
not move the analysis-code fingerprint. Read path only: stored JSON and ORM
queries, no model.
"""

import json
import logging
import statistics
import time
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Representative, Senator
from app.pipeline.analyze.score_calculator import ALGORITHM_VERSION
from app.score_keeping import load_breakdown
from app.score_display import thin_dimensions
from app.services.representative_service import get_representative_score_breakdown
from app.services.senator_service import get_senator_score_breakdown

logger = logging.getLogger(__name__)

# The word's cut points, in percentiles of the peer group, and the distance
# from the expected value (in peer standard deviations) a "well" word also
# needs. Conventions, not fitted values: CEL's "exceeds / meets / below
# expectations" shape with the quartiles and deciles a reader already knows.
# A row whose own record is shorter than `minRecord` (party-line votes, for
# breaks) draws no word, as does one whose dimension the scorer graded "low".
RULE = {"wellOutside": [10, 90], "outside": [25, 75], "wellGapSd": 1.0, "minRecord": 20}

_PARTY_PLURAL = {"D": "Democrats", "R": "Republicans"}

# A comparison stays valid while its chamber's rows and the scoring version
# are unchanged; the hour is a backstop for inputs a row's timestamp
# doesn't see (a reference file the API recomputes against).
_CACHE_TTL_S = 3600
_cache: dict[str, tuple[tuple, float, list[dict]]] = {}


@dataclass(frozen=True)
class Measure:
    key: str
    label: str
    dimension: str
    direction: str
    unit: str
    source: dict
    value: Callable[[dict], float | None]
    seat_expected: Callable[[dict], float | None]
    record: Callable[[dict], int | None]


def _funding(facts: dict) -> dict:
    return facts.get("fundingIndependence") or {}


def _alignment(facts: dict) -> dict:
    return facts.get("constituentAlignment") or {}


def _bills(facts: dict) -> dict:
    return facts.get("legislativeEffectiveness") or {}


def _past_committee(facts: dict) -> int | None:
    stages = _bills(facts).get("billsByStage")
    # [introduced only, committee action, past committee, passed the chamber, law]
    return sum(stages[2:]) if isinstance(stages, list) and len(stages) == 5 else None


_FEC = {"name": "FEC filings", "url": "https://www.fec.gov/data/"}
MEASURES = (
    Measure("smallGifts", "Small gifts", "fundingIndependence",
            "higher means more of the money came in gifts of $200 or less", "share", _FEC,
            lambda f: _funding(f).get("smallDonorShare"),
            # The House compares with the chamber median, not a seat fit: no
            # seat expectation then, so the row's tick is the peer median.
            lambda f: (_funding(f).get("smallDonorExpectedShare")
                       if _funding(f).get("smallDonorComparison") != "house-median" else None),
            lambda f: 1 if _funding(f).get("contributions") else 0),
    Measure("pacMoney", "PAC money", "fundingIndependence",
            "higher means more of the money came from PACs", "share", _FEC,
            lambda f: _funding(f).get("pacShare") if _funding(f).get("contributions") else None,
            lambda f: None,
            lambda f: 1 if _funding(f).get("contributions") else 0),
    Measure("breaks", "Breaks with party", "constituentAlignment",
            "higher means more breaks with the party on party-line votes", "rate",
            {"name": "Roll-call votes", "url": "https://voteview.com/"},
            lambda f: _alignment(f).get("breakRate"),
            lambda f: _alignment(f).get("expectedBreakRate"),
            lambda f: _alignment(f).get("partyVotes")),
    Measure("billsMoved", "Bills moved", "legislativeEffectiveness",
            "higher means more of the member's bills got past committee", "count",
            {"name": "Congress.gov", "url": "https://www.congress.gov/"},
            _past_committee,
            lambda f: None,
            lambda f: _bills(f).get("bills")),
)


def _model(chamber: str):
    if chamber == "senate":
        return Senator
    if chamber == "house":
        return Representative
    raise ValueError(f"no peer comparison for {chamber!r}")


def _live_breakdown(db: Session, chamber: str, member_id: str) -> dict | None:
    # A member whose run stored no breakdown (scored before breakdowns were
    # kept) is recomputed once per cache key, as the breakdown page does.
    # ponytail: recompute per missing member; drop once every row is kept.
    breakdown_of = get_senator_score_breakdown if chamber == "senate" else get_representative_score_breakdown
    try:
        return breakdown_of(db, member_id)
    except Exception:
        logger.warning("Peer comparison: no breakdown for %s", member_id, exc_info=True)
        return None


def chamber_rows(db: Session, chamber: str) -> list[dict]:
    """Every serving member of `chamber`: {id, name, party, caucus, state,
    district, thin, facts}, from the facts the scorer stored beside their
    scores. Cached per process while the chamber's rows are unchanged."""
    model = _model(chamber)
    count, newest = db.query(func.count(model.id), func.max(model.updated_at)).filter(
        model.is_current.is_(True)).one()
    key = (count, str(newest), ALGORITHM_VERSION)
    hit = _cache.get(chamber)
    if hit and hit[0] == key and time.monotonic() - hit[1] < _CACHE_TTL_S:
        return hit[2]
    rows = []
    for m in db.query(model).filter(model.is_current.is_(True)).all():
        kept, _ = load_breakdown(m)
        dims = kept or _live_breakdown(db, chamber, m.id) or {}
        facts = {k: (v or {}).get("facts") or {} for k, v in dims.items() if isinstance(v, dict)}
        rows.append({
            "id": m.id,
            "name": m.name,
            "party": m.party,
            "caucus": m.caucus_party or m.party,
            "state": m.state,
            "district": getattr(m, "district", None),
            "thin": thin_dimensions(_confidence(m)),
            "facts": facts,
        })
    _cache[chamber] = (key, time.monotonic(), rows)
    return rows


def _confidence(member) -> dict:
    try:
        return json.loads(member.score_confidence or "{}") or {}
    except (TypeError, ValueError):
        return {}


def percentile(value: float, values: list[float]) -> float:
    """Mid-rank percentile of `value` in `values` (which include it): the
    share below, plus half the ties, 0-100."""
    below = sum(1 for v in values if v < value)
    ties = sum(1 for v in values if v == value)
    return 100 * (below + ties / 2) / len(values)


def peer_word(pct: float, gap_sd: float | None) -> str:
    """The row's word from RULE: "well above/below peers" outside the
    deciles and more than `wellGapSd` from the expected value, "above/below
    peers" outside the quartiles, else "near peers"."""
    lo, hi = RULE["wellOutside"]
    side = "above" if pct > 50 else "below"
    if (pct > hi or pct < lo) and gap_sd is not None and abs(gap_sd) > RULE["wellGapSd"]:
        return f"well {side} peers"
    lo, hi = RULE["outside"]
    if pct > hi or pct < lo:
        return f"{side} peers"
    return "near peers"


def _shown(measure: Measure, value: float, facts: dict) -> str:
    if measure.unit == "share":
        return "under 1%" if 0 < value < 0.005 else f"{round(value * 100)}%"
    if measure.key == "breaks":
        a = _alignment(facts)
        return f"{a.get('breaks', 0):,} of {a.get('partyVotes', 0):,}"
    return f"{int(value):,} of {_bills(facts).get('bills', 0):,}"


def _group_label(chamber: str, group: str, caucus: str, members: list[dict]) -> str:
    house = chamber == "house"
    if group == "chamber":
        return "representatives" if house else "senators"
    plural = _PARTY_PLURAL.get(caucus, "members")
    independents = any(m["party"] not in ("D", "R") for m in members)
    return f"{'House' if house else 'Senate'} {plural}{' and independents' if independents else ''}"


# Template sentences, about grade 8 and subjectless (never a pronoun):
# the fact, then how many of the other peers sit on the far side of it.
_FACT = {
    "smallGifts": "Gifts of $200 or less were {shown} of the money.",
    "pacMoney": "PACs gave {shown} of the money.",
    "breaks": "Broke with the party on {shown} party-line votes.",
    "billsMoved": "{shown} bills got past committee.",
}
_MORE = {"smallGifts": "had a larger share", "pacMoney": "took a larger share",
         "breaks": "broke more often", "billsMoved": "moved more"}
_LESS = {"smallGifts": "had a smaller share", "pacMoney": "took a smaller share",
         "breaks": "broke less often", "billsMoved": "moved fewer"}


def _row(measure: Measure, me: dict, peers: list[dict], chamber: str, group: str) -> dict:
    facts = me["facts"]
    value = measure.value(facts)
    names = {p["id"]: p["name"] for p in peers}
    pairs = [(p["id"], measure.value(p["facts"])) for p in peers]
    pairs = sorted([(i, v) for i, v in pairs if v is not None], key=lambda x: x[1])
    label = _group_label(chamber, group, me["caucus"], peers)
    values = [v for _, v in pairs]
    record = measure.record(facts)
    median = statistics.median(values) if values else None
    seat = measure.seat_expected(facts)
    expected, basis = (seat, "seat") if seat is not None else (median, "peer median")
    row = {
        "key": measure.key,
        "label": measure.label,
        "direction": measure.direction,
        "unit": measure.unit,
        "value": value,
        "shown": None if value is None else _shown(measure, value, facts),
        "expected": expected,
        "expectedBasis": basis,
        "median": median,
        "peerGroup": {"key": group, "label": label, "n": len(values)},
        "distribution": values,
        "distributionIds": [i for i, _ in pairs],
        "distributionNames": [names[i] for i, _ in pairs],
        "record": record,
        "source": measure.source,
        "percentile": None,
        "word": None,
        "sentence": None,
        "thin": True,
    }
    thin = (value is None or len(values) < RULE["minRecord"] or measure.dimension in me["thin"]
            or (measure.key == "breaks" and (record or 0) < RULE["minRecord"]))
    if value is None:
        return row
    others = [v for i, v in pairs if i != me["id"]]
    more, less = sum(1 for v in others if v > value), sum(1 for v in others if v < value)
    pct = percentile(value, values)
    spread = statistics.pstdev(values) if len(values) > 1 else 0
    gap = (value - expected) / spread if spread and expected is not None else None
    fact = _FACT[measure.key].format(shown=row["shown"])
    if measure.key == "billsMoved":
        fact = fact[0].upper() + fact[1:]
    n_side, verb = (more, _MORE[measure.key]) if pct <= 50 else (less, _LESS[measure.key])
    row.update({
        "percentile": round(pct, 1),
        "word": None if thin else peer_word(pct, gap),
        "sentence": f"{fact} {n_side:,} of the other {len(others):,} {label} {verb}.",
        "thin": thin,
        "gapSd": None if gap is None else round(gap, 2),
    })
    return row


def peer_comparison(db: Session, chamber: str, member_id: str, group: str = "caucus") -> dict | None:
    """The member's rows, one per measure, against `group` ("caucus": the
    chamber's members who caucus with the member's party, independents
    with theirs; "chamber": every serving member), and the one line that
    stands out. None for a member who isn't serving in `chamber`."""
    rows = chamber_rows(db, chamber)
    me = next((r for r in rows if r["id"] == member_id), None)
    if me is None:
        return None
    peers = rows if group == "chamber" else [r for r in rows if r["caucus"] == me["caucus"]]
    measures = [_row(m, me, peers, chamber, group) for m in MEASURES]
    return {"group": group, "rule": RULE, "measures": measures, "standsOut": stands_out(measures)}


def stands_out(measures: list[dict]) -> str:
    """The one line above the stack: the measure furthest from the middle of
    its peers, in percentile terms, when its word is a "well" word; else
    that the member is close to peers on every measure."""
    strong = [m for m in measures if (m.get("word") or "").startswith("well")]
    if not strong:
        return "Close to peers on every measure."
    top = max(strong, key=lambda m: abs(m["percentile"] - 50))
    return f"{top['label']}: {top['sentence']}"
