"""The bill page's boxes: one short, templated line per part of a bill's
record, so the page can lead with what happened and keep the full text
behind each box's "More".

Every sentence here is a fixed template filled from the shaped record
(bill_record.shape_record): dates, counts and the sources' own labels. No
model wording, and nothing paraphrased from a source: where a box quotes
(the CRS summary's first sentence), it quotes verbatim and names its
source. Counts are of the record as served, so the box and the full list
behind it always agree.
"""

import re
from collections import Counter
from datetime import date

from app.services.bill_journey import long_date

_CHAMBER = {"house": "House", "senate": "Senate"}
_PARTY = {"R": ("Republican", "Republicans"), "D": ("Democrat", "Democrats"),
          "I": ("Independent", "Independents"), "ID": ("Independent", "Independents")}


def _party(code: str, n: int = 2) -> str:
    one, many = _PARTY.get(code, (code, code))
    return one if n == 1 else many

# Congress.gov's own action types, as words (a fixed vocabulary, read as a
# data format). An unlisted type is shown as Congress.gov spells it.
_ACTION_TYPE = {
    "IntroReferral": "introduction and referral",
    "Committee": "committee",
    "Calendars": "calendar",
    "Floor": "floor",
    "ResolvingDifferences": "resolving differences",
    "President": "President",
    "Veto": "veto",
    "BecameLaw": "law",
    "Discharge": "discharge",
}

# A sentence ends at ., ! or ? followed by a space and a capital, but only
# after an all-lowercase word, a number or a closing bracket: "H. Con. Res.
# 14" and "U.S. Code" are not sentence ends. A sentence that ends on a
# capitalised word runs on into the next, which keeps the quote verbatim,
# only longer.
_BREAK = re.compile(r"[.!?]\s+(?=[A-Z\"“(])")
_LAST_WORD = re.compile(r"(\S+)$")
# A CRS title line ("One Big Beautiful Bill Act") has no closing punctuation.
_SENTENCE_PUNCT = (".", "!", "?", ":", '"', "”", ")")


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _date(iso: str | None) -> str:
    """A date inside a sentence; a date the record doesn't give in ISO form
    is shown as given rather than breaking the page."""
    try:
        return long_date((iso or "")[:10])
    except ValueError:
        return iso or "an undated day"


def _days(a: str | None, b: str | None) -> int | None:
    try:
        return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    except (TypeError, ValueError):
        return None


def _stat(value, caption: str) -> dict:
    return {"type": "stat", "value": value, "caption": caption}


def first_sentence(text: str) -> str:
    for m in _BREAK.finditer(text):
        word = _LAST_WORD.search(text[:m.start()])
        token = word.group(1) if word else ""
        if token[-1:].isdigit() or token[-1:] in (")", "]") or (token.isalpha() and token.islower()):
            return text[:m.start() + 1]
    return text


def summary_lede(paragraphs: list[str]) -> dict | None:
    """The CRS summary's first sentence, verbatim, and where it came from:
    the first paragraph that is prose (a title line is skipped)."""
    prose = [p for p in paragraphs if p.rstrip().endswith(_SENTENCE_PUNCT)]
    if not prose:
        return None
    return {"text": first_sentence(prose[0]),
            "words": sum(len(p.split()) for p in paragraphs)}


def _status(record: dict, laws: list[dict]) -> dict | None:
    """The lead box: where the bill is, and its path in one dated line."""
    stops = (record.get("journey") or {}).get("stops") or []
    if not stops:
        return None
    by_key = {s["key"]: s for s in stops}
    intro = by_key.get("introduced")
    parts = [f"Introduced {_date(intro['date'])}."] if intro and intro["date"] else []
    passed = [s for s in stops if s["key"].startswith("floor-") and s["status"] in ("done", "current")]
    if passed:
        chambers = [f"the {s['label'].split()[0]} {_date(s['date'])}" for s in passed]
        parts.append(f"Passed {' and '.join(chambers)}.")
    law = by_key.get("law")
    if law and law["status"] == "done":
        number = next((x.get("number") for x in laws if x.get("number")), None)
        days = _days(intro and intro["date"], law["date"])
        named = f"Public Law {number}" if number else "Became law"
        after = ("the day it was introduced" if days == 0 else
                 f"{_plural(days, 'day')} after it was introduced") if days is not None else None
        parts.append(f"Became law {_date(law['date'])}.")
        return {"kicker": f"Became law · {_date(law['date'])}", "tone": "done",
                "headline": f"{named}, {after}" if after else named,
                "dek": " ".join(parts),
                "figure": _stat(days, "days, bill to law") if days is not None else None}
    failed = next((s for s in stops if s["status"] == "failed"), None)
    if failed:
        return {"kicker": f"Failed · {_date(failed['date'])}", "tone": "record",
                "headline": f"Failed on the {failed['label']} {_date(failed['date'])}",
                "dek": " ".join(parts), "figure": None}
    current = next((s for s in stops if s["status"] == "current"), stops[-1])
    # A step is taken once passed; "current" in committee means still in it.
    reached = sum(1 for s in stops if s["status"] in ("done", "skipped")
                  or (s["status"] == "current" and s["key"] != "committee"))
    figure = _stat(reached, f"of {len(stops)} steps taken")
    ahead = next((s for s in stops if s["status"] == "ahead"), None)
    if current["key"] == "committee" and current["status"] == "current":
        headline = f"In committee since {_date(current['date'])}"
    elif current["key"] == "introduced":
        headline = f"Introduced {_date(current['date'])}"
    else:
        headline = f"{current['label']}: {current['detail'] or 'reached'}, {_date(current['date'])}"
    if ahead:
        parts.append(f"Next: {ahead['label']}.")
    return {"kicker": f"Where it is · {current['label']}", "tone": "neutral",
            "headline": headline, "dek": " ".join(parts), "figure": figure}


def _party_line(parties: list[dict]) -> str:
    return " ".join(
        f"{_party(p['party'])} {p['yea']:,} yea, {p['nay']:,} nay." for p in parties
        if p.get("yea") or p.get("nay")
    )


def _passage(record: dict) -> list[dict]:
    """One box per chamber decision, with the roll call's split where
    Civitas holds the roll call."""
    held = {(v["chamber"], v["session"], v["number"]): v for v in record.get("votes") or []}
    out = []
    for p in record.get("passage") or []:
        rc = p.get("rollCall")
        vote = held.get((rc["chamber"], rc["session"], rc["number"])) if rc else None
        box = {"chamber": p["chamber"], "date": p["date"],
               "kicker": f"{_CHAMBER[p['chamber']]} · {_date(p['date'])}",
               "headline": p["headline"], "dek": p["methodMeaning"], "sentence": p["sentence"],
               "text": p["text"], "rollCall": rc, "figure": None}
        if vote:
            tie = vote.get("tieBreaker")
            box["headline"] = f"{p['headline']}, {vote['yeas']:,} to {vote['nays']:,}" + (
                f"; {tie['byWhom']} voted {tie['vote']}" if tie else "")
            box["dek"] = _party_line(vote.get("parties") or []) or None
            box["kicker"] = f"{_CHAMBER[p['chamber']]} roll call {vote['number']} · {_date(p['date'])}"
            total = vote["yeas"] + vote["nays"]
            box["figure"] = {
                "type": "split", "yeas": vote["yeas"], "nays": vote["nays"],
                "yeaShare": round(vote["yeas"] / total, 4) if total else 0,
                "caption": "Tie broken" if tie else (vote.get("resultLabel") or vote["result"]),
            }
        out.append(box)
    return out


def _votes(record: dict) -> dict | None:
    votes = [v for v in record.get("votes") or [] if v.get("kind", "question") == "question"]
    if not votes:
        return None
    failed = sum(1 for v in votes if v.get("rejected"))
    carried = len(votes) - failed
    labels = Counter(v.get("resultLabel") or v.get("result") or "No result recorded" for v in votes)
    return {"headline": f"{_plural(len(votes), 'recorded vote')} on this bill",
            "dek": f"{carried:,} carried and {failed:,} failed.",
            "results": [{"label": k, "count": n} for k, n in labels.most_common()],
            "figure": _stat(len(votes), "roll calls" if len(votes) != 1 else "roll call")}


def _history(record: dict) -> dict | None:
    actions = record.get("actions") or []
    if not actions:
        return None
    first, last = actions[-1]["date"], actions[0]["date"]
    span = (f"on {_date(first)}" if first == last else f"from {_date(first)} to {_date(last)}")
    types = Counter(a.get("type") or "Other" for a in actions)
    by_type = ", ".join(f"{_ACTION_TYPE.get(t, t)} {n:,}" for t, n in types.most_common())
    latest = record.get("latestAction") or {}
    return {"headline": f"{_plural(len(actions), 'action')} {span}",
            "dek": f"By Congress.gov's action type: {by_type}.",
            "latest": ({"date": latest.get("actionDate"), "text": latest["text"]} if latest.get("text") else None),
            "figure": _stat(len(actions), "actions" if len(actions) != 1 else "action")}


def _cost(record: dict) -> dict | None:
    estimates = sorted(c["pubDate"][:10] for c in record.get("cboCostEstimates") or [] if c.get("pubDate"))
    if not estimates:
        return None
    n = len(estimates)
    dek = (f"Published {_date(estimates[0])}." if n == 1 else
           f"From {_date(estimates[0])} to {_date(estimates[-1])}.")
    return {"headline": f"The Congressional Budget Office published {_plural(n, 'cost estimate')}",
            "dek": dek, "figure": _stat(n, "CBO estimates" if n != 1 else "CBO estimate")}


def _text(record: dict) -> dict | None:
    versions = record.get("textVersions") or []
    if not versions:
        return None
    dated = sorted((v for v in versions if v.get("date")), key=lambda v: v["date"])
    latest = dated[-1] if dated else versions[0]
    when = f", {_date(latest['date'])}" if latest.get("date") else ""
    return {"headline": f"{_plural(len(versions), 'text version')} published",
            "dek": f"Latest: {latest['type']}{when}.",
            "figure": _stat(len(versions), "versions" if len(versions) != 1 else "version")}


def _sponsor(record: dict) -> dict:
    co = record.get("cosponsors") or []
    parts = []
    if "cosponsors" not in (record.get("unavailable") or []):
        if co:
            parties = Counter(p.get("party") or "?" for p in co)
            split = ", ".join(f"{n:,} {_party(k, n)}" for k, n in parties.most_common())
            parts.append(f"{_plural(len(co), 'cosponsor')}: {split}.")
        else:
            parts.append("No cosponsors.")
    if record.get("introducedDate"):
        parts.append(f"Introduced {_date(record['introducedDate'])}.")
    if record.get("policyArea"):
        parts.append(f"Policy area: {record['policyArea']}.")
    return {"dek": " ".join(parts) or None}


def _floor(record: dict, label: str) -> dict | None:
    if "floorDebate" not in record:
        return None
    if (record.get("coverage") or {}).get("floor", {}).get("held") is False:
        return {"headline": "Floor speeches not held for this Congress", "figure": None}
    debate = record.get("floorDebate") or []
    n = record.get("floorSpeechCount") or 0
    if not debate:
        return {"headline": f"No floor speech heading names {label}", "figure": None}
    return {"headline": f"{_plural(n, 'floor speech', 'floor speeches')} under "
                        f"{_plural(len(debate), 'Record heading')}",
            "figure": _stat(n, "speeches" if n != 1 else "speech")}


def boxes(record: dict, laws: list[dict] | None = None) -> dict:
    """Every box's template line for a shaped bill record (see the module
    docstring). A part with nothing on record is None, and the page then
    says so in that part's own words."""
    summary = record.get("summary")
    lede = summary_lede(summary["paragraphs"]) if summary else None
    label = record.get("billLabel") or record.get("billId") or "this bill"
    return {
        "status": _status(record, laws or []),
        "summary": ({**lede, "source": ", ".join(x for x in (
                        "Congressional Research Service", summary.get("actionDesc"),
                        _date(summary["actionDate"]) if summary.get("actionDate") else None) if x)}
                    if lede else None),
        "passage": _passage(record),
        "votes": _votes(record),
        "history": _history(record),
        "cost": _cost(record),
        "text": _text(record),
        "sponsor": _sponsor(record),
        "floor": _floor(record, label),
    }
