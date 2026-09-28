"""Ohio's ballot-measure strategy — the Secretary of State's official
sample ballot for the November general election, issued with that
election's form-of-the-ballot directive (one of
MULTI_DOCUMENT_STRATEGIES in ballot_measures_pdf.py).

The Secretary tells every county board exactly what to print. Each
general election's directive comes with an "Official Sample Ballot"
whose pages each say, in their footer, what they are:

- "This SAMPLE ballot provides the CORRECT TITLE[S] and ORDER OF
  OFFICES ..." — candidate offices;
- "This SAMPLE ballot provides the CORRECT ballot format and ballot
  language for the state issue[s] that must appear on the November <year>
  General Election ballot." — the statewide issues, as they must appear;
- "This SAMPLE ballot provides the CORRECT ballot format for local
  questions or issues ..." (2025: "... various questions or issues that
  may appear on your local ballot.") — blank local templates.

Verified live 2026-09-28 against four real generals from
ohiosos.gov/elections/elections-officials/rules/: 2026 (Directive
2026-45: Issue 3, photo identification, one column), 2024 (Issue 1,
redistricting, set across all three ballot columns), 2022 (Issues 1 and
2 side by side) and 2025 (no state-issue page at all — Ohio had no
statewide issue that November).

The state-issue page is read column by column (the ballot's three
columns, split at the gutters near a third and two thirds of the page;
a word in a gutter refuses the page). Per issue, verbatim: "Issue N" is
the number; the title lines under it are the title printed on the ballot
(title and official_title — the Ohio Ballot Board prescribes statewide
ballot language, R.C. 3505.062, so it is named as drafter); everything
from the issue's kind ("Proposed Constitutional Amendment") through its
question ("SHALL THE AMENDMENT BE APPROVED?") is official_summary, one
line per bullet or numbered item as printed; the ballot's own
'A “YES” vote means ...' / 'A “NO” vote means ...' sentences, where it
prints them (2026 does, 2022 and 2024 did not), are yes_means /
no_means — each running to the period that really ends it (followed by the
end or a capital; a period ending an abbreviation such as "R.C." or
"U.S." does not count, a lone capital like "Plan B." does), the YES
sentence never running past the start of the NO one nor the NO past
its paragraph or the question, and one printed without the other, twice, or
with no clear end refuses the issue. The answer ovals are not text.

[] (confirmed none) only when every page of the sample ballot is one of
the three kinds above and none is a state-issue page: the Secretary's
own ballot for that election carries no statewide issue. A page of any
other kind, a state-issue page with no readable issue, or an issue
missing its title or question refuses the state (None).

Discovery: the directives page, the one PDF link whose text names
"Sample Ballot", "General Election" and the election's date in long
form ("November 3, 2026"); none there yet is NotYetPublished (the
directive comes out in August). ohiosos.gov answered this environment's
httpx client (the pipeline's) with 200 and curl with an Akamai
"Website Maintenance" 403 — the reverse of most sites here.
"""

import io
import logging
import re
from urllib.parse import urljoin

import httpx
import pdfplumber
from lxml import html as lxml_html

from app.pipeline.fetch.ballot_measure_text import NotYetPublished, join_lines
from app.pipeline.fetch.ballot_measures_state_common import election_day, get_bytes, get_text, long_date

logger = logging.getLogger(__name__)

DIRECTIVES_URL = "https://www.ohiosos.gov/elections/elections-officials/rules/"
TITLE_AUTHORITY = "Ohio Ballot Board"

_LINE_TOL = 3.0
_WORD_GAP = 1.0
_GUTTER_SLACK = 20.0
# Body lines are set 12pt apart and paragraphs 16.5pt (2022-2026 alike).
_PARA_GAP = 1.25
_ISSUE_ANYWHERE_RE = re.compile(r"\bIssue \d+\b")
_ISSUE_RE = re.compile(r"^Issue (\d+)$")
_PAGE_RE = re.compile(r"^Page \d+ of \d+$")
_ITEM_RE = re.compile(r"^(•|\d+\.\s)")
_YES_START = "A “YES” vote means "
_NO_START = "A “NO” vote means "
# A period that ends a word like these is an abbreviation, not the end of
# the sentence ("R.C. 3505.062", "the U.S. Constitution", "Sec. 5"). A
# lone capital ("Plan B.", "Article V.") is NOT one: it ends a sentence
# like any other word, and _SENTENCE_BREAK_RE already requires the next
# word to start with a capital, so "B. 12" or "V. of" never breaks.
_ABBREVIATION_RE = re.compile(r"(?:^|\s)(?:(?:[A-Z]\.)+[A-Z]|No|Nos|Sec|Secs|Art|Ch|Const|Stat|Rev|Div|Dist|vs?)$")
_SENTENCE_BREAK_RE = re.compile(r"\.(?=\s*$|\s+[A-Z“\"(•])")


class AmbiguousSentence(ValueError):
    pass


def _sentence_in(segment: str) -> str:
    """The first sentence of `segment`: through the first period followed
    by the segment's end or by whitespace and a capital (or a quotation
    mark, bullet or parenthesis), skipping periods that end an
    abbreviation. No such period is ambiguous."""
    for m in _SENTENCE_BREAK_RE.finditer(segment):
        if _ABBREVIATION_RE.search(segment[:m.start()]):
            continue
        return segment[:m.end()]
    raise AmbiguousSentence(segment[:60])


def yes_no_sentences(body: str) -> tuple[str | None, str | None]:
    """The ballot's own 'A “YES” vote means ...' and 'A “NO” vote means
    ...' sentences, whole, or (None, None) where it prints neither.

    Each is read only within its own bounds, so one can never run into
    the other: the YES sentence stops at the start of the NO sentence or
    at its paragraph's end, the NO sentence at its paragraph's end or the
    ballot's "SHALL ..." question. Raises AmbiguousSentence when one is
    printed without the other, more than once, or with no clear end."""
    starts = {}
    for prefix in (_YES_START, _NO_START):
        found = [m.start() for m in re.finditer(re.escape(prefix), body)]
        if len(found) > 1:
            raise AmbiguousSentence(prefix)
        starts[prefix] = found[0] if found else None
    yes_at, no_at = starts[_YES_START], starts[_NO_START]
    if yes_at is None and no_at is None:
        return None, None
    if yes_at is None or no_at is None:
        raise AmbiguousSentence("one of the YES/NO sentences is missing")

    def bound(start: int, *stops: int) -> str:
        para_end = body.find("\n", start)
        ends = [e for e in (para_end, *stops) if e is not None and e > start]
        return body[start:min(ends)] if ends else body[start:]

    shall = body.find("SHALL ", no_at)
    yes = _sentence_in(bound(yes_at, no_at))
    no = _sentence_in(bound(no_at, yes_at, shall if shall >= 0 else None))
    return yes, no
_FOOTER_KINDS = {
    "candidates": re.compile(r"provides the CORRECT TITLES? and ORDER OF OFFICES"),
    "state_issues": re.compile(r"provides the CORRECT ballot format and ballot language for the state issues?"),
    "local": re.compile(r"provides the CORRECT ballot format for (?:local questions or issues|various questions or issues)"),
}


def find_sample_ballot_url(page_html: str, year: int) -> tuple[str | None, bool]:
    """(url, page_ok): the one sample-ballot link for `year`'s general.
    page_ok False when it isn't the directives page or the link is
    ambiguous."""
    tree = lxml_html.fromstring(page_html)
    title = " ".join(" ".join(t.text_content().split()) for t in tree.xpath("//title"))
    if "Directives" not in title:
        return None, False
    date_text = long_date(election_day(year)).lower()
    hrefs = set()
    for a in tree.xpath("//a[@href]"):
        text = " ".join(a.text_content().split()).lower()
        href = a.get("href").strip()
        if (
            ".pdf" in href.lower() and "sample ballot" in text and "general election" in text
            and date_text in text and not text.startswith("directive")
        ):
            hrefs.add(urljoin(DIRECTIVES_URL, href))
    if len(hrefs) > 1:
        return None, False
    return (hrefs.pop() if hrefs else None), True


def _text(line: list[dict]) -> str:
    out, prev = "", None
    for w in line:
        if prev is not None and w["x0"] - prev["x1"] > _WORD_GAP:
            out += " "
        out += w["text"]
        prev = w
    return out


def _lines(words: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= _LINE_TOL:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def page_kind(page: dict) -> str | None:
    """Which of the sample ballot's page kinds this is, by its footer."""
    text = " ".join(_text(line) for line in _lines(page["words"]))
    kinds = [k for k, rx in _FOOTER_KINDS.items() if rx.search(text)]
    return kinds[0] if len(kinds) == 1 else None


def _gutters(words: list[dict], width: float) -> list[float] | None:
    """x of the two gutters between the ballot's three columns: the
    word-free band nearest a third and two thirds of the page width."""
    covered = [False] * (int(width) + 2)
    for w in words:
        for x in range(max(0, int(w["x0"])), min(len(covered), int(w["x1"]) + 1)):
            covered[x] = True
    out = []
    for k in (1, 2):
        target = width * k / 3
        free = [x for x in range(int(target - _GUTTER_SLACK), int(target + _GUTTER_SLACK) + 1)
                if 0 <= x < len(covered) and not covered[x]]
        if not free:
            return None
        out.append(min(free, key=lambda x: abs(x - target)))
    return out


def _body(page: dict) -> list[dict]:
    """The page's words between its header (the line naming the
    election) and its "Page N of M" footer."""
    lines = _lines(page["words"])
    top = next((ln[0]["top"] for ln in lines if "General Election" in _text(ln)), None)
    # "Page N of M" can share its baseline with a column's last line
    # (2024: "duty or gross misconduct." in column 1, "Page 3 of 4" in
    # column 3), so the label is found word by word, and words on its
    # line left of it are still body text.
    label = None
    for ln in lines:
        for i in range(len(ln)):
            if _PAGE_RE.match(" ".join(w["text"] for w in ln[i:i + 4])):
                label = ln[i]
                break
        if label is not None:
            break
    if top is None or label is None:
        return []
    return [
        w for w in page["words"]
        if top + _LINE_TOL < w["top"] and (
            w["top"] < label["top"] - _LINE_TOL
            or (abs(w["top"] - label["top"]) <= _LINE_TOL and w["x1"] < label["x0"] - _GUTTER_SLACK)
        )
    ]


def _paragraphs(lines: list[dict]) -> str:
    """Printed lines -> one line per paragraph. A paragraph ends where
    the ballot leaves vertical space (more than _PARA_GAP times the
    column's usual line pitch), where a bullet or numbered item starts,
    and where a line returns to the item's left edge after one (the
    item's own lines hang indented). A paragraph carried to the top of
    the next column continues."""
    pitches = sorted(
        b["top"] - a["top"] for a, b in zip(lines, lines[1:]) if a["col"] == b["col"] and b["top"] > a["top"]
    )
    pitch = pitches[len(pitches) // 2] if pitches else 0.0
    paras: list[list[str]] = []
    item_indent: float | None = None
    prev = None
    for line in lines:
        is_item = bool(_ITEM_RE.match(line["text"]))
        new = (
            prev is None
            or is_item
            or (prev["col"] == line["col"] and pitch and line["top"] - prev["top"] > _PARA_GAP * pitch)
            or (item_indent is not None and line["indent"] <= item_indent + 1.0)
        )
        if is_item:
            item_indent = line["indent"]
        elif new:
            item_indent = None
        if new:
            paras.append([line["text"]])
        else:
            paras[-1].append(line["text"])
        prev = line
    return "\n".join(join_lines(p) or "" for p in paras)


_KIND_RE = re.compile(
    r"^(?:(?P<before>.+?) )?(?P<kind>Proposed (?:Constitutional Amendment|Law|Amendment|Supplementary Law)"
    r"|Referendum)(?: (?P<after>.+))?$"
)


def parse_state_issue_page(page: dict) -> list[dict] | None:
    words = _body(page)
    gutters = _gutters(words, page["width"]) if words else None
    if gutters is None:
        logger.warning("OH state-issue page: body or column gutters not found — refusing")
        return None
    columns: list[list[dict]] = [[], [], []]
    for w in words:
        k = 0 if w["x0"] < gutters[0] else (1 if w["x0"] < gutters[1] else 2)
        columns[k].append(w)
    stream = []
    for k, col in enumerate(columns):
        left = min((w["x0"] for w in col), default=0.0)
        for line in _lines(col):
            stream.append({"text": _text(line), "top": line[0]["top"], "indent": line[0]["x0"] - left, "col": k})

    issues: list[dict] = []
    for line in stream:
        m = _ISSUE_RE.match(line["text"])
        if m:
            issues.append({"number": m.group(1), "lines": []})
        elif issues:
            issues[-1]["lines"].append(line)
        else:
            logger.warning("OH state-issue page: text %r before the first issue — refusing", line["text"][:60])
            return None

    results = []
    for issue in issues:
        lines = issue["lines"]
        texts = [ln["text"] for ln in lines]
        by_at = next((i for i, t in enumerate(texts) if t.startswith("Proposed by ")), None)
        q_at = next((i for i, t in enumerate(texts) if t.startswith("SHALL ")), None)
        if not by_at or q_at is None or q_at < by_at:
            logger.warning("OH Issue %s: no title/'Proposed by'/question in the verified order — refusing", issue["number"])
            return None
        head = _KIND_RE.match(join_lines(texts[:by_at]) or "")
        if head is None or bool(head.group("before")) == bool(head.group("after")):
            logger.warning("OH Issue %s: title and kind not in a verified arrangement — refusing", issue["number"])
            return None
        title = head.group("before") or head.group("after")
        q_end = next((i for i in range(q_at, len(texts)) if texts[i].rstrip().endswith("?")), None)
        if q_end is None:
            logger.warning("OH Issue %s: question has no '?'", issue["number"])
            return None
        rest = [t.strip() for t in texts[q_end + 1:] if t.strip()]
        if [re.sub(r"^O\s+", "", t) for t in rest] != ["YES", "NO"]:
            logger.warning("OH Issue %s: unexpected text after its question: %r", issue["number"], rest[:3])
            return None
        body = _paragraphs(lines[by_at:q_end + 1])
        try:
            yes, no = yes_no_sentences(body)
        except AmbiguousSentence as exc:
            logger.warning("OH Issue %s: yes/no sentences not in the verified form (%s)", issue["number"], exc)
            return None
        for sentence in (yes, no):
            if sentence:
                body = body.replace(sentence, "")
        body = "\n".join(p.strip() for p in body.split("\n") if p.strip())
        origin = re.match(r"^Proposed by (.+)$", body.split("\n")[0])
        results.append({
            "number": issue["number"],
            "title": title,
            "official_title": title,
            "origin": origin.group(1) if origin else None,
            "official_summary": f"{head.group('kind')}\n{body}",
            "fiscal_impact": None,
            "yes_means": yes,
            "no_means": no,
            "title_authority": TITLE_AUTHORITY,
            "fiscal_authority": None,
        })
    if not results:
        logger.warning("OH state-issue page carries no issue — refusing")
        return None
    return results


def parse_sample_ballot(pages: list[dict], year: int) -> list[dict] | None:
    """Every statewide issue on the Secretary's sample ballot for
    `year`'s general; [] only when every page is a known kind and none
    carries a state issue; None otherwise."""
    first = " ".join(_text(ln) for ln in _lines(pages[0]["words"])) if pages else ""
    if long_date(election_day(year)) not in first:
        logger.warning("OH sample ballot does not name %s", long_date(election_day(year)))
        return None
    kinds = [page_kind(p) for p in pages]
    if None in kinds:
        logger.warning("OH sample ballot page %d is not a kind this reader knows — refusing", kinds.index(None) + 1)
        return None
    results: list[dict] = []
    for page, kind in zip(pages, kinds):
        # A page is read for issues by what is on it, not by its footer
        # alone: 2024's state-issue page carried the candidate pages'
        # footer ("CORRECT TITLES and ORDER OF OFFICES ..."), and a reader
        # trusting footers would have read that ballot as having none.
        text = " ".join(_text(ln) for ln in _lines(page["words"]))
        if kind != "state_issues" and not _ISSUE_ANYWHERE_RE.search(text):
            continue
        parsed = parse_state_issue_page(page)
        if parsed is None:
            return None
        results.extend(parsed)
    return results


def pdf_words(raw: bytes) -> list[dict]:
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return [
            {
                "width": float(page.width),
                "words": [
                    {"text": w["text"], "x0": w["x0"], "x1": w["x1"], "top": w["top"]}
                    for w in page.extract_words()
                ],
            }
            for page in pdf.pages
        ]


async def fetch_measures(client: httpx.AsyncClient, year: int) -> list[tuple[dict, str]] | None:
    page_html = await get_text(client, DIRECTIVES_URL, "OH election directives")
    if page_html is None:
        return None
    try:
        url, page_ok = find_sample_ballot_url(page_html, year)
    except Exception:
        logger.exception("OH directives page was not parseable")
        return None
    if not page_ok:
        logger.warning("OH directives page is not the page this reader knows, or links two %d sample ballots", year)
        return None
    if url is None:
        raise NotYetPublished(f"the Ohio Secretary of State's official sample ballot for {long_date(election_day(year))}")
    raw = await get_bytes(client, url, "OH official sample ballot")
    if raw is None:
        return None
    try:
        parsed = parse_sample_ballot(pdf_words(raw), year)
    except Exception:
        logger.exception("OH official sample ballot was not parseable")
        return None
    if parsed is None:
        return None
    return [(m, url) for m in parsed]
