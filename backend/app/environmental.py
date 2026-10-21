"""What running Civitas costs, as record boxes for /environmental.

Every figure has a source a person or the site can check, and is read
again rather than typed where the site can read it:

- the machine (model, cores, memory): read from this server's own /proc
  when the page is asked for;
- the models' sizes: read from the models by the pipeline process
  (`app/environmental_sampler.py`) into /data/environmental_runtime.json;
- the pipelines' hours a day: from the five pipelines' run records over
  the last seven whole days;
- what the site can't measure itself, in `app/data/environmental.json`
  with each figure's source and date: the board's power (a dated reading of
  the Pi's power chip, which no container can read; see that file), the
  whole-machine budget (an assumption, labelled), one day's language-model
  log, the EPA grid factor (`data_refresh` alerts when a newer eGRID edition
  is published) and the EPA and Epoch AI comparisons.

Everything worked out from these (energy a day and a year, carbon, the car
and phone comparisons, one request's energy) is computed here, and every
box line is a template filled from those fields (AGENTS.md, "Boxes, not
blocks"): the page draws, it doesn't count. A figure that can't be read is
said to be not read, never shown as zero.
"""

import os
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app import data_refresh
from app.atomic_write import runtime_data_path
from app.file_cache import read_json
from app.models import (
    ElectionPipelineRun,
    HousePipelineRun,
    PipelineRun,
    PipelineStatus,
    StockTradesPipelineRun,
    SupplementaryPipelineRun,
)
from app.time_utils import utcnow

FILE = "environmental.json"
RUNTIME_FILE = "environmental_runtime.json"

HOURS_PER_DAY = 24
DAYS_PER_YEAR = 365
LB_PER_KG = 2.20462262
KIB_PER_GIB = 1024 * 1024
PIPELINE_DAYS = 7
PIPELINE_RUNS = (PipelineRun, SupplementaryPipelineRun, HousePipelineRun, StockTradesPipelineRun,
                 ElectionPipelineRun)


def figures() -> dict:
    return data_refresh.read(FILE)


def _day(iso: str) -> str:
    """"Sep 29, 2026"; a month or a year alone as written ("2025-06" → "June 2025")."""
    parts = iso[:10].split("-")
    if len(parts) == 3:
        return date.fromisoformat(iso[:10]).strftime("%b %-d, %Y")
    if len(parts) == 2:
        return date(int(parts[0]), int(parts[1]), 1).strftime("%B %Y")
    return iso


def _num(x: float, digits: int = 0) -> str:
    return f"{x:,.{digits}f}"


def _source(*items: dict) -> str:
    return "; ".join(f"{i['source']}, {_day(i['date'])}" for i in items)


# --- read by the site itself ------------------------------------------------

def read_machine(proc: str = "/proc") -> dict | None:
    """The board's model (the kernel's "Model" line), its cores and its
    memory, from /proc, which in a container is the host's; None when the
    memory can't be read."""
    try:
        with open(os.path.join(proc, "meminfo")) as f:
            kib = next(int(line.split()[1]) for line in f if line.startswith("MemTotal:"))
    except (OSError, StopIteration, ValueError):
        return None
    model, cores = None, 0
    try:
        with open(os.path.join(proc, "cpuinfo")) as f:
            for line in f:
                key, _, value = line.partition(":")
                if key.strip() == "processor":
                    cores += 1
                elif key.strip() == "Model":
                    model = value.strip()
    except OSError:
        pass
    return {
        "model": model,
        "cores": cores or os.cpu_count() or None,
        "memoryGiB": round(kib / KIB_PER_GIB),
        "source": "This server's own report (/proc/cpuinfo, /proc/meminfo)",
        "date": utcnow().date().isoformat(),
    }


def read_models() -> dict | None:
    """The models' sizes as the pipeline process last read them; None
    before the first reading."""
    try:
        doc = read_json(runtime_data_path(RUNTIME_FILE))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and (doc.get("llm") or doc.get("embedding")) else None


def pipeline_hours(db: Session, now: datetime | None = None) -> dict | None:
    """Hours a day any pipeline was running, over the last seven whole UTC
    days: each day's runs from every pipeline merged (two at once count
    once), a run still going counted to now. None with no runs at all."""
    now = now or utcnow()
    end = datetime.combine(now.date(), datetime.min.time())
    start = end - timedelta(days=PIPELINE_DAYS)
    spans = []
    for model in PIPELINE_RUNS:
        rows = (db.query(model.started_at, model.completed_at, model.elapsed_seconds, model.status)
                .filter(model.started_at < end, model.started_at > start - timedelta(days=1))
                .all())
        for s, c, elapsed, status in rows:
            # A run that ended without a completion time (killed) counts
            # for its recorded elapsed time; one still going, to now.
            c = c or (s + timedelta(seconds=elapsed) if elapsed else None) or (
                now if status == PipelineStatus.RUNNING else None)
            if s and c:
                spans.append((max(s, start), min(c, end)))
    spans = sorted((s, e) for s, e in spans if e > s)
    if not spans:
        return None
    merged: list[list[datetime]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    hours = []
    for i in range(PIPELINE_DAYS):
        d0 = start + timedelta(days=i)
        d1 = d0 + timedelta(days=1)
        secs = sum(max(0.0, (min(e, d1) - max(s, d0)).total_seconds()) for s, e in merged)
        hours.append(secs / 3600)
    return {
        "days": PIPELINE_DAYS,
        "minHours": round(min(hours), 1),
        "maxHours": round(max(hours), 1),
        "meanHours": round(sum(hours) / len(hours), 1),
        "through": (end - timedelta(days=1)).date().isoformat(),
        "source": "The pipelines' run records in the production database",
        "date": end.date().isoformat(),
    }


# --- worked out ---------------------------------------------------------------

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
    }


def _stat(value: float, text: str, caption: str) -> dict:
    return {"type": "stat", "value": value, "text": text, "caption": caption}


def _unread(box_id: str, kicker: str, title: str, what: str) -> dict:
    return {"id": box_id, "kicker": kicker, "title": title, "dek": f"Not read yet: {what}.",
            "figure": None, "source": None}


def _machine_box(hw: dict | None) -> dict:
    if not hw:
        return _unread("machine", "Hardware", "The machine", "this server couldn't read its own memory")
    name = hw["model"] or "One computer"
    return {
        "id": "machine", "kicker": "Hardware", "title": name,
        "dek": f"{hw['cores']} cores and {hw['memoryGiB']} GB of memory run the whole site, "
               "at the developer's home. No cloud hosting.",
        "figure": _stat(hw["memoryGiB"], f"{hw['memoryGiB']} GB", "of memory"),
        "source": _source(hw),
    }


def _models_box(m: dict | None) -> dict:
    llm = (m or {}).get("llm")
    if not llm:
        return _unread("models", "Models", "Model sizes", "the language-model server hasn't answered yet")
    emb = list(((m or {}).get("embedding") or {}).values())
    sources = [{"source": "The llama.cpp server's own model metadata", "date": llm["readAt"]}]
    dek = f"{llm['name']}: {_num(llm['bytes'] / 1e6)} MB of weights."
    if emb:
        dek += (f" Plus {len(emb)} embedding models of about "
                f"{_num(max(e['parameters'] for e in emb) / 1e6)} million parameters each.")
        sources.append({"source": "The embedding models' own files", "date": max(e["readAt"] for e in emb)})
    return {
        "id": "models", "kicker": "Models", "title": "Model sizes", "dek": dek,
        "figure": _stat(llm["parameters"], f"{_num(llm['parameters'] / 1e9, 1)}B", "parameters, language model"),
        "source": _source(*sources),
    }


def _pipelines_box(pl: dict | None) -> dict:
    if not pl:
        return _unread("pipelines", "Workload", "Nightly pipelines", "no pipeline run is on record")
    return {
        "id": "pipelines", "kicker": "Workload", "title": "Nightly pipelines",
        "dek": f"Between {_num(pl['minHours'], 1)} and {_num(pl['maxHours'], 1)} hours a day over the "
               f"{pl['days']} days to {_day(pl['through'])}. No language-model calls.",
        "figure": _stat(pl["meanHours"], f"{_num(pl['meanHours'], 1)} h", "a day on average"),
        "source": _source(pl),
    }


def boxes(f: dict, hw: dict | None, models: dict | None, pl: dict | None) -> list[dict]:
    d = derived(f)
    bp, aw, lu, gr, eq, cq = (f[k] for k in (
        "boardPower", "assumedWatts", "llmUse", "grid", "equivalents", "cloudQuery"))
    watts = f"{_num(aw['watts'])} W"
    return [
        _machine_box(hw),
        {
            "id": "board", "kicker": "Measured once", "title": "Power at the board",
            "dek": f"{_num(bp['meanWatts'], 1)} W on average while the nightly pipeline ran, "
                   f"{_num(bp['peakWatts'], 1)} W at peak.",
            "figure": _stat(bp["meanWatts"], f"{_num(bp['meanWatts'], 1)} W", "average, busiest stretch"),
            "source": _source(bp),
        },
        {
            "id": "assumed", "kicker": "Assumed", "title": "What we budget",
            "dek": f"{watts} for the whole machine, all day. An assumption above "
                   "the measured peak, not a meter reading at the wall.",
            "figure": _stat(aw["watts"], watts, "whole machine"),
            "source": _source(aw),
        },
        {
            "id": "energy", "kicker": "Worked out", "title": "Energy a day",
            "dek": f"{watts} × {HOURS_PER_DAY} hours. About {_num(d['kWhPerYear'], 1)} kWh a year, "
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
        _pipelines_box(pl),
        {
            "id": "llm", "kicker": "Workload", "title": "The language model",
            "dek": f"{lu['requests']} requests in {lu['hours']} hours, about {lu['meanSeconds']} seconds each, "
                   "finding claims in news articles.",
            "figure": _stat(d["llmMinutesPerDay"], f"{d['llmMinutesPerDay']} min", "of work a day"),
            "source": _source(lu),
        },
        _models_box(models),
        {
            "id": "request", "kicker": "For scale", "title": "One model request",
            "dek": f"At most, on the board: {_num(bp['peakWatts'], 1)} W for {lu['meanSeconds']} seconds. "
                   f"Epoch AI's estimate for a typical ChatGPT query is {_num(cq['wattHours'], 1)} Wh.",
            "figure": _stat(d["requestWh"], f"{_num(d['requestWh'], 2)} Wh", "upper bound"),
            "source": _source(bp, lu, cq),
        },
    ]


def payload(db: Session) -> dict:
    f = figures()
    hw, models, pl = read_machine(), read_models(), pipeline_hours(db)
    return {
        "reviewed": f["reviewed"],
        "boxes": boxes(f, hw, models, pl),
        "figures": f,
        "machine": hw,
        "models": models,
        "derived": derived(f),
    }
