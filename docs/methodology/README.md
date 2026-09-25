# Methodology decision records

Code comments state the **current** rule. The reasons a rule changed, and the
measurements behind each change, live here. They used to be written into the
code as comment-changelogs. By 2026-09 those comment-changelogs made up about
670 lines of `score_calculator.py`'s module docstring, a 250-line comment index
above `ALGORITHM_VERSION`, and most of `config_definitions.py`. Current rules
and superseded ones sat side by side, and stale claims survived in both.

| Where | What |
|---|---|
| [`member-score/`](member-score/) | One record per member-score `ALGORITHM_VERSION`, moved verbatim from `score_calculator.py`. `v4.md` also carries the v4.x–v5.9 notes that the docstring kept under its v3 → v4 heading. |
| [`weights.md`](weights.md) | Why `SCORE_WEIGHTS` and `PRESIDENT_SCORE_WEIGHTS` are what they are, moved verbatim from `config_definitions.py` |
| [`../research/`](../research/) | The v6.13 / president-v5 evidence notes, each with a reproduction script |
| `frontend/src/lib/scoreVersions.ts` | The public, plain-language summary of each version, shown at `/changelog` |

**When you change a score:**
- Bump the version.
- Add a record here named for the new version.
- Add the public summary to `scoreVersions.ts`.
- Leave the code comment describing only what the code now does.

The moved records are kept as they were written, in `text` blocks. Words like
"above", "top-of-file" and "this file" refer to the module they came from.
