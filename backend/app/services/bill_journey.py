"""A bill's path through Congress, read from its official actions.

Three things for the bill page, all from Congress.gov's action list and a
checked-in glossary (app/data/bill_action_glossary.json), none of it
model-written:

- `fold_actions`: Congress.gov lists many actions twice, once from the
  chamber's own system and once from the Library of Congress ("Signed by
  President." under E30000 and again under 36000; "Passed/agreed to in
  House: On motion to suspend ..." beside the House's "On motion to suspend
  ..."). Twins on the same date are folded into one, keeping the chamber's
  wording, every code and every roll call. Each folded action carries the
  glossary's plain meaning for its code, where the glossary has one.
- `passage`: how each chamber decided the bill. A roll call where the
  action records one; otherwise the method the chamber's own formula
  names ("by Unanimous Consent", "by voice vote"). Those formulas are
  fixed parliamentary wording, read as a data format (AGENTS.md principle
  1's documented-convention case), not a classification.
- `journey`: the stops a bill of its type passes (introduced, committee,
  each chamber's floor, resolving differences, the President, law), each
  dated from the action codes, with the time between them.
"""

import json
from datetime import date
from functools import cache
from pathlib import Path

_GLOSSARY = Path(__file__).resolve().parent.parent / "data" / "bill_action_glossary.json"

# The Library of Congress's source-system code: its actions restate a
# chamber's under a label ("Passed/agreed to in House: <the House's text>").
_LOC_SOURCE = 9
# A restated text shorter than this is not matched as a twin (a law
# number's "119-122." after "Became Public Law No:" must not fold anything).
_MIN_TWIN_CHARS = 20

# How a chamber decided, from its own fixed formula in the action text,
# most specific first. Lowercased substrings.
METHOD_FORMULAS = (
    ("by unanimous consent", "unanimousConsent"),
    ("by voice vote", "voiceVote"),
    ("by yea-nay vote", "rollCall"),
    ("by the yeas and nays", "rollCall"),
    ("by recorded vote", "rollCall"),
    ("record vote number", "rollCall"),
    ("without objection", "withoutObjection"),
)

# Library of Congress codes for the steps the journey dates.
_INTRODUCED = {"1000": "house", "Intro-H": "house", "1025": "house",
               "10000": "senate", "Intro-S": "senate", "10025": "senate"}
_REPORTED = {"5000": "house", "H12200": "house", "H12100": "house", "1010": "house",
             "5500": "house", "H12300": "house", "14000": "senate", "14500": "senate"}
_DISCHARGED = {"5500", "H12300", "14500"}
_REFERRED_HOUSE = "H11100"
_PASSED = {"8000": "house", "17000": "senate"}
_FAILED = {"9000": "house", "18000": "senate"}
_DIFFERENCES = {"19500": "house", "20500": "senate"}
_PRESENTED = {"28000", "E20000"}
_LAW = {"E40000", "41000", "36000"}
# The House's suspension vote: two thirds of those voting.
_SUSPENSION = {"H37300"}

_CHAMBER = {"house": "House", "senate": "Senate"}
_OTHER = {"house": "senate", "senate": "house"}


@cache
def glossary() -> dict:
    return json.loads(_GLOSSARY.read_text())


def _norm(text: str | None) -> str:
    return " ".join((text or "").split())


def _restated(action: dict) -> str | None:
    """The chamber text a Library of Congress action restates after its
    label, or None for any other action."""
    if (action.get("sourceSystem") or {}).get("code") != _LOC_SOURCE:
        return None
    text = _norm(action.get("text"))
    _, sep, rest = text.partition(": ")
    rest = rest.strip()
    return rest if sep and len(rest) >= _MIN_TWIN_CHARS else None


def _twin(a: dict, kept: dict) -> str | None:
    """How `a` twins `kept` on the same date: "same" text, "a" restates
    kept, "kept" restates a; None when they are different actions."""
    ta, tk = a["text"], kept["text"]
    if ta == tk:
        return "same"
    ra, rk = a["_restated"], kept["_restated"]
    if ra and tk.startswith(ra.rstrip(". ")):
        return "a"
    if rk and ta.startswith(rk.rstrip(". ")):
        return "kept"
    return None


def _term(codes: list[str]) -> dict | None:
    for entry in glossary()["terms"]:
        if any(c in codes for c in entry["codes"]):
            return entry
    return None


def fold_actions(raw: list[dict], roll_calls) -> list[dict]:
    """Congress.gov's actions (newest first, as it lists them) with same-day
    twins folded: {date, text, type, codes, rollCalls, term, meaning}.
    `roll_calls(action)` gives an action's roll-call links."""
    kept: list[dict] = []
    for a in raw:
        item = {
            "date": a.get("actionDate"), "text": _norm(a.get("text")), "type": a.get("type"),
            "codes": [a["actionCode"]] if a.get("actionCode") else [],
            "rollCalls": roll_calls(a), "_restated": _restated(a),
        }
        for k in kept:
            if k["date"] != item["date"]:
                continue
            how = _twin(item, k)
            if how is None:
                continue
            if how == "kept":
                # The chamber's own wording replaces the restatement.
                k["text"], k["type"], k["_restated"] = item["text"], item["type"], None
            k["codes"] += [c for c in item["codes"] if c not in k["codes"]]
            seen = {(r["chamber"], r["number"], r["session"]) for r in k["rollCalls"]}
            k["rollCalls"] += [r for r in item["rollCalls"] if (r["chamber"], r["number"], r["session"]) not in seen]
            break
        else:
            kept.append(item)
    for k in kept:
        del k["_restated"]
        entry = _term(k["codes"])
        k["term"] = entry["term"] if entry else None
        k["meaning"] = entry["meaning"] if entry else None
    return kept


def _method(action: dict) -> str | None:
    if action["rollCalls"]:
        return "rollCall"
    text = action["text"].lower()
    for formula, method in METHOD_FORMULAS:
        if formula in text:
            return method
    return None


def long_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


_METHOD_PHRASE = {
    "unanimousConsent": ("by unanimous consent", "no roll call is taken for that"),
    "withoutObjection": ("without objection", "no roll call is taken for that"),
    "voiceVote": ("by voice vote", "no names are recorded for that"),
    "rollCall": ("by a recorded vote", None),
}


def _sentence(verb: str, action: dict, method: str | None, two_thirds: bool) -> str:
    phrase, aside = _METHOD_PHRASE.get(method, (None, None))
    text = f"{verb} {phrase}" if phrase else verb
    text += f" on {long_date(action['date'])}"
    if two_thirds:
        text += ", under suspension of the rules, which needs two thirds"
    if method == "rollCall" and action["rollCalls"]:
        text += f" (roll call {action['rollCalls'][0]['number']})"
    return f"{text}; {aside}." if aside else f"{text}."


def passage(actions: list[dict]) -> list[dict]:
    """How each chamber decided the bill, oldest first: {chamber, kind
    ("passed" | "failed" | "agreed"), date, method, twoThirds, methodTerm
    and methodMeaning (the glossary's line on the method), sentence, text
    (the action, verbatim), rollCall}."""
    out = []
    for a in reversed(actions):
        codes = set(a["codes"])
        for table, kind in ((_PASSED, "passed"), (_FAILED, "failed"), (_DIFFERENCES, "agreed")):
            chamber = next((table[c] for c in codes if c in table), None)
            if chamber:
                break
        else:
            continue
        method = _method(a)
        two_thirds = bool(codes & _SUSPENSION)
        name = _CHAMBER[chamber]
        verb = {"passed": f"Passed the {name}", "failed": f"Failed in the {name}",
                "agreed": f"The {name} agreed to the {_CHAMBER[_OTHER[chamber]]}'s version"}[kind]
        explained = glossary()["methods"].get(method) or {}
        out.append({
            "chamber": chamber, "kind": kind, "date": a["date"], "method": method, "twoThirds": two_thirds,
            "methodTerm": explained.get("term"), "methodMeaning": explained.get("meaning"),
            "sentence": _sentence(verb, a, method, two_thirds), "text": a["text"],
            "rollCall": a["rollCalls"][0] if a["rollCalls"] else None,
        })
    return out


def _gap(days: int) -> str:
    if days <= 0:
        return "the same day"
    if days == 1:
        return "the next day"
    if days < 60:
        return f"{days} days later"
    months = round(days / 30.44)
    if months < 24:
        return f"{months} months later"
    return f"{round(days / 365.25)} years later"


def _first(actions: list[dict], match) -> dict | None:
    """The earliest action whose codes satisfy `match` (actions are newest first)."""
    return next((a for a in reversed(actions) if match(set(a["codes"]))), None)


def journey(actions: list[dict], bill_type: str) -> dict | None:
    """The stops a bill of this type passes, in order: {stops: [{key,
    label, status ("done" | "current" | "skipped" | "failed" | "ahead"),
    date, detail, after}]}. `bill_type` is Congress.gov's type path
    ("s", "hr", "hres", ...), whose first letter names the chamber it
    started in. None for a bill with no actions on record."""
    if not actions:
        return None
    # A committee's original measure has no introduction action: it is
    # introduced by being reported, its earliest action.
    intro = _first(actions, lambda c: bool(c & _INTRODUCED.keys())) or actions[-1]
    origin = "house" if bill_type.startswith("h") else "senate"
    one_chamber = bill_type in ("hres", "sres")
    to_president = bill_type in ("hr", "s", "hjres", "sjres")
    stops: list[dict] = [{"key": "introduced", "label": f"Introduced in the {_CHAMBER[origin]}",
                          "status": "done", "date": intro["date"], "detail": None}]

    passed = {ch: _first(actions, lambda c, ch=ch: any(_PASSED.get(x) == ch for x in c)) for ch in _CHAMBER}
    failed = {ch: _first(actions, lambda c, ch=ch: any(_FAILED.get(x) == ch for x in c)) for ch in _CHAMBER}
    reported = _first(actions, lambda c: any(_REPORTED.get(x) == origin for x in c))
    if reported:
        discharged = bool(set(reported["codes"]) & _DISCHARGED)
        stops.append({"key": "committee", "label": "Committee", "status": "done", "date": reported["date"],
                      "detail": "Taken out of committee" if discharged else "Reported by the committee"})
    elif passed[origin]:
        stops.append({"key": "committee", "label": "Committee", "status": "skipped", "date": None,
                      "detail": "Went to the floor without a committee report"})
    else:
        referred = _first(actions, lambda c: _REFERRED_HOUSE in c) if origin == "house" else intro
        stops.append({"key": "committee", "label": "Committee", "status": "current",
                      "date": (referred or intro)["date"], "detail": "In committee since"})

    for chamber in [origin] if one_chamber else [origin, _OTHER[origin]]:
        floor = passed[chamber] or failed[chamber]
        status = "done" if passed[chamber] else "failed" if failed[chamber] else "ahead"
        stops.append({"key": f"floor-{chamber}", "label": f"{_CHAMBER[chamber]} floor", "status": status,
                      "date": floor["date"] if floor else None,
                      "detail": ("Passed" if passed[chamber] else "Failed") if floor else None})

    differences = [a for a in actions if set(a["codes"]) & _DIFFERENCES.keys()]
    if differences and not one_chamber:
        stops.append({"key": "differences", "label": "Same text in both", "status": "done",
                      "date": differences[0]["date"], "detail": "Differences resolved"})
    if to_president:
        presented = _first(actions, lambda c: bool(c & _PRESENTED))
        law = _first(actions, lambda c: bool(c & _LAW))
        stops.append({"key": "president", "label": "President", "status": "done" if presented else "ahead",
                      "date": presented["date"] if presented else None,
                      "detail": "Presented" if presented else None})
        stops.append({"key": "law", "label": "Law", "status": "done" if law else "ahead",
                      "date": law["date"] if law else None, "detail": "Became law" if law else None})

    # The furthest step taken is the current one (unless the bill is still
    # in committee, which is current already); the time since the stop
    # before it is what a reader sees as dwell time.
    if not any(s["status"] == "current" for s in stops):
        last = max(i for i, s in enumerate(stops) if s["status"] in ("done", "failed"))
        if stops[last]["status"] == "done":
            stops[last]["status"] = "current" if stops[last]["key"] != "law" else "done"
    previous = None
    for s in stops:
        s["after"] = None
        if not s["date"]:
            continue
        # A bill still in committee is dated from its referral, which
        # starts the wait rather than ending one.
        if previous and not (s["key"] == "committee" and s["status"] == "current"):
            s["after"] = _gap((date.fromisoformat(s["date"]) - date.fromisoformat(previous)).days)
        previous = s["date"]
    return {"stops": stops}
