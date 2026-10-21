"""What running Civitas costs, as record boxes for /environmental.

The measured and sourced figures live in `app/data/environmental.json`, each
with its source and date. Everything worked out from them (energy a day and
a year, carbon, the car and phone comparisons, one request's energy) is
computed here, and every box line is a template filled from those fields
(AGENTS.md, "Boxes, not blocks"): the page draws, it doesn't count.
"""

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

_FILE = Path(__file__).parent / "data" / "environmental.json"

HOURS_PER_DAY = 24
DAYS_PER_YEAR = 365
LB_PER_KG = 2.20462262
KIB_PER_GIB = 1024 * 1024


@lru_cache(maxsize=1)
def figures() -> dict:
    return json.loads(_FILE.read_text())


def _day(iso: str) -> str:
    """"Sep 29, 2026"; a month or a year alone as written ("2025-06" → "June 2025")."""
    parts = iso.split("-")
    if len(parts) == 3:
        return date.fromisoformat(iso).strftime("%b %-d, %Y")
    if len(parts) == 2:
        return date(int(parts[0]), int(parts[1]), 1).strftime("%B %Y")
    return iso


def _num(x: float, digits: int = 0) -> str:
    return f"{x:,.{digits}f}"


def _source(*items: dict) -> str:
    return "; ".join(f"{i['source']}, {_day(i['date'])}" for i in items)


def derived(f: dict) -> dict:
    watts = f["assumedWatts"]["watts"]
    wh_day = watts * HOURS_PER_DAY
    kwh_year = wh_day * DAYS_PER_YEAR / 1000
    kg_per_kwh = f["grid"]["lbCo2ePerMWh"] / LB_PER_KG / 1000
    kg_year = kwh_year * kg_per_kwh
    llm = f["llmUse"]
    return {
        "whPerDay": wh_day,
        "kWhPerYear": round(kwh_year, 1),
        "kgCo2ePerKWh": round(kg_per_kwh, 3),
        "kgCo2ePerYear": round(kg_year, 1),
        "carMiles": round(kg_year / f["equivalents"]["kgCo2ePerMile"]),
        "phoneCharges": round(kwh_year / f["equivalents"]["kWhPerPhoneCharge"], -2),
        "llmMinutesPerDay": round(llm["requests"] * llm["meanSeconds"] / 60 * HOURS_PER_DAY / llm["hours"]),
        "requestWh": round(f["boardPower"]["peakWatts"] * llm["meanSeconds"] / 3600, 2),
        "memoryGiB": round(f["hardware"]["memoryKiB"] / KIB_PER_GIB),
    }


def _stat(value: float, text: str, caption: str) -> dict:
    return {"type": "stat", "value": value, "text": text, "caption": caption}


def boxes(f: dict | None = None) -> list[dict]:
    f = f or figures()
    d = derived(f)
    hw, bp, aw, pl, lu, gr, eq, cq = (f[k] for k in (
        "hardware", "boardPower", "assumedWatts", "pipelines", "llmUse", "grid", "equivalents", "cloudQuery"))
    llm, emb = f["models"]["llm"], f["models"]["embedding"]
    return [
        {
            "id": "machine", "kicker": "Hardware", "title": "One Raspberry Pi 5",
            "dek": f"{hw['cores']} {hw['core']} cores and {d['memoryGiB']} GB of memory run the whole site, "
                   "at the developer's home. No cloud hosting.",
            "figure": _stat(d["memoryGiB"], f"{d['memoryGiB']} GB", "of memory"),
            "source": _source(hw),
        },
        {
            "id": "board", "kicker": "Measured", "title": "Power at the board",
            "dek": f"{_num(bp['meanWatts'], 1)} W on average while the nightly pipeline ran, "
                   f"{_num(bp['peakWatts'], 1)} W at peak.",
            "figure": _stat(bp["meanWatts"], f"{_num(bp['meanWatts'], 1)} W", "average, busiest stretch"),
            "source": _source(bp),
        },
        {
            "id": "assumed", "kicker": "Assumed", "title": "What we budget",
            "dek": f"{watts_text(aw['watts'])} for the whole machine, all day. An assumption above "
                   "the measured peak, not a meter reading at the wall.",
            "figure": _stat(aw["watts"], watts_text(aw["watts"]), "whole machine"),
            "source": _source(aw),
        },
        {
            "id": "energy", "kicker": "Worked out", "title": "Energy a day",
            "dek": f"{watts_text(aw['watts'])} × {HOURS_PER_DAY} hours. About {_num(d['kWhPerYear'], 1)} kWh a year, "
                   f"enough to charge a phone about {_num(d['phoneCharges'])} times.",
            "figure": _stat(d["whPerDay"], f"{_num(d['whPerDay'])} Wh", "a day"),
            "source": _source(aw, eq),
        },
        {
            "id": "carbon", "kicker": "Worked out", "title": "Carbon a year",
            "dek": f"{_num(d['kWhPerYear'], 1)} kWh at the U.S. grid average ({_num(d['kgCo2ePerKWh'], 3)} kg per kWh). "
                   f"About what an average car emits in {_num(d['carMiles'])} miles.",
            "figure": _stat(d["kgCo2ePerYear"], f"{_num(d['kgCo2ePerYear'], 1)} kg", "CO₂e a year"),
            "source": _source(gr, eq),
        },
        {
            "id": "pipelines", "kicker": "Workload", "title": "Nightly pipelines",
            "dek": f"Between {pl['minHours']} and {pl['maxHours']} hours a day over the {pl['days']} days "
                   f"to {_day(pl['through'])}. No language-model calls.",
            "figure": _stat(pl["meanHours"], f"{_num(pl['meanHours'], 1)} h", "a day on average"),
            "source": _source(pl),
        },
        {
            "id": "llm", "kicker": "Workload", "title": "The language model",
            "dek": f"{lu['requests']} requests in {lu['hours']} hours, about {lu['meanSeconds']} seconds each, "
                   "finding claims in news articles.",
            "figure": _stat(d["llmMinutesPerDay"], f"{d['llmMinutesPerDay']} min", "of work a day"),
            "source": _source(lu),
        },
        {
            "id": "models", "kicker": "Models", "title": "Model sizes",
            "dek": f"{llm['name']}, {llm['quantisation']}, a {_num(llm['fileBytes'] / 1e6)} MB file. "
                   f"Plus {len(emb)} embedding models of about {_num(max(e['parameters'] for e in emb) / 1e6)} "
                   "million parameters each.",
            "figure": _stat(llm["parameters"], f"{_num(llm['parameters'] / 1e9, 1)}B", "parameters, language model"),
            "source": _source(llm, *emb),
        },
        {
            "id": "request", "kicker": "For scale", "title": "One model request",
            "dek": f"At most, on the board: {_num(bp['peakWatts'], 1)} W for {lu['meanSeconds']} seconds. "
                   f"Epoch AI's estimate for a typical ChatGPT query is {_num(cq['wattHours'], 1)} Wh.",
            "figure": _stat(d["requestWh"], f"{_num(d['requestWh'], 2)} Wh", "upper bound"),
            "source": _source(bp, lu, cq),
        },
    ]


def watts_text(w: float) -> str:
    return f"{_num(w)} W"


def payload() -> dict:
    f = figures()
    return {"reviewed": f["reviewed"], "boxes": boxes(f), "figures": f, "derived": derived(f)}
