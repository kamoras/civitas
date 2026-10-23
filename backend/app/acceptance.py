"""When a refit may replace the fit in use (AGENTS.md 3a).

A refit that changes a method's structure or a threshold is installed only
when, on its script's own held-out split, it beats the copy in use by more
than one standard error of the paired difference, and no subgroup the
script reports loses more than one standard error: the one-standard-error
rule (Hastie, Tibshirani & Friedman 2009), the rule the repo's model choices
already use. Anything short of that keeps the copy in use, with the
comparison recorded; never an alert.

Shared by app/data_refresh.py (a DataFile's `accept`) and the scripts that
decide inside one run between a refit and what they would otherwise write
(scripts/fit_forecast_model.py's priors).
"""

from __future__ import annotations


# The script measures both on the same probes and writes the comparison
# into its candidate:
#
#   "_comparison": {"metric": "...", "delta": candidate - in use,
#                   "se": standard error of the paired difference,
#                   "subgroups": {name: {"delta": ..., "se": ...}},
#                   "refused": ["a reason the script's own rule refuses it"]}
#
# or {"unchanged": true} when the refit's parameters are the copy in use's
# (newer measurements by the same parameters), which installs.
#
# The copy in use reaches the script as "{in_use}" in its argv.
def held_out_beats_in_use(new: dict, old: dict) -> str | None:
    """None when the candidate's comparison clears the rule above, else why
    the copy in use is kept."""
    c = new.get("_comparison")
    if isinstance(c, dict) and c.get("unchanged"):
        return None  # the refit reproduces the method's parameters: only its measurements are new
    if not isinstance(c, dict) or not {"delta", "se"} <= set(c):
        return "the refit reports no held-out comparison with the copy in use"
    if c.get("refused"):
        return "; ".join(str(r) for r in c["refused"])
    delta, se = float(c["delta"]), float(c["se"])
    if not delta > se:
        return f"refit within noise of the copy in use: {c.get('metric', 'held-out')} \u0394={delta:+.4f}, SE={se:.4f}"
    losers = [f"{g} \u0394={float(v['delta']):+.4f}, SE={float(v['se']):.4f}"
              for g, v in sorted((c.get("subgroups") or {}).items()) if float(v["delta"]) < -float(v["se"])]
    if losers:
        return (f"refit beats the copy in use overall (\u0394={delta:+.4f}, SE={se:.4f}) but loses more than one "
                f"standard error on " + "; ".join(losers))
    return None
