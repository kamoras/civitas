"""Expected source failures by host: the daily counters
error_utils.count_source_failure keeps (SourceFailureCount), summed over a
window for the admin's chart. Read path only."""
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import SourceFailureCount
from app.readout import mark
from app.time_utils import utcnow

KINDS = ("walled", "refused", "unavailable", "untrusted")


def run_label(run_kind: str) -> str:
    """A run table's name as the run: "stock_trades_pipeline_runs" is
    "stock trades", and the Senate's, the first, is "pipeline_runs"."""
    name = run_kind.removesuffix("pipeline_runs").strip("_").replace("_", " ")
    return name or "senate"


def by_host(db: Session, days: int = 30) -> dict:
    """Each host's expected failures over the last ``days`` UTC days, most
    first: the total, per kind, the runs and steps that met them, the last
    day, and its chart readout."""
    since = (utcnow().date() - timedelta(days=days - 1)).isoformat()
    hosts: dict[str, dict] = {}
    for row in db.query(SourceFailureCount).filter(SourceFailureCount.date >= since):
        h = hosts.setdefault(row.host, {
            "host": row.host, "total": 0, "byKind": dict.fromkeys(KINDS, 0),
            "steps": set(), "lastDate": row.date,
        })
        h["total"] += row.count
        h["byKind"][row.kind] = h["byKind"].get(row.kind, 0) + row.count
        h["steps"].add(f"{run_label(row.run_kind)} · {row.step}" if row.step else run_label(row.run_kind))
        h["lastDate"] = max(h["lastDate"], row.date)
    out = sorted(hosts.values(), key=lambda h: (-h["total"], h["host"]))
    most = out[0]["total"] if out else 0
    for h in out:
        h["barPct"] = round(100 * h["total"] / most, 1)  # the bar's length, against the most
        h["steps"] = sorted(h["steps"])
        kinds = ", ".join(f"{k} {n:,}" for k, n in h["byKind"].items() if n)
        h["mark"] = mark(
            h["host"], h["host"], f"{h['total']:,} failed step{'s' if h['total'] != 1 else ''}",
            detail=", ".join(h["steps"]), context=f"{kinds}; last {h['lastDate']}",
        )
    return {"days": days, "since": since, "hosts": out, "total": sum(h["total"] for h in out)}
