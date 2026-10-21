"""Fit the cosine at which the Action Center reposts a news outlet's post
(pipeline/analyze/bluesky_engagement.py), on labels the posts supply, and
write it to app/data/engagement_calibration.json (AGENTS.md §3a).

The gate: an outlet post is reposted when its text's cosine to the best
matching live issue's title (action_center._embed_texts, the model the gate
uses) reaches the threshold. Labels: an outlet post that links an article
an issue quotes as a source is about that issue. Each such post is scored
the way the gate scores it, against the issues of its own day and the day
before (the issues live when it was posted), and the gate is right when
its best match is an issue citing the post's article. Posts whose article
no issue cites are left out: nothing says what they are about.

Threshold: the Action Center's precision rule (app/precision_rule.py), the
grid point whose precision's exact lower 95% bound is highest, used only
when that bound is at least 0.80; otherwise null and nothing is reposted.
held_out repeats the choice on blocks of consecutive days.

Usage (network: Bluesky's public API and the outlets' short links; loads the
classification model):
    DATABASE_URL=sqlite:////path/to/snapshot.db python backend/scripts/calibrate_engagement.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib
import sys
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import urlsplit

import httpx
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.contact import BOT_USER_AGENT  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import ActionIssue  # noqa: E402
from app.pipeline.analyze import bluesky_engagement as be  # noqa: E402
from app.pipeline.analyze.action_center import _embed_texts  # noqa: E402
from app.precision_rule import PRECISION_LOWER_BOUND, choose, held_out, summary  # noqa: E402

FEED = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
GRID = [round(float(t), 2) for t in np.arange(0.30, 0.961, 0.01)]
FOLDS = 4


def article_key(url: str | None) -> str | None:
    """An article's address without scheme, query or trailing slash."""
    if not url:
        return None
    parts = urlsplit(url)
    return f"{parts.netloc.lower().removeprefix('www.')}{parts.path.rstrip('/')}" or None


async def outlet_posts(client: httpx.AsyncClient, handle: str, oldest: str) -> list[dict]:
    posts, cursor = [], None
    while True:
        params = {"actor": handle, "limit": 100, "filter": "posts_no_replies", **({"cursor": cursor} if cursor else {})}
        resp = await client.get(FEED, params=params)
        resp.raise_for_status()
        body = resp.json()
        for item in body.get("feed", []):
            if item.get("reason"):
                continue
            post = item["post"]
            link = ((post.get("embed") or {}).get("external") or {}).get("uri")
            posts.append({"uri": post["uri"], "day": post["indexedAt"][:10], "link": link,
                          "text": (post.get("record") or {}).get("text") or ""})
        cursor = body.get("cursor")
        if not body.get("feed") or not cursor or cursor[:10] < oldest:
            return posts


async def resolve(client: httpx.AsyncClient, links: list[str], known: set[str]) -> dict[str, str | None]:
    """Each link's article key, following redirects only for a link that
    isn't already an article an issue cites (a shortener)."""
    out, gate = {}, asyncio.Semaphore(8)

    async def one(link: str) -> None:
        key = article_key(link)
        if key in known:
            out[link] = key
            return
        async with gate:
            try:
                resp = await client.head(link)
                out[link] = article_key(str(resp.url))
            except httpx.HTTPError:
                out[link] = None
    await asyncio.gather(*(one(link) for link in links))
    return out


async def gather_posts(oldest: str, known: set[str]) -> list[dict]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": BOT_USER_AGENT}) as client:
        posts = [p for h in be.NEWS_OUTLET_HANDLES for p in await outlet_posts(client, h, oldest)]
        keys = await resolve(client, sorted({p["link"] for p in posts if p["link"]}), known)
    for p in posts:
        p["article"] = keys.get(p["link"]) if p["link"] else None
    return posts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        issues = [(i.id, i.date[:10], i.title or "", json.loads(i.source_urls or "[]"))
                  for i in db.query(ActionIssue).filter(ActionIssue.source_type.is_(None))]
    by_day = defaultdict(list)
    cites = defaultdict(set)
    for iid, day, _, urls in issues:
        by_day[day].append(iid)
        for u in urls:
            cites[article_key(u)].add(iid)
    titles = {iid: title for iid, _, title, _ in issues}
    posts = asyncio.run(gather_posts(min(by_day), set(cites) - {None}))

    labelled = []
    for p in posts:
        day = date.fromisoformat(p["day"])
        live = [i for d in (day - timedelta(days=1), day) for i in by_day.get(d.isoformat(), [])]
        about = cites.get(p["article"], set()) & set(live)
        if about and p["text"].strip():
            labelled.append((p, live, about))
    texts = [p["text"] for p, _, _ in labelled]
    live_ids = sorted({i for _, live, _ in labelled for i in live})
    post_vecs = np.array(_embed_texts(texts))
    issue_vecs = dict(zip(live_ids, np.array(_embed_texts([titles[i] for i in live_ids]))))
    best_sim, correct, days, labels = [], [], [], []
    for (p, live, about), vec in zip(labelled, post_vecs):
        sims = [float(issue_vecs[i] @ vec) for i in live]
        k = int(np.argmax(sims))
        best_sim.append(sims[k])
        correct.append(live[k] in about)
        days.append(p["day"])
        labels.append([p["uri"], p["article"], sorted(about)])
    scores, right, days = np.array(best_sim), np.array(correct), np.array(days)
    threshold, best = choose(scores, right, GRID)
    at = threshold if threshold is not None else best
    out = {
        "_source": (
            "Generated by backend/scripts/calibrate_engagement.py from the outlets' own Bluesky posts "
            f"({', '.join(be.NEWS_OUTLET_HANDLES)}) and the stored Action Center issues: a post linking an article "
            "an issue cites is about that issue; it is scored as the gate scores it (best cosine, "
            "action_center._embed_texts, against the issues of its day and the day before), right when the best "
            "match cites its article. threshold: the grid point whose precision's exact lower 95% bound is "
            f"highest, when that bound is at least {PRECISION_LOWER_BOUND}, else null (nothing is reposted). "
            f"held_out: the same choice on {FOLDS} blocks of consecutive days, each scored on the block left out."
        ),
        "_as_of": date.today().isoformat(),
        "posts_read": len(posts),
        "labelled_posts": len(labelled),
        "threshold": threshold,
        "best_bound_threshold": best,
        "at_threshold": summary(scores >= at, right),
        "all_labelled": {"best_match_right": int(right.sum()), "posts": len(right)},
        "held_out": held_out(scores, right, days, GRID, FOLDS),
        "labels_sha256": hashlib.sha256(json.dumps(sorted(labels)).encode()).hexdigest(),
        "labels": sorted(labels),
    }
    print(json.dumps({k: v for k, v in out.items() if k != "labels"}, indent=1))
    if not args.dry_run:
        be.CALIBRATION_PATH.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
