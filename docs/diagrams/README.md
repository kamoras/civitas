# Architecture diagrams

Rendered diagrams of how Civitas works, in [Mermaid](https://mermaid.js.org/).
GitHub renders these natively — open any file below and you get a picture, not
source.

These are a companion to the root [README](../../README.md), not a replacement.
The README keeps its ASCII diagrams so it stays readable in a terminal, in a
pager, and in editors that don't render Mermaid. These go further than ASCII
comfortably can: component-level detail, per-edge labels, decision branches,
and an entity-relationship view of the schema.

| Diagram | What it covers |
|---|---|
| [01 — System architecture](01-system-architecture.md) | End-to-end: sources → pipelines → storage → API → frontend |
| [02 — Nightly pipeline](02-nightly-pipeline.md) | The seven phases, and the Librarian/Analyst producer-consumer overlap |
| [03 — Action Center](03-action-center.md) | The hourly news pipeline, including the topic-keyed persistence branch |
| [04 — Classification tiers](04-classification-tiers.md) | The tier 1→4 escalation and the disclosed pre-filter exceptions |
| [05 — Scoring](05-scoring.md) | Score composition for members, presidents, and justices |
| [06 — Caching](06-caching.md) | The three cache layers and what invalidates each |
| [07 — Data model](07-data-model.md) | Entity relationships in SQLite |
| [08 — Deployment](08-deployment.md) | Swarm topology and the rolling-update sequence |
| [09 — Explore search](09-explore-search.md) | Indexing and query paths for semantic search |

## Keeping these honest

Every number, weight, and threshold in these diagrams is transcribed from code,
and the source file is named next to it so a reader can check. Duplicated facts
drift — that is the failure mode these are exposed to, and naming the source is
the mitigation.

The scoring diagram is the one most likely to go stale: weights live in
`SCORE_WEIGHTS` / `PRESIDENT_SCORE_WEIGHTS` / `JUSTICE_SCORE_WEIGHTS`
(`backend/app/config_definitions.py`) and component splits in
`score_calculator.py`, all of which are actively iterated. Those dicts are
authoritative; if this folder disagrees with them, this folder is wrong.

Diagrams are pinned to **`ALGORITHM_VERSION = v6.12`**. When that version
changes, re-check [05 — Scoring](05-scoring.md).

## Editing

Preview with GitHub's own renderer (push to a branch and view the file), or
locally:

```bash
npx @mermaid-js/mermaid-cli -i docs/diagrams/01-system-architecture.md -o /tmp/out.svg
```

Mermaid fails loudly — a syntax error renders as an error box rather than a
diagram — so check the rendered output, not just the diff.
