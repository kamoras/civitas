"""Learn how organization-name words are cased from running federal text.

The FEC publishes names in capitals ("UCLA", "MCDONNELL", "AFL-CIO"), and
the donor list shows them with only each word's first letter capitalized.
That is wrong for the words written some other way: an acronym ("Ucla",
"Cuny", "Uaw") or an interior capital ("Mcdonnell"). How a word is written
is a fact of usage, so it is read from usage: the Federal Register's daily
issues (GovInfo bulk XML, no key needed), mixed-case prose that names
universities, unions, companies and agencies every day.

For each word, the forms it takes in mid-sentence prose are counted (a
heading or a line set in capitals says nothing about how the word is
written, nor does a word that opens a sentence). A word whose dominant form
is all capitals or carries an interior capital is written out to
app/data/name_casing.json with that form; every other word keeps first-
letter capitalization, so the table only ever replaces a guess with an
observed form. normalize_finance._clean_donor_name reads it.

Run from backend/ (network required):
    python scripts/build_name_casing.py [--year 2025] [--days 120]
Rerun to refresh (new names enter the Register every day).
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import pathlib
import re
import sys
import time
import urllib.request
from lxml import etree

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.contact import BOT_USER_AGENT  # noqa: E402

OUTPUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "name_casing.json"
BULK = "https://www.govinfo.gov/bulkdata/FR/{y}/{m:02d}/FR-{y}-{m:02d}-{d:02d}.xml"
# A word enters the table when it was seen at least this often in mid-
# sentence prose and this share of those times all in capitals or with an
# interior capital. Chosen 2026-10-09 on 348 capitalized organization names
# (a random 300 of the FEC committee file's sponsors and the lobbying
# registry's clients, and 48 stored donors), every word whose casing any
# setting changed judged by hand: at 2 and 0.6 the table fixes 58 word
# occurrences ("UCLA", "IBEW", "UnitedHealth", "McDonnell", "PAC") and
# breaks 6 ("(Aka", "Co" for Colorado, "SYSCO", "INTL"); at 5 and 0.6, 55
# and 6; a share of 0.7 or 0.8 loses "AFL-CIO", "MacKenzie" and "FF" for
# one fewer break.
CASING_MIN_COUNT = 2
CASING_MIN_SHARE = 0.6

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z]*")
_ACRONYM_PLURAL_RE = re.compile(r"[A-Z]{2,}s")
_SENTENCE_START_RE = re.compile(r"(?:^|[.!?:]\s+)\W*$")


def form_of(word: str) -> str:
    if len(word) >= 2 and word.isupper():
        return "upper"
    if word.islower():
        return "lower"
    if word[0].isupper() and word[1:].islower():
        return "title"
    return "mixed"


def count_forms(text: str, counts: dict[str, collections.Counter], surfaces: dict[str, collections.Counter]) -> None:
    for line in text.splitlines():
        letters = [c for c in line if c.isalpha()]
        if len(letters) < 40 or sum(c.islower() for c in letters) < 0.6 * len(letters):
            continue  # a heading, a table row, a line set in capitals
        for m in _WORD_RE.finditer(line):
            if _SENTENCE_START_RE.search(line[:m.start()]):
                continue  # a sentence's first word is capitalized whatever it is
            word = m.group(0)
            if _ACRONYM_PLURAL_RE.fullmatch(word):
                continue  # "AAAs", "PACs": an acronym's plural, not the word's own form
            key = word.lower()
            form = form_of(word)
            counts[key][form] += 1
            if form in ("upper", "mixed"):
                surfaces[key][word] += 1


def issue_text(xml: bytes) -> str:
    root = etree.fromstring(xml, parser=etree.XMLParser(recover=True, huge_tree=True))
    return "\n".join(" ".join(p.itertext()) for p in root.iter("P", "FP"))


def fetch(day: datetime.date, cache: pathlib.Path) -> bytes | None:
    path = cache / f"{day.isoformat()}.xml"
    if path.exists():
        return path.read_bytes() or None
    url = BULK.format(y=day.year, m=day.month, d=day.day)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT}), timeout=120) as r:
            body = r.read()
    except Exception:
        body = b""  # no issue that day (weekend, holiday)
    cache.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    time.sleep(0.5)
    return body or None


def build(year: int, days: int, cache: pathlib.Path, min_count: int = CASING_MIN_COUNT, min_share: float = CASING_MIN_SHARE) -> dict[str, str]:
    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    surfaces: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    day, read = datetime.date(year, 1, 2), 0
    while read < days and day.year == year:
        if day.weekday() < 5 and (body := fetch(day, cache)):
            count_forms(issue_text(body), counts, surfaces)
            read += 1
        day += datetime.timedelta(days=1)
    table = {}
    for key, c in counts.items():
        total = sum(c.values())
        marked = c["upper"] + c["mixed"]
        if total >= min_count and marked >= min_share * total:
            table[key] = surfaces[key].most_common(1)[0][0]
    return table, read


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--cache", type=pathlib.Path, default=pathlib.Path("fr_bulk_cache"),
                    help="where downloaded issues are kept between runs")
    ap.add_argument("--min-count", type=int, default=CASING_MIN_COUNT)
    ap.add_argument("--min-share", type=float, default=CASING_MIN_SHARE)
    ap.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    args = ap.parse_args()
    table, read = build(args.year, args.days, args.cache, args.min_count, args.min_share)
    args.output.write_text(json.dumps({
        "_source": (f"Federal Register daily issues, GovInfo bulk XML, the first {read} issues of {args.year} "
                    f"(scripts/build_name_casing.py, generated {datetime.date.today().isoformat()}). Words whose "
                    "mid-sentence form is all capitals or has an interior capital, with that form."),
        "forms": dict(sorted(table.items())),
    }, indent=0, sort_keys=False) + "\n")
    print(f"{len(table)} words from {read} issues -> {args.output}")


if __name__ == "__main__":
    main()
