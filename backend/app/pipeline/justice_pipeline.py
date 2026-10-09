"""Justice scorecard pipeline: the voting record, and the appointing
president's effect on each justice's votes from the Supreme Court Database
(shown, not scored: justice v3, justice_loyalty)."""

import csv
import gzip
import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.contact import BOT_USER_AGENT
from app.http_client import make_async_client
from app.models import President
from app.pipeline.analyze.justice_analyzer import analyze_justice_votes
from app.pipeline.analyze.justice_loyalty import Estimate, Vote, estimates_by_justice, label, president_on
from app.pipeline.fetch.justice_records import RECENT_TERMS, fetch_fjc, fetch_martin_quinn, fetch_scdb
from app.pipeline.fetch.justice_votes import fetch_case_votes, fetch_current_justices
from app.services.justice_service import group_votes_by_case_and_justice, upsert_justice
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

# Epstein & Posner's hand-coded votes (their Solicitor General tiebreak)
# through the 2014 term, written by scripts/research_justice_loyalty.py;
# the Supreme Court Database's lead parties from 2015 on.
_BUNDLE = Path(__file__).resolve().parents[1] / "data" / "justice_president_votes_1937_2014.csv.gz"
_BUNDLE_LAST_TERM = 2014


def _bundled_rows() -> dict[str, list[tuple[int, int, int]]]:
    rows: dict[str, list[tuple[int, int, int]]] = {}
    with gzip.open(_BUNDLE, "rt", newline="") as f:
        for r in csv.DictReader(f):
            rows.setdefault(r["justice"], []).append(
                (int(r["for_government"]), int(r["appointer_in_office"]), int(r["government_petitioner"])),
            )
    return rows


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def _appointers(names: set[str], appointments: list[dict], terms: list[tuple[str, str, str | None]]) -> dict:
    """Each Database justice's appointments as (appointing president, from,
    until): the FJC appointment whose surname ends the Database's name and
    whose initial begins it, appointed by whoever was president on the
    nomination date."""
    out = {}
    for name in names:
        key = _name_key(name)
        spans = [
            (president_on(a["nominated"], terms), a["from"], a["until"])
            for a in appointments if a["last"] and key.endswith(a["last"]) and name[:1] == a["first"]
        ]
        out[name] = [s for s in spans if s[0]]
    return out


async def _measure_loyalty(client: httpx.AsyncClient, db: Session) -> tuple[dict | None, str | None]:
    """({"loyalty": {database name: Estimate}, "term", "current", "ideal"},
    None), or (None, what couldn't be read) when a source is down: the
    stored values then stand, and the reason goes into the run's alert,
    which outlives the logs."""
    scdb = await fetch_scdb(client, db)
    appointments = await fetch_fjc(client, db)
    terms = [(p.name, p.term_start, p.term_end) for p in db.query(President).order_by(President.term_start)]
    down = [name for name, missing in (
        ("the Supreme Court Database", scdb is None),
        ("the FJC judges file", appointments is None),
        ("the presidents table", not terms),
    ) if missing]
    if down:
        why = " and ".join(down) + " could not be read"
        logger.warning("Justice loyalty not measured: %s", why)
        return None, why
    votes = [Vote(j, d, pet, gov) for j, d, pet, gov, term in scdb["votes"] if term > _BUNDLE_LAST_TERM]
    rows = _bundled_rows()
    for justice, labeled in label(votes, _appointers({v.justice for v in votes}, appointments, terms), terms).items():
        rows.setdefault(justice, []).extend(labeled)
    loyalty = estimates_by_justice(rows)
    logger.info("Justice appointer effect: %d justices estimated (%s)", len(loyalty), scdb["release"])
    return {"loyalty": loyalty, "term": scdb["term"], "current": scdb["current"], "cases": scdb.get("cases") or [],
            # None when the Martin-Quinn file couldn't be read: the stored
            # positions stay (an outage read as {} erased every justice's).
            "ideal": await fetch_martin_quinn(client, db)}, None


def scdb_vote_records(cases: list[list], justices: list[dict], terms: set[int]) -> list[dict]:
    """The Database's votes in `terms` for the sitting justices, in the
    record shape fetch_case_votes gives from Oyez (scdb_case_votes)."""
    names = sorted({c[5] for c in cases})
    ids = {name: j["id"] for j in justices if (name := _database_name(j, names))}
    out = []
    for case_id, docket, name, term, decided, justice, side, opinion, maj, mnr in cases:
        if term not in terms or justice not in ids:
            continue
        out.append({
            "case_id": f"scotus-{term}-{docket or case_id}", "case_name": name, "case_term": str(term),
            "decided_date": decided, "justice_id": ids[justice], "vote": side, "opinion_type": opinion,
            "is_unanimous": mnr == 0 and maj > 0, "is_close": mnr > 0 and maj - mnr <= 1,
            "majority_votes": maj, "minority_votes": mnr,
        })
    return out


def _database_name(justice: dict, current: list[str]) -> str | None:
    """The Database's name for an Oyez justice: the justice of its newest
    term whose name ends with the surname and begins with the initial."""
    last, first = _name_key(justice.get("last_name") or ""), (justice.get("name") or "")[:1]
    matches = [n for n in current if last and _name_key(n).endswith(last) and n[:1] == first]
    return matches[0] if len(matches) == 1 else None


def _loyalty_fields(e: Estimate | None, term: int, ideal: list | None, ideal_read: bool = True) -> dict:
    """The stored fields for one justice. Never a score (justice v3): the
    v2 score and its shrunk estimate are cleared so no stale number
    outlives the change; the justice's own estimate and its standard error
    are stored, NULL for a justice the Database doesn't cover yet."""
    fields = {"loyalty_through_term": term, "score_loyalty": None, "loyalty": None, "loyalty_se": None}
    if ideal_read:
        fields["ideal_points"] = json.dumps(ideal) if ideal else None
    if e is None:
        return {**fields, "appointer_effect": None, "appointer_effect_se": None,
                "loyalty_votes_in": None, "loyalty_votes_out": None,
                "loyalty_rate_in": None, "loyalty_rate_out": None}
    return {
        **fields, "appointer_effect": round(e.raw, 4), "appointer_effect_se": round(e.se, 4),
        "loyalty_votes_in": e.votes_in, "loyalty_votes_out": e.votes_out,
        "loyalty_rate_in": round(e.rate_in, 4), "loyalty_rate_out": round(e.rate_out, 4),
    }


# Only when the justice's start date is unknown: Oyez and UCSB spell a
# president's name nearly alike ("Donald J. Trump" / "Donald Trump": 0.92
# as keyed below), but not always the same one — UCSB lists the 41st
# president as "George Bush", and Oyez's "George H. W. Bush" scored 0.93
# against "George W. Bush", so the name made the 43rd president every Bush
# appointee's (three justices, until 2026-10-08). The term dates can't be
# ambiguous. A name resolves only when its best match is near-exact AND
# clearly ahead of the runner-up.
_NAME_MATCH_MIN = 0.9
_NAME_MATCH_MARGIN = 0.05


def _president_key(name: str) -> str:
    return " ".join(re.sub(r"[^a-z ]", " ", name.lower()).split())


def resolve_appointment(
    appointing: str, date_start: str | None, presidents: list[President],
) -> tuple[str, str]:
    """(appointing president's name, party) from the presidents table
    (UCSB's roster, president_pipeline) — never a hand-typed list, so a new
    president is known the night the roster names them.

    The president in office on the day the justice took the seat; Oyez's
    name for the appointing president, matched by name, only when the date
    is unknown. ("", "") when neither resolves, which the site shows as no
    party."""
    if date_start:
        in_office = president_on(date_start, [(p.id, p.term_start, p.term_end) for p in presidents if p.term_start])
        for p in presidents:
            if p.id == in_office:
                return p.name, p.party or ""
    if appointing:
        key = _president_key(appointing)
        scored = sorted(
            ((SequenceMatcher(None, key, _president_key(p.name)).ratio(), p) for p in presidents),
            key=lambda t: t[0], reverse=True,
        )
        if scored and scored[0][0] >= _NAME_MATCH_MIN and (
            len(scored) == 1 or scored[0][0] - scored[1][0] >= _NAME_MATCH_MARGIN
        ):
            return scored[0][1].name, scored[0][1].party or ""
    return appointing or "", ""


async def run_justice_pipeline(db: Session) -> dict:
    """Fetch, analyze, and persist Supreme Court justice scorecards.

    Returns summary dict with counts.
    """
    logger.info("=== Justice pipeline starting ===")

    async with make_async_client(
        headers={"User-Agent": BOT_USER_AGENT},
        follow_redirects=True,
    ) as client:
        justices = await fetch_current_justices(client)
        if not justices:
            logger.warning("No justices found, aborting pipeline")
            return {"justices": 0, "votes": 0, "loyalty_unmeasured": "Oyez listed no sitting justices"}

        measured, unmeasured_why = await _measure_loyalty(client, db)
        # The record's terms: the Supreme Court Database for every term its
        # release covers, Oyez only after it. Oyez had entered the votes of
        # 13 of the 2024 term's 59 decided cases by 2026-10-08, so its
        # counts, shares and agreement rates read a fraction of that term;
        # the Database has all 60 orally argued cases.
        year = utcnow().year
        window = {year - i for i in range(RECENT_TERMS)}
        from_scdb = window & {c[3] for c in (measured or {}).get("cases") or []}
        all_votes = scdb_vote_records(measured["cases"], justices, from_scdb) if from_scdb else []
        rest = sorted(window - from_scdb, reverse=True)
        if rest:
            all_votes += await fetch_case_votes(client, [str(t) for t in rest])

    case_votes, justice_votes = group_votes_by_case_and_justice(all_votes)

    active_ids = {j["id"] for j in justices}
    presidents = db.query(President).all()

    for j in justices:
        jid = j["id"]
        jvotes = justice_votes.get(jid, [])

        analysis = analyze_justice_votes(jid, jvotes, dict(case_votes), active_ids)
        appointing, party = resolve_appointment(
            j.get("appointing_president") or "", j.get("date_start"), presidents,
        )

        record = {
            "id": jid,
            "name": j["name"],
            "last_name": j.get("last_name", ""),
            "role_title": j.get("role_title", "Associate Justice"),
            "appointing_president": appointing,
            "appointing_party": party,
            "date_start": j.get("date_start"),
            "date_end": j.get("date_end"),
            "is_active": j.get("is_active", True),
            "thumbnail_url": j.get("thumbnail_url", ""),
            "cases_decided": analysis["cases_decided"],
            "majority_pct": analysis["majority_pct"],
            "dissent_pct": analysis["dissent_pct"],
            "unanimous_pct": analysis["unanimous_pct"],
            "authored_majority": analysis["authored_majority"],
            "authored_dissent": analysis["authored_dissent"],
            "authored_concurrence": analysis["authored_concurrence"],
            "close_case_majority_pct": analysis["close_case_majority_pct"],
            "agreement_matrix": json.dumps(analysis["agreement_matrix"]),
            "summary": "",
        }
        if measured is not None:
            name = _database_name(j, measured["current"])
            ideal = measured["ideal"]
            record.update(_loyalty_fields(
                measured["loyalty"].get(name) if name else None, measured["term"],
                ideal.get(name) if name and ideal else None, ideal_read=ideal is not None,
            ))

        upsert_justice(db, record, jvotes)
        logger.info("  %s: appointer effect %s, cases=%d", j["name"], record.get("appointer_effect"),
                    analysis["cases_decided"])

    db.commit()
    logger.info("=== Justice pipeline complete: %d justices, %d votes ===", len(justices), len(all_votes))
    return {"justices": len(justices), "votes": len(all_votes), "loyalty_unmeasured": unmeasured_why}
