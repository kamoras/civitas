"""How long the stock-trades run's time-boxed steps may take — the
annual-holdings phases and the re-read of stored trades — and what the holdings
phases are.

Kept apart from the pipelines so the operator watchdogs that need these
numbers (ops_alerts.stock_trades_overrun_budget) don't import the whole
disclosure-fetch chain — Playwright, pdfplumber, lxml — just to read them.
"""

from datetime import timedelta

# Progress-tracker steps for the phases, appended to the stock-trades
# run's own (stock_pipeline.STOCK_PIPELINE_STEPS).
HOLDINGS_STEPS = [
    ("house_holdings",  "fetch", "Ingest House annual disclosures (holdings)"),
    ("senate_holdings", "fetch", "Ingest Senate annual disclosures (holdings)"),
    ("president_holdings", "fetch", "Ingest the president's annual disclosure (holdings)"),
]

# Wall-clock budgets for each holdings phase, in its three steps:
#
# - PREP_BUDGET: the House index download, or the Senate terms and search.
#   A step that outlasts it fails the phase.
# - FETCH_BUDGET: report fetching, from when it starts. Checked before
#   every fetch, and a download still in flight at the deadline is cut
#   off; a parse already running finishes (a CPU-bound thread can't be
#   stopped, and its work is kept). Measured 2026-09: a first House run
#   reads ~430 reports (~9 min in a dev container, network-bound at the
#   Clerk's 1 req/s; slower on the Pi's CPU), the Senate ~100 (~1 min after
#   a ~3 min browser search). Past the budget the remaining members wait for
#   the next run, so a first run or a PARSER_VERSION bump spreads over a few
#   nights.
# - PROBE_BUDGET: the outage probes at the end
#   (holdings_pipeline._SourceHealth). One retried request against a
#   hanging host alone takes ~3 minutes.
PREP_BUDGET = timedelta(minutes=6)
FETCH_BUDGET = timedelta(minutes=8)
PROBE_BUDGET = timedelta(minutes=2)
# Longest a phase can run, give or take one parse — what the stock-trades
# run's overrun alarm allows for (ops_alerts.stock_trades_overrun_budget).
PHASE_CEILING = PREP_BUDGET + FETCH_BUDGET + PROBE_BUDGET

# How long a night may spend re-reading stored trades an older PTR parser
# read, across all three sources (stock_pipeline._reread_trades). The rest
# wait for the next night.
PTR_REREAD_BUDGET = timedelta(minutes=10)
