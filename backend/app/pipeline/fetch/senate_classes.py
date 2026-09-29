"""The Senate's three classes, read from the Senate's own member list.

senate.gov/general/contact_information/senators_cfm.xml lists every sitting
senator with their <class> ("Class I", "Class II", "Class III"). Which states
hold a seat in which class is what decides the regular Senate races of a
cycle (election_calendar.seats_up_for_year), and the union of the three is
the set of states — the jurisdictions with Senate seats — every elections
surface filters on. Reading it rather than typing it means a new state, or
any change to the class assignments, is picked up the night the Senate's
list shows it.

A vacant seat drops its senator from the list until someone is sworn in,
and a state never leaves a class, so each refresh keeps every state already
on file (the union of the stored sets and the list's). The election
pipeline refreshes it at the start of each run; a failed read keeps what is
stored. The bundled app/data/senate_classes.json (written by
scripts/fetch_senate_classes.py) covers a fresh volume's first run.
"""

import json
import logging
import pathlib

import httpx
from lxml import etree

from app.atomic_write import write_text_atomic
from app.pipeline.fetch.http_utils import DEFAULT_FETCH_TIMEOUT_S
from app.time_utils import utcnow

logger = logging.getLogger(__name__)

SENATORS_URL = "https://www.senate.gov/general/contact_information/senators_cfm.xml"
_PERSISTENT_PATH = "/data/senate_classes.json"

_ROMAN = {"I": 1, "II": 2, "III": 3}


def parse_senate_classes(xml: bytes) -> dict[int, set[str]]:
    """{class number: {state codes}} from the Senate's member list; {} when
    it can't be read."""
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError:
        return {}
    classes: dict[int, set[str]] = {}
    for member in root.iterfind("member"):
        label = (member.findtext("class") or "").strip()
        state = (member.findtext("state") or "").strip()
        number = _ROMAN.get(label.removeprefix("Class ").strip())
        if number and len(state) == 2:
            classes.setdefault(number, set()).add(state)
    return classes


def gate(classes: dict[int, set[str]]) -> list[str]:
    """Structural checks from the Constitution's own shape, never a state
    list: three classes, none empty, and no state in all three (a state has
    two seats, in two different classes)."""
    failures = []
    if set(classes) != {1, 2, 3} or not all(classes.values()):
        failures.append(f"expected three non-empty classes, got {sorted(classes)}")
    in_all = set.intersection(*classes.values()) if len(classes) == 3 else set()
    if in_all:
        failures.append(f"states in all three classes: {sorted(in_all)}")
    return failures


def _stored(path: pathlib.Path) -> dict[int, set[str]]:
    try:
        raw = json.loads(path.read_text())["classes"]
        return {int(k): set(v) for k, v in raw.items()}
    except Exception:
        return {}


def write_classes(classes: dict[int, set[str]], path: pathlib.Path) -> None:
    write_text_atomic(path, json.dumps(
        {
            "_source": (
                "senate.gov senators_cfm.xml (each sitting senator's <class>), "
                "refreshed by app/pipeline/fetch/senate_classes.py; a state "
                "already on file is kept while its seat is vacant"
            ),
            "_as_of": utcnow().date().isoformat(),
            "classes": {str(k): sorted(v) for k, v in sorted(classes.items())},
        },
        indent=1,
    ) + "\n")


async def refresh_senate_classes(client: httpx.AsyncClient, path: str | None = None) -> bool:
    """Read the Senate's list and store its classes, merged with those on
    file. False, keeping what is stored, when the list can't be read or
    fails its gates. Never raises."""
    target = pathlib.Path(path or _PERSISTENT_PATH)
    try:
        resp = await client.get(SENATORS_URL, timeout=DEFAULT_FETCH_TIMEOUT_S)
        resp.raise_for_status()
        live = parse_senate_classes(resp.content)
        failures = gate(live)
        if failures:
            for f in failures:
                logger.warning("Senate classes not refreshed: %s", f)
            return False
        stored = _stored(target)
        merged = {n: live.get(n, set()) | stored.get(n, set()) for n in (1, 2, 3)}
        write_classes(merged, target)
        from app import election_calendar

        election_calendar.reset_senate_classes()
        logger.info("Senate classes refreshed: %s", {n: len(s) for n, s in merged.items()})
        return True
    except Exception:
        logger.warning("Senate classes refresh failed — keeping stored data", exc_info=True)
        return False
