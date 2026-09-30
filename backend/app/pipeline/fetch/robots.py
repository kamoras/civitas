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

Matching here (RFC 9309 §2.2): a group applies when one of its
user-agent lines names our product token, case-insensitively; the groups
naming it are combined, and only when none does do the "*" groups apply.
Of the rules whose pattern matches the path, the longest pattern wins, an
allow winning a tie; no match means allowed. /robots.txt itself is always
allowed.

What an unreadable file means is the caller's to apply (§2.3.1): a 4xx
means there are no rules, a 5xx or no answer means assume complete
disallow.
"""

import re
from dataclasses import dataclass, field


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)


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
        if path == "/robots.txt":
            return True
        token = agent.lower()
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
    """The product token a user-agent line names: "Civitas/1.0" -> "civitas"."""
    return re.split(r"[/\s]", value.strip(), maxsplit=1)[0].lower()


def parse(text: str) -> Robots:
    groups: list[_Group] = []
    current: _Group | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            # Consecutive user-agent lines share one group; one after a
            # rule starts the next.
            if current is None or current.rules:
                current = _Group()
                groups.append(current)
            current.agents.append(_agent_token(value) if value != "*" else "*")
        elif key in ("allow", "disallow") and current is not None:
            if value:  # An empty Disallow disallows nothing.
                current.rules.append((key == "allow", value))
    return Robots(groups=groups)


def _matches(pattern: str, path: str) -> bool:
    """RFC 9309 §2.2.3: "*" is any run of characters, a trailing "$" pins
    the end; otherwise a pattern matches as a prefix."""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.match(regex + ("$" if anchored else ""), path) is not None
