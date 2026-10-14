"""How often each member votes with the president's stated position.

Informational, never part of a score (it is not in SCORE_WEIGHTS and no
scoring module reads it).

The president's position on a vote comes only from an official, citable
statement tied to that measure: OMB's Statements of Administration Policy
(fetch/omb_statements.py), each addressed to one chamber, its position read
by analyze/sap_position.py. A roll call counts when all of these hold:

  1. It is the chamber's final vote on the measure (passage, adoption,
     concurrence, a veto override), or a vote on an amendment a statement
     names by number. The question is read from the chamber's own question
     vocabulary (FINAL_QUESTIONS below), a documented data format like the
     result vocabulary fetch/congress.roll_call_rejected reads. Procedural
     votes (cloture, motions to proceed, discharge, table, recommit) are
     left out: no statement addresses them.
  2. A statement addressed to that chamber covers the measure, issued on or
     before the vote. The latest such statement applies, and only until any
     later statement on the measure in either chamber: a newer statement can
     mean the measure, or the Administration's view of it, changed.
  3. That statement takes a position on passage (support or oppose).

A Yea on a counted question advances the measure, so the president's side
is Yea when the statement supports it and Nay when it opposes it. Present
and not voting are not votes either way.

The expectation. Each run fits, per chamber and per party, the share of
votes members cast with the president's position against their seat's
party-signed lean (score_calculator._signed_state_alignment, the measure
Constituent Alignment uses): a fractional-response logit (Papke &
Wooldridge 1996), each member weighted by their number of counted votes,
kinked at a swing seat when enough members of the party hold seats leaning
the other way. The members being described are the population (AGENTS.md
§3a, "measure it in the run"). A member's expected rate is their party's fit
at their seat's lean. No shrinkage is applied to either rate.
"""

import json
import logging
import math
import re
from collections import defaultdict

import httpx
import numpy as np
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    PolicyStatement, PresidentialSupport, Representative, RollCall, RollCallPosition, Senator, StatementRollCall,
)
from app.pipeline.analyze import sap_position
from app.pipeline.analyze.population_reference import ChamberReference
from app.pipeline.analyze.score_calculator import (
    _MIN_CONSTITUENT_REFERENCE_PARTY,
    _MIN_OPPOSED_SEATS_FOR_KINK,
    _fractional_logit,
    _signed_state_alignment,
)
from app.pipeline.fetch import omb_statements
from app.pipeline.fetch.floor_logs import NAY_POSITIONS, YEA_POSITIONS
from app.services.bill_record import MemberLinks
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

PRESIDENTIAL_SUPPORT_REFERENCE = ChamberReference("presidential_support_reference", required=False)

# Each chamber's wording of a question whose Yea passes, adopts or enacts the
# measure (clerk.house.gov <vote-question>, senate.gov <vote_question_text>,
# which appends the document: "On Passage of the Bill H.R. 5334"). Matched
# as a prefix. A data-format convention, not a classification.
FINAL_QUESTIONS = {
    "house": (
        "On Passage",
        "Passage, Objections of the President",
        "On Motion to Suspend the Rules and Pass",
        "On Motion to Suspend the Rules and Agree",
        "On Motion to Suspend the Rules and Concur",
        "On Agreeing to the Resolution",
        "On Motion to Concur in the Senate Amendment",
    ),
    "senate": (
        "On Passage of the Bill",
        "On the Joint Resolution",
        "On the Resolution",
        "On the Concurrent Resolution",
        "On Overriding the Veto",
        "On the Motion (Motion to Concur",
    ),
}
_AMENDMENT_QUESTIONS = ("On the Amendment", "On Agreeing to the Amendment")
# The amendment voted on: the first "Amdt. N" in a Senate question, or the
# House Clerk's "Amendment No. N" in the title.
_AMENDMENT_NUMBER_RE = re.compile(r"Amdt\.\s*(\d+)|Amendment No\.\s*(\d+)")
# 95% normal quantile for the Wilson score interval.
_Z95 = 1.959964


# ── Statements ────────────────────────────────────────────────────

async def refresh_statements(client: httpx.AsyncClient, db: Session) -> dict:
    """Read new statements from OMB's index (and any stored under an older
    parser or classifier). {"status": "ok" | "index unavailable", "read": n}.
    An unreadable index leaves the stored statements as they are."""
    entries = await omb_statements.fetch_index(client)
    if entries is None:
        return {"status": "index unavailable", "read": 0}
    stored = {s.url: s for s in db.query(PolicyStatement)}
    read = 0
    for entry in entries:
        row = stored.get(entry["url"])
        if row is not None and row.parser_version == omb_statements.PARSER_VERSION and (
            not row.readable or row.classifier_version == sap_position.CLASSIFIER_VERSION
        ):
            continue
        raw = await omb_statements.fetch_statement_pdf(client, entry["url"])
        if raw is None:
            continue  # a failed fetch is retried next run, never stored as unreadable
        parsed = omb_statements.read_statement(raw)
        if row is None:
            row = PolicyStatement(url=entry["url"])
            db.add(row)
        row.title = entry["title"]
        row.parser_version = omb_statements.PARSER_VERSION
        row.fetched_at = utcnow()
        row.readable = parsed is not None
        if parsed is None:
            logger.warning("OMB statement %s did not read as a SAP", entry["url"])
        else:
            position, sentence = sap_position.classify(parsed["opening"], parsed["closing"])
            row.issued, row.chamber = parsed["issued"], parsed["chamber"]
            row.measures, row.amendments = json.dumps(parsed["measures"]), json.dumps(parsed["amendments"])
            row.position, row.position_sentence = position, sentence
            row.classifier_version = sap_position.CLASSIFIER_VERSION
        db.commit()
        read += 1
    return {"status": "ok", "read": read}


# ── Which roll calls a statement applies to ───────────────────────

def is_final_question(chamber: str, question: str | None) -> bool:
    return (question or "").startswith(FINAL_QUESTIONS.get(chamber, ()))


def amendment_number(question: str | None, title: str | None) -> str | None:
    """The amendment a roll call votes on, or None for any other question."""
    if not (question or "").startswith(_AMENDMENT_QUESTIONS):
        return None
    m = _AMENDMENT_NUMBER_RE.search(question or "") or _AMENDMENT_NUMBER_RE.search(title or "")
    return (m.group(1) or m.group(2)) if m else None


def applicable_statement(roll_call: dict, statements: list[dict]) -> dict | None:
    """The statement whose position applies to a roll call, by the rules in
    the module docstring, or None.

    roll_call: {"chamber", "bill_id", "date", "question", "title"};
    statements: {"chamber", "issued", "measures", "amendments", ...}, every
    readable statement (either chamber)."""
    bill, day, chamber = roll_call.get("bill_id"), roll_call.get("date") or "", roll_call["chamber"]
    if not bill or not day:
        return None
    on_bill = [s for s in statements if bill in s["measures"]]
    amendment = amendment_number(roll_call.get("question"), roll_call.get("title"))
    if amendment is not None:
        named = [s for s in on_bill if s["chamber"] == chamber and amendment in s["amendments"] and s["issued"] <= day]
        return max(named, key=lambda s: s["issued"], default=None)
    if not is_final_question(chamber, roll_call.get("question")):
        return None
    on_measure = [s for s in on_bill if not s["amendments"]]
    addressed = [s for s in on_measure if s["chamber"] == chamber and s["issued"] <= day]
    latest = max(addressed, key=lambda s: s["issued"], default=None)
    if latest is None:
        return None
    if any(latest["issued"] < s["issued"] <= day for s in on_measure):
        return None
    return latest


def match_roll_calls(db: Session, chamber: str, congress: int) -> int:
    """Rewrite the chamber's statement_roll_calls for the Congress. Returns
    how many roll calls a statement applies to (whatever its position)."""
    statements = [
        {"id": s.id, "chamber": s.chamber, "issued": s.issued, "measures": json.loads(s.measures or "[]"),
         "amendments": json.loads(s.amendments or "[]")}
        for s in db.query(PolicyStatement).filter(PolicyStatement.readable.is_(True))
    ]
    measures = {m for s in statements for m in s["measures"]}
    rolls = db.query(RollCall).filter(
        RollCall.chamber == chamber, RollCall.congress == congress, RollCall.bill_id.in_(measures),
    ).all() if measures else []
    db.query(StatementRollCall).filter(
        StatementRollCall.roll_call_id.in_(db.query(RollCall.id).filter_by(chamber=chamber, congress=congress))
    ).delete()
    matched = 0
    for rc in rolls:
        found = applicable_statement(
            {"chamber": chamber, "bill_id": rc.bill_id, "date": rc.date, "question": rc.question, "title": rc.title},
            statements,
        )
        if found is not None:
            db.add(StatementRollCall(roll_call_id=rc.id, statement_id=found["id"]))
            matched += 1
    db.commit()
    return matched


def with_president(position: str | None, stance: str) -> bool | None:
    """Whether a recorded position is the president's side of a counted
    question: True / False, None when it is not a vote either way."""
    cast = (position or "").strip().lower()
    if cast in YEA_POSITIONS:
        return stance == "support"
    if cast in NAY_POSITIONS:
        return stance == "oppose"
    return None


def counted_votes(db: Session, chamber: str, congress: int) -> list[tuple[RollCall, PolicyStatement]]:
    """(roll call, statement) for each counted roll call: matched to a
    statement that takes a position."""
    return db.query(RollCall, PolicyStatement).join(
        StatementRollCall, StatementRollCall.roll_call_id == RollCall.id,
    ).join(PolicyStatement, PolicyStatement.id == StatementRollCall.statement_id).filter(
        RollCall.chamber == chamber, RollCall.congress == congress,
        PolicyStatement.position.in_(("support", "oppose")),
    ).order_by(RollCall.date.desc(), RollCall.number.desc()).all()


# ── Per member ────────────────────────────────────────────────────

def member_tallies(db: Session, chamber: str, counted: list[tuple[RollCall, PolicyStatement]]) -> dict[str, list[int]]:
    """{Civitas member id: [votes with the president, votes]}. House
    positions carry the bioguide id; Senate positions a last name, state
    and LIS id, resolved the way the vote pages resolve them (MemberLinks)."""
    stance = {rc.id: s.position for rc, s in counted}
    links = MemberLinks(db)
    tallies: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for p in db.query(RollCallPosition).filter(RollCallPosition.roll_call_id.in_(list(stance))):
        side = with_president(p.position, stance[p.roll_call_id])
        if side is None:
            continue
        page = (links.page(bioguide=p.member_id) if chamber == "house"
                else links.page(last_name=p.last_name, state=p.state, lis_id=p.member_id))
        if page is None:
            continue
        t = tallies[page.rsplit("/", 1)[-1]]
        t[0] += int(side)
        t[1] += 1
    return dict(tallies)


def _inverse_logit(x: float) -> float:
    return 0.5 * (1.0 + math.tanh(0.5 * x))  # overflow-safe


def expected_rate(fit: dict | None, alignment: float | None) -> float | None:
    if not fit or alignment is None:
        return None
    return _inverse_logit(fit["a"] + fit["b"] * alignment + fit.get("b_opposed", 0.0) * min(alignment, 0.0))


def fit_expectation(rows: list[tuple[str, float, int, int]]) -> dict[str, dict]:
    """Per party (D, R), the fractional-logit fit of the share of votes cast
    with the president on party-signed seat alignment, each member weighted
    by their votes: {"a", "b", "b_opposed", "members", "votes"}. A party with
    too few members, or whose rates admit no finite fit (every member at 100%,
    say), gets no fit. rows: (party, alignment, with, votes)."""
    fits = {}
    for party in ("D", "R"):
        mine = [(al, w / n, n) for p, al, w, n in rows if p == party and n > 0]
        if len(mine) < _MIN_CONSTITUENT_REFERENCE_PARTY:
            continue
        al = np.array([r[0] for r in mine])
        y = np.array([r[1] for r in mine])
        n = np.array([r[2] for r in mine], dtype=float)
        bends = (True, False) if int((al < 0).sum()) >= _MIN_OPPOSED_SEATS_FOR_KINK else (False,)
        for bend in bends:
            cols = [np.ones_like(al), al] + ([np.minimum(al, 0.0)] if bend else [])
            coef = _fractional_logit(np.column_stack(cols), y, n)
            if coef is not None:
                fits[party] = {
                    "a": round(float(coef[0]), 5), "b": round(float(coef[1]), 5),
                    "b_opposed": round(float(coef[2]), 5) if bend else 0.0,
                    "members": len(mine), "votes": int(n.sum()),
                }
                break
    return fits


def wilson_interval(successes: int, n: int, z: float = _Z95) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion (Wilson 1927; Agresti
    & Coull 1998 on why it, not the Wald interval p ± z·sqrt(p(1-p)/n),
    covers near 0% and 100%, where most members' rates sit)."""
    if n <= 0:
        return None
    p = successes / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def compute_chamber(db: Session, chamber: str, congress: int) -> dict:
    """Match, tally, fit and store one chamber. Returns a summary."""
    matched = match_roll_calls(db, chamber, congress)
    counted = counted_votes(db, chamber, congress)
    tallies = member_tallies(db, chamber, counted)
    model = Senator if chamber == "senate" else Representative
    members = {m.id: m for m in db.query(model).filter(model.id.in_(list(tallies)))} if tallies else {}
    rows = []
    for member_id, (w, n) in tallies.items():
        m = members.get(member_id)
        if m is None:
            continue
        party = (m.caucus_party or m.party or "?")[:1]
        alignment = _signed_state_alignment(
            m.state, m.party, effective_party=m.caucus_party,
            district=getattr(m, "district", None) if chamber == "house" else None,
        ) if party in ("D", "R") else None
        rows.append((member_id, party, alignment, w, n))
    fits = fit_expectation([(p, al, w, n) for _, p, al, w, n in rows if al is not None])
    stored = {r.member_id: r for r in db.query(PresidentialSupport).filter_by(chamber=chamber)}
    now = utcnow()
    for member_id, party, alignment, w, n in rows:
        row = stored.pop(member_id, None) or PresidentialSupport(chamber=chamber, member_id=member_id)
        row.congress, row.party, row.seat_alignment = congress, party, alignment
        row.with_president, row.votes, row.computed_at = w, n, now
        row.expected = expected_rate(fits.get(party), alignment)
        db.add(row)
    for gone in stored.values():  # no counted vote any more (a new Congress)
        db.delete(gone)
    db.commit()
    reference = {
        "congress": congress, "rollCalls": len(counted), "matched": matched,
        "statements": len({s.id for _, s in counted}), "expected": fits,
    }
    PRESIDENTIAL_SUPPORT_REFERENCE.write(chamber, reference)
    return {**reference, "members": len(rows)}


async def run_presidential_support(client: httpx.AsyncClient, db: Session) -> dict:
    """The pipeline step: read new statements, then rebuild both chambers."""
    statements = await refresh_statements(client, db)
    congress = settings.CURRENT_CONGRESS
    out = {"statements": statements}
    for chamber in ("senate", "house"):
        out[chamber] = compute_chamber(db, chamber, congress)
    return out
