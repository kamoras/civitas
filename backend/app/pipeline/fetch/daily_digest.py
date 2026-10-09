"""The Congressional Record's Daily Digest: what each chamber did each day.

The Digest is the Record's own summary of a day, written by its staff
under fixed headings ("Measures Passed:", "Nominations Confirmed:",
"Adjournment:" ...) and published by GPO the next day as granules of the
day's CREC package (granule class DAILYDIGEST: one for each chamber's
floor action, one for each chamber's committee meetings, and one with
both chambers' next meeting).

Everything kept is the Digest's own wording. Parsing only decides which
heading an entry sits under and pulls out the bill number, so no clause
of the published text is rewritten, summarized or dropped.

Layout (a <pre> block, fixed-width): an entry is a column-0 paragraph that
opens with its heading ("Measures Passed:"), sub-items under it are
indented two spaces, and nearly every entry ends with a page reference
line ("Pages S4897-98"). Two things break the simple reading, both seen in
real issues: some entries are followed directly by the next heading with
no page reference ("... no quorum calls." / "Adjournment: ..."), and a
House heading can run over several lines before its colon (the
"Recommending that the House of Representatives find ... in contempt"
entry of 2026-09-16). So an entry starts after a page reference, or at a
column-0 heading-shaped line that follows a finished sentence.
"""

import html as html_lib
import re

# ── Bill numbers ──────────────────────────────────────────────────

# The Record's spelling -> the site's bill id prefix (Congress.gov's type,
# upper-cased: "HCONRES.89", as SponsoredBill.bill_id and /bills use).
_MEASURE_TYPES = (
    (r"H\.\s?Con\.\s?Res\.", "HCONRES"),
    (r"S\.\s?Con\.\s?Res\.", "SCONRES"),
    (r"H\.\s?J\.\s?Res\.", "HJRES"),
    (r"S\.\s?J\.\s?Res\.", "SJRES"),
    (r"H\.\s?Res\.", "HRES"),
    (r"S\.\s?Res\.", "SRES"),
    (r"H\.\s?R\.", "HR"),
    (r"S\.", "S"),
)
_MEASURE_RE = re.compile(
    r"(?<![A-Za-z.])(" + "|".join(p for p, _ in _MEASURE_TYPES) + r")\s?(\d+)\b"
)


def first_bill_id(text: str) -> str | None:
    """The first bill or resolution number the text names, as a site bill
    id, or None. The Digest names the measure acted on first ("Senate
    passed S. 3257, to ..."; "... discharged from further consideration of
    H.R. 1721")."""
    m = _MEASURE_RE.search(text)
    if not m:
        return None
    for pattern, prefix in _MEASURE_TYPES:
        if re.fullmatch(pattern, m.group(1)):
            return f"{prefix}.{m.group(2)}"
    return None


# ── Numbers written as words ──────────────────────────────────────

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())}


def words_to_int(text: str) -> int | None:
    """"Seventy-four" -> 74, "One hundred twelve" -> 112, "85" -> 85; None
    for anything else. The Senate writes its counts out, the House uses
    digits."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    if text in ("a", "an", "one"):
        return 1
    total = current = 0
    seen = False
    for word in re.split(r"[\s-]+", text):
        if word == "and":
            continue
        if word in _UNITS:
            current += _UNITS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = max(current, 1) * 100
        elif word == "thousand":
            total += max(current, 1) * 1000
            current = 0
        else:
            return None
        seen = True
    return total + current if seen else None


# ── Text layout ───────────────────────────────────────────────────

_PAGE_REF_RE = re.compile(r"^\s*Pages?\s+[SHDE]?\d[\w-]*(?:,\s*[SHDE]?\d[\w-]*)*\s*$")
_PAGE_MARK_RE = re.compile(r"^\s*\[\[Page [A-Z]?\d+\]\]\s*$")
_HEADING_START_RE = re.compile(r"^[A-Z][^:]{1,200}?:(?:\s|$)")
# A measure's name as a heading, over up to five lines: it ends with the
# chamber's own sentence about it.
_HEADING_LINES = 5
_LONG_HEADING_RE = re.compile(r"^[A-Z][^:]{1,500}?:\s+(?:The House|Senate|By \d)")
# A line that ends a sentence, not an abbreviation ("... September 16th.",
# not a name broken after "S." or "U.S.").
_SENTENCE_END_RE = re.compile(r"(?:\d|[a-z]{2,}|\)|'')\.\s*$")
# "Routine Proceedings, pages S4885-S4958" closes the line before the
# first heading the way a page reference line closes an entry, and so
# does a list of them ("pages S95-S119, S121-S122", 2026-01-08).
_TRAILING_PAGES_RE = re.compile(r"\bpages?\s+[SHDE]\d[\w-]*(?:,\s*[SHDE]?\d[\w-]*)*\s*$", re.IGNORECASE)
# A line ending one of these has finished its sentence or clause, so the
# next line may open a new entry or item ("... as follows:", "...; and").
_FINISHED_ENDINGS = (".", ";", ":", "; and", "; or")


def digest_text(page_html: str) -> str:
    """The <pre> text of a GovInfo granule page, entities decoded, tags
    (the GPO link) removed."""
    m = re.search(r"<pre>(.*?)</pre>", page_html, re.S | re.I)
    body = m.group(1) if m else page_html
    text = html_lib.unescape(re.sub(r"<[^>]+>", "", body)).replace("\x00", "")
    # A page break ("[[Page D937]]" between blank lines) can fall mid-
    # sentence; drop it with its blank lines so the sentence stays whole.
    return re.sub(r"\n[ \t]*\n\[\[Page [A-Z]?\d+\]\][ \t]*\n[ \t]*\n", "\n", text)


def _clean(text: str) -> str:
    return " ".join(text.split())


def _entries(lines: list[str]) -> list[dict]:
    """Split a chamber's text into top-level entries, each with its
    sub-items, as {"text", "pages", "items": [{"text", "pages"}]}."""
    entries: list[dict] = []
    block: dict | None = None  # the entry or item being read
    boundary = True            # the previous line ended a block
    prev = ""
    for i, raw in enumerate(lines):
        if _PAGE_MARK_RE.match(raw) or not raw.strip():
            continue
        if _PAGE_REF_RE.match(raw):
            if block is not None:
                block["pages"] = raw.strip()
            boundary = True
            prev = raw
            continue
        # One leading space is a typesetting slip, not an indent: the House's
        # " Public Bills and Resolutions Introduced: ..." (2026-02-25,
        # 2026-03-24) was otherwise read as nothing at all.
        at_col0 = not raw.startswith("  ")
        indented_item = raw.startswith("  ") and not raw.startswith("   ")
        in_item = block is not None and "items" not in block
        # Inside an item only a full stop finishes it: an item's name can
        # break after "; and" ("Commerce, Justice, Science; Energy and Water
        # Development; and" / "Interior and Environment Appropriations Act:
        # ... Senate passed H.R. 6938", 2026-01-15).
        finished = prev.rstrip().endswith("." if in_item else _FINISHED_ENDINGS)
        heading_shaped = _HEADING_START_RE.match(raw.strip())
        if not heading_shaped and in_item and _SENTENCE_END_RE.search(prev):
            # After an item a heading can run over several lines before its
            # colon ("Guaranteeing Reliability through the Interconnection
            # of Dispatchable" / "Power Act: The House passed H.R. 1047",
            # 2025-09-18; a disapproval resolution's quoted rule title takes
            # four lines, 2026-09-16).
            ahead_lines: list[str] = []
            for ln in lines[i:i + _HEADING_LINES]:
                if ln.startswith("  ") or _PAGE_REF_RE.match(ln):
                    break
                ahead_lines.append(ln.strip())
            ahead = " ".join(ahead_lines)
            heading_shaped = _LONG_HEADING_RE.match(ahead)
        if at_col0 and (boundary or (finished and heading_shaped)):
            block = {"text": raw.strip(), "pages": "", "items": []}
            entries.append(block)
        elif indented_item and entries:
            # Continuation lines are always at column 0, so a two-space
            # indent opens a sub-item whatever the line before ended with
            # (a reported bill's item ends "(S. Rept. No. 119-151)").
            block = {"text": raw.strip(), "pages": ""}
            entries[-1]["items"].append(block)
        elif block is not None:
            block["text"] += " " + raw.strip()
        boundary = bool(_TRAILING_PAGES_RE.search(raw))
        prev = raw
    for e in entries:
        e["text"] = _clean(e["text"])
        for it in e["items"]:
            it["text"] = _clean(it["text"])
    return entries


def _split_heading(text: str) -> tuple[str, str]:
    """("Measures Passed", "rest") from "Measures Passed: rest"."""
    m = re.match(r"(.+?):\s+(.*)$", text) or re.match(r"(.+?):$", text)
    if not m:
        return "", text
    return m.group(1).strip(), (m.group(2) if m.lastindex and m.lastindex > 1 else "").strip()


def _split_name(text: str) -> tuple[str, str]:
    """An item "Short Title Act: Senate passed S. 1, ..." -> (name, text).
    Items with no leading "Name:" ("S. 1055, to amend ...") keep no name."""
    m = re.match(r"(.+?):\s+(.*)$", text)
    if m and not _MEASURE_RE.match(m.group(1)) and len(m.group(1)) <= 600:
        return m.group(1).strip(), m.group(2).strip()
    return "", text


# ── Chamber action ────────────────────────────────────────────────

# Seconds too: the Senate's pro forma entries give them ("10:30:06 a.m.").
_TIME = r"(\d{1,2}(?::\d{2}){0,2}\s[ap]\.m\.|12 noon|noon|midnight)"
# A pro forma day is one sentence under Chamber Action, with no
# "Adjournment:" heading: "The Senate met at 10:30:06 a.m. in pro forma
# session, and adjourned at 10:33:29 a.m., until 11 a.m., on Monday, ...".
_PRO_FORMA_RE = re.compile(r"\bThe (?:Senate|House) met at\b.*?\bpro forma session\b.*?(?:\d{4}\.|$)")
_CONVENED_RE = re.compile(r"\b(?:convened|met)\b.*?\bat " + _TIME)
_ADJOURNED_RE = re.compile(r"\badjourned\b.*?\bat " + _TIME)
_RECESSED_RE = re.compile(r"\brecessed\b.*?\bat " + _TIME)
_DAY_RECESS_RE = re.compile(r"^(?:The )?(?:Senate|House) convened at\b")
_NOT_IN_SESSION_RE = re.compile(r"\bwas not in session\b")
_INTRODUCED_RE = re.compile(
    r"^(?P<bills>[\w-]+(?: hundred(?: and)?(?: [\w-]+)?)?) (?:public )?bills?\b"
    r"(?:.*?\band (?P<res>[\w-]+(?: hundred(?: and)?(?: [\w-]+)?)?) resolutions?\b)?",
    re.IGNORECASE,
)

_PASSED_HEADINGS = {"measures passed"}
_FAILED_HEADINGS = {"measures failed", "measure failed"}
_REPORTED_HEADINGS = {"measures reported", "measure reported", "reports filed", "report filed"}
_CONFIRMED_HEADINGS = {"nominations confirmed", "nomination confirmed"}
_INTRODUCED_HEADINGS = {
    "measures introduced", "public bills and resolutions introduced",
    "public bill and resolutions introduced", "public bills and resolution introduced",
}
# The Senate's "House Messages": concurring in the House's amendment to a
# Senate measure (S. 1071, 2025-12-17) is the vote that sends it to the
# President, and is a passage like any other.
_HOUSE_MESSAGES_HEADINGS = {"house messages", "house message"}
# Under a measure in a list, the Senate prints what became of each
# amendment under a column-0 sub-heading of its own ("Adopted:",
# "Rejected:" ...). The list goes on under it: the five resolutions after
# H.R. 5371's amendments on 2025-11-10 were read as part of "Withdrawn:".
_AMENDMENT_HEADINGS = {"adopted", "rejected", "withdrawn", "pending", "agreed to", "not agreed to"}
_LIST_HEADINGS = (
    _PASSED_HEADINGS | _FAILED_HEADINGS | _REPORTED_HEADINGS | _CONFIRMED_HEADINGS | _HOUSE_MESSAGES_HEADINGS
)
# The House's own sentence for each way a measure passes: passed; agreed
# to a resolution; concurred in, or agreed to, the Senate's amendment;
# discharged a measure, or took it from the Speaker's table, and passed
# it. "The House agreed to the ... motion to table the resolution (H. Res.
# 539)" disposes of a resolution without passing it, and was read as
# passing it.
_HOUSE_PASSED_RE = re.compile(
    r"\bThe House (?:passed|cleared|concurred in"
    r"|agreed to (?:the motion to concur in|the (?:Senate|House) amendments? to"
    r"|(?:discharge from committee|take from the Speaker's table) and (?:pass|agree to)|(?=[HS]\.)))"
)
_HOUSE_FAILED_RE = re.compile(
    r"\b(?:failed of passage|The House (?:failed to (?:pass|agree to)|rejected|did not agree to) (?=[HS]\.))"
)
# A list item that records a measure passing: "Senate passed S. 3257",
# "Senate agreed to S. Res. 903", "... was discharged ... and the bill was
# then passed", "Senate concurred in the amendment of the House to S.
# 1071". Not "Senate agreed to the motion to close further debate on ...",
# which names the measure without passing it.
_PASSAGE_RE = re.compile(
    r"\b(?:Senate|The House) (?:passed|agreed to (?!the motion)|concurred in)"
    r"|\bwas then (?:passed|agreed to)\b"
)
# "H. Res. 566, amended, the rule providing for consideration of ... was
# agreed to by a yea-and-nay vote ..." — the House adopting a rule, in the
# Digest's passive form; "was agreed to yesterday" reports an earlier day.
_RULE_RE = re.compile(
    r"^(H\.\s?Res\.\s?\d+),(?: as)?(?: amended,)? the rule\b.*?\bwas (not )?agreed to"
    r"(?! (?:yesterday|earlier|on |(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b))"
)
# A suspension's measures are listed under the motion, the first of them
# often on the heading line itself ("... pass the following measure:
# Epstein Files Transparency Act: H.R. 4405, ...", 2025-11-18).
_FOLLOWING_RE = re.compile(r"\bthe following (?:measures?|bills?|resolutions?)\s*[:.]\s*(.*)$")


def _events_from_items(kind: str, items: list[dict], merge_details: bool, needs_statement: bool = False) -> list[dict]:
    """One event per sub-item. With merge_details, an item naming no
    measure of its own ("Doe (for Roe/Poe) Amendment No. 6834, in the
    nature of a substitute.") is the previous item's detail and is
    appended to it rather than standing as an event. With needs_statement,
    an item stands as an event only when it says the measure passed
    (_PASSAGE_RE): a cloture vote or an amendment listed under a measure
    can name a bill too.

    A confirmation item ending with a colon ("During consideration of this
    nomination today, Senate also took the following action:") introduces
    the next item, and both are the previous nomination's detail."""
    events: list[dict] = []
    lead_in = False
    for it in items:
        name, text = _split_name(it["text"])
        bill_id = first_bill_id(text) or first_bill_id(name)
        if kind == "confirmed":
            detail = bool(events) and (lead_in or it["text"].rstrip().endswith(":"))
            lead_in = it["text"].rstrip().endswith(":")
            if detail:
                events[-1]["text"] += " " + it["text"]
                continue
        elif needs_statement and not _PASSAGE_RE.search(it["text"]):
            if merge_details and events:
                events[-1]["text"] += " " + it["text"]
            continue
        elif merge_details and bill_id is None and events:
            events[-1]["text"] += " " + it["text"]
            events[-1]["pages"] = events[-1]["pages"] or it["pages"]
            continue
        events.append({"kind": kind, "name": name, "text": text, "bill_id": bill_id, "pages": it["pages"]})
    return events


def _last_sentence(heading: str) -> str:
    """A measure's name from a heading that a typesetting slip ran on from
    the paragraph before ("... was agreed to yesterday, March 6th.
    Censuring Representative Al Green of Texas", 2025-03-06)."""
    return re.split(r"(?<=[a-z]{2}\.)\s+", heading)[-1]


def _flatten(entries: list[dict]) -> list[dict]:
    """The entries as the Digest means them. A list heading ("Measures
    Passed:", "Suspensions:") keeps its items, and the items under an
    amendment sub-heading after it go back to it (_AMENDMENT_HEADINGS).
    Any other entry's indented paragraphs are entries of their own: the
    House indents a measure's paragraph after a page reference ("  One Big
    Beautiful Bill Act: The House agreed to the motion to concur in the
    Senate amendment to H.R. 1 ...", 2025-07-02), and read as items of the
    entry before them they were dropped."""
    grouped: list[dict] = []
    parent: dict | None = None
    for e in entries:
        heading, rest = _split_heading(e["text"])
        if heading.lower() in _AMENDMENT_HEADINGS and not rest and parent is not None:
            parent["items"] += [dict(it, under_heading=True) for it in e["items"]]
            continue
        parent = e
        grouped.append(e)
    flat: list[dict] = []
    for e in grouped:
        key = _split_heading(e["text"])[0].lower()
        if key in _LIST_HEADINGS or key.startswith("suspension"):
            flat.append(e)
            continue
        flat.append({**e, "items": []})
        flat += [{"text": it["text"], "pages": it["pages"], "items": []}
                 for it in e["items"] if not it.get("under_heading")]
    return flat


def parse_chamber_action(text: str) -> dict:
    """A chamber's Daily Digest floor section -> {in_session,
    adjournment_text, convened_at, adjourned_at, bills_introduced,
    resolutions_introduced, introduced_text, events}."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == "Chamber Action"), None)
    body = lines[start + 1:] if start is not None else lines
    joined = _clean(" ".join(body))
    out = {
        "in_session": True, "adjournment_text": "", "convened_at": None, "adjourned_at": None,
        "bills_introduced": None, "resolutions_introduced": None, "introduced_text": "", "events": [],
    }
    if _NOT_IN_SESSION_RE.search(joined):
        out["in_session"] = False
        out["adjournment_text"] = joined
        return out

    for entry in _flatten(_entries(body)):
        heading, rest = _split_heading(entry["text"])
        key = heading.lower()
        if key in _PASSED_HEADINGS:
            out["events"] += _events_from_items("passed", entry["items"], merge_details=True, needs_statement=True)
        elif key in _HOUSE_MESSAGES_HEADINGS:
            # Only a concurrence passes anything; the other messages are
            # requests for a conference, insistence, and the like.
            out["events"] += [
                e for e in _events_from_items("passed", entry["items"], merge_details=True, needs_statement=True)
                if "concurred in" in e["text"]
            ]
        elif key in _FAILED_HEADINGS:
            if rest:  # "Measures Failed: Name: By 49 yeas ..." on the heading line
                name, t = _split_name(rest)
                out["events"].append({"kind": "failed", "name": name, "text": t,
                                      "bill_id": first_bill_id(t), "pages": entry["pages"]})
            out["events"] += _events_from_items("failed", entry["items"], merge_details=True)
        elif key in _REPORTED_HEADINGS:
            out["events"] += _events_from_items("reported", entry["items"], merge_details=False)
        elif key in _CONFIRMED_HEADINGS:
            out["events"] += _events_from_items("confirmed", entry["items"], merge_details=False)
        elif key in _INTRODUCED_HEADINGS:
            out["introduced_text"] = rest
            m = _INTRODUCED_RE.match(rest)
            if m:
                out["bills_introduced"] = words_to_int(m.group("bills"))
                if m.group("res"):
                    out["resolutions_introduced"] = words_to_int(m.group("res"))
        elif key == "adjournment" or (key == "recess" and _DAY_RECESS_RE.search(rest)):
            # The Senate ends some days in recess rather than adjournment
            # ("Recess: Senate convened at 10 a.m. and recessed ... at 4:59
            # p.m., until 8:30 a.m. on Friday", 2026-01-15, 4 of 69 Senate
            # days sampled). The House's mid-day "Recess: The House
            # recessed at 11:50 a.m. and reconvened ..." is not the day's end.
            out["adjournment_text"] = rest
            if m := _CONVENED_RE.search(rest):
                out["convened_at"] = m.group(1)
            if m := (_ADJOURNED_RE.search(rest) or _RECESSED_RE.search(rest)):
                out["adjourned_at"] = m.group(1)
        elif key.startswith("suspension"):
            # "The House agreed to suspend the rules and pass the following
            # measures:" — each item is one of them, the first often on the
            # heading line. "Suspension--Proceedings Resumed: The House
            # failed to agree to suspend the rules and pass the following
            # measure." lists one that failed; so does "agreed to failed to
            # suspend" (sic, 2025-12-01: H.J. Res. 1, 212-206 on a 2/3
            # vote, had been recorded as passed). "Proceedings Postponed"
            # passes nothing yet.
            kind = "failed" if "failed" in rest else ("passed" if re.search(r"\b(?:pass|agree to)\b", rest) else None)
            if kind:
                items = entry["items"]
                # Only a measure: "... the following measures. Consideration
                # began Monday, May 5th." is a note, not one of them.
                if (m := _FOLLOWING_RE.search(rest)) and first_bill_id(m.group(1)):
                    items = [{"text": m.group(1), "pages": entry["pages"]}] + items
                out["events"] += _events_from_items(kind, items, merge_details=True)
        elif (m := _RULE_RE.match(entry["text"])):
            out["events"].append({"kind": "failed" if m.group(2) else "passed", "name": "", "text": entry["text"],
                                  "bill_id": first_bill_id(m.group(1)), "pages": entry["pages"]})
        elif heading and _HOUSE_FAILED_RE.search(rest):
            out["events"].append({"kind": "failed", "name": _last_sentence(heading), "text": rest,
                                  "bill_id": first_bill_id(rest), "pages": entry["pages"]})
        elif heading and _HOUSE_PASSED_RE.search(rest) and first_bill_id(rest):
            out["events"].append({"kind": "passed", "name": _last_sentence(heading), "text": rest,
                                  "bill_id": first_bill_id(rest), "pages": entry["pages"]})
    if not out["adjournment_text"] and (m := _PRO_FORMA_RE.search(joined)):
        # Read as a session with no times, the day said "met and took no
        # record votes" (2026-10-01, 10-05 and 10-06).
        out["adjournment_text"] = m.group(0).strip()
        if c := _CONVENED_RE.search(out["adjournment_text"]):
            out["convened_at"] = c.group(1)
        if a := _ADJOURNED_RE.search(out["adjournment_text"]):
            out["adjourned_at"] = a.group(1)
    # The House repeats a rule's paragraph under each measure it brought up
    # (H. Res. 953 three times on 2025-12-17): the same entry is one event.
    seen: set[tuple] = set()
    unique = []
    for e in out["events"]:
        key = (e["kind"], e["bill_id"], e["text"]) if e["kind"] != "committee" else (id(e),)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    out["events"] = unique
    return out


# ── Committee meetings ────────────────────────────────────────────

_COMMITTEE_RE = re.compile(r"^((?:Committee|Subcommittee|Select Committee|Special Committee|Joint)[^:]{0,160}):\s+(.*)$")


def parse_committee_meetings(text: str) -> list[dict]:
    """A chamber's "Committee Meetings" granule -> one event per meeting,
    named by committee, with the Digest's sentence as text. "No
    committee meetings were held." yields none."""
    lines = [ln for ln in text.splitlines() if not _PAGE_MARK_RE.match(ln)]
    events: list[dict] = []
    topic = ""
    current: dict | None = None
    for raw in lines:
        line = raw.strip()
        if not line:
            current = None
            continue
        if line.isupper() and len(line) > 3 and not line.startswith("["):
            topic = line
            current = None
            continue
        m = _COMMITTEE_RE.match(line)
        if m and not raw.startswith(" "):
            current = {"kind": "committee", "name": m.group(1), "text": m.group(2),
                       "topic": topic.title(), "bill_id": None, "pages": ""}
            events.append(current)
        elif current is not None:
            current["text"] += " " + line
    for e in events:
        e["text"] = _clean(e["text"])
        e["bill_id"] = first_bill_id(e["text"])
    return events


# ── Next meeting ──────────────────────────────────────────────────

# One quantifier per stretch of whitespace: nesting them ("(?:\s*\n)*\s*")
# backtracks exponentially on a long run of blank lines (CodeQL py/redos).
_NEXT_RE = re.compile(
    r"Next Meeting of the (SENATE|HOUSE OF REPRESENTATIVES)[ \t]*\n[ \t]*([^\n]+?)[ \t]*\n"
    r"\s*?(?:Senate|House) Chamber[ \t]*\n(.*?)(?=\n[ \t]*\n|\n[ \t]*Next Meeting|\n_{5,}|\Z)",
    re.S,
)


def parse_next_meetings(text: str) -> dict[str, dict]:
    """{"senate": {"when", "program"}, "house": {...}} from the Digest's
    "Next Meeting" granule, verbatim ("3 p.m., Monday, September 28")."""
    out: dict[str, dict] = {}
    for m in _NEXT_RE.finditer(text):
        chamber = "senate" if m.group(1) == "SENATE" else "house"
        out[chamber] = {"when": _clean(m.group(2)), "program": _clean(m.group(3))}
    return out


def granule_role(title: str) -> tuple[str, str] | None:
    """What a DAILYDIGEST granule holds, from its GovInfo title:
    ("senate"|"house", "floor"|"committees"), ("both", "next"), or None
    (tomorrow's committee schedule and end matter are not read). A day's
    first granule carries the Highlights with its chamber's floor section
    ("Daily Digest/Highlights + Senate"), so the title's last segment is
    read part by part."""
    t = title.lower()
    if "next meeting" in t:
        return ("both", "next")
    parts = {p.strip() for p in t.rsplit("/", 1)[-1].split("+")}
    if "senate" in parts:
        return ("senate", "floor")
    if "house of representatives" in parts:
        return ("house", "floor")
    if "senate committee meetings" in parts:
        return ("senate", "committees")
    if "house committee meetings" in parts:
        return ("house", "committees")
    return None
