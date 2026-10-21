"""Calibrate site search (app/site_search.py) on known items.

Writes app/data/site_search_calibration.json: the four scoring parameters
(prefix_credit, fuzzy_credit, label_floor, importance_weight), the two
direct-navigation thresholds (direct_floor, direct_margin), and what they
score on queries they were not fitted to.

Known items, built from the index itself (Explore's "known-item retrieval"
discipline, AGENTS.md §3b): each probe is a query a reader looking for one
page would plausibly type, built from that page's own name or identifier,
and the measurement is where the page lands. Probe types:

    name        an entity's full name (member, president, justice, agency,
                committee, state, monitor, nomination's nominee)
    surname     a person's last name alone
    acronym     an agency's short name ("EPA")
    identifier  a bill number in three spellings, a district code in two,
                a nomination's citation, an FR document number, an EO
    typo        a name of seven or more letters with one deterministic
                edit (a dropped, doubled, swapped or replaced letter), never
                the first letter
    page        a site page's label or alias
    deep        "<bill> votes", "<member> donors", "<district> <year>":
                the section of the page the words name
    prefix      the first 60% of a member's or agency's name, scored at 8
                (the autocomplete's list), not at 1
    document    an Explore document's title (with --database)

A query whose words name more than one page equally (two members with one
surname, two pages called "Archive") is ambiguous: ranking it counts as
right when the top result is any of them, and going straight to a page on
it counts as a wrong jump.

Fit and report on different pages: entities split in two by a hash of their
URL. The scoring parameters are chosen on the fit half by coordinate ascent
on mean reciprocal rank (candidates are gathered once per query; only the
scoring is redone). The direct thresholds are then the pair, on the fit
half, with the most right jumps among those whose wrong-jump rate is at or
under WRONG_DIRECT_TARGET overall and WRONG_DIRECT_TYPE_CAP in every probe
type. The report half is measured
once, at the end.

Rerun when the index's shape changes (new kinds, new aliases) or the corpus
grows a lot; it reads the live index, so run it in the pipeline container:

    python scripts/calibrate_site_search.py [--index /data/site_search.db]
        [--database sqlite:////data/civitas.db]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import sqlite3
import statistics
import sys
import time
from dataclasses import asdict, replace

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.site_search import CALIBRATION_PATH, Params, answer, gather, index_kinds, is_direct, score  # noqa: E402
from app.site_search_index import SITE_SEARCH_DB, normalize  # noqa: E402

# Wrong jumps allowed on the fit half: 0.5% of all probes, and none in any
# one type. A type has one to two hundred fit probes, so none of them
# bounds its rate near 3/n (the rule of three, 95%): 1.5-3%. A 1% cap per
# type let six of 385 held-out document titles jump wrong.
WRONG_DIRECT_TARGET = 0.005
WRONG_DIRECT_TYPE_CAP = 0.0
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
GRID = {
    "prefix_credit": [0.6, 0.7, 0.8, 0.9],
    "fuzzy_credit": [0.5, 0.6, 0.7, 0.8, 0.9],
    "label_floor": [0.3, 0.45, 0.6, 0.75, 0.9],
    "importance_weight": [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4],
}
FLOORS = [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.93, 0.95, 1.0]
MARGINS = [0.02, 0.05, 0.08, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4]
PER_TYPE = 400


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:12], 16)


def _typo(word: str) -> str:
    """One deterministic edit past the first letter."""
    n = _h(word)
    i = 1 + n % (len(word) - 1)
    op = (n >> 8) % 4
    if op == 0:
        return word[:i] + word[i + 1:]
    if op == 1:
        return word[:i] + word[i] + word[i:]
    if op == 2 and i < len(word) - 1:
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    repl = "e" if word[i] != "e" else "a"
    return word[:i] + repl + word[i + 1:]


def probes(index: str, db) -> list[dict]:
    conn = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
    rows = conn.execute("SELECT id, kind, label, url, names FROM entity").fetchall()
    keys: dict[int, list[str]] = {}
    for key, eid in conn.execute("SELECT key, entity_id FROM entity_key"):
        keys.setdefault(eid, []).append(key)
    conn.close()

    by_name: dict[str, set[str]] = {}
    for _id, _kind, _label, url, names in rows:
        for n in names.split(" | "):
            by_name.setdefault(n, set()).add(url)
    people = [(label, url) for _id, kind, label, url, _n in rows if kind in ("member", "president", "justice")]
    surnames: dict[str, set[str]] = {}
    for label, url in people:
        toks = [t for t in normalize(label).split() if t not in SUFFIXES]
        if toks:
            surnames.setdefault(toks[-1], set()).add(url)

    out: list[dict] = []

    def add(kind, q, url, same=None, k=1, anchor=None):
        same = same or by_name.get(normalize(q), {url}) | {url}
        out.append({"type": kind, "q": q, "url": url + (f"#{anchor}" if anchor else ""),
                    "ambiguous": len(same) > 1, "accept": sorted(same), "k": k,
                    "half": _h(url) % 2})

    for eid, kind, label, url, names in rows:
        n = normalize(label)
        if kind in ("member", "president", "justice", "agency", "committee", "monitor") or (
                kind == "state" and " members " not in f" {n} "):
            add("name", label, url)
        if kind in ("member", "president", "justice"):
            toks = [t for t in n.split() if t not in SUFFIXES]
            if toks and len(toks[-1]) >= 3:
                add("surname", toks[-1], url, surnames[toks[-1]] | by_name.get(toks[-1], set()))
        if kind == "agency":
            for alias in names.split(" | ")[1:]:
                if 2 <= len(alias) <= 8 and " " not in alias:
                    add("acronym", alias.upper(), url)
        if kind == "page":
            for alias in names.split(" | "):
                add("page", alias, url)
        if kind in ("member", "agency", "committee", "state", "page") and len(n.replace(" ", "")) >= 7:
            words = n.split()
            longest = max(range(len(words)), key=lambda i: len(words[i]))
            if len(words[longest]) >= 5:
                words[longest] = _typo(words[longest])
                add("typo", " ".join(words), url, by_name.get(n, {url}) | {url})
        if kind in ("member", "agency") and len(n) >= 8:
            add("prefix", n[: max(3, int(len(n) * 0.6))], url, {url}, k=8)
        for key in keys.get(eid, []):
            if key.startswith("bill:") and kind == "bill" and "?" not in url:
                bill = key[5:].upper()
                prefix, number = bill.split(".")
                for q in (label, f"{prefix} {number}", f"{prefix.lower()}{number}"):
                    add("identifier", q, url, {url})
                add("deep", f"{label} votes", url, {url + "#votes"}, anchor="votes")
            elif key.startswith("district:"):
                code = key[9:].upper()
                add("identifier", code, url, {url})
            elif key.startswith("race:") and "-sen" not in key:
                code = key[5:].upper()
                add("deep", f"{code} {label.rsplit(' ', 1)[-1]}", url, {url})
            elif key.startswith("pn:") and "-" in key:
                add("identifier", key[3:].upper(), url, {url})
            elif key.startswith(("frdoc:", "eo:")) and kind == "document":
                value = key.split(":", 1)[1]
                add("identifier", value if key.startswith("frdoc:") else f"EO {value}", url, {url})
        if kind == "member":
            add("deep", f"{label} donors", url, {url + "#funding-independence"}, anchor="funding-independence")

    if db is not None:
        # Documents with a comment period (open or closed): the ones a
        # reader looks up by title to comment on. Ambiguous against every
        # document of the same title, comment period or not.
        titles: dict[str, set[str]] = {}
        for did, title in db.execute(text("SELECT id, title FROM explore_documents")):
            titles.setdefault(normalize(title), set()).add(f"/explore/{did}")
        for did, title in db.execute(text(
            "SELECT id, title FROM explore_documents WHERE comment_url IS NOT NULL AND comment_url != '' ORDER BY id"
        )):
            add("document", title, f"/explore/{did}", titles[normalize(title)])

    # Cap each type, deterministically, so one large kind can't drown the rest.
    capped: list[dict] = []
    for t in sorted({p["type"] for p in out}):
        group = sorted((p for p in out if p["type"] == t), key=lambda p: _h(p["q"] + p["url"]))
        capped += group[:PER_TYPE * 2]
    return capped


def _rr(ranked, probe) -> float:
    accept = set(probe["accept"])
    for i, h in enumerate(ranked[:10]):
        if h.url in accept:
            return 1.0 / (i + 1)
    return 0.0


def fit_params(gathered: list[tuple[dict, str, list]]) -> Params:
    p = Params()

    def mrr(params: Params) -> float:
        return statistics.fmean(_rr(score(q, hits, params), probe) for probe, q, hits in gathered)

    best = mrr(p)
    for _round in range(3):
        moved = False
        for name, values in GRID.items():
            for v in values:
                trial = replace(p, **{name: v})
                m = mrr(trial)
                if m > best + 1e-9:
                    best, p, moved = m, trial, True
        if not moved:
            break
    return p


def fit_direct(gathered, p: Params) -> tuple[float, float]:
    """The (floor, margin) with the most right jumps whose wrong-jump rate
    is at or under the target overall and the cap in every probe type: a mix
    that is fine on average can still jump wrong on every other surname."""
    ranked = [(probe, score(q, hits, p)) for probe, q, hits in gathered if probe["k"] == 1]
    best, choice = -1.0, (1.0, 0.4)
    for floor in FLOORS:
        for margin in MARGINS:
            trial = replace(p, direct_floor=floor, direct_margin=margin)
            right = 0
            wrong: dict[str, int] = {}
            count: dict[str, int] = {}
            for probe, r in ranked:
                count[probe["type"]] = count.get(probe["type"], 0) + 1
                if is_direct(r, trial):
                    if not probe["ambiguous"] and r[0].url == probe["url"]:
                        right += 1
                    else:
                        wrong[probe["type"]] = wrong.get(probe["type"], 0) + 1
            ok = (sum(wrong.values()) / len(ranked) <= WRONG_DIRECT_TARGET
                  and all(wrong.get(t, 0) / n <= WRONG_DIRECT_TYPE_CAP for t, n in count.items()))
            if ok and right > best:
                best, choice = right, (floor, margin)
    return choice


def measure(probes_: list[dict], index: str, db, p: Params) -> dict:
    """Each probe through the whole read path (answer(), section words
    included), timed."""
    by_type: dict[str, dict] = {}
    times = []
    for probe in probes_:
        t0 = time.perf_counter()
        a = answer(probe["q"], index, db, p)
        times.append((time.perf_counter() - t0) * 1000)
        accept = set(probe["accept"]) | {probe["url"]}
        top = [h.url for h in a.ranked[: probe["k"]]]
        s = by_type.setdefault(probe["type"], {"n": 0, "hit": 0, "direct_right": 0, "direct_wrong": 0,
                                               "unambiguous": 0})
        s["n"] += 1
        s["hit"] += any(u in accept for u in top)
        if probe["k"] == 1:
            if not probe["ambiguous"]:
                s["unambiguous"] += 1
            if a.direct is not None:
                if not probe["ambiguous"] and a.direct.url == probe["url"]:
                    s["direct_right"] += 1
                else:
                    s["direct_wrong"] += 1
    report = {}
    for t, s in sorted(by_type.items()):
        report[t] = {
            "probes": s["n"],
            f"success_at_{8 if t == 'prefix' else 1}": round(s["hit"] / s["n"], 3),
        }
        if t != "prefix":
            report[t]["direct_hit_rate"] = round(s["direct_right"] / max(s["unambiguous"], 1), 3)
            report[t]["wrong_direct_rate"] = round(s["direct_wrong"] / s["n"], 4)
    n1 = [s for t, s in by_type.items() if t != "prefix"]
    total = sum(s["n"] for s in n1)
    report["all"] = {
        "probes": total,
        "direct_hit_rate": round(sum(s["direct_right"] for s in n1) / max(sum(s["unambiguous"] for s in n1), 1), 3),
        "wrong_direct_rate": round(sum(s["direct_wrong"] for s in n1) / max(total, 1), 4),
    }
    times.sort()
    report["latency_ms"] = {"p50": round(times[len(times) // 2], 1), "p95": round(times[int(len(times) * 0.95)], 1)}
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=SITE_SEARCH_DB)
    ap.add_argument("--database", default=None, help="SQLAlchemy URL of the main database, for Explore titles")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db = Session(create_engine(args.database)) if args.database else None

    all_probes = probes(args.index, db)
    fit = [p for p in all_probes if p["half"] == 0 and p["type"] != "deep"]
    held = [p for p in all_probes if p["half"] == 1]
    gathered = [(probe, *gather(probe["q"], args.index, db)) for probe in fit]
    params = fit_params(gathered)
    floor, margin = fit_direct(gathered, params)
    params = replace(params, direct_floor=floor, direct_margin=margin)
    report = measure(held, args.index, db, params)
    out = {
        "_source": (
            "scripts/calibrate_site_search.py on the live index "
            f"({datetime.date.today().isoformat()}): parameters fitted on half the entities' known-item "
            "probes, measured on the other half."
        ),
        "params": {k: v for k, v in asdict(params).items()},
        "wrong_direct_target": WRONG_DIRECT_TARGET,
        "wrong_direct_type_cap": WRONG_DIRECT_TYPE_CAP,
        "held_out": report,
        "kinds": sorted(index_kinds(args.index) or ()),
    }
    print(json.dumps(out, indent=2))
    if not args.dry_run:
        CALIBRATION_PATH.write_text(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main()
