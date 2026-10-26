"""Every external source and whether Civitas can read it now: the admin's
Sources tab (GET /api/admin/source-health) and the map on /about/data
(GET /api/sources/health, which leaves out run internals).

Every host comes by rule, never from a list typed here: the hosts the code
names (app/crawler.py, the same list /about/crawler shows), its name
(app/source_names.py), what its readers say they read (the admin's), and
the nightly runs and scheduler jobs that read it, from the code
(crawler.run_readers, crawler.job_readers: the runs and jobs whose code
reaches a reader of the host), so it is right before anything has recorded
a read.

**A good read is a step that got what it came for**, not a 200 on the way:
eFD's home page loads, then its terms agreement is refused. For a host a
pipeline step reads, the last good read and "failing since" come from step
outcomes: the step's SourceReadStat rows (a step that read the host and
did not fail on it with an ExpectedSourceFailure is good; one that did is a
failure), and before those rows existed, the same steps' outcomes in the
runs' own progress records (the steps the rows name, so history is read
only for steps known to read the host). Raw answers (app/source_reads.py)
date only hosts no step reads (the hourly feeds).

Its state, in this order:

1. **refused / walled**: the host's refusal backoff holds now
   (crawl_policy.refusals: a bot wall's challenge is walled, a 429 refused).
2. **walled**: a wall written up in the ballot-measure registry (`wall`:
   host, date measured), with no good read since.
3. **walled / refused / unavailable**: its latest step outcome was a
   failure (else, for a host no step reads, its last request failed),
   failing since the first failure after its last good read.
4. **not read this run**: nothing on record, or a run read it and that run
   kind's latest run didn't. A source not asked is not a source that failed
   (outage is not "no data").
5. **ok**: its latest step outcome, or answer, was good.

Read path only: no fetch, no model. Outside app/pipeline/, so it never
moves the analysis-code fingerprint.
"""

import ast
import json
import re
from datetime import date, datetime, timedelta
from functools import lru_cache

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import crawl_policy, crawler, source_names, source_reads
from app.models import SourceFailureCount, SourceReadStat
from app.pipeline import run_tracker
from app.pipeline.fetch import ballot_measure_pdf_sources
from app.source_failures import run_label
from app.time_utils import utcnow

WINDOW_DAYS = 30
# How many of a run kind's newest runs are read for step history.
HISTORY_RUNS = 120

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


def _code_runs() -> dict[str, set[str]]:
    """host -> the run tables whose runs read it, from the code."""
    by_module: dict[str, set[str]] = {}
    for table, modules in crawler.run_readers().items():
        for module in modules:
            by_module.setdefault(module, set()).add(table)
    return {h["host"]: set().union(*(by_module.get(r["module"], set()) for r in h["readers"]))
            for h in crawler.hosts()}


def _code_jobs() -> dict[str, set[str]]:
    """host -> the scheduler jobs that read it, named with when they run
    ("Action Center, every hour at :15"), from the code (crawler.job_readers)."""
    by_module: dict[str, set[str]] = {}
    for job in crawler.job_readers().values():
        for module in job["modules"]:
            by_module.setdefault(module, set()).add(job["label"])
    return {h["host"]: set().union(*(by_module.get(r["module"], set()) for r in h["readers"]))
            for h in crawler.hosts()}


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _step_events(db: Session, code_runs: dict[str, set[str]]) -> dict[str, list[tuple[str, bool]]]:
    """host -> [(when, good)] for every step outcome on record that read
    it: its SourceReadStat rows (good: answered and didn't fail on the
    host), then, for the (run, step) pairs those rows name, the outcome of
    the same step in the runs' progress records (done is good, failed is
    not), which reaches back before the rows existed. A row from a run the
    code says doesn't read the host is left out: the hourly refresh's
    requests fall in a pipeline step's window."""
    events: dict[str, list[tuple[str, bool]]] = {}
    pairs: dict[tuple[str, str], set[str]] = {}
    seen: set[tuple[str, int, str, str]] = set()
    for r in db.query(SourceReadStat):
        if r.run_kind not in code_runs.get(r.host, ()):
            continue
        good = not r.failed_on_host and r.answered > 0
        events.setdefault(r.host, []).append((_iso(r.recorded_at)[:19], good))
        pairs.setdefault((r.run_kind, r.step_key), set()).add(r.host)
        seen.add((r.run_kind, r.run_id, r.step_key, r.host))
    models = {m.__tablename__: m for m in run_tracker.run_tables().values()}
    for table in {kind for kind, _ in pairs}:
        model = models.get(table)
        if model is None:
            continue
        runs = (db.query(model.id, model.progress_detail).order_by(model.id.desc()).limit(HISTORY_RUNS))
        for run_id, detail in runs:
            try:
                steps = json.loads(detail or "[]")
            except ValueError:
                continue
            for step in steps if isinstance(steps, list) else []:
                hosts = pairs.get((table, step.get("key")), ())
                status, when = step.get("status"), step.get("completedAt")
                if status not in ("done", "failed") or not when:
                    continue
                for host in hosts:
                    if (table, run_id, step["key"], host) not in seen:
                        events.setdefault(host, []).append((when[:19], status == "done"))
    return events


def _outcomes(events: list[tuple[str, bool]]) -> tuple[str | None, str | None, bool | None]:
    """(last good, failing since, latest was good) from step outcomes."""
    if not events:
        return None, None, None
    events = sorted(events)
    good = [w for w, ok in events if ok]
    last_good = good[-1] if good else None
    after = [w for w, ok in events if not ok and (last_good is None or w > last_good)]
    return last_good, (after[0] if after else None), events[-1][1]


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


def _latest_kinds(db: Session) -> dict[str, str]:
    """host -> the kind of its newest counted source failure."""
    out: dict[str, str] = {}
    for host, kind in (db.query(SourceFailureCount.host, SourceFailureCount.kind)
                       .order_by(SourceFailureCount.date)):
        out[host] = kind
    return out


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
    code_runs = _code_runs()
    code_jobs = _code_jobs()
    events = _step_events(db, code_runs)
    kinds = _latest_kinds(db)
    labels = {m.__tablename__: label for label, m in run_tracker.run_tables().items()}

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
        stepped = host in events
        if stepped:
            # A step reads it: step outcomes decide (see the module docstring).
            last_ok, step_since, latest_good = _outcomes(events[host])
        else:
            last_ok, step_since, latest_good = rec.get("lastOk"), rec.get("failingSince"), None
        wall = walls.get(host)
        ref = refusing.get(host)
        reason, since = None, None
        if ref is not None:
            state = "walled" if "bot wall" in (ref.get("reason") or "") else "refused"
            reason, since = ref.get("reason"), step_since or ref.get("since")
        elif wall and (not last_ok or last_ok[:10] < wall.get("measured", "")):
            state, reason = "walled", "the site blocks automated reads (a bot wall)"
            since = step_since or wall.get("measured")
        elif (latest_good is False) if stepped else bool(step_since):
            state = rec.get("kind") if rec.get("failingSince") else None
            state = state or kinds.get(host) or "unavailable"
            reason, since = rec.get("reason"), step_since
        elif not last_ok or (host in run_reads and not in_latest.get(host)):
            state = "not_read"
        else:
            state = "ok"
        # When it was last asked: a request, a step, or what a refusal or
        # the registry's wall dates. "Never" only with no record at all.
        asked = [rec.get("lastAttempt"), max((w for w, _ in events.get(host, ())), default=None),
                 ref.get("since") if ref else None, wall.get("measured") if wall else None]
        last_asked = max(filter(None, asked), default=None, key=lambda v: v[:19])
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
            "lastAttempt": last_asked,
            "lastAttemptText": _text(last_asked),
            # The nightly runs and scheduler jobs that read it, from the code;
            # the steps that did, as recorded.
            "readBy": (sorted(labels.get(t, t) for t in code_runs.get(host, ()))
                       + sorted(code_jobs.get(host, ()))
                       # Nothing scheduled reads it, but a page asks for it.
                       or (["Site pages, when a reader asks"] if {r["module"] for r in h["readers"]}
                           & crawler.request_readers() else [])),
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
