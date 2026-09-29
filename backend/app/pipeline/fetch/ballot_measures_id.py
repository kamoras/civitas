"""Idaho's ballot-measure PDF strategy — the Secretary of State's
official Idaho Voter Pamphlet (one of potentially many per-state
strategies; see ballot_measures_pdf.py for the shared contract).

Discovered from voteidaho.gov's "<year> Idaho General Election" page
(the pamphlet link is "Voter Pamphlet PDF", served from
archive.voteidaho.gov/download/<year>_Voter_Pamphlet.pdf; the Spanish
"Folleto para Votantes" sits beside it and is excluded). Verified live
2026-09-28 against the real November 3, 2026 pamphlet (20 pages): all
four statewide questions parse — House Joint Resolutions 4 and 6
(constitutional amendments), Proposition One (an initiative) and the
advisory question in House Bill 932 (the state gun).

Every measure opens with a 16pt bold heading ("Amendment to Idaho
Constitution - House Joint Resolution 4", "Proposition One: ...",
"Advisory Question - House Bill 932") and runs until the next 16pt
heading of any kind (the pamphlet's non-measure sections — "ABSENTEE
VOTING", "SECURE ELECTIONS" — end the last one). A 16pt heading that
names a Proposition / Joint Resolution / Referendum / Advisory question
in any other wording raises, as does a recognised heading missing its
sections: the pamphlet then reads as ingest_failed, never as a list with
a measure left out. Inside, 12pt bold
headings name each section. What this module keeps, all verbatim:

- amendments: the quoted ballot question ("Shall Section 26 ...?"), then
  the Legislative Council's "Statement of Meaning, Purpose, and Result
  to Be Accomplished", each under the pamphlet's own heading;
- the initiative: its "Short Ballot Title" and "Long Ballot Title", and
  the "Summary of Fiscal Impact" from the FISCAL IMPACT STATEMENT, whose
  drafter is read from its own "Provided by ..." line (the sponsor's
  FUNDING SOURCE STATEMENT is not the state's fiscal statement and is
  not used);
- the advisory question: the question and its six options (printed in
  two columns, (a)-(c) then (d)-(f), re-read column by column), then the
  "Brief Statement of Purpose".

"WHAT YOUR VOTE WILL DO" is Idaho's own YES/NO framing: two columns
under 48pt "Yes"/"No" labels. The columns are split at the "No"
label's x-position — measured, not fixed — and each side is kept only
if it reads "A YES vote ..." / "A NO vote ..." respectively; anything
else leaves both null. The advisory question is multiple choice and has
no such section, so its yes/no stay null.

Origin comes from what the heading names: a Joint Resolution or House
Bill is the Legislature's; a Proposition is an initiative only when its
own full text is enacted "by the People of the State of Idaho" (a veto
referendum would also be a Proposition), otherwise null.
"""

import re

from app.pipeline.fetch.ballot_measure_pdf_geometry import clean_text, rows

_MEASURE_HEADING_SIZE = 15.0  # 16.2pt in the real pamphlet
_SECTION_HEADING_SIZE = 11.5  # 12pt bold
_LABEL_SIZE = 30.0  # the 48pt "Yes"/"No" labels

_AMENDMENT_RE = re.compile(r"^Amendment to Idaho Constitution\s*[-–]\s*((?:House|Senate) Joint Resolution \d+)$")
_PROPOSITION_RE = re.compile(r"^(Proposition \w+)\s*:\s*(.+)$")
_ADVISORY_RE = re.compile(r"^Advisory Question\s*[-–]\s*((?:House|Senate) Bill \d+)$")
_PROVIDED_BY_RE = re.compile(r"^Provided by (?:the )?(.+)$")
_OPTION_RE = re.compile(r"^\([a-z]\)$")
_MEASURE_WORDS_RE = re.compile(r"\b(?:Proposition|Joint Resolution|Referendum|Advisory|Initiative|Amendment)\b", re.IGNORECASE)

STATEMENT_HEADING = "Legislative Council’s Statement of Meaning, Purpose, and Result to Be Accomplished"


def _is_bold(word: dict) -> bool:
    font = (word.get("fontname") or "")
    return "Bold" in font and "SemiBold" not in font


def _row_objects(pages) -> list[dict]:
    out = []
    for page_no, page in enumerate(pages):
        words = page.extract_words(extra_attrs=["fontname", "size"])
        bands = rows(words)
        for key in sorted(bands):
            ws = sorted(bands[key], key=lambda w: w["x0"])
            out.append({
                "page": page_no,
                "words": ws,
                "text": " ".join(w["text"] for w in ws),
                "size": max(w["size"] for w in ws),
                "first": ws[0],
            })
    return out


def _is_measure_heading(row: dict) -> bool:
    return row["first"]["size"] >= _MEASURE_HEADING_SIZE and _is_bold(row["first"]) and row["first"]["size"] < _LABEL_SIZE


def _is_section_heading(row: dict) -> bool:
    first = row["first"]
    return _SECTION_HEADING_SIZE <= first["size"] < _MEASURE_HEADING_SIZE and _is_bold(first)


def _sections(block: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """(rows before the first section heading, {heading text: rows})."""
    preamble: list[dict] = []
    sections: dict[str, list[dict]] = {}
    current: list[dict] | None = None
    for row in block:
        if _is_section_heading(row):
            current = sections.setdefault(clean_text(row["text"]) or "", [])
            continue
        (preamble if current is None else current).append(row)
    return preamble, sections


def _prose(rows_: list[dict]) -> str | None:
    """Rows up to the first semibold/bold sub-heading, joined — a
    line-final hyphen joins its word across the break."""
    parts: list[str] = []
    for row in rows_:
        font = row["first"].get("fontname") or ""
        if "SemiBold" in font or "Italic" in font:
            break
        parts.append(row["text"])
    text = ""
    for part in parts:
        if text.endswith("-"):
            text += part
        else:
            text = f"{text} {part}" if text else part
    return clean_text(text)


def _yes_no(section_rows: list[dict]) -> tuple[str | None, str | None]:
    body: list[dict] = []
    for row in section_rows:
        font = row["first"].get("fontname") or ""
        if "SemiBold" in font and row["first"]["size"] < _LABEL_SIZE:
            break
        body.extend(row["words"])
    no_label = next((w for w in body if w["text"] == "No" and w["size"] >= _LABEL_SIZE), None)
    if no_label is None:
        return None, None
    text_words = [w for w in body if w["size"] < _LABEL_SIZE]
    left = [w for w in text_words if w["x0"] < no_label["x0"]]
    right = [w for w in text_words if w["x0"] > no_label["x0"]]

    def _join(words):
        bands = rows(words)
        return clean_text(" ".join(
            " ".join(w["text"] for w in sorted(bands[k], key=lambda w: w["x0"])) for k in sorted(bands)
        ))

    yes, no = _join(left), _join(right)
    if not (yes and yes.startswith("A YES vote") and no and no.startswith("A NO vote")):
        return None, None
    return yes, no


def _quoted_question(preamble: list[dict]) -> str | None:
    text = clean_text(" ".join(r["text"] for r in preamble)) or ""
    m = re.search(r"“(.+?)”", text)
    return clean_text(m.group(1)) if m else None


def _advisory_question(preamble: list[dict]) -> str | None:
    if not preamble:
        return None
    question = preamble[0]["text"]
    option_words = [w for r in preamble[1:] for w in r["words"]]
    markers = [w for w in option_words if _OPTION_RE.match(w["text"])]
    if not markers:
        return clean_text(question)
    split = min((w["x0"] for w in markers if w["x0"] > markers[0]["x0"] + 50), default=None)
    columns = [option_words] if split is None else [
        [w for w in option_words if w["x0"] < split - 1],
        [w for w in option_words if w["x0"] >= split - 1],
    ]
    parts = [question]
    for col in columns:
        bands = rows(col)
        for k in sorted(bands):
            parts.append(" ".join(w["text"] for w in sorted(bands[k], key=lambda w: w["x0"])))
    text = ""
    for part in parts:
        text = text + part if text.endswith("-") else (f"{text} {part}" if text else part)
    return clean_text(text)


def _fiscal(sections: dict[str, list[dict]]) -> tuple[str | None, str | None]:
    authority = None
    for row in sections.get("FISCAL IMPACT STATEMENT", []):
        m = _PROVIDED_BY_RE.match(row["text"])
        if m:
            authority = clean_text(m.group(1))
            break
    summary = _prose(sections.get("Summary of Fiscal Impact", []))
    return summary, (authority if summary else None)


def _parse_block(heading: str, block: list[dict]) -> dict | None:
    preamble, sections = _sections(block)
    yes_means, no_means = _yes_no(sections.get("WHAT YOUR VOTE WILL DO", []))
    full_text = " ".join(r["text"] for r in block)

    m = _AMENDMENT_RE.match(heading)
    if m:
        question = _quoted_question(preamble)
        statement = _prose(sections.get(STATEMENT_HEADING, []))
        if not question or not statement:
            return None
        # Two drafters, two fields: the ballot question (the Legislature's,
        # written into the joint resolution — the words on the ballot) is
        # the official title and carries title_authority; the Legislative
        # Council's statement is the summary, under the pamphlet's own
        # heading, which names its drafter as printed. They used to be
        # joined into one official_summary under a two-drafter label.
        return {
            "number": m.group(1),
            "title": heading,
            "official_title": question,
            "origin": "Idaho Legislature",
            "official_summary": f"{STATEMENT_HEADING}: {statement}",
            "fiscal_impact": None,
            "yes_means": yes_means,
            "no_means": no_means,
            "title_authority": "Idaho Legislature",
            "fiscal_authority": None,
        }

    m = _PROPOSITION_RE.match(heading)
    if m:
        short = _prose(sections.get("Short Ballot Title", []))
        long_ = _prose(sections.get("Long Ballot Title", []))
        if not short or not long_:
            return None
        fiscal, fiscal_authority = _fiscal(sections)
        initiative = "Be it enacted by the People of the State of Idaho" in full_text
        return {
            "number": m.group(1),
            "title": clean_text(m.group(2)),
            "origin": "Idaho voters (initiative petition)" if initiative else None,
            "official_summary": f"Short Ballot Title: {short} Long Ballot Title: {long_}",
            "fiscal_impact": fiscal,
            "yes_means": yes_means,
            "no_means": no_means,
            "title_authority": "Idaho Attorney General (ballot titles)",
            "fiscal_authority": fiscal_authority,
        }

    m = _ADVISORY_RE.match(heading)
    if m:
        question = _advisory_question(preamble)
        purpose = _prose(sections.get("BRIEF STATEMENT OF PURPOSE", []))
        if not question:
            return None
        summary = question if not purpose else f"{question} Brief Statement of Purpose: {purpose}"
        return {
            "number": m.group(1),
            "title": heading,
            "origin": "Idaho Legislature",
            "official_summary": summary,
            "fiscal_impact": None,
            "yes_means": None,
            "no_means": None,
            "title_authority": "Idaho Legislature",
            "fiscal_authority": None,
        }
    return None


def parse_document(pages) -> list[dict]:
    row_list = _row_objects(pages)
    results: list[dict] = []
    heading: str | None = None
    block: list[dict] = []
    for row in row_list + [None]:
        if row is None or _is_measure_heading(row):
            if heading is not None:
                parsed = _parse_block(heading, block)
                if parsed is None:
                    # Recognised heading, unexpected sections: fail the whole
                    # pamphlet rather than publish a list missing a measure.
                    raise ValueError(f"ID {heading!r}: unexpected section layout")
                results.append(parsed)
            heading, block = None, []
            if row is None:
                break
            text = clean_text(row["text"]) or ""
            if _AMENDMENT_RE.match(text) or _PROPOSITION_RE.match(text) or _ADVISORY_RE.match(text):
                heading = text
            elif _MEASURE_WORDS_RE.search(text):
                # A measure-looking heading in a shape we don't know (a
                # referendum, a Senate advisory question worded
                # differently): fail rather than leave it out.
                raise ValueError(f"ID unrecognised measure heading {text!r}")
            continue
        if heading is None:
            continue
        # Running header ("PROPOSITION ONE: ...") and page footer rows.
        if row["first"]["size"] < 9.5:
            continue
        if "SemiBold" in (row["first"].get("fontname") or "") and row["first"]["top"] < 60:
            continue
        block.append(row)
    return results
