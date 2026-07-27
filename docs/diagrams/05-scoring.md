# Scoring

Pinned to `ALGORITHM_VERSION = v6.12`. Weights live in
`backend/app/config_definitions.py` and component splits in
`analyze/score_calculator.py` — **those are authoritative; if this page
disagrees with them, this page is wrong.**

No LLM input enters any score. Every formula is deterministic and auditable.
Missing data defaults to a neutral 50 rather than a penalty.

## Members of Congress

Identical framework for all 100 senators and 435 representatives, with
chamber-specific calibration only where the chambers' real baselines genuinely
differ.

```mermaid
flowchart LR
    subgraph FI["Funding Independence — 33%"]
        FI1["PAC dependency<br/>share × closeness to legal cap<br/>chamber-specific multiplier"]
        FI2["Small-donor share<br/>&lt;$200 unitemized, state-relative"]
        FI3["Top-donor concentration<br/>top 10 of external pool<br/>v6.12: anchors 0.15→100, 0.40→0"]
        FI4["Source breadth<br/>folded in from Funding Diversity, v6.5"]
        FI5["Industry concentration<br/>inverse HHI, folded in v6.5"]
    end

    subgraph CA["Constituent Alignment — 33%"]
        CA1["Seat-relative vote alignment — 70%<br/>break rate vs Cook PVI expectation<br/>below-expected loyalty floors at neutral (v6.6)"]
        CA2["Position congruence — 30%, v6.11<br/>DW-NOMINATE vs seat-conditional<br/>per-party expectation"]
        CA3["Fallback when no ideal points:<br/>100% vote alignment<br/>+ v6.7 cosponsorship discount"]
    end

    subgraph LE["Legislative Effectiveness — 34%"]
        LE1["Bill significance &amp; advancement — 60%<br/>cumulative stage credit, 5× substantive<br/>vs chamber + majority-status baseline"]
        LE2["Legislative leadership — 25%<br/>cosponsorship PageRank<br/>tenure-confidence-scaled"]
        LE3["Bipartisan coalition attraction — 15%<br/>v6.11, moved from Constituent Alignment<br/>receive-only cross-party share"]
    end

    FI --> OVERALL["<b>Representation Score</b><br/>0.33 FI + 0.33 CA + 0.34 LE"]
    CA --> OVERALL
    LE --> OVERALL

    OVERALL --> SNAP[("ScoreSnapshot<br/>one row per member per run")]

    UNW["<i>Computed, stored, displayed —<br/>excluded from the weighted overall</i><br/><br/>Promise Persistence (unweighted since v6.0)<br/>Funding Diversity (folded into FI, v6.5)"]
```

### Why two dimensions are unweighted

**Promise Persistence** was removed from the weighted overall in v6.0 after a
live measurement across all 100 senators found *zero* reached even "medium"
evidence confidence — mean 0.3 evaluable promises, 76% with none at all. Real
campaign promises are generic platform language that embedding-based matching
against specific vote text structurally can't bridge. Four fix attempts are
documented in `policy_alignment.compute_promise_vote_alignment`'s docstring.

**Funding Diversity** was folded into Funding Independence in v6.5 after an
audit found the two correlated at r=0.72 — the same underlying funding-profile
signal measured twice under two labels.

Both still compute, store, and display. Only their contribution to the weighted
sum changed.

### Informational member metrics

| Metric | Technique | Note |
|---|---|---|
| Legislative Leadership | PageRank on the cosponsorship graph | **Not** purely informational — this is the same score feeding LE at 25% |
| Ideology Score | SVD on the cosponsorship matrix (2nd singular vector) | Purely informational |
| Partisan Depth | Content-based voting analysis, SVD ideology as Bayesian prior | Purely informational |

## Presidents

```mermaid
flowchart LR
    PM["Public Mandate — 21.67%<br/>70% average approval + 30% trend<br/>UCSB polling, Truman onward<br/>pre-Truman: election margin"]
    EFF["Effectiveness — 21.67%<br/>60% GDP growth + 40% job creation<br/>BEA/FRED modern, MeasuringWorth pre-1929<br/>payrolls exist only from 1939"]
    AA["Agency Alignment — 21.67%<br/>Federal Register rulemaking counts<br/>+ finalized fraction<br/>N/A before Clinton"]
    HL["Historical Legacy — 35%<br/>C-SPAN Presidential Historians Survey<br/>2021 cycle, ~142 historians"]

    PM --> OVR["<b>Presidential score</b>"]
    EFF --> OVR
    AA --> OVR
    HL --> OVR

    OVR --> RENORM{"≥ 2 mechanical<br/>dimensions present?"}
    RENORM -->|yes| HOLD["Historical Legacy held at exactly 35%<br/>mechanical dimensions renormalize<br/>among themselves"]
    RENORM -->|"no — only 1"| FLAT["Flat renormalization<br/>one number can't carry 65%"]
```

**Removed rather than left hand-set:** Independence, Follow-Through (both
2026-07) and Competence (shortly after). Each was a one-time hand-set value
with no live formula and no realistic path to one. Competence's only live
signal, executive-order activity rate, correlated 0.097 (p=0.53) with C-SPAN's
own Administrative Skill category across 44 rated presidents — statistically
indistinguishable from noise.

**Why Historical Legacy is 35%.** At equal fifths the four mechanical
dimensions — which individually correlate 0.17 with historian judgment —
outvoted the one dimension that tracks it, putting Coolidge, McKinley and
Harding in the top 10 while Lincoln and Eisenhower fell out. At 50% the overall
ranking correlated 0.96 with simply using C-SPAN's ranking directly, meaning the
mechanical dimensions contributed nothing. 35% is where the top of the ranking
is recognizable while the mechanical dimensions still move the rest
(correlation to pure C-SPAN: 0.89).

**Why the renormalization branch exists.** Flat renormalization let Historical
Legacy's *effective* weight rise to ~44.7% for the ~36 presidents predating
Agency Alignment data, and ~61.8% for the four non-elected successors — so 35%
was the true weight for only 4 of 47 presidents. The floor at "one mechanical
dimension" exists because Fillmore's Effectiveness is 100/100 from a Gold-Rush
GDP boom, which under a flat 65% share would have swapped his near-bottom
historian rating for a top-10 placement.

## Supreme Court justices

```mermaid
flowchart LR
    JC["Consistency — 35%"] --> JOVR["<b>Justice score</b>"]
    JI["Independence — 30%"] --> JOVR
    JR["Judicial restraint — 20%"] --> JOVR
    JB["Bipartisan agreement — 15%"] --> JOVR
```

Single source of truth in `JUSTICE_SCORE_WEIGHTS` — shared by the scorer, the
directory's overall calculation, and the public weights endpoint. These were
previously three independent copies that could silently drift.

## Source map

| Concern | Code |
|---|---|
| Weights | `backend/app/config_definitions.py` — `SCORE_WEIGHTS`, `PRESIDENT_SCORE_WEIGHTS`, `JUSTICE_SCORE_WEIGHTS` |
| Member formulas + full changelog | `analyze/score_calculator.py` (module docstring is the source of truth) |
| President formulas | `analyze/president_scorer.py` |
| Justice formulas | `services/justice_service.py` |
| Public weights endpoint | `GET /api/config` |
| Human-readable version history | `frontend/src/lib/scoreVersions.ts` → `/changelog` |
