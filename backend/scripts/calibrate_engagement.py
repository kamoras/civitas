"""Fit the cosine at which the Action Center reposts a news outlet's post
(pipeline/analyze/bluesky_engagement.py), on labels the posts' own links
supply, and write it to app/data/engagement_calibration.json (AGENTS.md §3a).

The gate: an outlet post is reposted when its text's cosine to the best
matching live issue's title (action_center._embed_texts, the model the gate
uses) reaches the threshold. Each post is scored the way the gate scores it,
against the issues of its own day and the day before (the issues live when
it was posted), and labelled by its link (bluesky_engagement.link_label),
resolved through the outlet's shortener:
- right: the matched issue cites the article the post links;
- wrong: another live issue cites it, so the post is that story's coverage;
- unlabelled: no live issue cites it. Nothing says what such a post is
  about. (An outlet article no issue cites, beside a matched issue citing
  the same outlet's article on its story, was considered as a negative and
  rejected: `uncited_same_outlet` counts it, and on the 2026-10-18 snapshot
  only 9 such posts had their article cited by another issue later, none
  of which the data could place as the same or a different story, so
  nothing showed it reliable; issues citing an outlet already cite two or
  more of its articles on one story in 9 of 208 cases.)
A link that could not be resolved (a 429, a timeout) is unlabelled too.

Threshold: the Action Center's precision rule (app/precision_rule.py), the
grid point whose precision's exact lower 95% bound is highest, used only
when that bound is at least 0.80; otherwise null, and the gate keeps
running at bluesky_engagement.UNMEASURED_THRESHOLD, which the file names
as unmeasured (`in_use`). The labels are kept with their scores, and the
gate's daily refit (bluesky_engagement.refit) pools them with the posts it
records from then on. held_out repeats the choice on blocks of consecutive
days. `volume` is the reposts a week the gate would make, every post
scored, before the per-run cap; `coverage` the share of them the links can
label.

Usage (network: Bluesky's public API and the outlets' short links; loads the
classification model). --cache DIR keeps the posts and the resolved links,
so a rerun reads each link once:
    DATABASE_URL=sqlite:////path/to/snapshot.db python backend/scripts/calibrate_engagement.py [--dry-run] [--cache DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

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
FOLDS = 4
article_key = be.article_key


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
            posts.append({"uri": post["uri"], "at": post["indexedAt"], "link": be.post_link(post),
                          "text": (post.get("record") or {}).get("text") or ""})
        cursor = body.get("cursor")
        if not body.get("feed") or not cursor or cursor[:10] < oldest:
            return posts


async def resolve(client: httpx.AsyncClient, links: list[str], known: dict[str, str | None]) -> None:
    """Fill `known` with each link's final address (None when it could not
    be read), one request at a time: the shorteners answer a burst with 429."""
    for link in links:
        if link in known:
            continue
        final = None
        for wait in (0, 5, 30):
            await asyncio.sleep(wait or 1)
            try:
                resp = await client.head(link)
            except httpx.HTTPError:
                continue
            if be.resolved(resp.status_code):
                final = str(resp.url)
                break
        known[link] = final


async def gather_posts(oldest: str, cache: pathlib.Path | None) -> list[dict]:
    posts_file, links_file = (cache / "posts.json", cache / "links.json") if cache else (None, None)
    known = json.loads(links_file.read_text()) if links_file and links_file.exists() else {}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": BOT_USER_AGENT}) as client:
        if posts_file and posts_file.exists():
            posts = json.loads(posts_file.read_text())
        else:
            posts = [p | {"outlet": h} for h in be.NEWS_OUTLET_HANDLES for p in await outlet_posts(client, h, oldest)]
            if posts_file:
                posts_file.write_text(json.dumps(posts))
        try:
            await resolve(client, sorted({p["link"] for p in posts if p["link"]}), known)
        finally:
            if links_file:
                links_file.write_text(json.dumps(known))
    for p in posts:
        p["article"] = article_key(known.get(p["link"])) if p["link"] else None
    return posts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cache", type=pathlib.Path)
    args = parser.parse_args()
    with SessionLocal() as db:
        issues = [(i.id, i.date[:10], i.title or "", json.loads(i.source_urls or "[]"))
                  for i in db.query(ActionIssue).filter(ActionIssue.source_type.is_(None))]
    by_day, cites, issue_hosts = defaultdict(list), defaultdict(set), {}
    for iid, day, _, urls in issues:
        by_day[day].append(iid)
        keys = {article_key(u) for u in urls} - {None}
        issue_hosts[iid] = {k.split("/")[0] for k in keys}
        for k in keys:
            cites[k].add(iid)
    titles = {iid: title for iid, _, title, _ in issues}
    first, last = min(by_day), max(by_day)
    posts = asyncio.run(gather_posts(first, args.cache))
    posts = [p for p in posts if first <= p["at"][:10] <= last and p["text"].strip()]

    scored = []
    for p in posts:
        day = date.fromisoformat(p["at"][:10])
        live = [i for d in (day - timedelta(days=1), day) for i in by_day.get(d.isoformat(), [])]
        if live:
            scored.append((p, live))
    live_ids = sorted({i for _, live in scored for i in live})
    issue_vecs = dict(zip(live_ids, np.array(_embed_texts([titles[i] for i in live_ids]))))
    post_vecs = np.array(_embed_texts([p["text"] for p, _ in scored]))
    best, label, days, labels = [], [], [], []
    uncited = Counter()
    for (p, live), vec in zip(scored, post_vecs):
        sims = [float(issue_vecs[i] @ vec) for i in live]
        k = int(np.argmax(sims))
        matched, sim = live[k], round(sims[k], 4)
        citing = sorted(cites.get(p["article"], set()) & set(live))
        right = be.link_label(matched, citing)
        best.append(sim)
        label.append(right)
        days.append(p["at"][:10])
        if right is not None:
            labels.append([p["uri"], p["article"], matched, citing, sim])
        elif p["article"] and not cites.get(p["article"]) and p["article"].split("/")[0] in issue_hosts[matched]:
            uncited["posts"] += 1
            uncited["at_unmeasured_threshold"] += sim >= be.UNMEASURED_THRESHOLD
    best, days = np.array(best), np.array(days)
    known = np.array([x is not None for x in label])
    right = np.array([bool(x) for x in label])
    scores, correct, ldays = best[known], right[known], days[known]
    threshold, best_t = choose(scores, correct, be.GRID)
    at = threshold if threshold is not None else best_t
    weeks = ((date.fromisoformat(last) - date.fromisoformat(first)).days + 1) / 7

    def volume(t: float) -> dict:
        passed = best >= t
        return {"threshold": t, "reposts_per_week": round(int(passed.sum()) / weeks, 1),
                "labelled_per_week": round(int((passed & known).sum()) / weeks, 1),
                "coverage": round(int((passed & known).sum()) / max(int(passed.sum()), 1), 3)}

    # The outlets file several articles on one story: how often an issue
    # citing an outlet cites two or more of its articles.
    outlet_issues = Counter()
    for iid, _, _, urls in issues:
        per_host = Counter(k.split("/")[0] for k in {article_key(u) for u in urls} - {None})
        for host in ("apnews.com", "npr.org"):
            if per_host[host]:
                outlet_issues["citing"] += 1
                outlet_issues["citing_two_or_more"] += per_host[host] >= 2
    sorted_labels = sorted(labels)
    out = {
        "_source": (
            "Generated by backend/scripts/calibrate_engagement.py from the outlets' own Bluesky posts "
            f"({', '.join(be.NEWS_OUTLET_HANDLES)}) and the stored Action Center issues. Each post is scored as the "
            "gate scores it (best cosine, action_center._embed_texts, against the issues of its day and the day "
            "before) and labelled by the article it links, resolved through the outlet's shortener: right when the "
            "matched issue cites it, wrong when another live issue cites it, unlabelled otherwise. threshold: the "
            "grid point whose precision's exact lower 95% bound is highest, when that bound is at least "
            f"{PRECISION_LOWER_BOUND}, else null (the gate then runs at the unmeasured in_use value). held_out: the "
            f"same choice on {FOLDS} blocks of consecutive days, each scored on the block left out. volume: reposts "
            "a week before the per-run cap, every post scored; coverage: the share of them the links label."
        ),
        "_as_of": date.today().isoformat(),
        "window": {"from": first, "to": last, "weeks": round(weeks, 1)},
        "posts_scored": len(scored),
        "links_unresolved": sum(1 for p in posts if p["link"] and p["article"] is None),
        "labelled_posts": len(labels),
        "threshold": threshold,
        "in_use": {"threshold": threshold if threshold is not None else be.UNMEASURED_THRESHOLD,
                   "measured": threshold is not None,
                   "why": None if threshold is not None else (
                       "No fit meets the precision rule; the hand-set value the gate used before it was measured is "
                       "kept by the user's decision (2026-10-21), unmeasured, until one does.")},
        "at_unmeasured_threshold": summary(scores >= be.UNMEASURED_THRESHOLD, correct),
        "best_bound_threshold": best_t,
        "at_threshold": summary(scores >= at, correct),
        "all_labelled": {"best_match_right": int(correct.sum()), "posts": int(len(correct))},
        "volume": {"unmeasured": volume(be.UNMEASURED_THRESHOLD), "best_bound": volume(at)},
        "held_out": held_out(scores, correct, ldays, be.GRID, FOLDS),
        "uncited_same_outlet": {**uncited, "issues_citing_an_outlet": outlet_issues["citing"],
                                "of_which_cite_two_or_more_of_its_articles": outlet_issues["citing_two_or_more"]},
        "labels_sha256": be.labels_sha256(sorted_labels),
        "labels": sorted_labels,
    }
    print(json.dumps({k: v for k, v in out.items() if k != "labels"}, indent=1))
    if not args.dry_run:
        be.CALIBRATION_PATH.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
