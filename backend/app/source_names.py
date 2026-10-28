"""Each source's public name: who publishes it and what Civitas takes from
it, the words /about/data's source map shows (app/source_health.py).

Named where the code already names it, else from the source ledger's data:

1. **News feeds**: the outlet's name in fetch/news_feeds.py (NEWS_FEEDS,
   STATE_NEWS_FEEDS).
2. **State election sources**: the state's entry in a sources registry
   (`*_sources.json`, the registries app/crawler.py reads): the name of the
   office that republished the document where the host is that office's,
   else the entry's `source_name` without its parenthetical. A per-state
   reader module (`ballot_measures_xx`, `state_candidates_xx`) names its
   state's entry the same way.
3. **Everything else**: `app/data/source_ledger.json`.

A host none of these names has no public name, and a test fails until the
ledger names it. Read path only, outside app/pipeline/.
"""

import json
import re
from functools import lru_cache

from app import crawler
from app.pipeline.fetch.news_feeds import NEWS_FEEDS, STATE_NEWS_FEEDS
from app.state_names import STATE_NAMES

LEDGER_FILE = "source_ledger.json"

# What each kind of state source supplies, by the registry or reader module
# it comes from.
_CANDIDATE_TAKES = "{state}'s certified candidates and election results"
_MEASURE_TAKES = "{state}'s ballot measures, quoted as published"
_TAKES = {
    "state_candidate_sources": _CANDIDATE_TAKES,
    "ballot_measure_pdf_sources": _MEASURE_TAKES,
}
_MODULE_TAKES = {"ballot_measures": _MEASURE_TAKES, "state_candidates": _CANDIDATE_TAKES}
_MODULE_REGISTRY = {"ballot_measures": "ballot_measure_pdf_sources",
                    "state_candidates": "state_candidate_sources"}
_MODULE_STATE = re.compile(r"\.(ballot_measures|state_candidates)_([a-z]{2})$")


def _host(url: str) -> str | None:
    m = crawler._ANY_URL.search(url)
    return m.group(1).lower() if m else None


def _plain(name: str) -> str:
    """A registry's name as site copy: no parenthetical, nothing after a dash."""
    return re.sub(r"\s*\([^()]*\)", "", name).split(" — ")[0].strip()


def _registry(name: str) -> dict:
    try:
        return json.loads((crawler.DATA / f"{name}.json").read_text(encoding="utf-8")).get("states") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def _registry_names(registry: str, out: dict) -> None:
    """host -> name from each state entry of a sources registry: the
    nearest enclosing `name` (a republishing office) or the entry's
    `source_name`."""
    takes = _TAKES[registry]
    for code, entry in _registry(registry).items():
        if not isinstance(entry, dict) or code not in STATE_NAMES:
            continue
        state_name = _plain(entry.get("source_name") or f"{STATE_NAMES[code]} election officials")

        def walk(node, name):
            if isinstance(node, dict):
                name = node.get("name") if isinstance(node.get("name"), str) else name
                for k, v in node.items():
                    if not str(k).startswith("_"):
                        walk(v, name)
            elif isinstance(node, list):
                for v in node:
                    walk(v, name)
            elif isinstance(node, str) and (host := _host(node)):
                out.setdefault(host, set()).add((name, takes.format(state=STATE_NAMES[code])))

        walk(entry, state_name)


@lru_cache(maxsize=1)
def names() -> dict[str, dict]:
    """host -> {"publisher", "takes"} for every host a rule or the ledger names."""
    out: dict[str, dict] = {}
    for feed in NEWS_FEEDS:
        out[_host(feed["url"])] = {"publisher": feed["name"], "takes": "News articles for the Action Center"}
    for feed in STATE_NEWS_FEEDS:
        out[_host(feed["url"])] = {
            "publisher": feed["name"],
            "takes": f"News coverage of {STATE_NAMES.get(feed['state'], feed['state'])}'s races",
        }
    found: dict[str, set] = {}
    for registry in _TAKES:
        _registry_names(registry, found)
    for reader in crawler.readers():
        if not (m := _MODULE_STATE.search(reader["module"])):
            continue
        kind, code = m.group(1), m.group(2).upper()
        entry = _registry(_MODULE_REGISTRY[kind]).get(code) or {}
        if code in STATE_NAMES and entry.get("source_name"):
            for host in reader["hosts"]:
                found.setdefault(host, set()).add(
                    (_plain(entry["source_name"]), _MODULE_TAKES[kind].format(state=STATE_NAMES[code])))
    for host, pairs in found.items():
        if host in out:
            continue
        publishers = sorted({p for p, _ in pairs})
        takes = sorted({t for _, t in pairs})
        # One host can serve two registries or readers (a state's ballot
        # measures and its candidates): the shortest name, each thing taken.
        out[host] = {"publisher": min(publishers, key=len), "takes": "; ".join(takes)}
    try:
        ledger = json.loads((crawler.DATA / LEDGER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}
    for entry in ledger.get("sources", []):
        for host in entry["hosts"]:
            out[host] = {"publisher": entry["publisher"], "takes": entry["takes"]}
    return out
