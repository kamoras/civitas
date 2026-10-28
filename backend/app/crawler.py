"""What Civitas's crawler is, what it reads and how fast: the facts behind
/about/crawler (GET /api/crawler), read from the code so the page can't
drift from what the code does.

- **Who**: the one User-Agent every request carries and its contact
  (app/contact.py), and whether requests are signed (Web Bot Auth, a key
  this instance holds).
- **What it reads**: every module under app/ that imports an HTTP client,
  with the hosts its source names (string constants that are URLs, read
  with `ast`, never imported: importing every fetch module would load
  OpenCV and the browser into the API process) and the hosts in the
  `*_sources.json` registries it names. Those registries are where the
  per-state readers keep their addresses.
- **How fast**: each module's own RateLimiter, read from the same source:
  the requests a second it is constructed with, a literal, a setting or a
  module constant. A module without one says so (`rps: null`).
- **When**: the schedules of the jobs that read from outside, which the
  scheduler takes from SCHEDULE below rather than typing its own, so the
  page and the scheduler can't disagree.

Outside app/pipeline/: nothing here classifies or scores, so it never
moves the analysis-code fingerprint.
"""

import ast
import json
import re
from functools import lru_cache
from pathlib import Path

from app import web_bot_auth
from app.pipeline import run_tracker
from app.config import settings
from app.contact import CONTACT_EMAIL, CRAWLER_PAGE, HOST_USER_AGENTS, USER_AGENT
from app.photo_hosts import PHOTO_SOURCE_HOSTS
from app.site_url import SITE_DOMAIN


APP = Path(__file__).resolve().parent
DATA = APP / "data"

# A module that imports one of these sends requests of its own.
_CLIENTS = frozenset({
    "httpx", "requests", "urllib.request", "playwright.async_api",
    "app.http_client", "app.pipeline.fetch.http_utils",
})
# A string constant that is a URL (starts with one; prose that mentions a
# URL, such as a licence line, is not a request).
_URL = re.compile(r"^https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?=[:/?#]|$)")
_ANY_URL = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?=[:/?#\"'\s]|$)")
# Never a source: this site.
_OWN = frozenset({SITE_DOMAIN, f"www.{SITE_DOMAIN}"})
# URL constants that are never fetched: the poll licence the map prints
# (forecast/polls.POLL_LICENSE_URL), and XML namespace names, which are
# identifiers in a URL's shape (news_feeds' Atom, the xlsx readers').
_LINKED_ONLY = frozenset({"creativecommons.org", "www.w3.org", "schemas.openxmlformats.org"})
# The frontend's photo route (frontend/src/app/photo) fetches each picture
# from the hosts app/photos.py allows for its kind, once per picture; nginx
# keeps it a week.
PHOTO_ROUTE = "frontend/src/app/photo"

# The jobs that read from outside, by the scheduler's job id: their plain
# name, when they run (APScheduler cron fields, UTC) and what they read. scheduler.start
# builds each trigger from here. The nightly chain's time is a setting
# (PIPELINE_CRON_SCHEDULE).
SCHEDULE: dict[str, dict] = {
    "action_refresh": {
        "name": "Action Center",
        "cron": {"minute": "15"},
        "reads": "News feeds, Google Trends and Bluesky's public trends for the Action Center",
    },
    "bill_status_refresh": {
        "name": "Bill status refresh",
        "cron": {"minute": "45"},
        "reads": "Congress.gov's list of bills updated since the last pass",
    },
    "congress_activity_sync": {
        "name": "Congress floor record",
        "cron": {"minute": "10,40"},
        "reads": "The House and Senate floor records and the Congressional Record (GovInfo)",
    },
    "agency_sync": {
        "name": "Agency sync",
        "cron": {"minute": "30"},
        "reads": "The Federal Register's agency list and USAspending's budgets, once a week each",
    },
    "nominations_sync": {
        "name": "Nominations",
        "cron": {"hour": "14", "minute": "50"},
        "reads": "Congress.gov's presidential nominations",
    },
    "data_refresh": {
        "name": "Data file refresh",
        "cron": {"hour": "15-23,0-1", "minute": "40"},
        "reads": "Public datasets behind the site's reference files, each on its own cadence (weekly to yearly)",
    },
    "election_map_refresh": {
        "name": "Election map",
        "cron": {"hour": "*/3", "minute": "55"},
        "reads": "Wikipedia's race articles for polls, before an election only",
    },
    "election_coverage_refresh": {
        "name": "Race coverage",
        "cron": {"minute": "*/15"},
        "reads": "State newsrooms' feeds for race coverage, in election season only",
    },
    "election_ballot_sync": {
        "name": "Ballot sync",
        "cron": {"hour": "*/6", "minute": "50"},
        "reads": "State election offices' candidate lists, in election season only",
    },
    "election_results_readiness": {
        "name": "Results feed check",
        "cron": {"hour": "14", "minute": "20"},
        "reads": "Each covered state's results feed, once a day in the two weeks before election day",
    },
    "election_results_sync": {
        "name": "Election night results",
        "cron": {"minute": "*/5"},
        "reads": "State election-night results feeds, from poll close until the count settles",
    },
}


# The nightly chain's links, in order (scheduler._nightly_pipeline builds
# its Link list in this order; test_crawler holds the two together).
NIGHTLY_LINKS = ("Senate", "Supplementary", "House", "Stock trades", "Election")


def _fields(fields: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in fields.items())


def when(fields: dict) -> str:
    """A cron's fields in words, for the page: "every hour at :15", "every
    15 minutes", "every 6 hours at :50", "daily at 14:50 UTC", "every hour
    at :40 from 15:40 to 01:40 UTC". Anything else is given as its fields."""
    minute, hour = str(fields.get("minute", "*")), str(fields.get("hour", "*"))
    if any(str(fields.get(k, "*")) != "*" for k in ("day", "month", "day_of_week")):
        return _fields(fields)
    if minute.startswith("*/") and hour == "*":
        return f"every {minute[2:]} minutes"
    marks = minute.split(",")
    if not all(m.isdigit() for m in marks):
        return _fields(fields)
    at = " and ".join(f":{int(m):02d}" for m in marks)
    if hour == "*":
        return f"every hour at {at}"
    if hour.startswith("*/"):
        return f"every {hour[2:]} hours at {at}"
    if len(marks) > 1:
        return _fields(fields)
    if hour.isdigit():
        return f"daily at {int(hour):02d}:{int(minute):02d} UTC"
    spans = hour.split(",")
    if all(re.fullmatch(r"\d+-\d+", s) for s in spans):
        first, last = int(spans[0].split("-")[0]), int(spans[-1].split("-")[1])
        return f"every hour at {at} from {first:02d}{at} to {last:02d}{at} UTC"
    return _fields(fields)


def cron(job_id: str) -> dict:
    """The cron fields the scheduler builds `job_id`'s trigger from."""
    return {**SCHEDULE[job_id]["cron"], "timezone": "UTC"}


def _strings(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, ast.JoinedStr) and node.values:
            first = node.values[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                yield first.value


def _registry_hosts(value, out: set[str]) -> None:
    """Every URL host in a sources registry, leaving out "_"-keyed notes
    (their citations are not addresses the readers fetch)."""
    if isinstance(value, dict):
        for k, v in value.items():
            if not str(k).startswith("_"):
                _registry_hosts(v, out)
    elif isinstance(value, list):
        for v in value:
            _registry_hosts(v, out)
    elif isinstance(value, str):
        out.update(_ANY_URL.findall(value))


def _constants(tree: ast.Module) -> dict[str, ast.expr]:
    return {
        t.id: node.value
        for node in tree.body if isinstance(node, ast.Assign)
        for t in node.targets if isinstance(t, ast.Name)
    }


def _evaluate(node: ast.expr, consts: dict[str, ast.expr]) -> float | None:
    """A RateLimiter's requests a second, from its source: a number, a
    settings field, a module constant, or a conditional of those."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Attribute) and getattr(node.value, "id", None) == "settings":
        value = getattr(settings, node.attr, None)
        return float(value) if isinstance(value, (int, float)) else None
    if isinstance(node, ast.Name) and node.id in consts:
        return _evaluate(consts[node.id], consts)
    if isinstance(node, ast.IfExp):
        test = node.test
        if isinstance(test, ast.Attribute) and getattr(test.value, "id", None) == "settings":
            return _evaluate(node.body if getattr(settings, test.attr, None) else node.orelse, consts)
    return None


def _limiters(tree: ast.Module) -> list[float | None]:
    consts = _constants(tree)
    rates = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "RateLimiter":
            arg = node.args[0] if node.args else next(
                (k.value for k in node.keywords if k.arg == "rps"), None)
            rates.append(_evaluate(arg, consts) if arg is not None else None)
    return rates


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(APP.parent).with_suffix("").parts)


@lru_cache(maxsize=1)
def readers() -> list[dict]:
    """Every module that sends requests, with the hosts it names and its
    rate limit. Read once per process: the source doesn't change under a
    running process."""
    out = []
    for path in sorted(APP.rglob("*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        hosts: set[str] = set()
        registry_hosts: set[str] = set()
        registries = set()
        for text in _strings(tree):
            if (m := _URL.match(text)):
                hosts.add(m.group(1))
            if text.endswith("_sources.json") and (DATA / text).exists():
                registries.add(text)
                _registry_hosts(json.loads((DATA / text).read_text(encoding="utf-8")), registry_hosts)
        hosts = {h.lower() for h in hosts} - _OWN - _LINKED_ONLY
        registry_hosts = {h.lower() for h in registry_hosts} - _OWN - hosts
        if not (hosts and imports & _CLIENTS) and not registry_hosts:
            continue
        rates = [r for r in _limiters(tree) if r]
        out.append({
            "module": _module_name(path),
            "hosts": sorted(hosts) if imports & _CLIENTS else [],
            # Addresses in a sources registry this module loads: each is
            # read by the per-state reader its entry names, at that reader's pace.
            "registryHosts": sorted(registry_hosts),
            "registries": sorted(registries),
            # The slowest of its limiters: what every request it sends waits for at least.
            "rps": min(rates) if rates else None,
        })
    return out


def pace(rps: float | None, registry: str | None = None) -> str:
    """A reader's limit in words: "1 request a second at most", "1 request
    every 4 seconds at most", "2 requests a second at most"."""
    if registry:
        return f"paced by the state reader its entry in {registry} names"
    if not rps:
        return "no limiter of its own; the site's pace below applies"
    if rps < 1:
        gap = 1 / rps
        return f"1 request every {gap:g} seconds at most"
    return f"{rps:g} request{'s' if rps != 1 else ''} a second at most"


@lru_cache(maxsize=1)
def run_readers() -> dict[str, set[str]]:
    """Each pipeline run's table name -> the reader modules its run imports,
    read from the code so it is right before any run has recorded a read.

    A run's module is the one that takes its run row (calls an `acquire_*`
    lock with the run model, run_tracker.run_tables' models). From it,
    every `app.pipeline.*` module it imports, anywhere in the file, and so
    on, but not through another run's module: the Senate helpers the House
    run imports are not the House run reading the Senate's sources. Outside
    app/pipeline/ nothing is followed (the scheduler and alert modules
    import nearly everything)."""
    index = _module_index()
    owners = _run_modules(index)
    reader_modules = {r["module"] for r in readers()}
    out: dict[str, set[str]] = {}
    for start, table in owners.items():
        others = set(owners) - {start}
        out.setdefault(table, set()).update(_closure(index, {start}, others) & reader_modules)
    return out


SCHEDULER_MODULE = "app.scheduler"
# The job that runs the nightly chain: its runs are run_readers' own.
NIGHTLY_JOB = "pipeline_run"


def _module_index() -> dict[str, dict]:
    """Every app module: its parse, the names its top level imports (name ->
    (module, attribute)) and its top-level functions."""
    index = {}
    for path in sorted(APP.rglob("*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
                for a in node.names:
                    names[a.asname or a.name] = (node.module, a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("app."):
                        names[a.asname or a.name] = (a.name, None)
        functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        constants = {t.id: n.value for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value
                     for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)}
        index[_module_name(path).removesuffix(".__init__")] = {
            "tree": tree, "names": names, "functions": functions, "constants": constants,
        }
    return index


def _function_seeds(index: dict, module: str, node: ast.AST, orchestrators: set[str], seen: set) -> set[str]:
    """The modules a function reaches. An app.pipeline module it names
    counts whole (and _closure follows its imports). Any other module, and
    a run's module, is followed function by function: the functions of it
    the code calls, and what those name, so a module used for one helper
    (data_refresh.read, a file the refresh keeps) isn't taken for the
    module's reading (data_refresh's fetches). A module counts as reached
    when one of its functions it reaches sends a request (_requests)."""
    seeds: set[str] = set()
    info = index[module]

    def walk_function(mod: str, name: str) -> None:
        fn = index[mod]["functions"].get(name)
        if fn is None or (mod, name) in seen:
            return
        seen.add((mod, name))
        if mod not in orchestrators and _requests(index, mod, fn):
            seeds.add(mod)
        seeds.update(_function_seeds(index, mod, fn, orchestrators, seen))

    def ref(mod: str, attr: str | None) -> None:
        if attr and f"{mod}.{attr}" in index:
            mod, attr = f"{mod}.{attr}", None
        if mod not in index:
            return
        if mod.startswith("app.pipeline.") and mod not in orchestrators:
            seeds.add(mod)
        elif attr:
            walk_function(mod, attr)

    modules = {name: target for name, target in info["names"].items()}
    for n in ast.walk(node):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("app"):
            for a in n.names:
                ref(n.module, a.name)
                modules[a.asname or a.name] = (n.module, a.name)
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in modules:
            mod, attr = modules[n.value.id]
            target = f"{mod}.{attr}" if attr else mod
            if target in index:  # module.function
                ref(target, n.attr)
        elif isinstance(n, ast.Name):
            if n.id in info["functions"]:
                walk_function(module, n.id)
            elif n.id in modules:
                ref(*modules[n.id])
            elif n.id in info["constants"] and (module, n.id) not in seen:
                # A module table of callbacks (data_refresh's DATA_FILES): the
                # functions it names are called through it.
                seen.add((module, n.id))
                value = info["constants"][n.id]
                if any(isinstance(m, ast.Name) and m.id in info["functions"] for m in ast.walk(value)):
                    if _requests(index, module, value):
                        seeds.add(module)
                    seeds.update(_function_seeds(index, module, value, orchestrators, seen))
    return seeds


def _requests(index: dict, module: str, fn: ast.AST) -> bool:
    """Whether a function sends a request itself: it names a URL, a module
    constant that is one, or anything imported from an HTTP client module
    (_CLIENTS: httpx, requests, the app's own client and fetch helpers)."""
    info = index[module]
    tree_consts = info["constants"]
    clients = {name for name, (mod, _) in info["names"].items() if mod in _CLIENTS}
    clients |= {a.asname or a.name for node in info["tree"].body if isinstance(node, ast.Import)
                for a in node.names if a.name in _CLIENTS}
    for n in ast.walk(fn):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and _URL.match(n.value):
            return True
        if isinstance(n, ast.Name):
            if n.id in clients:
                return True
            value = tree_consts.get(n.id)
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and _URL.match(value.value):
                return True
        if isinstance(n, ast.ImportFrom) and n.module in _CLIENTS:
            return True
    return False


def _closure(index: dict, seeds: set[str], orchestrators: set[str]) -> set[str]:
    """The seeds and every app.pipeline module an app.pipeline seed imports,
    anywhere in the file, and so on, never through a run's module. A seed
    outside app/pipeline/ was reached function by function
    (_function_seeds) and is not expanded."""
    seen, todo = set(), list(seeds)
    while todo:
        module = todo.pop()
        if module in seen:
            continue
        seen.add(module)
        if module.startswith("app.pipeline."):
            todo.extend(m for m in _imports(ast.walk(index[module]["tree"]))
                        if m.startswith("app.pipeline.") and m in index and m not in orchestrators)
    return seen


def _imports(nodes) -> set[str]:
    """The app modules these statements import ("from app import x" is app.x)."""
    found: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and node.module and (node.module == "app" or node.module.startswith("app.")):
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith("app."))
    return found


def _cron_literal(trigger: ast.AST) -> dict | None:
    """A CronTrigger(...)'s fields when written as literals."""
    if not isinstance(trigger, ast.Call):
        return None
    fields = {k.arg: k.value.value for k in trigger.keywords
              if k.arg and isinstance(k.value, ast.Constant) and k.arg != "timezone"}
    return fields or None


@lru_cache(maxsize=1)
def job_readers() -> dict[str, dict]:
    """Each scheduler job but the nightly chain -> {"label", "modules"}: its
    plain name and when it runs ("Action Center, every hour at :15") and
    the reader modules it reaches, read from scheduler.py's add_job calls
    the way run_readers reads the runs, so a host the hourly and weekly jobs
    read is never "read by" nothing."""
    index = _module_index()
    orchestrators = set(_run_modules(index))
    reader_modules = {r["module"] for r in readers()}
    tree = index[SCHEDULER_MODULE]["tree"]
    out = {}
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and getattr(call.func, "attr", None) == "add_job" and call.args):
            continue
        job = next((k.value.value for k in call.keywords if k.arg == "id" and isinstance(k.value, ast.Constant)), None)
        if not job or job == NIGHTLY_JOB:
            continue
        seeds = _function_seeds(index, SCHEDULER_MODULE, call.args[0], orchestrators, set())
        modules = _closure(index, seeds - orchestrators, orchestrators) & reader_modules
        spec = SCHEDULE.get(job, {})
        fields = spec.get("cron") or (_cron_literal(call.args[1]) if len(call.args) > 1 else None)
        name = spec.get("name") or job.replace("_", " ").capitalize()
        out[job] = {"label": f"{name}, {when(fields)}" if fields else name, "modules": modules}
    return out


@lru_cache(maxsize=1)
def request_readers() -> set[str]:
    """The reader modules an app.api module imports itself, anywhere in the
    file: what the API reads when a page asks (a document's public comments
    from Regulations.gov). Direct imports only: the API imports far more
    for its constants and helpers than it reads on request."""
    index = _module_index()
    return {m for name, info in index.items() if name.startswith("app.api.")
            for m in _imports(ast.walk(info["tree"]))} & {r["module"] for r in readers()}


def _run_modules(index: dict) -> dict[str, str]:
    """The module that takes each run's row -> the run's table name."""
    models = {m.__name__: m.__tablename__ for m in run_tracker.run_tables().values()}
    owner = {}
    for name, info in index.items():
        for node in ast.walk(info["tree"]):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "").startswith("acquire_"):
                for arg in node.args:
                    if isinstance(arg, ast.Name) and arg.id in models:
                        owner[name] = models[arg.id]
    return owner


def hosts() -> list[dict]:
    """The readers by host: each host, and each module that reads it with
    that module's limit (`rps`), or the registry that lists it
    (`registry`, paced by the reader its entry names)."""
    by_host: dict[str, list[dict]] = {}
    for r in readers():
        for h in r["hosts"]:
            by_host.setdefault(h, []).append({"module": r["module"], "rps": r["rps"], "registry": None})
        for h in r["registryHosts"]:
            for reg in r["registries"]:
                entry = {"module": r["module"], "rps": None, "registry": reg}
                if entry not in by_host.setdefault(h, []):
                    by_host[h].append(entry)
    for h in sorted(set().union(*PHOTO_SOURCE_HOSTS.values())):
        by_host.setdefault(h, []).append({"module": PHOTO_ROUTE, "rps": None, "registry": None})
    return [
        {"host": h, "readers": [{**r, "pace": pace(r["rps"], r["registry"])} for r in by_host[h]]}
        for h in sorted(by_host)
    ]


def _signing() -> dict:
    configured = web_bot_auth.KEY is not None
    return {
        "configured": configured,
        "directory": (
            f"{settings.SITE_URL.rstrip('/')}{web_bot_auth.DIRECTORY_PATH}" if configured else None
        ),
    }


def _nightly_when() -> str:
    parts = settings.PIPELINE_CRON_SCHEDULE.split()
    if len(parts) != 5:
        return settings.PIPELINE_CRON_SCHEDULE
    return when(dict(zip(("minute", "hour", "day", "month", "day_of_week"), parts)))


def summary() -> dict:
    """GET /api/crawler's body."""
    listed = hosts()
    return {
        "userAgent": USER_AGENT,
        "userAgentExceptions": [
            {"host": host, "userAgent": ua, "reason": why}
            for host, (ua, why) in sorted(HOST_USER_AGENTS.items())
        ],
        "page": CRAWLER_PAGE,
        "contact": CONTACT_EMAIL or None,
        "signing": _signing(),
        "nightly": {"when": _nightly_when(), "links": list(NIGHTLY_LINKS)},
        "schedule": [
            {"job": job, "when": when(spec["cron"]), "reads": spec["reads"]}
            for job, spec in SCHEDULE.items()
        ],
        "hosts": listed,
        "hostCount": len(listed),
        "readerCount": len({r["module"] for h in listed for r in h["readers"]}),
        "unlimitedHostCount": sum(
            1 for h in listed if all(r["rps"] is None and r["registry"] is None for r in h["readers"])
        ),
    }
