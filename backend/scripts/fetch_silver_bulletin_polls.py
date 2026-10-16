"""Fetch Silver Bulletin's raw polls into app/data/forecast_polls.json.

The training polls of the election map's fits (app/forecast/
training_polls.py says what is kept and why). Reads the canonical page,
takes its "Rawpolls" attachment (an .xlsx, read with the standard
library), keeps the Senate, House and generic-ballot general rows, and
adds MEDSL's certified Senate margins so the gate can check the file's
stated results against them (app.data_refresh runs the gate; nothing here
decides whether the file is accepted).

A page or attachment that can't be read exits non-zero, naming what
failed: an outage, never an empty file. A page that no longer links the
attachment is the same (the post moved): nothing falls back to a search.

    python3 backend/scripts/fetch_silver_bulletin_polls.py [OUT] [--cache DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fit_forecast_model as fit  # noqa: E402
from app.atomic_write import runtime_data_path  # noqa: E402
from app.contact import BOT_USER_AGENT  # noqa: E402
from app.forecast import training_polls as tp  # noqa: E402
from app.pipeline.fetch.state_candidates_tabular import _xlsx_rows  # noqa: E402
from app.time_utils import utcnow  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "app" / "data"
REGENERATE = "backend/scripts/fetch_silver_bulletin_polls.py"


def get(url: str) -> bytes:
    """One GET, identified by BOT_USER_AGENT and nothing else."""
    req = urllib.request.Request(url, headers={"User-Agent": BOT_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise tp.FetchFailed(f"{url} could not be read ({exc})") from exc


def in_use() -> dict:
    for path in (Path(runtime_data_path(tp.FILE_NAME)), DATA / tp.FILE_NAME):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            continue
    return {}


def certified_senate(cache: Path) -> dict[str, float]:
    """{"2024-PA": R two-party margin} for every contested Senate general
    in MEDSL's newest Senate file."""
    text, _ = fit.fetch_medsl(fit.SENATE_DOI, cache)
    races = fit.senate_races(text)
    return {f"{r['year']}-{r['state']}{'-special' if r['special'] else ''}": r["margin"] for r in races}


def build(cache: Path) -> dict:
    html = get(tp.PAGE_URL).decode("utf-8", "replace")
    found = tp.attachment(html)
    if found is None:
        raise tp.FetchFailed(f"{tp.PAGE_URL} no longer links a {tp.ATTACHMENT_LABEL!r} attachment "
                             "(the post may have moved)")
    label, url = found
    raw = get(url)
    rows = _xlsx_rows(raw)
    if not rows:
        raise tp.FetchFailed(f"{url} is not a readable workbook")
    header = list(rows[0].keys())
    kept = tp.kept_rows(header, rows)
    doc = {
        "_source": tp.SOURCE,
        "_regenerate": REGENERATE,
        "_license": f"Silver Bulletin's terms, as the page states them: \"{tp.terms_sentence(html) or ''}\"",
        "_attribution": tp.ATTRIBUTION,
        "page": tp.PAGE_URL,
        "file": {"label": label, "url": url, "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(rows)},
        "retrieved": utcnow().date().isoformat(),
        "terms": tp.terms_sentence(html),
        "columns": header,
        "kept": f"rows of type {', '.join(tp.KEPT_TYPES)} (Senate and House generals, and the generic ballot "
                "filed as a House general in US); every other row is left out",
        "rowColumns": list(tp.ROW_COLUMNS),
        "rows": kept,
        "certified": certified_senate(cache),
    }
    old = {r["poll_id_nate"]: r for r in tp.as_dicts(in_use())}
    fields = ("polldate", "electiondate", "cand1_pct", "cand2_pct", "location", "type_detail")
    doc["corrections"] = [r["poll_id_nate"] for r in tp.as_dicts(doc) if r["poll_id_nate"] in old
                          and any(old[r["poll_id_nate"]][f] != r[f] for f in fields)]
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", type=Path, default=DATA / tp.FILE_NAME)
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "civitas-forecast")
    args = ap.parse_args(argv)
    args.cache.mkdir(parents=True, exist_ok=True)
    try:
        doc = build(args.cache)
    except tp.FetchFailed as exc:
        print(f"outage: {exc}", file=sys.stderr)
        return 2
    args.out.write_text(json.dumps(doc, separators=(",", ":")) + "\n")
    print(f"{len(doc['rows'])} rows kept of {doc['file']['rows']}; window {tp.window_days(doc)} days; "
          f"{len(doc['corrections'])} corrections")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
