"""This year's polls, read from Wikipedia's race articles.

Wikipedia's race articles for the cycle are the one source of poll tables whose
licence can be read first-hand (CC BY-SA 4.0, stated on every page and in
the API's own rights metadata). Every poll Civitas derives from them, and
every range built on them, carries that licence and the attribution
"Wikipedia contributors" (POLL_LICENSE / POLL_ATTRIBUTION below).

What is read. Each race article's "General election" section, its
"Polling" subsection, and in it the table of individual polls whose
candidate columns are this race's Democratic and Republican nominees
(matched by surname to Civitas's confirmed candidates). Hypothetical
matchups against candidates who are not the nominees are other tables
and are never read. Each poll keeps its pollster, fieldwork dates,
sample size and population, margin of error, the two nominees' shares,
the partisan marker the article prints after a sponsored poll ("(R)",
"(D)"), and the URL the article cites for it.

Provenance. The exact revision read is recorded (its revid and
timestamp): any figure on the map can be traced to the text it came from.
The revision is the article's current one when the build runs, not a
hand-advanced pin: polls arrive daily, and a pin someone has to move
would leave the map a week stale whenever nobody did. Instead each
revision has to pass the gates below, and one that fails keeps the
previous good revision's polls (with the failure recorded), so an edit in
progress never reaches the map.

Gates (a table that fails any is rejected whole):
  * every row has a pollster, a fieldwork end date on or before the
    revision's own date and not before January 1 of the year before the
    election (earliest_fieldwork: the cycle's own polls, never the last
    cycle's), and an end date not before
    its start;
  * both nominees' shares are numbers between 0 and 100, and the row's
    shares sum to no more than 102 (100 plus rounding);
  * no two rows are the same poll (pollster, dates, sample and shares);
  * the header has exactly one column for each nominee.
"""

from __future__ import annotations

import html
import re
import unicodedata
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from app.contact import BOT_USER_AGENT
from app.pipeline.fetch.http_utils import fetch_with_retry_requests
from app.pipeline.rate_limiter import RateLimiter

API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": BOT_USER_AGENT}
POLL_LICENSE = "CC BY-SA 4.0"
POLL_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
POLL_ATTRIBUTION = "Wikipedia contributors"
SHARE_TOTAL_MAX = 102.0
# A handful of requests per build to a large public site.
_rate_limiter = RateLimiter(rps=1.0)

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9


@dataclass
class ParsedPoll:
    pollster: str
    start: str  # ISO date
    end: str
    sample: int | None
    population: str  # LV / RV / A / V, as printed
    moe: float | None
    dem: float
    rep: float
    partisan: str  # "R" / "D" when the article marks the poll, else ""
    url: str | None
    other: float | None = None  # the best-polling other named candidate's share, when the table has one

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class TableResult:
    polls: list[ParsedPoll] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


_NO_SHARE = {"", "–", "-", "—"}


class GateFailure(Exception):
    pass


# ---------------------------------------------------------------------------
# Wikitext helpers
# ---------------------------------------------------------------------------

_REF_FULL = re.compile(r"<ref(?:\s+[^>/]*)?>(.*?)</ref>", re.S | re.I)
_REF_NAMED_DEF = re.compile(r"<ref\s+name\s*=\s*\"?([^\">/]+?)\"?\s*>(.*?)</ref>", re.S | re.I)
_REF_NAMED_USE = re.compile(r"<ref\s+name\s*=\s*\"?([^\">/]+?)\"?\s*/>", re.I)
_URL_IN_CITE = re.compile(r"\burl\s*=\s*([^|}\s]+)", re.I)
_BRACKET_URL = re.compile(r"\[(https?://[^\s\]]+)")


def _url_of(ref_body: str) -> str | None:
    m = _URL_IN_CITE.search(ref_body) or _BRACKET_URL.search(ref_body)
    return m.group(1).strip() if m else None


def named_refs(text: str) -> dict[str, str]:
    """{ref name: cited URL} for every named reference the article defines."""
    out = {}
    for name, body in _REF_NAMED_DEF.findall(text):
        url = _url_of(body)
        if url:
            out[name.strip()] = url
    return out


def _split_top(s: str, sep: str) -> list[str]:
    """Split on `sep` outside [[...]], {{...}} and <ref>...</ref>."""
    parts, depth_sq, depth_cu, buf, i = [], 0, 0, [], 0
    in_ref = False
    while i < len(s):
        if s.startswith("<ref", i) and not in_ref:
            end = s.find(">", i)
            if end != -1 and s[end - 1] != "/":
                in_ref = True
        if in_ref and s.startswith("</ref>", i):
            in_ref = False
        two = s[i:i + 2]
        if two == "[[":
            depth_sq += 1
        elif two == "]]":
            depth_sq = max(0, depth_sq - 1)
        elif two == "{{":
            depth_cu += 1
        elif two == "}}":
            depth_cu = max(0, depth_cu - 1)
        if not in_ref and depth_sq == 0 and depth_cu == 0 and s.startswith(sep, i):
            parts.append("".join(buf))
            buf = []
            i += len(sep)
            continue
        buf.append(s[i])
        i += 1
    parts.append("".join(buf))
    return parts


def plain(s: str) -> str:
    """Wikitext cell content as plain text: refs, templates, markup out."""
    s = _REF_FULL.sub("", s)
    s = _REF_NAMED_USE.sub("", s)
    s = re.sub(r"\{\{\s*(?:nbsp|nowrap\|)\s*\}\}", " ", s, flags=re.I)
    s = re.sub(r"\{\{\s*nowrap\s*\|([^{}]*)\}\}", r"\1", s, flags=re.I)
    s = re.sub(r"\{\{\s*(?:sdash|ndash|mdash|snd)\s*\}\}", "–", s, flags=re.I)
    s = re.sub(r"\{\{\s*efn[^{}]*\}\}", "", s, flags=re.I)
    for _ in range(3):
        s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", s)
    s = re.sub(r"\[https?://[^\]]*\]", "", s)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("'''", "").replace("''", "")
    s = html.unescape(s).replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def _unclosed(s: str) -> bool:
    opened = len(re.findall(r"<ref(?:\s[^>]*)?(?<!/)>", s, re.I))
    return s.count("{{") > s.count("}}") or opened > len(re.findall(r"</ref>", s, re.I))


_LEADING_ATTRS = re.compile(r"^\s*((?:(?:rowspan|colspan|style|class|align)\s*=\s*(?:\"[^\"]*\"|\S+)\s*)+)(.*)$",
                            re.S | re.I)


def _cell(raw: str) -> tuple[dict, str]:
    """(attributes, content) of one table cell. An attribute an editor
    left without its "|" separator ('rowspan="2" {{sdash}}') still spans,
    as the rendered table shows it does."""
    parts = _split_top(raw, "|")
    if len(parts) >= 2 and ("=" in parts[0] or "{{" in parts[0]) and "[[" not in parts[0] and "<ref" not in parts[0]:
        attrs_text, content = parts[0], "|".join(parts[1:])
    elif (m := _LEADING_ATTRS.match(raw)) and re.search(r"rowspan|colspan", m.group(1), re.I):
        attrs_text, content = m.group(1), m.group(2)
    else:
        attrs_text, content = "", raw
    attrs = {}
    for k in ("rowspan", "colspan"):
        m = re.search(rf"{k}\s*=\s*\"?(\d+)", attrs_text, re.I)
        if m:
            attrs[k] = int(m.group(1))
    return attrs, content


def tables(text: str) -> list[str]:
    """Top-level {| ... |} tables in order."""
    out, depth, start = [], 0, None
    for m in re.finditer(r"^\s*\{\||^\s*\|\}", text, re.M):
        if m.group(0).strip() == "{|":
            if depth == 0:
                start = m.start()
            depth += 1
        else:
            depth -= 1
            if depth == 0 and start is not None:
                out.append(text[start:m.end()])
                start = None
    return out


def rows_of(table: str) -> tuple[list[list[str]], list[list[tuple[dict, str]]]]:
    """(header rows of plain cell text, data rows of (attrs, raw content)),
    with rowspans and colspans expanded into a rectangular grid."""
    body = table.strip()[2:-2]
    raw_rows = re.split(r"^\s*\|-[^\n]*$", body, flags=re.M)
    headers: list[list[str]] = []
    data: list[list[tuple[dict, str]]] = []
    carry: dict[int, tuple[int, tuple[dict, str]]] = {}
    for rr in raw_rows:
        cells: list[tuple[str, str]] = []
        lines: list[str] = []
        for line in rr.split("\n"):
            # A reference or template can run over several lines, and a
            # continuation line may itself start with "|" (a citation's
            # "|first1=..." parameter): it belongs to the open cell.
            if lines and _unclosed(lines[-1]):
                lines[-1] += "\n" + line
            else:
                lines.append(line)
        for line in lines:
            if line.startswith("!"):
                for c in _split_top(line[1:], "!!"):
                    cells.append(("h", c))
            elif line.startswith("|") and not line.startswith("|+") and not line.startswith("|}"):
                for c in _split_top(line[1:], "||"):
                    cells.append(("d", c))
            elif cells and line.strip():
                kind, prev = cells[-1]
                cells[-1] = (kind, prev + "\n" + line)
        if not cells:
            continue
        if all(k == "h" for k, _ in cells):
            row = []
            for _, c in cells:
                attrs, content = _cell(c)
                row.extend([plain(content)] * attrs.get("colspan", 1))
            headers.append(row)
            continue
        row: list[tuple[dict, str]] = []
        queue = [_cell(c) for _, c in cells]
        col = 0
        while queue or col in carry:
            if col in carry:
                left, cell = carry[col]
                row.append(cell)
                if left > 1:
                    carry[col] = (left - 1, cell)
                else:
                    del carry[col]
                col += 1
                continue
            cell = queue.pop(0)
            attrs = cell[0]
            for _ in range(attrs.get("colspan", 1)):
                if attrs.get("rowspan", 1) > 1:
                    carry[col] = (attrs["rowspan"] - 1, cell)
                row.append(cell)  # the same object across its span (see _rows)
                col += 1
        data.append(row)
    return headers, data


def section(text: str, heading: str, level: int, within: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """(start, end) of the first section titled `heading` at `level` ("="
    count), inside `within` when given; it ends at the next heading of the
    same or a higher level."""
    lo, hi = within or (0, len(text))
    pat = re.compile(rf"^={{{level}}}\s*{re.escape(heading)}\s*={{{level}}}\s*$", re.M | re.I)
    m = pat.search(text, lo, hi)
    if not m:
        return None
    nxt = re.compile(rf"^={{1,{level}}}[^=].*?={{1,{level}}}\s*$", re.M).search(text, m.end(), hi)
    return m.end(), nxt.start() if nxt else hi


# ---------------------------------------------------------------------------
# Field parsers
# ---------------------------------------------------------------------------


def parse_dates(s: str, year_hint: int) -> tuple[date, date] | None:
    """'October 7–9, 2026', 'September 28 – October 2, 2026', 'Oct 2, 2026'."""
    t = s.replace("–", "-").replace("—", "-").replace(",", " ")
    t = re.sub(r"\s+", " ", t).strip().lower()
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", t)]
    t_noyear = re.sub(r"\b20\d\d\b", " ", t)
    toks = re.findall(r"[a-z]+|\d+", t_noyear)
    parts: list[tuple[int | None, int]] = []
    month = None
    for tok in toks:
        if tok in _MONTHS:
            month = _MONTHS[tok]
        elif tok.isdigit() and month is not None and 1 <= int(tok) <= 31:
            parts.append((month, int(tok)))
    if not parts:
        return None
    end_year = years[-1] if years else year_hint
    start_year = years[0] if len(years) > 1 else end_year
    try:
        start = date(start_year, parts[0][0], parts[0][1])
        end = date(end_year, parts[-1][0], parts[-1][1])
    except ValueError:
        return None
    if start > end and len(years) <= 1:
        start = date(end_year - 1, parts[0][0], parts[0][1])
    return start, end


def parse_sample(s: str) -> tuple[int | None, str]:
    t = plain(s)
    m = re.search(r"([\d,]+)", t)
    pop = re.search(r"\((LV|RV|A|V)\)", t)
    n = int(m.group(1).replace(",", "")) if m and m.group(1).replace(",", "") else None
    return n, pop.group(1) if pop else ""


def parse_share(s: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%?", plain(s))
    return float(m.group(1)) if m else None


def parse_moe(s: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", plain(s))
    return float(m.group(1)) if m else None


def fold(s: str) -> str:
    """Lowercase ASCII letters only: "Luján" and "LUJAN" read the same."""
    s = unicodedata.normalize("NFD", s)
    return re.sub(r"[^a-z' -]", " ", "".join(c for c in s if not unicodedata.combining(c)).lower())


def name_words(name: str) -> tuple[str, list[str]]:
    """(surname, the other words) of a printed name, folded; nicknames in
    quotes, one-letter initials and suffixes dropped. Handles the FEC's
    "LAST, FIRST" order."""
    clean = re.sub(r"\"[^\"]*\"|\([^)]*\)", " ", name)
    if "," in clean:
        last, first = clean.split(",", 1)
        clean = f"{first} {last}"
    words = [w for w in fold(clean).replace("-", " ").split()
             if len(w.strip(".")) > 1 and w not in {"jr", "sr", "ii", "iii", "iv", "senator", "dr"}]
    return (words[-1], words[:-1]) if words else ("", [])


def candidate_columns(low_headers: list[str], name: str, party_tag: str) -> list[int]:
    """Header columns naming this candidate: the party tag and the surname,
    narrowed by the other name words when two candidates share a surname."""
    surname, others = name_words(name)
    hits = [i for i, h in enumerate(low_headers)
            if f"({party_tag}" in h and surname in fold(h).replace("-", " ").split()]
    if len(hits) > 1:
        # Initials count here ("Dan S. Sullivan" vs "Dan J. Sullivan").
        words = [w.strip(".") for w in fold(re.sub(r"\"[^\"]*\"", " ", name)).replace(".", " ").split()]
        narrowed = [i for i in hits
                    if all(w in fold(low_headers[i]).replace(".", " ").split() for w in words if w != surname)]
        hits = narrowed or hits
    return hits


# ---------------------------------------------------------------------------
# One article
# ---------------------------------------------------------------------------


def earliest_fieldwork(cycle: int) -> date:
    """The first day a poll of the `cycle` election may have been in the
    field: January 1 of the year before it. A row dated earlier belongs to
    the previous cycle (a table carried over, a misdated row)."""
    return date(cycle - 1, 1, 1)


def poll_table(text: str, dem_name: str, rep_name: str, as_of: date, refs: dict[str, str], cycle: int,
               district: int | None = None) -> TableResult | None:
    """The general-election poll table of one race article, or None when
    the article has none for these two nominees. Raises GateFailure when
    the table is there but fails a gate.

    A Senate race, or an at-large House seat, has an article of its own,
    with "General election" at the top level. A districted state's House
    article has a "District N" section per seat, with the same structure
    one level down."""
    within, level = None, 2
    if district is not None:
        within = section(text, f"District {district}", 2)
        if within is None:
            return None
        level = 3
    general = section(text, "General election", level, within)
    if general is None:
        return None
    polling = section(text, "Polling", level + 1, general)
    if polling is None:
        return None
    block = text[polling[0]:polling[1]]
    block = re.sub(r"<!--.*?-->", "", block, flags=re.S)
    for tbl in tables(block):
        headers, data = rows_of(tbl)
        if not headers:
            continue
        head = headers[-1]
        low = [h.lower() for h in head]
        if not any("poll source" in h for h in low) or not any("administered" in h for h in low):
            continue
        dcols = candidate_columns(low, dem_name, "d")
        rcols = candidate_columns(low, rep_name, "r")
        if not dcols or not rcols:
            continue  # a matchup with someone else: a hypothetical, not this race
        if len(dcols) != 1 or len(rcols) != 1:
            raise GateFailure(f"header has {len(dcols)} column(s) for {dem_name} and {len(rcols)} for {rep_name}")
        # Any other candidate column (a third candidate in the same table)
        # is a three-way question; this table is still the two nominees'.
        col = {
            "pollster": next(i for i, h in enumerate(low) if "poll source" in h),
            "dates": next(i for i, h in enumerate(low) if "administered" in h),
            "sample": next((i for i, h in enumerate(low) if "sample" in h), None),
            "moe": next((i for i, h in enumerate(low) if "error" in h), None),
        }
        col["round"] = next((i for i, h in enumerate(low) if "round" in h), None)
        share_cols = [i for i, h in enumerate(low)
                      if i not in (col["pollster"], col["dates"], col["sample"], col["moe"], col["round"])]
        # Other named candidates (a column with a party tag that is not
        # one of the nominees'): how a third candidate polls decides
        # whether this is a Democrat-vs-Republican race at all.
        others = [i for i in share_cols if i not in (dcols[0], rcols[0]) and re.search(r"\([a-z]+\)\s*$", low[i])]
        return _rows(data, col, dcols[0], rcols[0], share_cols, others, as_of, earliest_fieldwork(cycle), refs, low)
    return None


def _rows(data, col, dcol, rcol, share_cols, others, as_of: date, earliest: date, refs: dict[str, str],
          low_of: list[str]) -> TableResult:
    out = TableResult()
    seen = set()
    if col["round"] is not None:
        # A ranked-choice table prints each poll once per round; the last
        # round is the head-to-head the method describes.
        last = {}
        for i, row in enumerate(data):
            last[id(row[col["pollster"]])] = i
        data = [row for i, row in enumerate(data) if last[id(row[col["pollster"]])] == i]
    for row in data:
        if len(row) <= max(dcol, rcol, col["pollster"], col["dates"]):
            raise GateFailure(f"a row has {len(row)} cells, fewer than the header")
        if row[dcol] is row[rcol]:
            # One cell spanning both nominees' columns: a dividing line
            # the article draws between polls ("June 16, 2026 — Republican
            # primary runoff"), not a poll.
            continue
        raw_pollster = row[col["pollster"]][1]
        name = plain(raw_pollster)
        # The article's partisan marker: "(R)" or "(D)" after a pollster.
        # A joint poll by one firm of each party ("X (R)/ Y (D)") is
        # bipartisan and carries no sponsor flag.
        marks = set(re.findall(r"\((R|D)\)", name))
        partisan = marks.pop() if len(marks) == 1 else ""
        name = re.sub(r"\s*\((?:R|D)\)", "", name).strip()
        if not name:
            raise GateFailure("a row has no pollster")
        url = None
        full = _REF_FULL.search(raw_pollster)
        if full:
            url = _url_of(full.group(1))
        if url is None:
            use = _REF_NAMED_USE.search(raw_pollster) or _REF_NAMED_DEF.search(raw_pollster)
            if use:
                url = refs.get(use.group(1).strip())
        if url is None:
            link = _BRACKET_URL.search(raw_pollster)
            url = link.group(1) if link else None
        dates = parse_dates(plain(row[col["dates"]][1]), as_of.year)
        if dates is None:
            raise GateFailure(f"{name}: fieldwork dates {plain(row[col['dates']][1])!r} do not parse")
        start, end = dates
        if end > as_of or end < earliest or start > end:
            raise GateFailure(f"{name}: fieldwork {start}..{end} outside {earliest}..{as_of}")
        if plain(row[dcol][1]) in _NO_SHARE or plain(row[rcol][1]) in _NO_SHARE:
            # A question this poll asked without one of the nominees (a
            # head-to-head with someone else): not a reading of this race.
            continue
        dem, rep = parse_share(row[dcol][1]), parse_share(row[rcol][1])
        if dem is None or rep is None or not (0 < dem <= 100 and 0 < rep <= 100):
            raise GateFailure(f"{name} ({end}): nominee shares {dem}, {rep} are not percentages")
        # In a ranked-choice round the ballots no longer ranking anyone are
        # printed beside the shares, outside the 100%.
        counted = [i for i in share_cols if col["round"] is None or "not ranked" not in low_of[i]]
        # A cell spanning two columns ("Other" and "Undecided" together) is
        # one share, counted once.
        cells = list({id(row[i]): row[i] for i in counted if i < len(row)}.values())
        total = sum(v for cell in cells if (v := parse_share(cell[1])) is not None)
        if total > SHARE_TOTAL_MAX:
            raise GateFailure(f"{name} ({end}): shares sum to {total:.1f}")
        n, pop = parse_sample(row[col["sample"]][1]) if col["sample"] is not None and col["sample"] < len(row) else (None, "")
        moe = parse_moe(row[col["moe"]][1]) if col["moe"] is not None and col["moe"] < len(row) else None
        other = max((v for i in others if i < len(row) and (v := parse_share(row[i][1])) is not None), default=None)
        key = (name.lower(), start, end, n, pop, dem, rep, other)
        if key in seen:
            raise GateFailure(f"{name} ({end}): the same poll is listed twice")
        seen.add(key)
        out.polls.append(ParsedPoll(name, start.isoformat(), end.isoformat(), n, pop, moe, dem, rep, partisan, url,
                                    other))
    return out


# ---------------------------------------------------------------------------
# Generic ballot (published averages)
# ---------------------------------------------------------------------------


def generic_ballot(text: str, as_of: date, cycle: int) -> list[dict]:
    """The generic-ballot averages the article's aggregate table lists:
    [{"source", "updated", "dem", "rep", "url"}]. Wikipedia lists averages
    here, not individual generic-ballot polls; the gates are the same
    shape (dates, shares, duplicates), with the cycle's fieldwork floor."""
    earliest = earliest_fieldwork(cycle)
    m = re.search(r'<section begin="?GenericBallotAgg"?\s*/>(.*?)<section end="?GenericBallotAgg"?\s*/>', text, re.S)
    if not m:
        raise GateFailure("no generic-ballot aggregate table")
    refs = named_refs(text)
    headers, data = rows_of(tables(m.group(1))[0])
    low = [h.lower() for h in headers[-1]]
    ci = {k: next((i for i, h in enumerate(low) if k in h), None)
          for k in ("source", "updated", "republicans", "democrats")}
    if None in ci.values():
        raise GateFailure(f"generic-ballot header not recognised: {headers[-1]}")
    out, seen = [], set()
    for row in data:
        name = plain(row[ci["source"]][1])
        if not name or name.lower() == "average" or len(row) <= max(ci.values()):
            continue
        updated = parse_dates(plain(row[ci["updated"]][1]), as_of.year)
        dem, rep = parse_share(row[ci["democrats"]][1]), parse_share(row[ci["republicans"]][1])
        if updated is None or dem is None or rep is None:
            raise GateFailure(f"generic-ballot row {name!r} does not parse")
        if updated[1] > as_of or updated[1] < earliest or dem + rep > SHARE_TOTAL_MAX:
            raise GateFailure(f"generic-ballot row {name!r} fails the date or share gate")
        if name.lower() in seen:
            raise GateFailure(f"generic-ballot source {name!r} is listed twice")
        seen.add(name.lower())
        raw = row[ci["source"]][1]
        full = _REF_FULL.search(raw)
        url = _url_of(full.group(1)) if full else None
        if url is None and (use := _REF_NAMED_USE.search(raw)):
            url = refs.get(use.group(1).strip())
        out.append({"source": name, "updated": updated[1].isoformat(), "dem": dem, "rep": rep, "url": url})
    if not out:
        raise GateFailure("the generic-ballot table has no rows")
    return out


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def revision_url(title: str, revid: int) -> str:
    return f"https://en.wikipedia.org/w/index.php?title={urllib.parse.quote(title.replace(' ', '_'))}&oldid={revid}"


async def fetch_current(titles: list[str]) -> dict[str, dict] | None:
    """{requested title: {"title", "revid", "timestamp", "content"}} for the
    current revision of each article (redirects followed), or None when
    Wikipedia could not be reached at all — never an empty dict standing
    in for an outage."""
    out: dict[str, dict] = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        params = {"action": "query", "prop": "revisions", "rvprop": "ids|timestamp|content",
                  "rvslots": "main", "format": "json", "formatversion": "2", "redirects": "1",
                  "titles": "|".join(chunk)}
        resp = await fetch_with_retry_requests(
            _rate_limiter, "GET", API + "?" + urllib.parse.urlencode(params), retries=4, backoff_s=10.0,
            log_label="election-map wikipedia revisions", headers=HEADERS,
        )
        if resp is None:
            return None
        q = resp.json().get("query", {})
        alias = {r["to"]: r["from"] for r in q.get("redirects", [])}
        alias.update({n["to"]: n["from"] for n in q.get("normalized", [])})
        for page in q.get("pages", []):
            if not page.get("revisions"):
                continue
            rev = page["revisions"][0]
            requested = page["title"]
            while requested in alias:
                requested = alias[requested]
            out[requested] = {"title": page["title"], "revid": rev["revid"], "timestamp": rev["timestamp"],
                              "content": rev.get("slots", {}).get("main", {}).get("content", "")}
    return out


async def titles_with_prefix(prefix: str) -> list[str] | None:
    """Every article title starting with `prefix` (main namespace), or None
    when Wikipedia could not be reached."""
    out: list[str] = []
    params = {"action": "query", "list": "allpages", "apprefix": prefix, "apnamespace": "0",
              "apfilterredir": "nonredirects", "aplimit": "500", "format": "json", "formatversion": "2"}
    while True:
        resp = await fetch_with_retry_requests(
            _rate_limiter, "GET", API + "?" + urllib.parse.urlencode(params), retries=4, backoff_s=10.0,
            log_label="election-map wikipedia titles", headers=HEADERS,
        )
        if resp is None:
            return None
        body = resp.json()
        out += [p["title"] for p in body.get("query", {}).get("allpages", [])]
        if "continue" not in body:
            return out
        params.update(body["continue"])


def revision_date(rev: dict) -> date:
    return datetime.fromisoformat(rev["timestamp"].replace("Z", "+00:00")).date()
