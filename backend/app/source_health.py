"""Every external source and whether Civitas can read it now: the admin's
Sources tab (GET /api/admin/source-health) and the map on /about/data
(GET /api/sources/health, which leaves out run internals).

Every host comes by rule, never from a list typed here: the hosts the code
names (app/crawler.py, the same list /about/crawler shows), each with what
its readers read (the first sentence of each reading module's docstring).
Its state comes from what happened when it was last asked, in this order:

1. **refused / walled**: the host's refusal backoff holds now
   (crawl_policy.refusals: a bot wall's challenge is walled, a 429 refused).
2. **walled**: a wall written up in the ballot-measure registry (`wall`:
   host, date measured), with no answer from the host since.
3. **walled / refused / unavailable**: the host's last request failed
   (app/source_reads.py), failing since the first failure after its last
   answer.
4. **not read this run**: no request on record, or a pipeline run read it
   (SourceReadStat) and that run kind's latest run didn't. A source not
   asked is not a source that failed (outage is not "no data").
5. **ok**: it answered when last asked.

Read path only: no fetch, no model. Outside app/pipeline/, so it never
moves the analysis-code fingerprint.
"""

import ast
import re
from datetime import date, datetime, timedelta
from functools import lru_cache

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import crawl_policy, crawler, source_names, source_reads
from app.models import SourceFailureCount, SourceReadStat
from app.pipeline.fetch import ballot_measure_pdf_sources
from app.source_failures import run_label
from app.time_utils import utcnow

WINDOW_DAYS = 30

STATE_LABELS = {
    "ok": "Read normally",
    "refused": "Refusing Civitas",
    "walled": "Behind a bot wall",
    "unavailable": "Not answering",
    "not_read": "Not read this run",
}
# Most in need of a look first.
# What a failing state means, for the public line: the recorded reason
# (a status code, a vendor's wording) stays on the admin tab.
PLAIN_REASONS = {
    "walled": "the site blocks automated reads",
    "refused": "the site is turning Civitas away",
    "unavailable": "the site did not answer, or answered with an error",
}
_ORDER = ("walled", "refused", "unavailable", "not_read", "ok")


def _day(value: str | None) -> str | None:
    return value[:10] if value else None


def _text(day: str | None) -> str | None:
    """"Oct 23, 2026"."""
    if not day:
        return None
    d = date.fromisoformat(day[:10])
    return f"{d:%b} {d.day}, {d.year}"


def _first_sentence(text: str) -> str:
    """A docstring's first sentence as site prose: no parenthetical asides
    (file names, cross-references), no em dashes, no code marks."""
    para = " ".join(text.split("\n\n")[0].split())
    while (cut := re.sub(r"\s*\([^()]*\)", "", para)) != para:
        para = cut
    para = para.replace(" — ", ": ").replace("—", ": ").replace("`", "")
    if ". " in para:
        para = para[: para.index(". ") + 1]
    para = para.rstrip(" :;,")
    return para if para.endswith(".") else para + "."


@lru_cache(maxsize=1)
def _purposes() -> dict[str, str]:
    """module -> the first sentence of its docstring: what it reads."""
    out = {}
    for reader in crawler.readers():
        path = crawler.APP.parent.joinpath(*reader["module"].split(".")).with_suffix(".py")
        try:
            doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
        except (OSError, SyntaxError):
            doc = ""
        if doc:
            out[reader["module"]] = _first_sentence(doc)
    out[crawler.PHOTO_ROUTE] = "The photos the site shows, fetched once each by its photo route."
    return out


def _run_reads(db: Session, since: datetime) -> tuple[dict, dict]:
    """host -> {"runs": {"stock trades · senate_ptr", ...}, "answered", "failed"}
    over the window, and host -> whether the latest run of a run kind that
    read it read it too (False when none did)."""
    rows = db.query(SourceReadStat).filter(SourceReadStat.recorded_at >= since).all()
    latest = dict(
        db.query(SourceReadStat.run_kind, func.max(SourceReadStat.run_id)).group_by(SourceReadStat.run_kind).all()
    )
    reads: dict[str, dict] = {}
    in_latest: dict[str, bool] = {}
    for r in rows:
        h = reads.setdefault(r.host, {"runs": set(), "answered": 0, "failed": 0})
        h["runs"].add(f"{run_label(r.run_kind)} · {r.step_key}")
        h["answered"] += r.answered
        h["failed"] += r.failed
        in_latest[r.host] = in_latest.get(r.host, False) or r.run_id == latest.get(r.run_kind)
    return reads, in_latest


def _failed_steps(db: Session, since: str) -> dict[str, int]:
    return dict(
        db.query(SourceFailureCount.host, func.sum(SourceFailureCount.count))
        .filter(SourceFailureCount.date >= since).group_by(SourceFailureCount.host).all()
    )


def _line(state: str, since: str | None, last_good: str | None, reason: str | None) -> str:
    """The entry's one line, a template over its fields."""
    good = f"Last good read {_text(last_good)}." if last_good else "No good read on record."
    if state == "ok":
        return f"Answered when last asked. {good}"
    if state == "not_read":
        return f"Not asked in its latest run. {good}" if last_good else "No read on record yet."
    head = f"Not readable since {_text(since)}" if since else "Not readable now"
    return f"{head}: {reason}. {good}" if reason else f"{head}. {good}"


def _count(n: int, one: str) -> str:
    return f"{n:,} {one}{'' if n == 1 else 's'}"


def _failures_text(steps: int, failed: int, requests: int) -> str:
    """"1 failed step; 2 of 3 requests failed", for the admin's column."""
    return f"{_count(steps, 'failed step')}; {failed:,} of {_count(requests, 'request')} failed"


def build(db: Session) -> dict:
    """Every source's entry, most in need of a look first, with the admin's
    fields (runs and steps, counts, last attempt). `public` strips them."""
    now = utcnow()
    since_dt = now - timedelta(days=WINDOW_DAYS - 1)
    since_day = since_dt.date().isoformat()
    purposes = _purposes()
    named = source_names.names()
    refusing = crawl_policy.refusals()
    walls = ballot_measure_pdf_sources.walls()
    records = source_reads.reads()
    run_reads, in_latest = _run_reads(db, since_dt)
    failed_steps = _failed_steps(db, since_day)

    entries = []
    hosts = crawler.hosts()
    # The frontend's photo route fetches these from Node, outside the
    # backend's client, so nothing here sees its reads: such a host would
    # read as never asked. Left off the map, and the summary says so.
    photo_only = {h["host"] for h in hosts if {r["module"] for r in h["readers"]} == {crawler.PHOTO_ROUTE}}
    for h in hosts:
        host = h["host"]
        if host in photo_only:
            continue
        rec = records.get(host, {})
        last_ok, reason, since = rec.get("lastOk"), None, None
        if (ref := refusing.get(host)) is not None:
            state = "walled" if "bot wall" in (ref.get("reason") or "") else "refused"
            reason, since = ref.get("reason"), ref.get("since")
        elif (wall := walls.get(host)) and (not last_ok or last_ok[:10] < wall.get("measured", "")):
            state, reason, since = "walled", "the site blocks automated reads (a bot wall)", wall.get("measured")
        elif rec.get("failingSince"):
            state, reason, since = rec.get("kind") or "unavailable", rec.get("reason"), rec["failingSince"]
        elif not last_ok or (host in run_reads and not in_latest.get(host)):
            state = "not_read"
        else:
            state = "ok"
        runs = run_reads.get(host, {})
        entries.append({
            "host": host,
            "publisher": named.get(host, {}).get("publisher"),
            "takes": named.get(host, {}).get("takes"),
            # Admin only: what each reading module says it reads.
            "reads": sorted({purposes[r["module"]] for r in h["readers"] if r["module"] in purposes}),
            "state": state,
            "stateLabel": STATE_LABELS[state],
            "since": _day(since),
            "lastGood": _day(last_ok),
            "reason": reason,
            "plainReason": PLAIN_REASONS.get(state),
            "line": _line(state, _day(since), _day(last_ok), PLAIN_REASONS.get(state)),
            # Admin only (public() drops them).
            "lastAttempt": rec.get("lastAttempt"),
            "runs": sorted(runs.get("runs", ())),
            "requests30d": runs.get("answered", 0) + runs.get("failed", 0),
            "failedRequests30d": runs.get("failed", 0),
            "failedSteps30d": int(failed_steps.get(host, 0)),
            "failuresText": _failures_text(int(failed_steps.get(host, 0)), runs.get("failed", 0),
                                           runs.get("answered", 0) + runs.get("failed", 0)),
        })
    entries.sort(key=lambda e: (_ORDER.index(e["state"]), (e["publisher"] or e["host"]).lower(), e["host"]))
    counts = {s: sum(1 for e in entries if e["state"] == s) for s in _ORDER}
    not_ok = counts["walled"] + counts["refused"] + counts["unavailable"]
    summary = (
        f"Of {len(entries):,} sources, {counts['ok']:,} answered when last asked, "
        f"{not_ok:,} can't be read now and {counts['not_read']:,} have no recent read on record."
        + (f" The {len(photo_only):,} photo hosts the site's photo route reads are not tracked here."
           if photo_only else "")
    )
    return {"asOf": now.isoformat(timespec="minutes"), "windowDays": WINDOW_DAYS,
            "counts": counts, "summary": summary, "sources": entries}


_PUBLIC = ("host", "publisher", "takes", "state", "stateLabel", "since", "lastGood", "line")


def public(body: dict) -> dict:
    """The map without run internals: state, last good read, the reason."""
    return {**{k: body[k] for k in ("asOf", "counts", "summary")},
            "sources": [{k: e[k] for k in _PUBLIC} for e in body["sources"]]}
