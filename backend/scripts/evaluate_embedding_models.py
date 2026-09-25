"""Compare candidate embedding models on this platform's OWN live failure
cases — the selection instrument for the 2026-07 embedding-model swap.

Why this exists: the current model (snowflake-arctic-embed-xs) places all
same-register text in a ~0.55-0.87 raw-cosine band, which is the root
cause under roughly a third of the open findings registry (see
docs/action_center_audit_2026-07.md and the permanent-solutions research):
reject/abstain thresholds that can never fire, explore-doc anchors from
unrelated floor speeches, politician-name disambiguation with fully
overlapping legit/bogus ranges, and a per-fact topicality check that had
to be rejected on measurement. Per the repo's calibration discipline, the
replacement is chosen by measured SEPARATION on real production failures,
not leaderboard rank.

Each task below is built from text that actually flowed through
production in July 2026 (verbatim or lightly trimmed), with known-correct
and known-incorrect pairs. The score per task is the separation gap:

    gap = min(similarity of should-match pairs)
        - max(similarity of should-NOT-match pairs)

gap > 0 means a clean threshold exists for that task; the bigger, the
more margin. The current model measures NEGATIVE gaps on several tasks —
that is the pathology being fixed, and the baseline row makes it visible.

CLASSIFICATION tasks (added 2026-09; see docs/research/embedding-models.md).
The four tasks above are similarity gates. The classification subsystem
(donor type, bill policy area, kNN) had never been measured against
labels a human assigned, so there was no instrument to decide whether it
should leave the primary model. Two ground-truth tasks, each scored the way
the production call site encodes (symmetric, no query prompt):

  donor_type  — FEC committee master (cmYY.txt, public bulk data): the
                committee's own filed type is the label (candidate
                committee H/S/P -> CandidateAffiliated; party X/Y ->
                Party/Ideological; PAC N/Q with a connected organization
                -> Org/Employees). Input is the committee NAME only, which
                is all tiers 2-3 see when FEC metadata is missing.
  bill_policy — the CRS policy area Congress.gov assigns every bill, read
                from this deployment's own api_cache (sponsored-legislation
                responses). Input is the bill title only.

Per task and model:
  zeroshot    — donor: macro recall of argmax over the production
                prototypes; bill: adjusted mutual information between the
                POLICY_TAXONOMY argmax and the CRS label (the taxonomies
                differ, so AMI needs no hand-made mapping between them).
  knn         — leave-one-out macro recall of a cosine kNN vote over the
                labelled set (tier 3's mechanism).
  conf_auroc  — how well the top-1 prototype similarity ranks correct above
                incorrect zero-shot calls (donor) / kNN calls (bill). 0.5
                means no threshold on that similarity can mean anything;
                this is the "inert threshold" question, asked directly.
  knn_auroc   — the same for the kNN winner's vote share.
  margin      — median top-1 minus runner-up prototype similarity.

Run (downloads models on first use; CPU is fine):
    cd backend && python3 scripts/evaluate_embedding_models.py \
        [--fec-cm cm26.txt] [--db /data/civitas.db] [--models NAME ...]
Without Hugging Face access, a model exported to ONNX can stand in:
    --onnx NAME=DIR:mean|cls   (DIR holds model*.onnx + tokenizer.json)
"""

import argparse
import collections
import io
import json
import pathlib
import random
import sqlite3
import sys
import urllib.request
import zipfile

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


CANDIDATES = [
    # (model_name, notes)
    ("Snowflake/snowflake-arctic-embed-xs", "CURRENT baseline — retrieval-asymmetric"),
    ("sentence-transformers/all-MiniLM-L6-v2", "22M symmetric-similarity classic"),
    ("BAAI/bge-small-en-v1.5", "33M retrieval + similarity"),
    ("thenlper/gte-small", "33M symmetric-friendly"),
    ("google/embeddinggemma-300m", "300M — check Pi CPU cost before adopting"),
]

# ---------------------------------------------------------------------------
# Task 1 — Politician-name disambiguation (the Ferran Torres failure).
# Prototype phrases vs mention contexts. Legit civic references must score
# HIGHER than a different person's (sports) usage of the same surname.
# ---------------------------------------------------------------------------
DISAMBIG_POSITIVE = [
    ("Senator Lindsey Graham from SC", "the floor speech in which Graham criticized the bill as"),
    ("Senator John Thune from SD", "leadership change. Thune announced the tribute details on"),
    ("Representative Ted Lieu from CA", "Committee hearing. Lieu criticized Ambassador Mike Waltz during"),
    ("Senator Gary Peters from MI", "planning gaps; Peters noted the Pentagon budget lacked"),
]
DISAMBIG_NEGATIVE = [
    ("Representative Ritchie Torres from NY", "Spain defeated Argentina 1-0 in a match featuring Ferran Torres' late goal"),
    ("Representative Norma J. Torres from CA", "Spain defeated Argentina 1-0 in a match featuring Ferran Torres' late goal"),
    ("Senator Tim Scott from SC", "the film's director Scott accepted the award at the festival"),
]

# ---------------------------------------------------------------------------
# Task 2 — Explore-doc anchoring (the PROMESA/World Cup failure). Issue
# titles vs civic-document titles. A genuinely related doc must outscore
# an unrelated floor speech.
# ---------------------------------------------------------------------------
DOC_POSITIVE = [
    ("House approves Pentagon funding framework", "DEPARTMENT OF DEFENSE APPROPRIATIONS ACT"),
    ("HIV prevention funding freeze announced", "FUNDING FOR HIV PREVENTION PROGRAMS"),
    ("DOJ seeks communications records from NYT reporters", "PROTECTING JOURNALISTS FROM GOVERNMENT SURVEILLANCE"),
]
DOC_NEGATIVE = [
    ("Spanish and Argentine reactions to World Cup final", "PROMESA IS A DEMOCRATIC TRAGEDY"),
    ("Spanish and Argentine reactions to World Cup final", "DEPARTMENT OF DEFENSE APPROPRIATIONS ACT"),
    ("Cyclosporiasis outbreak investigation updates", "PROMESA IS A DEMOCRATIC TRAGEDY"),
]

# ---------------------------------------------------------------------------
# Task 3 — Per-fact topicality (audit M6, rejected on measurement against
# the current model). Issue title vs facts; on-topic facts must outscore
# the cross-topic contaminants that actually published.
# ---------------------------------------------------------------------------
FACT_POSITIVE = [
    ("New York City may not arrest Netanyahu; federal action urged",
     "The federal government is considering issuing an arrest warrant for Benjamin Netanyahu."),
    ("Trump-backed candidates win Arizona primaries",
     "The Democratic primary for a Phoenix-area battleground race has not yet concluded."),
    ("FDA investigation continues over Taylor Farms lettuce",
     "Multiple states are reporting over 7,000 confirmed cases of cyclosporiasis nationwide."),
]
FACT_NEGATIVE = [
    ("New York City may not arrest Netanyahu; federal action urged",
     "President Zelenskyy removed his army chief amid protests and appointed a new leader."),
    ("Trump-backed candidates win Arizona primaries",
     "Over 27 senior officials have left their positions in the Trump administration since the start of his first term."),
    ("FDA investigation continues over Taylor Farms lettuce",
     "PhRMA has noted the situation as a key point of discussion in industry discussions."),
]

# ---------------------------------------------------------------------------
# Task 4 — Policy relevance (the old civic-gate failure: sports/celebrity
# passed at every threshold). Policy prototypes vs article headlines.
# ---------------------------------------------------------------------------
POLICY_PROTOTYPE = "US Congress bill vote legislation Senate House passed signed"
POLICY_POSITIVE = [
    "House approves Pentagon funding framework in narrow 216-212 vote",
    "Senate Budget Committee convenes after panel leadership change",
]
POLICY_NEGATIVE = [
    "Spain defeats Argentina 1-0 in World Cup final on Ferran Torres goal",
    "Pop star announces record-breaking stadium tour dates",
]


def _pair_sims(model, pairs):
    lefts = [a for a, _ in pairs]
    rights = [b for _, b in pairs]
    ea = model.encode(lefts, normalize_embeddings=True)
    eb = model.encode(rights, normalize_embeddings=True)
    return [float(x @ y) for x, y in zip(ea, eb)]


def _proto_sims(model, proto, texts):
    ep = model.encode([proto], normalize_embeddings=True)[0]
    et = model.encode(texts, normalize_embeddings=True)
    return [float(ep @ e) for e in et]


def evaluate(model) -> dict[str, float]:
    gaps = {}

    pos = _pair_sims(model, DISAMBIG_POSITIVE)
    neg = _pair_sims(model, DISAMBIG_NEGATIVE)
    gaps["disambiguation"] = min(pos) - max(neg)

    pos = _pair_sims(model, DOC_POSITIVE)
    neg = _pair_sims(model, DOC_NEGATIVE)
    gaps["explore_docs"] = min(pos) - max(neg)

    pos = _pair_sims(model, FACT_POSITIVE)
    neg = _pair_sims(model, FACT_NEGATIVE)
    gaps["fact_topicality"] = min(pos) - max(neg)

    pos = _proto_sims(model, POLICY_PROTOTYPE, POLICY_POSITIVE)
    neg = _proto_sims(model, POLICY_PROTOTYPE, POLICY_NEGATIVE)
    gaps["policy_relevance"] = min(pos) - max(neg)

    return gaps


# ---------------------------------------------------------------------------
# Classification tasks (ground truth assigned by FEC filers / CRS analysts)
# ---------------------------------------------------------------------------
FEC_CM_URL = "https://www.fec.gov/files/bulk-downloads/20{yy}/cm{yy}.zip"
# FEC committee-master codes (fec.gov data dictionary, "Committee type" /
# "Interest group category") -> the donor types they unambiguously fix.
# A documented data-format convention, used here only as evaluation labels.
_CANDIDATE_TYPES = {"H", "S", "P"}
_PARTY_TYPES = {"X", "Y"}
_PAC_TYPES = {"N", "Q"}
_CONNECTED_ORG_TYPES = {"C", "L", "M", "T", "V", "W"}
PER_CLASS = 300
SEED = 20260924


def load_donor_task(path: str | None) -> tuple[list[str], list[str]]:
    """(committee names, donor-type labels), PER_CLASS per class, seeded."""
    if path:
        text = pathlib.Path(path).read_text(encoding="latin-1")
    else:
        yy = "26"
        with urllib.request.urlopen(FEC_CM_URL.format(yy=yy), timeout=120) as r:
            z = zipfile.ZipFile(io.BytesIO(r.read()))
            text = z.read(z.namelist()[0]).decode("latin-1")
    by_label: dict[str, set[str]] = collections.defaultdict(set)
    for line in text.splitlines():
        f = line.split("|")
        if len(f) < 15 or f[11] == "T":  # terminated committees
            continue
        name, ctype, org_type = f[1].strip(), f[9], f[12]
        if not name:
            continue
        if ctype in _CANDIDATE_TYPES:
            by_label["CandidateAffiliated"].add(name)
        elif ctype in _PARTY_TYPES:
            by_label["Party/Ideological"].add(name)
        elif ctype in _PAC_TYPES and org_type in _CONNECTED_ORG_TYPES:
            by_label["Org/Employees"].add(name)
    rng = random.Random(SEED)
    names, labels = [], []
    for label in sorted(by_label):
        pool = sorted(by_label[label])
        for n in rng.sample(pool, min(PER_CLASS, len(pool))):
            names.append(n)
            labels.append(label)
    return names, labels


def load_bill_task(db_path: str) -> tuple[list[str], list[str]]:
    """(bill titles, CRS policy areas) from cached sponsored-legislation
    responses, deduplicated by bill and capped per area, seeded."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT data_json FROM api_cache WHERE tier='congress' "
        "AND cache_key LIKE 'member-sponsored-v2-%'"
    ).fetchall()
    conn.close()
    bills: dict[tuple, tuple[str, str]] = {}
    for (data,) in rows:
        for b in json.loads(data) or []:
            area = ((b.get("policyArea") or {}).get("name") or "").strip()
            title = (b.get("title") or "").strip()
            if area and title and b.get("number"):
                bills[(b.get("congress"), b.get("type"), b.get("number"))] = (title, area)
    by_area: dict[str, list[str]] = collections.defaultdict(list)
    for title, area in bills.values():
        by_area[area].append(title)
    rng = random.Random(SEED)
    titles, labels = [], []
    for area in sorted(by_area):
        pool = sorted(by_area[area])
        if len(pool) < 10:  # too few for a leave-one-out vote to mean anything
            continue
        for t in rng.sample(pool, min(PER_CLASS, len(pool))):
            titles.append(t)
            labels.append(area)
    return titles, labels


def _embed(model, texts: list[str]) -> np.ndarray:
    return np.asarray(model.encode(texts, normalize_embeddings=True), dtype=np.float32)


def _macro_recall(pred: list[str], gold: list[str]) -> float:
    hit, tot = collections.Counter(), collections.Counter()
    for p, g in zip(pred, gold):
        tot[g] += 1
        hit[g] += p == g
    return float(np.mean([hit[c] / tot[c] for c in tot]))


def _loo_knn(emb: np.ndarray, labels: list[str], k: int) -> tuple[list[str], np.ndarray]:
    """Leave-one-out similarity-weighted kNN: (predictions, winner's vote share)."""
    sims = emb @ emb.T
    np.fill_diagonal(sims, -np.inf)
    out, share = [], []
    for row in sims:
        votes = collections.Counter()
        for j in np.argpartition(-row, k)[:k]:
            votes[labels[j]] += max(float(row[j]), 0.0)
        label, weight = votes.most_common(1)[0]
        out.append(label)
        share.append(weight / (sum(votes.values()) or 1.0))
    return out, np.array(share)


def _auroc(scores: np.ndarray, correct: np.ndarray) -> float:
    from sklearn.metrics import roc_auc_score
    if correct.all() or not correct.any():
        return float("nan")
    return float(roc_auc_score(correct, scores))


def _prototype_scores(model, emb: np.ndarray, prototypes: dict[str, str]):
    keys = list(prototypes)
    sims = emb @ _embed(model, [prototypes[k] for k in keys]).T
    top2 = np.sort(sims, axis=1)[:, -2:]
    return keys, sims, top2[:, 1], top2[:, 1] - top2[:, 0]


def evaluate_donor(model, names: list[str], labels: list[str]) -> dict[str, float]:
    from app.pipeline.analyze.nn_classifier import DONOR_TYPE_PROTOTYPES

    protos = {k: DONOR_TYPE_PROTOTYPES[k] for k in sorted(set(labels))}
    emb = _embed(model, names)
    keys, sims, top1, margin = _prototype_scores(model, emb, protos)
    pred = [keys[i] for i in sims.argmax(axis=1)]
    correct = np.array([p == g for p, g in zip(pred, labels)])
    knn, share = _loo_knn(emb, labels, 5)
    return {
        "zeroshot": _macro_recall(pred, labels),
        "knn": _macro_recall(knn, labels),
        "conf_auroc": _auroc(top1, correct),
        "knn_auroc": _auroc(share, np.array([p == g for p, g in zip(knn, labels)])),
        "margin": float(np.median(margin)),
    }


def evaluate_bills(model, titles: list[str], labels: list[str]) -> dict[str, float]:
    from sklearn.metrics import adjusted_mutual_info_score

    from app.pipeline.analyze.bill_analyzer import POLICY_TAXONOMY

    protos = {k: v for k, v in POLICY_TAXONOMY.items() if k != "PROCEDURAL"}
    emb = _embed(model, [t[:500] for t in titles])
    keys, sims, top1, margin = _prototype_scores(model, emb, protos)
    knn, share = _loo_knn(emb, labels, 7)
    knn_correct = np.array([p == g for p, g in zip(knn, labels)])
    return {
        "zeroshot": float(adjusted_mutual_info_score(labels, [keys[i] for i in sims.argmax(axis=1)])),
        "knn": _macro_recall(knn, labels),
        "conf_auroc": _auroc(top1, knn_correct),
        "knn_auroc": _auroc(share, knn_correct),
        "margin": float(np.median(margin)),
    }


class OnnxEncoder:
    """Minimal SentenceTransformer.encode stand-in over an ONNX export, for
    environments that can reach an ONNX mirror but not Hugging Face."""

    def __init__(self, directory: str, pooling: str):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        d = pathlib.Path(directory)
        self.tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        self.tok.enable_truncation(max_length=256)
        self.tok.enable_padding()
        self.sess = ort.InferenceSession(str(sorted(d.glob("*.onnx"))[0]))
        self.inputs = {i.name for i in self.sess.get_inputs()}
        self.pooling = pooling

    def encode(self, texts, normalize_embeddings=True, **_):
        out = []
        for i in range(0, len(texts), 64):
            enc = self.tok.encode_batch(list(texts[i:i + 64]))
            feed = {
                "input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
                "token_type_ids": np.array([e.type_ids for e in enc], dtype=np.int64),
            }
            hidden = self.sess.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
            if self.pooling == "cls":
                pooled = hidden[:, 0]
            else:
                mask = feed["attention_mask"][..., None].astype(np.float32)
                pooled = (hidden * mask).sum(1) / mask.sum(1)
            out.append(pooled)
        emb = np.concatenate(out)
        if normalize_embeddings:
            emb /= np.linalg.norm(emb, axis=1, keepdims=True)
        return emb


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--models", nargs="*", help="subset of CANDIDATES (default: all)")
    ap.add_argument("--onnx", action="append", default=[], metavar="NAME=DIR:mean|cls")
    ap.add_argument("--fec-cm", help="FEC committee master cmYY.txt (default: download cm26)")
    ap.add_argument("--db", default="/data/civitas.db", help="deployment DB for the bill task")
    args = ap.parse_args()

    onnx = {}
    for spec in args.onnx:
        name, rest = spec.split("=", 1)
        directory, pooling = rest.rsplit(":", 1)
        onnx[name] = (directory, pooling)
    candidates = [(n, notes) for n, notes in CANDIDATES if not args.models or n in args.models]

    donor = load_donor_task(args.fec_cm)
    print(f"donor_type task: {len(donor[0])} committee names, {collections.Counter(donor[1])}")
    bills = None
    if pathlib.Path(args.db).exists():
        bills = load_bill_task(args.db)
        print(f"bill_policy task: {len(bills[0])} titles over {len(set(bills[1]))} CRS areas")
    else:
        print(f"bill_policy task: skipped ({args.db} not found — run inside the backend container)")

    header = f"{'model':<40} {'disambig':>8} {'docs':>7} {'facts':>7} {'policy':>7}"
    for task in ("donor", "bill"):
        header += f" | {task + ' zs':>9} {'knn':>6} {'auroc':>6} {'kauroc':>6} {'margin':>7}"
    print(header)
    for name, notes in candidates:
        try:
            if name in onnx:
                model = OnnxEncoder(*onnx[name])
            else:
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer(name)
            gaps = evaluate(model)
            results = [evaluate_donor(model, *donor)]
            results.append(evaluate_bills(model, *bills) if bills else None)
        except Exception as exc:  # model unavailable — report, keep going
            print(f"{name:<40} FAILED: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        line = (f"{name:<40} {gaps['disambiguation']:>+8.3f} {gaps['explore_docs']:>+7.3f} "
                f"{gaps['fact_topicality']:>+7.3f} {gaps['policy_relevance']:>+7.3f}")
        for r in results:
            line += (" | " + " " * 33 + "n/a") if r is None else (
                f" | {r['zeroshot']:>9.3f} {r['knn']:>6.3f} {r['conf_auroc']:>6.3f} "
                f"{r['knn_auroc']:>6.3f} {r['margin']:>7.3f}")
        print(f"{line}   # {notes}")


if __name__ == "__main__":
    main()
