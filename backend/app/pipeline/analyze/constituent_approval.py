"""Senators' approval among their state's other-party voters and
independents, a Constituent Alignment component since v6.29.

Read from the Cooperative Election Study (app/data/ces_member_approval.json,
scripts/fetch_ces_approval.py): each respondent rates their own senators.
Only the other party's and independents' ratings are used. Constituents'
approval responds to how their member actually votes, and most among those
two groups; the member's own party rates mostly by party (Ansolabehere &
Kuriwaki 2022, AJPS 66(1); Ansolabehere & Jones 2010, AJPS 54(3)). Each
group's rating is compared with the typical senator of the same party (the
survey file's empirical-Bayes prior), in log-odds, and corrected for the
state's lean (independents in a safe state lean toward its majority:
r = +0.29 before the correction), then the two are averaged.

Measured before adoption (2026-10-07,
docs/methodology/member-score/v6.29.md):
  - It repeats: the same senator's figure in the 2022 and 2024 waves,
    different respondents, correlates 0.55 to 0.59 per group.
  - It predicts how far a senator runs ahead of their party's presidential
    vote, beyond the other two parts: +0.77 points of vote share per
    standard deviation (95% CI +0.22 to +1.52; 45 senators, each part
    measured over the Congress before the 2022 or 2024 election, approval
    from the wave fielded with it), against +1.27 for position congruence.
  - It overlaps the existing score little (r = 0.12 with Constituent
    Alignment).
The House is not scored on it: a House member's figure rests on a few
dozen respondents, and the same member's 2022 and 2024 figures correlate
0.09 to 0.24, so most of an individual reading is sampling noise.
"""

import math
import statistics

from app.services.constituent_survey import _entry_for, _survey
from app.time_utils import utcnow

# Respondent groups read, by the member's party: the other party and
# independents ("I" in the survey file).
_GROUPS = {"D": ("R", "I"), "R": ("D", "I")}

_cache: dict = {}


def _logit(p: float) -> float:
    p = min(max(p, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


def _deviations(member: dict, priors: dict) -> list[float] | None:
    """Log-odds of each group's (shrunk) approval minus the typical
    same-party senator's, or None without both."""
    out = []
    for group in _GROUPS.get(member["member_party"], ()):
        cell = member["by_party"].get(group)
        prior = priors.get(f"senate/{member['member_party']}/{group}")
        if not cell or cell.get("shrunk") is None or not prior:
            return None
        out.append(_logit(cell["shrunk"]) - _logit(prior["mu"]))
    return out or None


def _own_lean(state: str, party: str, state_pvi: dict[str, int]) -> float | None:
    lean = state_pvi.get(state)
    return None if lean is None else (lean if party == "R" else -lean)


def _key(member: dict) -> tuple:
    return (member["state"], member["name"], member["member_party"])


def _population(state_pvi: dict[str, int]) -> dict:
    """Every surveyed senator's index, the state-lean fit per (party,
    group) it is corrected by, and the index's standard deviation, measured
    from the survey file (cached per file)."""
    data = _survey()
    key = (data.get("survey"), len(data.get("members") or []), id(state_pvi))
    if _cache.get("key") == key:
        return _cache["value"]
    priors = data.get("priors") or {}
    rows = []
    for m in data.get("members") or []:
        if m["chamber"] != "senate" or m["member_party"] not in _GROUPS:
            continue
        dev, lean = _deviations(m, priors), _own_lean(m["state"], m["member_party"], state_pvi)
        if dev is not None and lean is not None:
            rows.append((m, dev, lean))
    fits = {}
    for party in _GROUPS:
        group_rows = [(dev, lean) for m, dev, lean in rows if m["member_party"] == party]
        for g in range(2):
            xs = [lean for _, lean in group_rows]
            ys = [dev[g] for dev, _ in group_rows]
            if len(xs) < 3 or len(set(xs)) < 2:
                fits[(party, g)] = (statistics.mean(ys) if ys else 0.0, 0.0)
                continue
            mx, my = statistics.mean(xs), statistics.mean(ys)
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
            fits[(party, g)] = (my - slope * mx, slope)
    index = {
        _key(m): statistics.mean(dev[g] - (fits[(m["member_party"], g)][0] + fits[(m["member_party"], g)][1] * lean)
                               for g in range(2))
        for m, dev, lean in rows
    }
    value = {"fits": fits, "index": index, "sd": statistics.stdev(index.values()) if len(index) > 2 else None}
    _cache.update(key=key, value=value)
    return value


def senate_approval(
    state: str, name: str, party: str | None, years_in_office: int, state_pvi: dict[str, int],
) -> dict | None:
    """{"index", "z", "groups": [(group, approve, typical)], "survey",
    "fielded"} for a sitting senator the survey rated, or None: not a
    senator of a major party, not rated (in office after it was fielded),
    or no usable cells. Only a senator already serving when the survey was
    fielded can have a reading (constituent_survey's rule)."""
    data = _survey()
    fielded_year = data.get("fielded_year")
    if party not in _GROUPS or not fielded_year or years_in_office < utcnow().year - fielded_year:
        return None
    entry = _entry_for("senate", state, name, None, party)
    if entry is None or entry["member_party"] != party:
        return None
    population = _population(state_pvi)
    value = population["index"].get(_key(entry))
    if value is None or not population["sd"]:
        return None
    priors = data.get("priors") or {}
    groups = [
        (g, entry["by_party"][g]["shrunk"], priors[f"senate/{party}/{g}"]["mu"])
        for g in _GROUPS[party]
    ]
    return {
        "index": value,
        "z": value / population["sd"],
        "groups": groups,
        "survey": data.get("survey", ""),
        "fielded": data.get("fielded", ""),
    }
