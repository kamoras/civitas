"""Justice scorecard pipeline: the voting record from Oyez, and loyalty to
the appointing president (the score) from the Supreme Court Database."""

import csv
import gzip
import json
import logging
import re
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.http_client import make_async_client
from app.models import President
from app.pipeline.analyze.justice_analyzer import analyze_justice_votes
from app.pipeline.analyze.justice_loyalty import Loyalty, Vote, label, loyalty_by_justice, president_on
from app.pipeline.fetch.justice_records import fetch_fjc, fetch_martin_quinn, fetch_scdb
from app.pipeline.fetch.justice_votes import fetch_case_votes, fetch_current_justices
from app.services.justice_service import group_votes_by_case_and_justice, upsert_justice

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


async def _measure_loyalty(client: httpx.AsyncClient, db: Session) -> dict | None:
    """{"loyalty": {database name: Loyalty}, "term", "current", "ideal"}, or
    None when a source can't be read (the stored values then stand)."""
    scdb = await fetch_scdb(client, db)
    appointments = await fetch_fjc(client, db)
    terms = [(p.name, p.term_start, p.term_end) for p in db.query(President).order_by(President.term_start)]
    if scdb is None or appointments is None or not terms:
        logger.warning("Justice loyalty not measured: %s", "no presidents stored" if not terms else "a source is down")
        return None
    votes = [Vote(j, d, pet, gov) for j, d, pet, gov, term in scdb["votes"] if term > _BUNDLE_LAST_TERM]
    rows = _bundled_rows()
    for justice, labeled in label(votes, _appointers({v.justice for v in votes}, appointments, terms), terms).items():
        rows.setdefault(justice, []).extend(labeled)
    loyalty, mean, spread = loyalty_by_justice(rows)
    logger.info("Justice loyalty: %d justices, mean %+.3f, between-justice sd %.3f (%s)",
                len(loyalty), mean, spread, scdb["release"])
    return {"loyalty": loyalty, "term": scdb["term"], "current": scdb["current"],
            "ideal": await fetch_martin_quinn(client, db) or {}}


def _database_name(justice: dict, current: list[str]) -> str | None:
    """The Database's name for an Oyez justice: the justice of its newest
    term whose name ends with the surname and begins with the initial."""
    last, first = _name_key(justice.get("last_name") or ""), (justice.get("name") or "")[:1]
    matches = [n for n in current if last and _name_key(n).endswith(last) and n[:1] == first]
    return matches[0] if len(matches) == 1 else None


def _loyalty_fields(result: Loyalty | None, term: int, ideal: list | None) -> dict:
    fields = {"ideal_points": json.dumps(ideal) if ideal else None, "loyalty_through_term": term}
    if result is None:
        # Not in the Database yet (a justice newer than its release):
        # unscored, never a neutral or fabricated number.
        return {**fields, "score_loyalty": None, "loyalty": None, "loyalty_se": None,
                "loyalty_votes_in": None, "loyalty_votes_out": None,
                "loyalty_rate_in": None, "loyalty_rate_out": None}
    e = result.estimate
    return {
        **fields, "score_loyalty": result.score, "loyalty": round(result.loyalty, 4),
        "loyalty_se": round(result.se, 4), "loyalty_votes_in": e.votes_in, "loyalty_votes_out": e.votes_out,
        "loyalty_rate_in": round(e.rate_in, 4), "loyalty_rate_out": round(e.rate_out, 4),
    }


async def run_justice_pipeline(db: Session) -> dict:
    """Fetch, analyze, and persist Supreme Court justice scorecards.

    Returns summary dict with counts.
    """
    logger.info("=== Justice pipeline starting ===")

    async with make_async_client(
        headers={"User-Agent": "Civitas/1.0 (civic-transparency-tool) httpx/0.27"},
        follow_redirects=True,
    ) as client:
        justices = await fetch_current_justices(client)
        if not justices:
            logger.warning("No justices found, aborting pipeline")
            return {"justices": 0, "votes": 0}

        all_votes = await fetch_case_votes(client)
        measured = await _measure_loyalty(client, db)

    case_votes, justice_votes = group_votes_by_case_and_justice(all_votes)

    active_ids = {j["id"] for j in justices}

    for j in justices:
        jid = j["id"]
        jvotes = justice_votes.get(jid, [])

        analysis = analyze_justice_votes(jid, jvotes, dict(case_votes), active_ids)

        record = {
            "id": jid,
            "name": j["name"],
            "last_name": j.get("last_name", ""),
            "role_title": j.get("role_title", "Associate Justice"),
            "appointing_president": j.get("appointing_president", ""),
            "appointing_party": j.get("appointing_party", ""),
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
            record.update(_loyalty_fields(
                measured["loyalty"].get(name) if name else None, measured["term"],
                measured["ideal"].get(name) if name else None,
            ))

        upsert_justice(db, record, jvotes)
        logger.info("  %s: loyalty score %s, cases=%d", j["name"], record.get("score_loyalty"), analysis["cases_decided"])

    db.commit()
    logger.info("=== Justice pipeline complete: %d justices, %d votes ===", len(justices), len(all_votes))
    return {"justices": len(justices), "votes": len(all_votes)}
