"""Hosts whose own terms of use forbid automated access
(app/data/host_terms.json): crawl_policy never asks them, /about/crawler
lists them and the source map says why."""

import json
import os
from functools import lru_cache

_PATH = os.path.join(os.path.dirname(__file__), "data", "host_terms.json")


@lru_cache(maxsize=1)
def all_hosts() -> dict[str, dict]:
    """The file's hosts, by lowercase name."""
    with open(_PATH, encoding="utf-8") as f:
        return {h.lower(): e for h, e in (json.load(f).get("hosts") or {}).items()}


def forbid(host: str) -> dict | None:
    """The entry covering `host` (its own name, or a name above it unless
    that entry sets "subdomains": false), or None."""
    host = (host or "").lower().rstrip(".")
    parts = host.split(".")
    terms = all_hosts()
    for i in range(len(parts) - 1):
        name = ".".join(parts[i:])
        entry = terms.get(name)
        if entry and (name == host or entry.get("subdomains", True)):
            return entry
    return None
