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
(Google's parser ends it only on a rule; a crawl-delay between two groups'
agent lines is read here as the author laid it out.) Sitemap lines belong
to no group and end nothing. Lines end at CR, LF or CRLF only. A UTF-8
byte-order mark is dropped, and only the first MAX_BYTES are read (§2.5
asks for at least 500 KiB). Like Google's parser, a record may use
whitespace instead of the colon and the common misspellings of its key
("useragent", "user agent", "disalow", "dissallow"): a crawler that wants
to be told no reads a rule its author clearly meant.

Matching (§2.2): a group applies when one of its user-agent lines names
our product token, case-insensitively; the groups naming it are combined,
and only when none does do the "*" groups apply. Paths and patterns are
compared after the same percent-encoding normalisation (§2.2.2). Of the
rules whose pattern matches, the longest pattern wins, an allow winning a
tie; no match means allowed. /robots.txt itself is always allowed.
Reserved characters are compared as written (so "https://" in a pattern's
query does not match "https%3A%2F%2F" in a path), as Google's parser does.

A site's file is external input and matching runs on the event loop, so
each pattern is split at its "*"s once, when parsed, and matched by
finding its pieces left to right with str.find — no backtracking, and no
Python-level loop over the path.

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


@dataclass(frozen=True)
class _Rule:
    allow: bool
    length: int  # of the pattern as written, "*" and "$" included
    pieces: tuple[str, ...]  # the pattern split at its "*"s
    anchored: bool  # ends in "$"

    def matches(self, path: str) -> bool:
        first, *rest = self.pieces
        if not path.startswith(first):
            return False
        if not rest:
            return path == first if self.anchored else True
        pos = len(first)
        *middle, last = rest
        for piece in middle:
            found = path.find(piece, pos)
            if found < 0:
                return False
            pos = found + len(piece)
        if self.anchored:
            # Each piece placed as far left as it can go leaves the most
            # room for the rest; the last must then end the path.
            return len(path) - len(last) >= pos and path.endswith(last)
        return path.find(last, pos) >= 0


def _rule(allow: bool, pattern: str) -> _Rule:
    anchored = pattern.endswith("$")
    body = re.sub(r"\*+", "*", pattern[:-1] if anchored else pattern)
    return _Rule(allow, len(pattern), tuple(body.split("*")), anchored)


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[_Rule] = field(default_factory=list)
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
        for rule in rules:
            key = (rule.length, rule.allow)
            if (best is None or key > best) and rule.matches(path):
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


# Record keys as Google's parser accepts them, spaces, "-" and "_" removed.
_KEYS = {
    "useragent": "user-agent",
    "allow": "allow",
    "disallow": "disallow", "disalow": "disallow", "dissallow": "disallow",
    "dissalow": "disallow", "disallaw": "disallow",
    "sitemap": "sitemap", "sitemaps": "sitemap",
}
_RECORD = re.compile(r"^([A-Za-z][A-Za-z _-]*?)\s*(?::|\s)\s*(.*)$")


def _record(line: str) -> tuple[str, str] | None:
    """(key, value) of one line, or None for a blank or unreadable one."""
    line = line.split("#", 1)[0].strip()
    if ":" in line:
        key, value = (part.strip() for part in line.split(":", 1))
    else:
        match = _RECORD.match(line)
        if not match:
            return None
        key, value = match.group(1), match.group(2).strip()
    folded = re.sub(r"[\s_-]", "", key.lower())
    return _KEYS.get(folded, folded), value


def parse(text: str) -> Robots:
    groups: list[_Group] = []
    current: _Group | None = None
    if len(text) > MAX_BYTES:
        # Cut at the last line end before the limit: a half-read final line
        # would be a rule its author never wrote ("/*a*a…" read as "/*").
        text = text[:max(text.rfind("\n", 0, MAX_BYTES), text.rfind("\r", 0, MAX_BYTES), 0)]
    for raw in re.split(r"\r\n|\r|\n", text.lstrip("\ufeff")):
        record = _record(raw)
        if record is None:
            continue
        key, value = record
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
                current.rules.append(_rule(key == "allow", normalize(value)))
    return Robots(groups=groups)


def _matches(pattern: str, path: str) -> bool:
    """§2.2.3: "*" is any run of characters, a trailing "$" pins the end;
    otherwise a pattern matches as a prefix."""
    return _rule(False, pattern).matches(path)
