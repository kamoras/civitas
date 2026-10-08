"""Regenerate app/data/fec_party_codes.json: the FEC's party codes and what
each names, from its published "Party code descriptions" table
(https://www.fec.gov/campaign-finance-data/party-code-descriptions/).

The elections API labels a candidate's party from it: the FEC's candidate
files carry only the code ("TX" is the Taxpayers party, "PG" Pacific
Green), and a code the table doesn't define is one a filer typed, shown as
filed. Rerun when the FEC adds a party.

Run from the repo (network required):
    cd backend && PYTHONPATH=. python3 scripts/fetch_fec_party_codes.py
"""

import datetime
import html
import json
import pathlib
import re
import urllib.request

from app.contact import CONTACT_EMAIL

URL = "https://www.fec.gov/campaign-finance-data/party-code-descriptions/"
OUT = pathlib.Path(__file__).resolve().parents[1] / "app" / "data" / "fec_party_codes.json"


def parse(page: str) -> dict[str, str]:
    """{code: description} from the table's rows."""
    codes: dict[str, str] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) >= 2 and re.fullmatch(r"[A-Z0-9]{1,4}", cells[0]) and cells[1]:
            codes[cells[0]] = " ".join(cells[1].split())
    return codes


def main() -> None:
    req = urllib.request.Request(URL, headers={"User-Agent": f"Civitas ({CONTACT_EMAIL})"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        codes = parse(resp.read().decode("utf-8", errors="replace"))
    if len(codes) < 50:
        raise SystemExit(f"Only {len(codes)} codes parsed; the page layout may have changed")
    OUT.write_text(json.dumps({
        "_source": f"{URL}, read {datetime.date.today().isoformat()} by scripts/fetch_fec_party_codes.py",
        "codes": dict(sorted(codes.items())),
    }, indent=2) + "\n")
    print(f"wrote {OUT} ({len(codes)} codes)")


if __name__ == "__main__":
    main()
