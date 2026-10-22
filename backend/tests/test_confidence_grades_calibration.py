"""scripts/calibrate_confidence_grades.py: split-half reliability of party-line
break rates by record length."""

import importlib.util
import random
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "calibrate_confidence_grades", Path(__file__).resolve().parents[1] / "scripts" / "calibrate_confidence_grades.py")
cal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cal)


def _roll_call(i, yea_party, breakers, bill=None, question="On Passage"):
    """A roll call `yea_party`'s members carried and the other party's
    opposed, with `breakers` voting the other way."""
    positions = []
    for party in ("R", "D"):
        for k in range(10):
            member = f"{party}{k}"
            yea = (party == yea_party) != (member in breakers)
            positions.append((member, party, "Yea" if yea else "Nay"))
    return {"id": i, "chamber": "senate", "question": question, "bill_id": bill, "positions": positions}


def test_breaks_are_counted_once_per_measure_and_never_on_housekeeping():
    rcs = [
        _roll_call(1, "R", {"R0"}, bill="S.1"),
        _roll_call(2, "R", {"R0"}, bill="S.1"),  # the same bill again: counts once
        _roll_call(3, "D", set(), question="On the Motion to Table"),  # housekeeping
    ]
    records = cal.break_records(rcs)
    assert records["R0"] == [1] and records["R1"] == [0] and records["D0"] == [0]


def test_reliability_rises_with_record_length_and_finds_the_bars():
    rng = random.Random(3)
    # Members with distinct true break rates: longer records tell them apart.
    rates = {f"m{i}": i / 100 for i in range(40)}
    records = {m: [int(rng.random() < p) for _ in range(400)] for m, p in rates.items()}
    curve = cal.reliability_curve(records, draws=5)
    ns = sorted(curve)
    assert curve[ns[-1]] >= cal.FULL_RELIABILITY > curve[ns[0]]
    partial = cal.first_reaching(curve, cal.PARTIAL_RELIABILITY)
    assert partial is not None and partial <= cal.first_reaching(curve, cal.FULL_RELIABILITY)


def test_too_few_members_measures_nothing():
    assert cal.reliability_curve({f"m{i}": [0, 1] * 50 for i in range(5)}) == {}
