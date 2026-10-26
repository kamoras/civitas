"""The U.S. grid's emission rate for /environmental, from the EPA's newest
eGRID edition.

Reads the EPA's eGRID summary-data page, takes the summary tables workbook
it links (the newest edition: the page carries one), and finds in its
subregion table the "U.S." row's total output emission rate, CO2e, in
lb/MWh, by the table's own headers. Writes the copy of environmental.json
in use with its `grid` replaced: the rate, the edition (from the workbook's
title), the revision (from the file's name) and the month the file was
posted (from its address). Everything else in the file is kept as it is.

Run by app/data_refresh.py monthly:
    python scripts/fetch_egrid.py IN_USE OUT
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.contact import USER_AGENT  # noqa: E402
from app.pipeline.fetch.state_candidates_tabular import xlsx_sheet_files, xlsx_sheet_rows  # noqa: E402

SUMMARY_PAGE = "https://www.epa.gov/egrid/summary-data"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def workbook_url(page: str, base: str = SUMMARY_PAGE) -> str:
    """The summary tables workbook the page links."""
    links = re.findall(r'href="([^"]*summary_tables[^"]*\.xlsx)"', page, flags=re.I)
    if not links:
        raise SystemExit(f"{base} links no summary tables workbook")
    return urllib.parse.urljoin(base, links[0])


def _norm(cell) -> str:
    return str(cell or "").replace("₂", "2").replace(" ", "").lower()


def _number(cell: str) -> float | None:
    try:
        return float(cell)
    except (TypeError, ValueError):
        return None


def us_rate(sheets: dict[str, list[list[str]]]) -> tuple[float, int]:
    """(the U.S. total output CO2e rate in lb/MWh, the edition's year) from
    the workbook's sheets ({name: rows}), read with the standard library."""
    edition = None
    for rows in sheets.values():
        for row in rows[:3]:
            for cell in row:
                found = re.search(r"eGRID ?(?:Summary Tables )?(\d{4})", cell or "")
                if found and edition is None:
                    edition = int(found[1])
    for rows in sheets.values():
        col = None
        for cells in rows:
            if col is None:
                # The first CO2e column is the total output rate's (the
                # non-baseload rates follow it under their own heading).
                col = next((i for i, c in enumerate(cells) if _norm(c) == "co2e"), None)
                continue
            first = next((c for c in cells if c not in (None, "")), None)
            value = _number(cells[col]) if col < len(cells) else None
            if str(first).strip() == "U.S." and value is not None:
                if edition is None:
                    raise SystemExit("the workbook names no eGRID edition")
                return value, edition
    raise SystemExit("no U.S. total output CO2e rate in the workbook")


def workbook_sheets(body: bytes) -> dict[str, list[list[str]]]:
    files = xlsx_sheet_files(body)
    if not files:
        raise SystemExit("the workbook could not be read")
    return {name: xlsx_sheet_rows(body, f) or [] for name, f in files.items()}


def main() -> None:
    in_use, out = sys.argv[1], sys.argv[2]
    url = workbook_url(_get(SUMMARY_PAGE).decode("utf-8", "replace"))
    body = _get(url)
    if not body.startswith(b"PK"):
        raise SystemExit(f"{url} did not return a workbook")
    rate, year = us_rate(workbook_sheets(body))
    rev = re.search(r"rev(\d+)", url, flags=re.I)
    posted = re.search(r"/(\d{4}-\d{2})/", url)
    doc = json.loads(pathlib.Path(in_use).read_text())
    doc["grid"] = {
        "lbCo2ePerMWh": round(rate, 1),
        "edition": f"eGRID{year}",
        "source": f"U.S. EPA eGRID{year}" + (f" (rev. {rev[1]})" if rev else "") + ", U.S. total output rate",
        "date": posted[1] if posted else doc.get("grid", {}).get("date"),
    }
    doc.pop("_refreshed", None)
    pathlib.Path(out).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    print(f"eGRID{year}: {rate:.1f} lb CO2e/MWh from {url}")


if __name__ == "__main__":
    main()
