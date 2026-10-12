"""Fetch each congressional committee's jurisdiction from the chambers' rules.

Regenerates app/data/committee_jurisdictions.json, the prototype input for
the Action Center's committee routing (app/committee_routing.py):
each committee's jurisdiction as the list of subjects its chamber's rules
assign to it, one string per subject.

Sources, in order of authority:
  - House Rule X, clause 1 (rules.house.gov, the current Congress's rules) —
    the subjects referred to each House standing committee.
  - Senate Rule XXV, paragraph 1 (rules.senate.gov) — the subjects referred
    to each Senate standing committee.
  - For the three committees with legislative jurisdiction that neither
    rule lists (the two intelligence committees and Senate Indian Affairs,
    created by resolution), the committee's own published jurisdiction
    statement as unitedstates/congress-legislators records it, with the
    page it was taken from.

Committee names, pages and codes come from congress-legislators'
committees-current.yaml, the file fetch/committee_leadership.py already
reads membership against, so the codes here join the stored rosters.

Not routed (left out on purpose): the ethics committees (member conduct,
not policy), the Senate Special Committee on Aging (no legislative
jurisdiction by its own statement), joint committees, caucuses, and select
committees with no standing jurisdiction.

Run from the repo (network required; pdfplumber is in the backend image):
    python3 backend/scripts/fetch_committee_jurisdictions.py [output.json]

Rerun when either chamber adopts new rules (the House at the start of each
Congress), then rerun scripts/calibrate_committee_routing.py: the routing
threshold is measured against these exact strings.
"""

import datetime
import io
import json
import pathlib
import re
import sys
import urllib.request

import pdfplumber
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.contact import CONTACT_EMAIL  # noqa: E402

UA = {"User-Agent": f"CivitasCivicPlatform/1.0 (committee jurisdictions; contact: {CONTACT_EMAIL})"}
HOUSE_RULES_URL = "https://rules.house.gov/sites/evo-subsites/rules.house.gov/files/documents/houserules119thupdated.pdf"
SENATE_RULES_URL = "https://www.rules.senate.gov/download/the-rules-of-the-senate-117th"
COMMITTEES_URL = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/committees-current.yaml"
DEFAULT_OUTPUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "committee_jurisdictions.json"

# Committees whose jurisdiction is set by resolution, not by either rule:
# their own published statement (congress-legislators' `jurisdiction`).
STATEMENT_COMMITTEES = ("HLIG", "SLIN", "SLIA")
# S. Res. 445 (108th Congress) gave Homeland Security and Governmental
# Affairs the Department of Homeland Security without amending Rule XXV
# (the rule's own footnote says so), so the committee's statement is added
# to the rule's subjects.
RULE_PLUS_STATEMENT = ("SSGA",)
# Listed in the rules but not routed — see the module docstring.
NOT_ROUTED = {"HSSO"}
# Senate Rule XXV still names Homeland Security and Governmental Affairs by
# its pre-2004 name (S. Res. 445, 108th Congress, renamed it in a footnote).
SENATE_RENAMED = {"Governmental Affairs": "Homeland Security and Governmental Affairs"}


def _get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as resp:
        return resp.read()


def _pdf_text(body: bytes, columns: list[tuple[float, float]] | None = None) -> list[str]:
    """Lines of a PDF, reading each page's columns left to right."""
    lines: list[str] = []
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        for page in pdf.pages:
            x0, top, x1, bottom = page.bbox
            for a, b in columns or [(x0, x1)]:
                lines.extend((page.crop((a, top, b, bottom)).extract_text() or "").splitlines())
    return lines


def _join(lines: list[str]) -> str:
    """One string, rejoining words the typesetter broke across lines."""
    out = ""
    for line in (s.strip() for s in lines):
        if not line:
            continue
        if out.endswith("-") and line[:1].islower():
            out = out[:-1] + line
        else:
            out = f"{out} {line}" if out else line
    return re.sub(r"\s+", " ", out)


def _clean_item(text: str) -> str:
    """One subject, or "" for a lead-in ("... relating to the following:")."""
    text = re.sub(r"\s+", " ", text).strip(" ;.")
    return "" if text.endswith((":", "—")) else text


def house_jurisdictions(body: bytes) -> dict[str, list[str]]:
    # The House pamphlet is set in three columns.
    lines = _pdf_text(body, [(0, 215), (215, 395), (395, 612)])
    start = next(i for i, s in enumerate(lines) if s.startswith("(a) Committee on Agriculture"))
    end = next(i for i, s in enumerate(lines[start:], start) if s.startswith("General oversight"))
    running_heads = re.compile(r"^(H|ES|RULES OF THE|HOUSE OF REPRESENTATIVE|Rule X, clause.*|.*PAMPHLET.*)$")
    # The print shop's slug at the foot of each page, wherever the crop cut it.
    page_slug = re.compile(r"VerDate.*?PO 00\d*|\d{4,5} Frm \d+ Fmt \d+ Sfmt \d+ \S+(?: \d+)*")
    text = _join([s for s in lines[start:end] if not running_heads.match(s.strip())])
    text = re.sub(r"\s+", " ", page_slug.sub(" ", text))
    # Clause 1 ends where clause 2 begins.
    text = re.split(r"\s2\. \(a\)", text)[0]
    parts = re.split(r"\(([a-z])\) Committee on (.+?)\.\s", text)
    out: dict[str, list[str]] = {}
    for i in range(1, len(parts) - 2, 3):
        name, body_text = parts[i + 1], parts[i + 2]
        items = [_clean_item(s) for s in re.split(r"(?:^|\s)\((?:\d+|[A-Z])\)\s", body_text) if _clean_item(s)]
        out[f"House Committee on {name}"] = items
    return out


def senate_jurisdictions(body: bytes) -> dict[str, list[str]]:
    lines = _pdf_text(body)
    start = next(i for i, s in enumerate(lines) if s.startswith("(a)(1) Committee on Agriculture"))
    kept: list[str] = []
    in_footnote = False
    for s in lines[start:]:
        s = s.strip()
        if re.match(r"^2\.\d* Except", s):
            break  # paragraph 2: committee sizes
        if re.fullmatch(r"\d+", s):
            in_footnote = False  # a page number closes the page's footnotes
            continue
        if re.match(r"^\d+[A-Z‘]", s):
            in_footnote = True
        if not in_footnote:
            kept.append(s)
    text = _join(kept)
    parts = re.split(r"\(([a-z])\)(?:\(1\))?\d* Committee on (.+?), to which", text)
    out: dict[str, list[str]] = {}
    for i in range(1, len(parts) - 2, 3):
        name, body_text = parts[i + 1], parts[i + 2]
        name = SENATE_RENAMED.get(name, name)
        # Drop the referral boilerplate up to "subjects:" where present.
        body_text = re.split(r"following subjects:", body_text, maxsplit=1)[-1]
        segments = re.split(r"\s(?=\d{1,2}\. [A-Z(])|\s(?=\(\d\) )|\s(?=\([A-Z]\) )", " " + body_text)
        items = []
        for seg in segments:
            seg = re.sub(r"^(\d{1,2}\.|\(\d\)|\([A-Z]\))\s*", "", seg.strip())
            seg = _clean_item(seg)
            if len(seg) > 3:
                items.append(seg)
        out[f"Senate Committee on {name}"] = items
    return out


def build(house_pdf: bytes, senate_pdf: bytes, committees_yaml: bytes) -> dict:
    committees = yaml.safe_load(committees_yaml)
    by_name = {c["name"]: c for c in committees}
    by_code = {c["thomas_id"]: c for c in committees}
    rules = {**house_jurisdictions(house_pdf), **senate_jurisdictions(senate_pdf)}
    out: dict[str, dict] = {}
    for name, items in rules.items():
        c = by_name.get(name.replace("’", "'"))
        if c is None:
            raise SystemExit(f"rules name not in committees-current.yaml: {name!r}")
        if c["thomas_id"] in NOT_ROUTED:
            continue
        source = HOUSE_RULES_URL if c["type"] == "house" else SENATE_RULES_URL
        out[c["thomas_id"]] = {"name": c["name"], "chamber": c["type"], "url": c.get("url"),
                               "jurisdiction": items, "jurisdiction_source": source}
    for code in STATEMENT_COMMITTEES + RULE_PLUS_STATEMENT:
        c = by_code[code]
        sentences = [s.strip() for s in re.split(r"(?<=[.])\s+", c["jurisdiction"]) if s.strip()]
        if code in out:
            out[code]["jurisdiction"] += sentences
            out[code]["jurisdiction_source"] += f" and {c.get('jurisdiction_source')}"
            continue
        out[code] = {"name": c["name"], "chamber": c["type"], "url": c.get("url"),
                     "jurisdiction": sentences, "jurisdiction_source": c.get("jurisdiction_source")}
    for code, entry in out.items():
        if len(entry["jurisdiction"]) < 1:
            raise SystemExit(f"{code}: no jurisdiction parsed")
    return out


def main() -> None:
    output = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    committees = build(_get(HOUSE_RULES_URL), _get(SENATE_RULES_URL), _get(COMMITTEES_URL))
    doc = {
        "_source": (
            "Generated by backend/scripts/fetch_committee_jurisdictions.py: each committee's "
            "subjects as House Rule X clause 1 and Senate Rule XXV paragraph 1 list them "
            f"({HOUSE_RULES_URL}, {SENATE_RULES_URL}); HLIG, SLIN and SLIA, whose jurisdiction "
            "is set by resolution (and SSGA, whose DHS jurisdiction S. Res. 445 added without amending "
            "the rule), from the committee's own statement as unitedstates/"
            "congress-legislators records it (jurisdiction_source names the page). Names, "
            f"pages and codes from {COMMITTEES_URL}."
        ),
        "_as_of": datetime.date.today().isoformat(),
        "committees": dict(sorted(committees.items())),
    }
    output.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(committees)} committees -> {output}")


if __name__ == "__main__":
    main()
