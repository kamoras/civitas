"""Calibrate the Action Center's article clustering against judged pairs.

Writes app/data/cluster_pair_calibration.json: which text
action_center._cluster_articles embeds, the complete-linkage cut, the hash
of the judged set it was fitted to, and the measurement behind the choice.
Rerun when app/data/cluster_pair_judgments.json changes, or when the
embedding model does (tests/test_action_center.py fails until the hash
matches).

Method. Each judged pair is scored the way one hourly run would score it:
both articles embedded, centred on the mean of every judged article
published in the 48 hours up to the later of the two (the run's pool,
MAX_ARTICLE_AGE_HOURS), and compared by cosine. A pair whose article the
ingest filter drops (_digest_reason) never reaches clustering, so it is
left out, and so are the pairs judged unsure.

Complete linkage merges a cluster only when every pair inside it clears
the cut, so the pairwise decision is the whole decision. A wrong merge
publishes a chimera, an issue whose facts come from different events,
while a missed merge costs a story one outlet's worth of evidence. The rule
is precision first: for each candidate, the lowest cut (0.01 grid) at
which no judged different-event pair merges, and among the candidates the
one that merges the most same-event pairs at its cut. The shared-entity
gate is _shares_distinctive_entity with the judged articles as its
document corpus.

Usage (loads the retrieval model; no network once it is cached):
    python backend/scripts/calibrate_cluster_pairs.py
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys
from collections import Counter

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.pipeline.analyze.action_center import (  # noqa: E402
    _CLUSTER_CALIBRATION_PATH,
    _CLUSTER_JUDGMENTS_PATH,
    _center_titles,
    _digest_reason,
    _embed_texts,
    _issue_signature,
    _shares_distinctive_entity,
    cluster_judgments_hash,
    cluster_text,
)
from app.pipeline.fetch.news_feeds import MAX_ARTICLE_AGE_HOURS, NewsArticle  # noqa: E402

GRID = np.round(np.arange(0.0, 1.0, 0.01), 2)
# Cuts reported for the title cosine clustering used before this file
# existed: the bundled start and the values the daily self-labelled fit
# stored in its last two weeks.
TITLE_CUTS_REPORTED = (0.40, 0.50, 0.57, 0.63, 0.70, 0.78)
# How much of the description follows the title: the relevance gate's 200
# (_filter_policy_relevant embeds the same text) and two longer reads.
DESCRIPTION_CHARS = (200, 300, 500)


def _parse(ts: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def pair_scores(articles: list[dict], pairs: list[dict], texts: list[str]) -> np.ndarray:
    """Day-centred cosine of each pair, centred on the pool a run holding
    both would have seen."""
    emb = _embed_texts(texts)
    when = [_parse(a["published"]) for a in articles]
    index = {a["id"]: n for n, a in enumerate(articles)}
    window = datetime.timedelta(hours=MAX_ARTICLE_AGE_HOURS)
    out = []
    for p in pairs:
        i, j = index[p["a"]], index[p["b"]]
        latest = max(when[i], when[j])
        pool = [n for n, w in enumerate(when) if latest - window < w <= latest]
        centred = _center_titles(emb[[i, j]], emb[pool].mean(axis=0))
        out.append(float(centred[0] @ centred[1]))
    return np.array(out)


def zero_chimera_cut(scores: np.ndarray, same: np.ndarray, allowed: np.ndarray) -> tuple[float, int]:
    """The lowest cut at which no different-event pair merges, and how many
    same-event pairs merge there."""
    for cut in GRID:
        merged = allowed & (scores >= cut)
        if not (merged & ~same).any():
            return float(cut), int((merged & same).sum())
    return 1.0, 0


def main() -> None:
    data = json.loads(_CLUSTER_JUDGMENTS_PATH.read_text())
    articles = data["articles"]
    dropped = {
        a["id"] for a in articles
        if _digest_reason(NewsArticle(title=a["title"], url="", source_name=a["source"] or "", summary=a["description"]))
    }
    pairs = [
        p for p in data["pairs"]
        if p["label"] != "unsure" and p["a"] not in dropped and p["b"] not in dropped
    ]
    same = np.array([p["label"] == "same" for p in pairs])
    report = np.array([p.get("relation") == "report" for p in pairs])

    df: Counter = Counter()
    for a in articles:
        df.update(_issue_signature(a["title"], [a["description"]]))
    sig = {a["id"]: _issue_signature(a["title"], [a["description"]]) for a in articles}
    entity = np.array([
        _shares_distinctive_entity(sig[p["a"]], sig[p["b"]], df, len(articles)) for p in pairs
    ])
    everything = np.ones(len(pairs), dtype=bool)

    title = pair_scores(articles, pairs, [a["title"] for a in articles])
    candidates = {"title": (title, everything), "title + shared distinctive entity": (title, entity)}
    for chars in DESCRIPTION_CHARS:
        scores = pair_scores(articles, pairs, [cluster_text(a["title"], a["description"], chars) for a in articles])
        candidates[f"title+description[{chars}]"] = (scores, everything)
        candidates[f"title+description[{chars}] + shared distinctive entity"] = (scores, entity)
    measured = {}
    for name, (scores, gate) in candidates.items():
        cut, recall = zero_chimera_cut(scores, same, gate)
        # Against the stricter rubric too: a reaction counts as a different
        # event, so only pairs that both report the occurrence are same.
        strict_cut, strict_recall = zero_chimera_cut(scores, report, gate)
        pos, neg = scores[same], scores[~same]
        auc = float(((pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()) / (len(pos) * len(neg)))
        measured[name] = {
            "auc": round(auc, 3), "cut": cut, "same_merged": recall, "same_pairs": int(same.sum()),
            "strict_cut": strict_cut, "strict_report_merged": strict_recall, "strict_report_pairs": int(report.sum()),
        }
        print(f"{name:48} AUC {auc:.3f}  cut {cut:.2f}  same merged {recall}/{same.sum()}"
              f"  (report-only rubric: cut {strict_cut:.2f}, {strict_recall}/{report.sum()})")
    # Ties go to the shorter name: no gate, then the shorter description.
    best = max(measured, key=lambda n: (measured[n]["same_merged"], -len(n), -measured[n]["cut"]))
    chosen = measured[best]
    print(f"\nchosen: {best} at {chosen['cut']:.2f}")

    title_table = {}
    for cut in TITLE_CUTS_REPORTED:
        merged = title >= cut
        tp, fp = int((merged & same).sum()), int((merged & ~same).sum())
        title_table[f"{cut:.2f}"] = {"same_merged": tp, "different_merged": fp}
        print(f"title cosine {cut:.2f}: same {tp}/{same.sum()}, different {fp}/{(~same).sum()}")

    payload = {
        "_source": (
            "Generated by backend/scripts/calibrate_cluster_pairs.py from app/data/cluster_pair_judgments.json. "
            "text is what _cluster_articles embeds (retrieval model, centred on the run's mean): the title and the "
            "first description_chars characters of the description; threshold is its "
            "complete-linkage cut: the lowest cut at which no judged different-event pair merges, for the "
            "candidate that merges the most same-event pairs there. candidates holds each one's measurement; "
            "title_cosine is the title-only clustering this replaced, at the cuts it used."
        ),
        "_as_of": datetime.date.today().isoformat(),
        "judgments_hash": cluster_judgments_hash(),
        "pairs_scored": len(pairs),
        "pairs_left_out_ingest_filter": sum(
            1 for p in data["pairs"] if p["label"] != "unsure" and (p["a"] in dropped or p["b"] in dropped)
        ),
        "text": best,
        "description_chars": int(best.split("[")[1].split("]")[0]) if "[" in best else 0,
        "threshold": chosen["cut"],
        "candidates": measured,
        "title_cosine": title_table,
    }
    if "entity" in best or not payload["description_chars"]:
        # _cluster_articles embeds title+description and has no gate; a
        # different winner needs the code changed with the file.
        raise SystemExit(f"calibration chose {best!r}: update _cluster_articles before writing it")
    _CLUSTER_CALIBRATION_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {_CLUSTER_CALIBRATION_PATH}")


if __name__ == "__main__":
    main()
