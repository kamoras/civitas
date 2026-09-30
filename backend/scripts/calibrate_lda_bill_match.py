"""Build the bill-title word table and measure LDA bill matching.

Two jobs, both behind app/pipeline/analyze/lobbying_records.py:

1. Regenerate app/data/bill_title_token_df.json — how many bill titles each
   word appears in, over every bill and resolution of the current and
   previous congress (GovInfo's BILLSTATUS bulk data: every title Congress
   recorded for each measure). title_match_score weights words by this.
   Rerun at the start of each congress, or when titles have grown a lot.

2. Reproduce the calibration behind BILL_TITLE_MATCH_MIN: pull 2025 LDA
   filings for a fixed list of large clients, find every bill number in
   their activity descriptions, and score each against the titles that
   number has in the current congress and in the previous one. Prints the
   score histogram, the spelling counts, the in-between band in full (the
   cases that decide the threshold) and a random sample above it, for
   reading by hand.

Run from backend/ (network required; no API key needed):
    python scripts/calibrate_lda_bill_match.py [--congress 119] [--skip-df] [--skip-calibration]
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import pathlib
import random
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.contact import BOT_USER_AGENT  # noqa: E402
from app.pipeline.analyze import lobbying_records as lr  # noqa: E402
from app.pipeline.fetch.congress import BILL_TYPES, congress_first_year, congress_for_year  # noqa: E402

UA = {"User-Agent": BOT_USER_AGENT}
BULK = "https://www.govinfo.gov/bulkdata/BILLSTATUS/{congress}/{kind}/BILLSTATUS-{congress}-{kind}.zip"
LDA = "https://lda.gov/api/v1/filings/"
OUTPUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "bill_title_token_df.json"

# Large, frequent LDA clients across sectors. The list only has to yield a
# few thousand bill references in varied filer styles; it is not a sample
# of anything else.
CLIENTS = (
    "PFIZER", "BOEING", "EXXON MOBIL", "GOOGLE", "AMERICAN HOSPITAL ASSOCIATION",
    "NATIONAL ASSOCIATION OF REALTORS", "LOCKHEED MARTIN", "COMCAST", "AT&T", "VERIZON",
    "CHEVRON", "MICROSOFT", "AMAZON", "META PLATFORMS", "APPLE", "UNITEDHEALTH",
    "BLUE CROSS BLUE SHIELD ASSOCIATION", "AMERICAN MEDICAL ASSOCIATION",
    "PHARMACEUTICAL RESEARCH AND MANUFACTURERS OF AMERICA", "U.S. CHAMBER OF COMMERCE",
    "NATIONAL ASSOCIATION OF HOME BUILDERS", "AMERICAN BANKERS ASSOCIATION", "GOLDMAN SACHS",
    "JPMORGAN", "GENERAL MOTORS", "FORD MOTOR", "RAYTHEON", "GENERAL DYNAMICS",
    "NORTHROP GRUMMAN", "KOCH", "AMERICAN FARM BUREAU", "NATIONAL RIFLE ASSOCIATION",
    "PLANNED PARENTHOOD", "AMERICAN PETROLEUM INSTITUTE", "EDISON ELECTRIC INSTITUTE",
    "NATIONAL CABLE", "AMERICAN INVESTMENT COUNCIL", "CVS", "WALMART",
    "NATIONAL EDUCATION ASSOCIATION", "AFL-CIO", "TEAMSTERS", "AMGEN", "JOHNSON & JOHNSON",
    "ELI LILLY", "MERCK", "NATIONAL BEER WHOLESALERS", "CREDIT UNION NATIONAL ASSOCIATION",
)


CACHE_DIR: pathlib.Path | None = None


def _get(url: str, timeout: int = 600) -> bytes:
    """GET, through --cache-dir when given (reruns then skip ~12 minutes of
    rate-limited LDA requests and bulk downloads)."""
    cached = CACHE_DIR / hashlib.sha256(url.encode()).hexdigest() if CACHE_DIR else None
    if cached and cached.exists():
        return cached.read_bytes()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    if cached:
        cached.write_bytes(body)
    return body


def bill_titles(congress: int) -> tuple[dict[str, list[str]], dict[str, str]]:
    """({site bill id: every title recorded for it}, {site bill id: its
    display title}) for one congress."""
    out: dict[str, list[str]] = {}
    display: dict[str, str] = {}
    for kind in BILL_TYPES:
        url = BULK.format(congress=congress, kind=kind)
        print(f"  {url}", file=sys.stderr)
        with zipfile.ZipFile(io.BytesIO(_get(url))) as zf:
            for name in zf.namelist():
                if not name.endswith(".xml"):
                    continue
                bill = ET.fromstring(zf.read(name)).find("bill")
                if bill is None:
                    continue
                kind_code = (bill.findtext("type") or bill.findtext("billType") or "").upper()
                titles = {bill.findtext("title") or ""}
                titles |= {it.findtext("title") or "" for it in bill.iter("item") if it.find("titleType") is not None}
                key = f"{kind_code}.{bill.findtext('number')}"
                out[key] = sorted(t for t in titles if t)
                if bill.findtext("title"):
                    display[key] = bill.findtext("title")
    return out, display


def build_df(titles_by_congress: dict[int, dict[str, list[str]]]) -> dict:
    counts: collections.Counter[str] = collections.Counter()
    documents = 0
    for titles in titles_by_congress.values():
        for ts in titles.values():
            for t in ts:
                documents += 1
                counts.update(lr._tokens(t, years=True))
    congresses = sorted(titles_by_congress)
    return {
        "_source": (
            f"GovInfo BILLSTATUS bulk data, congresses {congresses[0]}-{congresses[-1]}, every "
            f"recorded title of every bill and resolution; generated {date.today().isoformat()} by "
            "backend/scripts/calibrate_lda_bill_match.py"
        ),
        "_note": "Words in only one title are omitted and read as 1 (lobbying_records._idf).",
        "documents": documents,
        "df": {w: c for w, c in sorted(counts.items()) if c >= 2},
    }


def lda_descriptions(year: int) -> list[str]:
    descs: list[str] = []
    for client in CLIENTS:
        q = urllib.parse.urlencode({"client_name": client, "filing_year": year, "page_size": 25})
        url = f"{LDA}?{q}"
        was_cached = bool(CACHE_DIR and (CACHE_DIR / hashlib.sha256(url.encode()).hexdigest()).exists())
        try:
            data = json.loads(_get(url, timeout=60))
        except Exception as exc:  # the anonymous limit is ~15/minute
            print(f"  LDA {client}: {exc}", file=sys.stderr)
            continue

        for filing in data.get("results", []):
            for act in filing.get("lobbying_activities") or []:
                descs.append(act.get("description") or "")
        if not was_cached:
            time.sleep(5)
    return descs


def calibrate(current: dict[str, list[str]], display: dict[str, str], previous: dict[str, list[str]], year: int) -> None:
    """Every (bill, before, after) mention in the sample, judged the way
    lobbied_bills_for judges one: names_bill against the current congress's
    titles, with the rival pool built from display titles only (all that
    Congress.gov's bill list returns in production) and the previous
    congress's same-numbered bill as a further rival."""
    descs = lda_descriptions(year)
    spelled = collections.Counter()
    mentions: set[tuple[str, str, str]] = set()
    for text in descs:
        for m in lr._MEASURE_RE.finditer(text):
            spelled["no period" if "." not in m.group(0) else "with periods"] += 1
        mentions.update(lr.bill_mentions(text))
    print(f"{len(descs)} activity descriptions, {sum(spelled.values())} bill references: {dict(spelled)}")

    pool = lr.TitlePool({b: [t] for b, t in display.items()})
    rows = []
    for bid, before, after in sorted(mentions):
        if bid not in current:
            continue
        own = max(lr.title_match_score(before, current[bid]), lr.title_match_score(after, current[bid]))
        prev = previous.get(bid) or []
        prev_fit = max(lr.title_match_score(before, prev), lr.title_match_score(after, prev)) if prev else 0.0
        kept = lr.names_bill(before, after, current[bid], bid, pool, prev)
        # How far the best rival beats this bill on the side where this
        # bill fits best — the quantity RIVAL_TOLERANCE is set against.
        side = after if lr.title_match_score(after, current[bid]) >= lr.title_match_score(before, current[bid]) else before
        gap = pool.best_rival_score(side, bid) - lr.title_match_score(side, current[bid])
        rows.append((kept, own, prev_fit, bid, before, after, gap))
    kept = [r for r in rows if r[0]]
    above = [r for r in rows if r[1] >= lr.BILL_TITLE_MATCH_MIN]
    print(f"{len(rows)} distinct mentions of current-congress numbers")
    print("own-title fit:", sorted(collections.Counter(round(r[1], 1) for r in rows).items()))
    print(f"fit >= {lr.BILL_TITLE_MATCH_MIN}: {len(above)}; kept after the rival checks: {len(kept)}")

    def show(r):
        best = min(current[r[3]], key=len)
        print(f"  {r[1]:.2f} (prev {r[2]:.2f}) {r[3]} [{best[:60]}]")
        print(f"      before: {' '.join(r[4].split())[-110:]}")
        print(f"      after:  {' '.join(r[5].split())[:110]}")

    fit = [r for r in rows if r[1] >= lr.BILL_TITLE_MATCH_MIN]
    print("\nRival margin (best rival minus own fit) where own fit clears the threshold:")
    print("  ", sorted(collections.Counter(round(max(r[6], 0), 2) if r[6] <= 0.1 else round(r[6], 1) for r in fit).items()))
    print("\nEvery case with a rival ahead by 0 < margin <= 0.3:")
    for r in sorted((r for r in fit if 0 < r[6] <= 0.3), key=lambda r: r[6]):
        print(f"  margin {r[6]:.2f}", end="")
        show(r)
    print("\nKept with own fit under 0.9 (the cases a threshold alone decides least well):")
    for r in sorted((r for r in kept if r[1] < 0.9), key=lambda r: r[1]):
        show(r)
    print("\nKept although the previous congress's bill also fits (>= 0.5):")
    for r in (r for r in kept if r[2] >= 0.5):
        show(r)
    print("\nFit the threshold but rejected by a rival (sample of 40):")
    random.seed(7)
    rejected = [r for r in above if not r[0]]
    for r in random.sample(rejected, min(40, len(rejected))):
        show(r)
    print("\nRandom sample of kept:")
    for r in random.sample(kept, min(30, len(kept))):
        show(r)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--congress", type=int, default=congress_for_year(date.today().year))
    ap.add_argument("--skip-df", action="store_true")
    ap.add_argument("--skip-calibration", action="store_true")
    ap.add_argument("--cache-dir", type=pathlib.Path)
    args = ap.parse_args()
    global CACHE_DIR
    if args.cache_dir:
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        CACHE_DIR = args.cache_dir

    print(f"Downloading titles for congresses {args.congress - 1} and {args.congress}...", file=sys.stderr)
    fetched = {c: bill_titles(c) for c in (args.congress - 1, args.congress)}
    titles = {c: all_titles for c, (all_titles, _) in fetched.items()}
    if not args.skip_df:
        table = build_df(titles)
        OUTPUT.write_text(json.dumps(table, indent=0, sort_keys=False) + "\n")
        print(f"Wrote {OUTPUT} ({table['documents']} titles, {len(table['df'])} words)", file=sys.stderr)
        lr._document_frequencies.cache_clear()
    if not args.skip_calibration:
        first_year = congress_first_year(args.congress)
        calibrate(titles[args.congress], fetched[args.congress][1], titles[args.congress - 1], first_year)


if __name__ == "__main__":
    main()
