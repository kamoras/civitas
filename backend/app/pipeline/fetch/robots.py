"""robots.txt, read by the rules its own standard sets (RFC 9309).

urllib.robotparser predates the RFC and differs from it where it matters
to a crawler that wants to be told no:

- it names a group's agent by substring of the User-Agent cut at its first
  "/" — the whole browser-shaped string reads as "mozilla", and a group
  for "vita" applies to "Civitas";
- it applies the first rule whose path is a prefix, where the RFC applies
  the longest match ("Allow: /" then "Disallow: /results/" blocked
  nothing);
- it has no "*" or "$" in paths.

Parsing (§2.1): a group is a run of user-agent lines followed by its
records; any other record — a rule, even an empty one, or crawl-delay and
the like — ends the run, so the next user-agent line starts a new group.
Sitemap lines belong to no group. A UTF-8 byte-order mark is dropped, and
only the first MAX_BYTES are read (§2.5 asks for at least 500 KiB).

Matching (§2.2): a group applies when one of its user-agent lines names
our product token, case-insensitively; the groups naming it are combined,
and only when none does do the "*" groups apply. Paths and patterns are
compared after the same percent-encoding normalisation (§2.2.2). Of the
rules whose pattern matches, the longest pattern wins, an allow winning a
tie; no match means allowed. /robots.txt itself is always allowed. The
matcher is linear in path × pattern: a site's file is external input, and
a backtracking regex over a run of "*"s could stall the event loop.

What an unreadable file means is the caller's to apply (§2.3.1): a 4xx
means there are no rules, a 5xx or no answer means assume complete
disallow.
"""

import re
from dataclasses import dataclass, field

# §2.5: parse at least 500 KiB; nothing more is read.
MAX_BYTES = 512 * 1024
# RFC 3986 unreserved characters, which percent-encoding must not change.
_UNRESERVED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_HEX = set("0123456789abcdefABCDEF")


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)
    # Any record after the user-agent lines ends the run of them, even one
    # that adds no rule (an empty Disallow, a crawl-delay).
    closed: bool = False


@dataclass
class Robots:
    """A parsed robots.txt, or one of the two blanket answers."""

    groups: list[_Group] = field(default_factory=list)
    # When set, the answer for every path: True for no rules (a 4xx),
    # False for complete disallow (unreachable).
    blanket: bool | None = None

    def allows(self, agent: str, path: str) -> bool:
        if self.blanket is not None:
            return self.blanket
        path = normalize(path or "/")
        if path == "/robots.txt":
            return True
        token = _agent_token(agent)
        mine = [g for g in self.groups if token in g.agents]
        rules = [r for g in (mine or [g for g in self.groups if "*" in g.agents]) for r in g.rules]
        best: tuple[int, bool] | None = None
        for allow, pattern in rules:
            if _matches(pattern, path):
                key = (len(pattern), allow)
                if best is None or key > best:
                    best = key
        return True if best is None else best[1]


ALLOW_ALL = Robots(blanket=True)
DISALLOW_ALL = Robots(blanket=False)


def _agent_token(value: str) -> str:
    """The product token a user-agent line names: "Civitas/1.0" and
    "Civitas;" -> "civitas" (§2.2.1: letters, "_" and "-")."""
    match = re.match(r"[A-Za-z_-]+", value.strip())
    return match.group(0).lower() if match else ""


def normalize(value: str) -> str:
    """§2.2.2: non-ASCII as UTF-8 percent-encoding, %XX of an unreserved
    character decoded, every other %XX in upper case — so /~joe,
    /%7Ejoe and /%7ejoe are one path. "*" and "$" pass through."""
    out = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "%" and i + 2 < len(value) and value[i + 1] in _HEX and value[i + 2] in _HEX:
            decoded = chr(int(value[i + 1:i + 3], 16))
            out.append(decoded if decoded in _UNRESERVED else "%" + value[i + 1:i + 3].upper())
            i += 3
            continue
        if ord(ch) > 127:
            out.append("".join(f"%{b:02X}" for b in ch.encode("utf-8")))
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def parse(text: str) -> Robots:
    groups: list[_Group] = []
    current: _Group | None = None
    for raw in text[:MAX_BYTES].lstrip("﻿").splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if current is None or current.closed:
                current = _Group()
                groups.append(current)
            current.agents.append("*" if value == "*" else _agent_token(value))
        elif key == "sitemap" or current is None:
            continue  # Belongs to no group.
        else:
            current.closed = True
            if key in ("allow", "disallow") and value:  # An empty Disallow disallows nothing.
                current.rules.append((key == "allow", normalize(value)))
    return Robots(groups=groups)


def _matches(pattern: str, path: str) -> bool:
    """§2.2.3: "*" is any run of characters, a trailing "$" pins the end;
    otherwise a pattern matches as a prefix. Tracks the set of path
    positions the pattern so far can end at, so it never backtracks."""
    anchored = pattern.endswith("$")
    body = re.sub(r"\*+", "*", pattern[:-1] if anchored else pattern)
    positions = [0]
    for ch in body:
        if ch == "*":
            positions = list(range(positions[0], len(path) + 1))
        else:
            positions = [p + 1 for p in positions if p < len(path) and path[p] == ch]
            if not positions:
                return False
    return len(path) in positions if anchored else True
