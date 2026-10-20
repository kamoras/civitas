"""Generated data files refresh themselves (AGENTS.md 3a).

Every file under app/data/ that a script computes from public data is
regenerated here on its own cadence, by that same script (never a second
copy of its logic), run as a subprocess at low priority:

  1. the script writes a candidate file;
  2. the candidate is gated: its shape, plausibility, and drift against the
     copy in use (each file's `gate`);
  3. only a candidate that passes replaces the copy in use, written to the
     data volume (/data/<name>, ahead of the bundled app/data/<name> every
     reader falls back to) with its provenance (`_refreshed`: when, by
     which command, and the content's sha256);
  4. a candidate that fails, or a script that fails, keeps the last good
     copy and opens an ops alert for that file, resolved by its next good
     refresh.

Files whose regeneration can't honestly be automated (a new survey's
question ids, a model-selection refit that is a method decision, a source
behind research-only dependencies) have no command, only a `stale` check
that alerts when a newer version should exist, saying why a person is
needed. The bundled copy stays in git as the pre-first-refresh fallback and
the reproducible baseline; a refresh that moves scores is a data refresh
within the same method (recorded in `_refreshed` and announced by an ops
notice), not a new scoring version, exactly as the per-run population
references are.

Readers call `read(name)`, which notices a rewrite from either process.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx

from app import committee_routing
from app.atomic_write import runtime_data_path, write_text_atomic
from app.config import settings
from app.contact import BOT_USER_AGENT
from app.database import SessionLocal
from app.election_calendar import last_certified_general
from app.election_phase import active_election
from app.error_utils import output_summary
from app.file_cache import Uncached, files_stamp, read_json_preferring
from app.forecast import model, training_polls
from app.ops_alerts import resolve_ops_alert, send_ops_alert
from app.pipeline.cache import api_cache_get, api_cache_set
from app.state_names import STATE_NAMES
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

BACKEND = pathlib.Path(__file__).resolve().parent.parent
BUNDLED = BACKEND / "app" / "data"
_STATE_TIER = "data-refresh"

_read_cache: dict[str, tuple[object, dict]] = {}


def read(name: str) -> dict:
    """The copy of app/data/`name` in use: the refreshed one on the data
    volume when there is one, else the bundled one; {} when neither reads.
    Re-read whenever either file changes (the refresh runs in the pipeline
    process; the API process sees it on its next call)."""
    paths = (runtime_data_path(name), BUNDLED / name)
    stamp = files_stamp(paths)
    hit = _read_cache.get(name)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    try:
        data = read_json_preferring(*paths, default={}, accept=lambda d: isinstance(d, dict))
    except Uncached as unreadable:
        return unreadable.value
    _read_cache[name] = (stamp, data)
    return data


@dataclass
class Context:
    """What a file's checks decide with."""

    name: str
    doc: dict  # the copy in use
    today: date
    last_tried: datetime | None
    last_failed: bool = False  # the last try kept the copy in use

    def tried_within(self, days: int) -> bool:
        if self.last_failed:
            days = min(days, RETRY_FAILED_AFTER_DAYS)
        return self.last_tried is not None and utcnow() - self.last_tried < timedelta(days=days)


@dataclass(frozen=True)
class DataFile:
    """One generated file and how it stays current. `command` returns the
    script's argv (with "{out}" where the candidate is written), or None
    when there is nothing to run now; a file with no command is checked
    for staleness only."""

    name: str
    command: Callable[[Context], list[str] | None] | None = None
    due: Callable[[Context], bool] = lambda ctx: False
    gate: Callable[[dict, dict], list[str]] = lambda new, old: []
    stale: Callable[[Context], str | None] = lambda ctx: None
    heavy: bool = False  # loads the embedding model or is CPU-bound: not while a pipeline runs
    moves_scores: bool = False
    timeout_s: int = 3600
    extra: dict = field(default_factory=dict)
    # Further files the same command writes ("{out:<name>}" in its argv),
    # installed with this one when its gate passes, never on their own.
    outputs: tuple[str, ...] = ()
    # After an install (or an unchanged refresh): a finding about the files
    # in use ({name: doc}) worth an ops alert that doesn't hold them back.
    check: Callable[[dict[str, dict]], str | None] | None = None

    @property
    def stem(self) -> str:
        return self.name.removesuffix(".json").replace("_", "-")


def content_sha256(doc: dict) -> str:
    """The content's hash, without its own provenance."""
    body = {k: v for k, v in doc.items() if k != "_refreshed"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# A try that kept the copy in use (refresh_one's "kept: ..." outcome) is
# tried again within a week, whatever the file's cadence: a yearly file
# (name_casing.json) otherwise kept its alert open, and its fix untried, for
# a year, and a monthly one for a month. Weekly rather than daily, since the
# heavy generators take hours.
RETRY_FAILED_AFTER_DAYS = 7


def _last_try(name: str) -> tuple[datetime | None, bool]:
    """When `name` was last tried, and whether that try kept the copy in use."""
    db = SessionLocal()
    try:
        state = api_cache_get(db, _STATE_TIER, name, max_age_hours=24 * 365 * 5) or {}
    finally:
        db.close()
    tried = datetime.fromisoformat(state["triedAt"]) if state.get("triedAt") else None
    return tried, str(state.get("outcome") or "").startswith("kept")


def _record_try(name: str, outcome: str) -> None:
    db = SessionLocal()
    try:
        api_cache_set(db, _STATE_TIER, name, {"triedAt": utcnow().isoformat(), "outcome": outcome},
                      normal_ttl_hours=24 * 365 * 5)
    finally:
        db.close()


def run_command(argv: list[str], timeout_s: int) -> tuple[int | None, str]:
    """Run a generator script from backend/, at the lowest CPU priority so
    page requests never wait on it. (exit code, or None when it ran past
    `timeout_s` and was stopped; its whole output, stdout then stderr)."""
    env = {**os.environ, "PYTHONPATH": str(BACKEND)}
    try:
        # nice(1) rather than preexec_fn: the pipeline process is threaded
        # (the scheduler), and preexec_fn is not safe to use with threads.
        done = subprocess.run(
            ["nice", "-n", "19", *argv], cwd=BACKEND, env=env, capture_output=True, text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as stopped:
        # What it printed before it was stopped: bytes here, whatever `text` says.
        return None, "".join(
            part.decode(errors="replace") if isinstance(part, bytes) else part
            for part in (stopped.stdout, stopped.stderr) if part
        )
    return done.returncode, done.stdout + done.stderr


# A crash whose exception is one of these is the source not answering, not
# a bug in the generator: the alert says so. Names, since the exception was
# raised in the child process (httpx's, urllib's, the socket layer's).
_SOURCE_ERRORS = frozenset({
    "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout", "ReadError",
    "RemoteProtocolError", "HTTPStatusError", "URLError", "HTTPError", "gaierror", "ConnectionError",
    "ConnectionRefusedError", "ConnectionResetError", "TimeoutError",
})


def failure_alert(name: str, what: str, detail: str | None, logged_at: str | None) -> str:
    """An ops alert body for a refresh that kept the copy in use: one plain
    sentence (which file, what happened, what is still served), then the one
    line that says why, then where the full output is. Never the output
    itself: it is in the pipeline log."""
    body = f"app/data/{name} was not refreshed: {what}. The previous copy stays in use."
    if detail:
        body += f"\n{detail}"
    if logged_at:
        body += f"\nFull output in the pipeline log at {logged_at}."
    return body


def install(spec: DataFile | str, new: dict, argv: list[str]) -> bool:
    """Write a gated candidate as the copy in use, with its provenance.
    False when it is the same content as the copy in use (nothing written)."""
    name = spec if isinstance(spec, str) else spec.name
    old = read(name)
    sha = content_sha256(new)
    if old and content_sha256(old) == sha:
        return False
    rel = [a if not a.startswith(str(BACKEND)) else os.path.relpath(a, BACKEND) for a in argv]
    doc = {**new, "_refreshed": {"at": utcnow().isoformat(timespec="seconds"),
                                 "by": "app/data_refresh.py", "command": " ".join(rel), "sha256": sha}}
    write_text_atomic(runtime_data_path(name), json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    _read_cache.pop(name, None)
    return True


def refresh_one(spec: DataFile, ctx: Context) -> str:
    """Regenerate, gate and install one file. Returns the outcome."""
    condition = f"data-refresh-{spec.stem}"
    template = spec.command(ctx) if spec.command else None
    if template is None:
        return "nothing to run"
    out_dir = pathlib.Path(runtime_data_path(spec.name)).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates = {}
    for name in (spec.name, *spec.outputs):
        fd, candidates[name] = tempfile.mkstemp(prefix=f".{name}.", suffix=".candidate", dir=out_dir)
        os.close(fd)
        os.unlink(candidates[name])
    # "{tmp}": a scratch directory for the script's downloads, removed after.
    scratch = tempfile.mkdtemp(prefix=f".{spec.name}.", suffix=".scratch", dir=out_dir)

    def fill(arg: str) -> str:
        for name in spec.outputs:
            arg = arg.replace("{out:%s}" % name, candidates[name])
        return arg.replace("{out}", candidates[spec.name]).replace("{tmp}", scratch)

    argv = [fill(a) for a in template]
    docs: dict[str, dict] = {}
    try:
        code, output = run_command(argv, spec.timeout_s)
        what, detail = None, None
        if code is None:
            what = "its generator ran too long and was stopped"
            limit = spec.timeout_s
            detail = (f"Time limit: {limit} second{'' if limit == 1 else 's'}." if limit < 120
                      else f"Time limit: {limit / 60:g} minutes.")
        elif code != 0:
            detail = output_summary(output) or f"It printed nothing (exit code {code})."
            error_type = re.match(r"([\w.]+)(?::|$)", detail)
            if error_type and error_type[1].rsplit(".", 1)[-1] in _SOURCE_ERRORS:
                what = "its generator could not reach its source"
            else:
                what = f"its generator crashed (exit code {code})"
        else:
            unreadable = []
            for name, path in candidates.items():
                try:
                    docs[name] = json.loads(pathlib.Path(path).read_text())
                    if not (isinstance(docs[name], dict) and docs[name]):
                        unreadable.append(f"{name} holds no JSON object")
                except (OSError, ValueError) as e:
                    unreadable.append(f"{name} is unreadable: {e}")
            if unreadable:
                what, detail = "its generator finished without writing a usable file", "; ".join(unreadable)
            elif refused := spec.gate(docs[spec.name], ctx.doc):
                # The gate's reasons are the whole story; the generator ran fine.
                what, detail, output = "its gate refused the new candidate", "; ".join(refused), ""
    finally:
        for path in candidates.values():
            pathlib.Path(path).unlink(missing_ok=True)
        shutil.rmtree(scratch, ignore_errors=True)
    new = docs.get(spec.name, {})
    if what:
        logged_at = None
        if output.strip():
            logged_at = utcnow().strftime("%Y-%m-%d %H:%M UTC")
            logger.warning("Data refresh of %s kept the copy in use (%s); generator output, %s:\n%s",
                           spec.name, what, logged_at, output)
        send_ops_alert(
            f"{spec.name} not refreshed",
            failure_alert(spec.name, what, detail, logged_at),
            dedupe_key=f"{condition}-{content_sha256({'w': what, 'd': detail})[:12]}",
            condition=condition,
        )
        return f"kept: {what}" + (f": {detail}" if detail else "")
    changed = install(spec, new, argv)
    for name in spec.outputs:
        changed = install(name, docs[name], argv) or changed
    resolve_ops_alert(condition)
    if spec.check:
        finding = spec.check({name: read(name) for name in (spec.name, *spec.outputs)})
        if finding:
            send_ops_alert(f"{spec.name}: check after refresh", f"app/data/{spec.name}: {finding}",
                           dedupe_key=f"data-check-{spec.stem}-{content_sha256({'f': finding})[:12]}",
                           condition=f"data-check-{spec.stem}")
        else:
            resolve_ops_alert(f"data-check-{spec.stem}")
    if changed and spec.moves_scores:
        send_ops_alert(
            f"{spec.name} refreshed",
            f"app/data/{spec.name} was regenerated from current data and passed its gates. Scores that read "
            f"it move at the next run: a data refresh within the same method, recorded in the file's _refreshed.",
            dedupe_key=f"data-refreshed-{spec.stem}-{content_sha256(new)[:12]}",
        )
    return "refreshed" if changed else "unchanged"


def run_refreshes(files: list[DataFile] | None = None, *, pipeline_busy: bool = False) -> dict[str, str]:
    """Every file's staleness check, and each due refresh. One file's
    failure never stops the others."""
    outcomes: dict[str, str] = {}
    for spec in files if files is not None else DATA_FILES:
        try:
            ctx = Context(spec.name, read(spec.name), utcnow().date(), *_last_try(spec.name))
            if spec.command and spec.due(ctx):
                if spec.heavy and pipeline_busy:
                    outcomes[spec.name] = "waiting for the pipeline"
                else:
                    outcomes[spec.name] = refresh_one(spec, ctx)
                    _record_try(spec.name, outcomes[spec.name])
                    ctx.doc = read(spec.name)
            why = spec.stale(ctx)
            if why:
                send_ops_alert(
                    f"{spec.name} is out of date",
                    f"app/data/{spec.name}: {why}",
                    dedupe_key=f"data-stale-{spec.stem}-{content_sha256({'w': why})[:12]}",
                    condition=f"data-stale-{spec.stem}",
                )
            else:
                resolve_ops_alert(f"data-stale-{spec.stem}")
            outcomes.setdefault(spec.name, "current")
        except Exception:
            logger.exception("Data refresh of %s failed", spec.name)
            outcomes[spec.name] = "error"
    return outcomes


# --- the files -----------------------------------------------------------

PY = sys.executable


def _script(name: str) -> str:
    return str(BACKEND / "scripts" / name)


def _drift(new: dict, old: dict, key: str, *, keep: float) -> list[str]:
    """A keyed collection that lost more than (1 - keep) of its entries."""
    before, after = set((old or {}).get(key) or {}), set((new or {}).get(key) or {})
    if before and len(before & after) < keep * len(before):
        return [f"{key}: only {len(before & after)} of the {len(before)} entries in use are still there"]
    return []


def _election():
    return active_election()


# Poll closing times: one Ballotpedia page per election year. Read for the
# coming election from January of its year, weekly until it is the right
# year, then monthly in case a state changes its hours.
def _poll_close_command(ctx: Context) -> list[str] | None:
    election = _election()
    if election.phase != "campaign" or ctx.today.year != election.cycle:
        return None
    return [PY, _script("fetch_poll_close_times.py"), str(election.cycle), "{out}"]


def _poll_close_due(ctx: Context) -> bool:
    election = _election()
    if election.phase != "campaign" or ctx.today.year != election.cycle:
        return False
    return not ctx.tried_within(7 if ctx.doc.get("year") != election.cycle else 30)


def _poll_close_gate(new: dict, old: dict) -> list[str]:
    states = new.get("states") or {}
    failures = []
    expected = {s for s in STATE_NAMES if s != "DC"}
    if set(states) != expected:
        failures.append(f"states listed: {len(states)} of {len(expected)}")
    for code, entry in states.items():
        close = entry.get("close")
        if close is not None and not ("17:00" <= close <= "23:59"):
            failures.append(f"{code} closes at {close}")
    if old.get("states"):
        moved = [c for c, e in states.items() if (old["states"].get(c) or {}).get("close") != e.get("close")]
        if len(moved) > 8:
            failures.append(f"{len(moved)} states' closing times changed at once ({', '.join(sorted(moved))})")
    return failures


# The voter calendar: re-dated for the coming election from each rule
# (build_election_calendar.carry), every cited page re-read; weekly, from
# 200 days out, which re-checks every quote of the election it is for.
def _calendar_command(ctx: Context) -> list[str] | None:
    election = _election()
    if election.phase != "campaign":
        return None
    return [PY, _script("build_election_calendar.py"), "--election", election.election_day.isoformat(),
            "--output", "{out}"]


def _calendar_due(ctx: Context) -> bool:
    election = _election()
    return (election.phase == "campaign" and ctx.today >= election.election_day - timedelta(days=200)
            and not ctx.tried_within(7))


def _calendar_gate(new: dict, old: dict) -> list[str]:
    failures = []
    if set(new.get("states") or {}) != set(old.get("states") or {}):
        failures.append("the states covered changed")
    items = sum(len(e.get("items") or []) for e in (new.get("states") or {}).values())
    before = sum(len(e.get("items") or []) for e in (old.get("states") or {}).values())
    same_election = new.get("electionDate") == old.get("electionDate")
    if before and items < (0.95 if same_election else 0.3) * before:
        failures.append(f"{items} deadlines where the copy in use has {before}")
    return failures


def _every(days: int) -> Callable[[Context], bool]:
    return lambda ctx: not ctx.tried_within(days)


def _fec_party_gate(new: dict, old: dict) -> list[str]:
    codes = new.get("codes") or {}
    failures = [] if len(codes) >= 50 and {"REP", "DEM"} <= set(codes) else [f"{len(codes)} codes, REP/DEM missing?"]
    return failures + _drift(new, old, "codes", keep=0.9)


# Committee jurisdictions: the rules each chamber adopts at the start of a
# Congress. Weekly once a new Congress sits and until the file is for it,
# monthly after.
def _jurisdictions_due(ctx: Context) -> bool:
    current = ctx.doc.get("congress") == settings.CURRENT_CONGRESS
    return not ctx.tried_within(30 if current else 7)


def _jurisdictions_gate(new: dict, old: dict) -> list[str]:
    committees = new.get("committees") or {}
    failures = [f"{code} has no jurisdiction" for code, c in committees.items() if not (c or {}).get("jurisdiction")]
    if len(committees) < len(old.get("committees") or {}) - 3:
        failures.append(f"{len(committees)} committees where the copy in use has {len(old['committees'])}")
    return failures + _drift(new, old, "committees", keep=0.8)


# The committee-routing fit: refit whenever its inputs change (the
# jurisdictions above, or the labels in a commit) — its data_hash names them.
def _routing_due(ctx: Context) -> bool:
    return ctx.doc.get("data_hash") != committee_routing.data_hash() and not ctx.tried_within(7)


def _routing_gate(new: dict, old: dict) -> list[str]:
    failures = []
    if new.get("data_hash") != committee_routing.data_hash():
        failures.append("the fit is not of the inputs in use")
    if not (0 < float(new.get("threshold") or 0) < 1) or int(new.get("k") or 0) < 1:
        failures.append(f"threshold {new.get('threshold')}, k {new.get('k')}")
    return failures


def _population_gate(new: dict, old: dict) -> list[str]:
    states, before = new.get("states") or {}, old.get("states") or {}
    failures = []
    missing = {s for s in STATE_NAMES if s != "DC"} - set(states)
    if missing:
        failures.append(f"missing {sorted(missing)}")
    same_census = new.get("census") is not None and new.get("census") == old.get("census")
    limit = 0.01 if same_census else 0.25
    for code, value in states.items():
        if code in before and before[code] and abs(float(value) / float(before[code]) - 1) > limit:
            failures.append(f"{code} moved {float(value) / float(before[code]) - 1:+.1%}")
    return failures


def _token_df_gate(new: dict, old: dict) -> list[str]:
    docs, before = int(new.get("documents") or 0), int(old.get("documents") or 0)
    failures = [] if new.get("df") else ["no word table"]
    if before and docs < 0.9 * before and new.get("congresses") == old.get("congresses"):
        failures.append(f"{docs} titles where the copy in use counts {before}")
    return failures


def _coverage_gate(new: dict, old: dict) -> list[str]:
    failures = []
    for chamber in ("house", "senate"):
        floors = ((new.get(chamber) or {}).get("floors")) or {}
        if not floors:
            failures.append(f"no {chamber} floors")
        for ref, value in floors.items():
            if not 0 <= float(value) <= 1:
                failures.append(f"{chamber} {ref} floor {value}")
            prior = ((old.get(chamber) or {}).get("floors") or {}).get(ref)
            if prior is not None and abs(float(value) - float(prior)) > 0.3:
                failures.append(f"{chamber} {ref} floor moved {prior} -> {value}")
    return failures


# More than a year's weekdays: build_name_casing.py stops at the year's end.
NAME_CASING_ISSUES = 366


def _casing_gate(new: dict, old: dict) -> list[str]:
    forms = new.get("forms") or {}
    failures = [] if len(forms) >= 0.7 * len(old.get("forms") or {}) else [f"{len(forms)} forms"]
    return failures + _drift(new, old, "forms", keep=0.8)


# --- staleness of the files a person has to regenerate -------------------

def _ces_stale(ctx: Context) -> str | None:
    # The CES fields every even year and releases its Common Content the
    # following spring.
    fielded = ctx.doc.get("fielded_year")
    year = ctx.today.year - 1 if ctx.today >= date(ctx.today.year, 4, 1) else ctx.today.year - 2
    latest = year if year % 2 == 0 else year - 1
    if isinstance(fielded, int) and fielded < latest:
        return (f"the {latest} Cooperative Election Study should be out (this file is the {fielded} survey). "
                f"Its question ids change every survey (CC24_312f...), so map them in "
                f"scripts/fetch_ces_approval.py and rerun it; that mapping can't be derived by rule.")
    return None


def _bluesky_windows_stale(ctx: Context) -> str | None:
    found = re.search(r"generated (\d{4}-\d{2}-\d{2})", str(ctx.doc.get("_source") or ""))
    if found and ctx.today - date.fromisoformat(found[1]) > timedelta(days=365):
        return ("the posting windows were measured over a year ago; audiences shift by season. Rerun "
                "scripts/fetch_bluesky_activity.py (it needs websockets from scripts/requirements-research.txt, "
                "which the image doesn't carry).")
    return None


def _position_confidence_stale(ctx: Context) -> str | None:
    through = ctx.doc.get("calibrated_through")
    if isinstance(through, int) and settings.CURRENT_CONGRESS - 1 > through:
        return (f"calibrated through the {through}th Congress; the {through + 1}th has ended, so its "
                f"Voteview positions are complete. Rerun scripts/calibrate_position_confidence.py: its "
                f"structure choice (the one-standard-error rule) is a method decision, so the refit is reviewed, "
                f"and a changed structure is a scoring version.")
    return None


def _commemorative_stale(ctx: Context) -> str | None:
    measured = [int(m) for m in re.findall(r"les_spearman_(\d+)", json.dumps(sorted(ctx.doc)))]
    if measured and settings.CURRENT_CONGRESS - 2 > max(measured):
        return (f"measured against Volden & Wiseman's {max(measured)}th-Congress coding; the "
                f"{settings.CURRENT_CONGRESS - 2}th's should be published. Rerun "
                f"scripts/calibrate_commemorative.py (embedding model; a moved threshold is a scoring version).")
    return None


def _district_lean_stale(ctx: Context) -> str | None:
    years = [int(y) for y in re.findall(r"\d{4}", str(ctx.doc.get("_window") or ""))]
    if years and ctx.today >= date(max(years) + 4, 12, 15):
        return (f"windowed to {ctx.doc['_window']}; the {max(years) + 4} presidential election's precinct returns "
                f"should be published. The builder (scripts/build_district_lean.py) needs that cycle's sources "
                f"and certified-canvass corrections researched, as state_pvi.json's do.")
    return None


def _peer_gdp_stale(ctx: Context) -> str | None:
    """A newer Maddison Project release than the one on file, from
    DataverseNL's search (monthly; None when it can't be read)."""
    if ctx.tried_within(30):
        return None
    _record_try(ctx.name, "release check")
    have = re.search(r"Maddison Project Database (\d{4})", str(ctx.doc.get("_source") or ""))
    try:
        resp = httpx.get("https://dataverse.nl/api/search", params={
            "q": '"Maddison Project Database"', "type": "dataset", "per_page": 50,
        }, headers={"User-Agent": BOT_USER_AGENT}, timeout=30)
        resp.raise_for_status()
        names = [i.get("name", "") for i in resp.json().get("data", {}).get("items", [])]
    except Exception:
        logger.info("Maddison release check: DataverseNL search unreadable", exc_info=True)
        return None
    releases = [int(y) for n in names for y in re.findall(r"Maddison Project Database (\d{4})", n)]
    if have and releases and max(releases) > int(have[1]):
        return (f"Maddison Project Database {max(releases)} is published (this file is {have[1]}). It is a new "
                f"dataset with its own file id and layout: point scripts/fetch_peer_gdp.py at it and rerun "
                f"(a new release revises history, so president Effectiveness moves).")
    return None


# The election map's training polls (app/forecast/training_polls.py):
# Silver Bulletin adds a cycle each winter once its results are rated, so
# weekly until the file holds the last certified general, monthly after.
def _poll_cycles(doc: dict) -> set[int]:
    return {c for c in (training_polls.cycle_of(r) for r in training_polls.as_dicts(doc)) if c is not None}


def _training_polls_due(ctx: Context) -> bool:
    due = last_certified_general(ctx.today)
    behind = due is not None and due not in _poll_cycles(ctx.doc)
    return not ctx.tried_within(7 if behind else 30)


def _training_polls_command(ctx: Context) -> list[str]:
    return [PY, _script("fetch_silver_bulletin_polls.py"), "{out}", "--cache", "{tmp}"]


# The map's fits (scripts/fit_forecast_model.py): refit whenever the
# training polls in use change, and monthly besides, which picks up a new
# MEDSL version or a newly published FEC House compilation or Clerk's
# Statistics; weekly while a fit lacks the last certified general, so a
# newly published one is read within a week. Display only: nothing in
# scoring reads them.
FORECAST_OUTPUTS = ("forecast_training.json", "forecast_house_training.json", "forecast_house_model.json")
FORECAST_TRAINING = ("forecast_training.json", "forecast_house_training.json")


def _fits_behind(today: date) -> bool:
    due = last_certified_general(today)
    return due is not None and any(
        max((r["year"] for r in read(name).get("races") or []), default=0) < due for name in FORECAST_TRAINING)


def _forecast_due(ctx: Context) -> bool:
    polls = (read(training_polls.FILE_NAME).get("file") or {}).get("sha256")
    fitted = (ctx.doc.get("_polls") or {}).get("sha256")
    # A fit whose calibration table predates the code's bins is refitted
    # the next day, not at the month's end: the map hides that table.
    outdated = any(not model.calibration_current(ev.get("calibration"))
                   for ev in (ctx.doc.get("evaluation") or {}).values())
    return ((((polls is not None and polls != fitted) or outdated) and not ctx.tried_within(1))
            or not ctx.tried_within(30)
            or (not ctx.tried_within(7) and _fits_behind(ctx.today)))


def _forecast_command(ctx: Context) -> list[str]:
    runtime = runtime_data_path(training_polls.FILE_NAME)
    polls = runtime if pathlib.Path(runtime).exists() else str(BUNDLED / training_polls.FILE_NAME)
    return [PY, _script("fit_forecast_model.py"), "--cache", "{tmp}", "--polls", polls,
            "--senate-model", "{out}", "--senate-training", "{out:forecast_training.json}",
            "--house-model", "{out:forecast_house_model.json}",
            "--house-training", "{out:forecast_house_training.json}"]


def _cycles_fitted(doc: dict) -> set[int]:
    return {int(y) for ev in (doc.get("evaluation") or {}).values() for y in ev.get("cycles") or []}


def _forecast_gate(new: dict, old: dict) -> list[str]:
    failures = []
    fits, evaluation = new.get("fits") or {}, new.get("evaluation") or {}
    horizons = {str(h) for h in (old.get("fits") or {})} or set(fits)
    if not horizons or set(fits) != horizons or set(evaluation) != horizons:
        failures.append(f"horizons fitted {sorted(fits)}, evaluated {sorted(evaluation)}, expected {sorted(horizons)}")
    dropped = _cycles_fitted(old) - _cycles_fitted(new)
    if dropped:
        failures.append(f"the refit dropped cycles {sorted(dropped)}")
    return failures


# Familywise error rate of the forward check's alert.
FORWARD_ALPHA = 0.05


def _forward_finding(docs: dict[str, dict]) -> str | None:
    """Each chamber's forward check (its newest cycle, predicted by a fit on
    the cycles before it only), as an alert only when it is actionable.

    Every horizon of every chamber is tested in both directions
    (model.coverage_test: too narrow against 0.80, far too wide against
    model.WIDE_COVERAGE), and Holm's step-down procedure holds the chance
    of any false alert across all of those hypotheses to FORWARD_ALPHA.
    Holm rather than one pre-registered horizon: the live map uses every
    horizon in turn, so each is a claim the page makes, and Holm is never
    less powerful than Bonferroni over the same family. The horizons score
    the same races, so the correction is conservative; it errs toward
    silence, which suits an alert a person has to act on.

    Too narrow is the harmful direction (overconfident ranges) and alerts.
    Far too wide alerts naming the knob; a range a little wide in one
    cycle (2024's Senate: 31 of 32 at one and two weeks out) does not, and
    the page shows the figure either way. The fit is published regardless,
    so the page's figures match its inputs."""
    checks, pvalues = {}, {}
    for chamber, name in (("Senate", "forecast_model.json"), ("House", "forecast_house_model.json")):
        for h, ev in ((docs.get(name) or {}).get("evaluation") or {}).items():
            fw = ev.get("forward")
            if not fw or "coverage80Test" not in fw:
                continue
            checks[(chamber, int(h))] = fw
            for direction in ("under", "over"):
                pvalues[(chamber, int(h), direction)] = fw["coverage80Test"][direction]
    rejected = model.holm(pvalues, FORWARD_ALPHA)
    if not rejected:
        return None
    lines = []
    for chamber, h, direction in sorted(rejected):
        fw = checks[(chamber, h)]
        t = fw["coverage80Test"]
        what = (f"too narrow: 80% ranges held {t['held']} of {t['n']} results ({fw['coverage80']:.0%}), "
                f"p={t['under']:.2g} against 80%" if direction == "under" else
                f"far too wide: 80% ranges held {t['held']} of {t['n']} ({fw['coverage80']:.0%}), "
                f"p={t['over']:.2g} against {model.WIDE_COVERAGE:.0%}; the knob is the spread's scale factor "
                f"(model.fit_scale, `scale` in the fit), fitted on all training cycles alike")
        lines.append(f"{chamber} {fw['cycle']}, {h} days out (fitted on {fw['trainedOn'][0]}-{fw['trainedOn'][1]}): "
                     f"{what}")
    return ("the forward check (each chamber's newest cycle, fitted on earlier cycles only) fails at familywise "
            f"{FORWARD_ALPHA:.0%} (Holm, {len(pvalues)} hypotheses): " + "; ".join(lines) + ". The refit is published "
            "and the map shows these figures.")


DATA_FILES: list[DataFile] = [
    DataFile("poll_close_times.json", command=_poll_close_command, due=_poll_close_due, gate=_poll_close_gate,
             timeout_s=300),
    DataFile("election_calendar.json", command=_calendar_command, due=_calendar_due, gate=_calendar_gate,
             timeout_s=1800),
    DataFile("fec_party_codes.json", command=lambda ctx: [PY, _script("fetch_fec_party_codes.py"), "{out}"],
             due=_every(30), gate=_fec_party_gate, timeout_s=300),
    DataFile("committee_jurisdictions.json",
             command=lambda ctx: [PY, _script("fetch_committee_jurisdictions.py"), "{out}"],
             due=_jurisdictions_due, gate=_jurisdictions_gate, timeout_s=900),
    DataFile("committee_routing_calibration.json",
             command=lambda ctx: [PY, _script("calibrate_committee_routing.py"), "--output", "{out}"],
             due=_routing_due, gate=_routing_gate, heavy=True, timeout_s=3600),
    DataFile("state_population.json", command=lambda ctx: [PY, _script("fetch_state_population.py"), "{out}"],
             due=_every(90), gate=_population_gate, moves_scores=True, timeout_s=300),
    DataFile("bill_title_token_df.json",
             command=lambda ctx: [PY, _script("calibrate_lda_bill_match.py"), "--congress",
                                  str(settings.CURRENT_CONGRESS), "--skip-calibration", "--output", "{out}"],
             due=_every(30), gate=_token_df_gate, heavy=True, timeout_s=3600),
    DataFile("reference_coverage.json",
             command=lambda ctx: [PY, _script("calibrate_reference_coverage.py"), "--out", "{out}"],
             due=_every(30), gate=_coverage_gate, heavy=True, moves_scores=True, timeout_s=3 * 3600),
    # The whole of last year's issues, as the bundled table was built (its
    # _source: "the first 247 issues of 2025"); the script's own default of
    # 120 issues read half the prose and lost a third of the forms, which the
    # gate refused (2026-10-18: 8,832 forms against 13,395).
    DataFile("name_casing.json",
             command=lambda ctx: [PY, _script("build_name_casing.py"), "--year", str(ctx.today.year - 1),
                                  "--days", str(NAME_CASING_ISSUES), "--output", "{out}", "--cache", "{tmp}"],
             due=lambda ctx: not ctx.tried_within(365) and ctx.today >= date(ctx.today.year, 2, 1),
             gate=_casing_gate, heavy=True, timeout_s=4 * 3600),
    DataFile(training_polls.FILE_NAME, command=_training_polls_command, due=_training_polls_due,
             gate=training_polls.gate, timeout_s=900),
    DataFile("forecast_model.json", command=_forecast_command, due=_forecast_due, gate=_forecast_gate,
             outputs=FORECAST_OUTPUTS, check=_forward_finding, heavy=True, timeout_s=3600),
    DataFile("ces_member_approval.json", stale=_ces_stale),
    DataFile("peer_gdp_per_capita.json", stale=_peer_gdp_stale),
    DataFile("bluesky_post_windows.json", stale=_bluesky_windows_stale),
    DataFile("position_confidence.json", stale=_position_confidence_stale),
    DataFile("commemorative_calibration.json", stale=_commemorative_stale),
    DataFile("district_lean.json", stale=_district_lean_stale),
]
