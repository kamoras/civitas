# Should classification leave the primary embedding model?

The review proposed moving classification (donor type, bill policy area and
the kNN tier) off `snowflake-arctic-embed-xs`, then re-measuring the
similarity thresholds that the 2026-07 notes call inert. This note records
what can be measured, what was measured, and why the model has **not** been
switched in this change.

The numbers come from
[`backend/scripts/evaluate_embedding_models.py`](../../backend/scripts/evaluate_embedding_models.py).

## The instrument had no classification task

The harness picked the 2026-07 similarity model by its separation on four
production failure cases. All four are similarity gates. The classification
subsystem was left on arctic "until their own measurement" (`vector_store.
get_similarity_model`), but nothing could perform that measurement, because
no task scored a classifier against labels a person assigned. Two such
tasks were added:

| Task | Labels | Input | Source |
|---|---|---|---|
| `donor_type` | The committee's filed FEC type. Candidate committee (H/S/P) → CandidateAffiliated. Party (X/Y) → Party/Ideological. PAC (N/Q) with a connected organization → Org/Employees. | Committee name only, which is all tiers 2–3 see when metadata is missing | FEC committee master, public bulk data |
| `bill_policy` | The CRS policy area Congress.gov assigns every bill | Bill title only | The deployment's own `api_cache` (sponsored-legislation responses) |

Each task is sampled at 300 per class with a fixed seed. Both tasks encode
the way the production call sites do: symmetrically, with no query prompt.
Each model gets four scores:

- **zero-shot:** macro recall of the prototype argmax. For bills this is
  adjusted mutual information with CRS instead, since the taxonomies differ;
  AMI needs no hand-made mapping between them.
- **kNN:** leave-one-out macro recall of kNN, tier 3's mechanism.
- **AUROC:** whether the confidence signal each tier thresholds on (top-1
  prototype similarity, kNN vote share) ranks right calls above wrong ones.
  An AUROC of 0.5 means no threshold on that signal can mean anything.
  This asks the "inert threshold" question directly.
- **margin:** median top-1 minus runner-up similarity.

## Results where the models could be loaded

Arctic, the model in production, is published only on Hugging Face, and
Hugging Face is blocked by this environment's network policy. So is every
mirror tried: hf-mirror, ModelScope, and fastembed, which fetches arctic from
Hugging Face. Two candidates are mirrored on reachable hosts as ONNX exports:
MiniLM on Chroma's S3 bucket and bge-small on fastembed's GCS bucket. The
harness's `--onnx` option ran them with each model's own pooling. The
`bill_policy` task needs a deployment database, so it could not run here.

`donor_type`, 900 active committees from `cm26`:

| Model | Zero-shot | kNN | AUROC, prototype sim | AUROC, kNN share | Margin |
|---|---|---|---|---|---|
| all-MiniLM-L6-v2 | 0.666 | 0.921 | 0.574 | 0.849 | 0.059 |
| bge-small-en-v1.5 | 0.494 | 0.920 | 0.528 | 0.854 | 0.021 |
| snowflake-arctic-embed-xs (production) | not run: weights unreachable | | | | |

Chance is 0.333.

Two findings do not depend on which of the two models is used:

1. **kNN over labelled examples is far stronger than prototype zero-shot.**
   It gets 0.92 against 0.49–0.67. Tier 3 works, and its vote share is an
   informative confidence (AUROC 0.85).
2. **Top-1 prototype similarity is close to uninformative as a
   confidence** (AUROC 0.53–0.57). Re-tuning a threshold on that similarity
   cannot make it separate right calls from wrong ones in either model. This
   matches the 2026-07 notes on arctic, which found no gap between top-1 and
   runner-up (mean 0.759 against 0.740).

## Why nothing was switched

The decision is between arctic and a candidate, and arctic could not be
measured. Choosing MiniLM because it beat bge-small, or re-tuning arctic's
thresholds from MiniLM's numbers, would be the opinion-based change this
series is trying to avoid. The two findings above also point at the tiering,
not only the model. If arctic shows the same pattern, a better model will
not rescue threshold-gated zero-shot. The data would then favor leaning on
tier 3, which would be a different change.

## To decide

Run the harness once inside the backend container on the Pi, where Hugging
Face and the bill cache are both available:

    docker exec "$(docker ps -q -f name=civitas_backend)" python3 scripts/evaluate_embedding_models.py

It prints both tasks for all five candidates, including arctic. The decision
rule, fixed before seeing the numbers:

- **Switch** classification to a candidate only if it beats arctic on kNN
  recall and prototype AUROC in both tasks.
- **If every model's prototype AUROC stays below about 0.65,** the change is
  structural rather than a model swap: prototype zero-shot then
  cannot carry a threshold under any model.
- Either change bumps `ALGORITHM_VERSION`. The learning store and sqlite-vec
  corpus reset through the analysis-code hash as usual.
