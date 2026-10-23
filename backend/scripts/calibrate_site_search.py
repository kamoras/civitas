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
right when the top result is any of them. A jump is right only when it lands
on the probe's own page, and wrong anywhere else. Each of an ambiguous
query's pages has its own probe with the same words (both members are
probed by the surname), so a jump on it is right for one and wrong for the
other, and the zero-wrong-jumps rule below still refuses to jump on it.

Until 2026-10-23 every jump on an ambiguous probe counted as wrong, even one
landing on the probe's own page. Congressional Record speeches with bare
titles ("Illinois", "ICE", "NOMINATIONS") then made a state's, an agency's
and a site page's names ambiguous, though no probe looks for those speeches
(only documents with a comment period are probed by title): on the
2026-10-22 index all six "wrong" jumps on the fit half landed on the
probe's own page, no floor and margin met the rule, and a refit turned off
direct navigation (held-out jumps 0.927 to 0.334, #1248).

Fit and report on different pages: entities split in two by a hash of their
URL. The scoring parameters are chosen on the fit half by coordinate ascent
on mean reciprocal rank (candidates are gathered once per query; only the
scoring is redone). The direct thresholds are then the pair, on the fit
half, with the most right jumps among those whose wrong-jump rate is at or
under WRONG_DIRECT_TARGET overall and WRONG_DIRECT_TYPE_CAP in every probe
type. The report half is measured
once, at the end.

With --in-use (the pipeline's refresh, app/data_refresh.py), the copy in use
is measured on the same held-out probes and the paired comparison written
as `_comparison`: per probe, the fit's two objectives summed, the reciprocal
rank of its page (a jump counting as rank 1 when it lands on the page and as
a miss when it doesn't) plus 1 for a right jump; per type and as the macro
mean over types. The refresh installs the refit only when it wins by more
than one standard error and no type loses more than one, and never with a
wrong jump on the fit half.

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

from app.site_search import (  # noqa: E402
    CALIBRATION_PATH, Params, _candidates, _connect, answer, gather, index_kinds, is_direct, params as fitted_params, score,
)
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
# Candidate-pool sizes tried, smallest first, against a pool far larger than
# any query's candidates (REFERENCE): the smallest that changes no fit-half
# probe's answer rank is kept. The grid only sets where the search looks;
# the rule (no answer lost) decides.
POOLS = [10, 20, 40, 80, 120, 160, 240, 320, 480, 640]
ENOUGH = [5, 10, 20, 40, 80, 160, 320]
REFERENCE = 5000
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


def fit_params(gathered: list[tuple[dict, str, list]], start: Params | None = None) -> Params:
    p = start or Params()

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


def _answer_ranks(fit: list[dict], index: str, p: Params) -> list[int | None]:
    """Each probe's answer rank (0-based, top 10) from its entity
    candidates alone under p's pool and enough, listed at any match."""
    open_floor = replace(p, list_floor=0.0)
    conn = _connect(index)
    ranks = []
    try:
        for probe in fit:
            q = normalize(probe["q"])
            hits, _ = _candidates(conn, probe["q"], q, p) if q else ({}, set())
            ranked = score(q, list(hits.values()), open_floor)
            accept = set(probe["accept"]) | {probe["url"]}
            ranks.append(next((i for i, h in enumerate(ranked[:10]) if h.url in accept), None))
    finally:
        conn.close()
    return ranks


def fit_pool(fit: list[dict], index: str, p: Params) -> tuple[int, int]:
    """(pool, enough): each the smallest on its grid that changes no fit
    probe's answer rank against REFERENCE (pool first, with enough at
    REFERENCE, then enough at the chosen pool)."""
    ref = _answer_ranks(fit, index, replace(p, pool=REFERENCE, enough=REFERENCE))
    pool = next((n for n in POOLS if _answer_ranks(fit, index, replace(p, pool=n, enough=REFERENCE)) == ref), REFERENCE)
    enough = next((n for n in ENOUGH if _answer_ranks(fit, index, replace(p, pool=pool, enough=n)) == ref), REFERENCE)
    return pool, enough


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
                    if r[0].url == probe["url"]:
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
        s = by_type.setdefault(probe["type"], {"n": 0, "hit": 0, "direct_right": 0, "direct_wrong": 0})
        s["n"] += 1
        s["hit"] += any(u in accept for u in top)
        if probe["k"] == 1:
            if a.direct is not None:
                if a.direct.url == probe["url"]:
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
            report[t]["direct_hit_rate"] = round(s["direct_right"] / max(s["n"], 1), 3)
            report[t]["wrong_direct_rate"] = round(s["direct_wrong"] / s["n"], 4)
    n1 = [s for t, s in by_type.items() if t != "prefix"]
    total = sum(s["n"] for s in n1)
    report["all"] = {
        "probes": total,
        "direct_hit_rate": round(sum(s["direct_right"] for s in n1) / max(total, 1), 3),
        "wrong_direct_rate": round(sum(s["direct_wrong"] for s in n1) / max(total, 1), 4),
    }
    times.sort()
    report["latency_ms"] = {"p50": round(times[len(times) // 2], 1), "p95": round(times[int(len(times) * 0.95)], 1)}
    return report


def utility(probe: dict, a) -> float:
    """One probe's share of the fit's two objectives: the reciprocal rank of
    its page in the top 10 (a jump is the whole answer: rank 1 when it lands
    on the probe's page, a miss when it doesn't), plus 1 for a right jump."""
    if a.direct is not None and probe["k"] == 1:
        return 2.0 if a.direct.url == probe["url"] else 0.0
    accept = set(probe["accept"]) | {probe["url"]}
    return next((1.0 / (i + 1) for i, h in enumerate(a.ranked[:10]) if h.url in accept), 0.0)


def _se(xs: list[float]) -> float:
    return statistics.stdev(xs) / len(xs) ** 0.5 if len(xs) > 1 else 0.0


def paired_comparison(diffs: dict[str, list[float]], metric: str) -> dict:
    """The candidate against the copy in use from per-probe differences
    (candidate minus in use) by type: the macro mean over types and its
    standard error (types independent), and each type's own."""
    types = sorted(diffs)
    if not types:
        return {"metric": metric, "delta": 0.0, "se": 0.0, "subgroups": {}}
    return {
        "metric": metric,
        "delta": round(statistics.fmean(statistics.fmean(diffs[t]) for t in types), 6),
        "se": round(sum(_se(diffs[t]) ** 2 for t in types) ** 0.5 / len(types), 6),
        "subgroups": {t: {"probes": len(diffs[t]), "delta": round(statistics.fmean(diffs[t]), 6),
                          "se": round(_se(diffs[t]), 6)} for t in types},
    }


def compare(probes_: list[dict], index: str, db, candidate: Params, in_use: Params) -> dict:
    diffs: dict[str, list[float]] = {}
    for probe in probes_:
        diffs.setdefault(probe["type"], []).append(
            utility(probe, answer(probe["q"], index, db, candidate)) - utility(probe, answer(probe["q"], index, db, in_use)))
    return paired_comparison(diffs, "held-out reciprocal rank plus right jumps, macro over probe types")


def wrong_jumps(probes_: list[dict], index: str, db, p: Params) -> list[str]:
    """The k=1 probes whose query jumps anywhere but the probe's page."""
    out = []
    for probe in probes_:
        if probe["k"] == 1:
            a = answer(probe["q"], index, db, p)
            if a.direct is not None and a.direct.url != probe["url"]:
                out.append(f"{probe['type']} {probe['q']!r} to {a.direct.url}")
    return out


def _params_of(path: str | None) -> Params:
    """The parameters of a calibration file (the copy in use), or the code's
    defaults when it holds none."""
    fitted = {}
    if path:
        try:
            fitted = json.loads(pathlib.Path(path).read_text()).get("params") or {}
        except (OSError, ValueError, AttributeError):
            fitted = {}
    return Params(**{k: float(v) for k, v in fitted.items() if k in Params.__dataclass_fields__})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=SITE_SEARCH_DB)
    ap.add_argument("--database", default=None, help="SQLAlchemy URL of the main database, for Explore titles")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--candidates-only", action="store_true",
                    help="fit pool and enough only, keeping the ranking parameters in use")
    ap.add_argument("--in-use", default=None, help="the calibration in use, to measure the refit against")
    ap.add_argument("--output", default=None, help="where to write (default: the bundled file)")
    args = ap.parse_args()
    db = Session(create_engine(args.database)) if args.database else None

    all_probes = probes(args.index, db)
    fit = [p for p in all_probes if p["half"] == 0 and p["type"] != "deep"]
    held = [p for p in all_probes if p["half"] == 1]
    current = _params_of(args.in_use) if args.in_use else fitted_params()
    pool, enough = fit_pool(fit, args.index, current)
    if args.candidates_only:
        params = replace(current, pool=pool, enough=enough)
    else:
        start = replace(Params(), pool=pool, enough=enough)
        gathered = [(probe, *gather(probe["q"], args.index, db, start)) for probe in fit]
        params = replace(fit_params(gathered, start), pool=pool, enough=enough)
        floor, margin = fit_direct(gathered, params)
        params = replace(params, direct_floor=floor, direct_margin=margin)
    report = measure(held, args.index, db, params)
    out = {
        "_source": (
            "scripts/calibrate_site_search.py on the live index "
            f"({datetime.date.today().isoformat()}): parameters fitted on half the entities' known-item "
            "probes, measured on the other half. pool and enough: the smallest on their grids that change no "
            "fit probe's answer rank against a 5,000-candidate pool; list_floor is not fitted: "
            "known-item probes say whether an answer is listed, never whether the rest of a list is junk."
            + (" Ranking parameters kept from the calibration in use (--candidates-only)." if args.candidates_only
               else "")
        ),
        "params": {k: v for k, v in asdict(params).items()},
        "wrong_direct_target": WRONG_DIRECT_TARGET,
        "wrong_direct_type_cap": WRONG_DIRECT_TYPE_CAP,
        "held_out": report,
        "kinds": sorted(index_kinds(args.index) or ()),
    }
    if args.in_use:
        comparison = compare(held, args.index, db, params, current)
        wrong = wrong_jumps(fit, args.index, db, params)
        comparison["fit_half_wrong_jumps"] = len(wrong)
        if wrong:
            comparison["refused"] = [f"the refit jumps wrong on {len(wrong)} fit-half probes (allowed: none), "
                                     f"e.g. {wrong[0]}"]
        out["_comparison"] = comparison
    print(json.dumps(out, indent=2))
    if not args.dry_run:
        pathlib.Path(args.output or CALIBRATION_PATH).write_text(json.dumps(out, indent=2) + "\n")


if __name__ == "__main__":
    main()
