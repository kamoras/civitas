"""Measure when Bluesky's users are active, and write the hours Civitas
sends its schedulable posts in: app/data/bluesky_post_windows.json, read by
app.post_timing (AGENTS.md §3a).

Two measurements, both from Bluesky's public, unauthenticated services:

  sample   Replays Jetstream (the firehose as JSON, which keeps about 36
           hours) for each past UTC hour not yet in SAMPLES: posts created
           in POST_MINUTES from :OFFSET, counted by their first declared
           language, and likes created in LIKE_MINUTES. Rerun it to add
           hours — a daily run for a week covers every hour of the week.
           English is the stand-in for the U.S. audience; it also counts
           the U.K., Canada, Australia and English-writing users anywhere.
  account  Civitas's own posts (getAuthorFeed) and the time of every like
           on them (getLikes). Reposts carry no time on the public AppView
           (getRepostedBy returns profiles only), so they are not timed.
  write    The window: the Eastern hours whose English post volume, averaged
           over every sample of that hour, is above the mean hour, merged
           into runs. The account's likes and the global like volume are
           written beside it as cross-checks, not inputs: the account's
           sample is too small to choose hours from, and likes have no
           language.

    python3 backend/scripts/fetch_bluesky_activity.py sample ~/.cache/bsky/jetstream.jsonl
    python3 backend/scripts/fetch_bluesky_activity.py account ~/.cache/bsky/account.json
    python3 backend/scripts/fetch_bluesky_activity.py write ~/.cache/bsky/jetstream.jsonl ~/.cache/bsky/account.json

Rerun all three when the audience might have moved (a season, a year).
"""

import asyncio
import json
import pathlib
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import websockets

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.site_url import SITE_DOMAIN  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "app" / "data" / "bluesky_post_windows.json"

JETSTREAM = "wss://jetstream2.us-east.bsky.network/subscribe"
APPVIEW = "https://public.api.bsky.app/xrpc"
ACCOUNT = SITE_DOMAIN  # the account's Bluesky handle is the site's domain
EASTERN = ZoneInfo("America/New_York")

# Jetstream keeps about 36 hours; asking for more just starts at its oldest.
REPLAY_HOURS = 35
# A few minutes of each hour, away from :00, where scheduled bots cluster.
OFFSET_MINUTES = 20
POST_MINUTES = 5
LIKE_MINUTES = 1


async def _window(collection: str, start_us: int, minutes: int) -> tuple[int, Counter]:
    end_us = start_us + minutes * 60_000_000
    langs: Counter = Counter()
    created = 0
    url = f"{JETSTREAM}?wantedCollections={collection}&cursor={start_us}"
    async with websockets.connect(url, max_size=None, open_timeout=30) as ws:
        while True:
            event = json.loads(await asyncio.wait_for(ws.recv(), 60))
            if event.get("time_us", 0) >= end_us:
                return created, langs
            commit = event.get("commit") or {}
            if event.get("kind") != "commit" or commit.get("operation") != "create":
                continue
            created += 1
            if collection == "app.bsky.feed.post":
                declared = (commit.get("record") or {}).get("langs") or []
                langs[declared[0].split("-")[0].lower() if declared else "none"] += 1


async def sample(out: pathlib.Path) -> None:
    done = {json.loads(line)["hour"] for line in out.read_text().splitlines() if line.strip()} if out.exists() else set()
    this_hour = int(time.time() // 3600) * 3600
    for hour in range(this_hour - REPLAY_HOURS * 3600, this_hour + 3600, 3600):
        start_s = hour + OFFSET_MINUTES * 60
        if hour in done or start_s + POST_MINUTES * 60 > time.time() - 60:
            continue
        for _ in range(3):
            try:
                posts, langs = await _window("app.bsky.feed.post", start_s * 1_000_000, POST_MINUTES)
                likes, _ = await _window("app.bsky.feed.like", start_s * 1_000_000, LIKE_MINUTES)
                break
            except (OSError, TimeoutError, websockets.WebSocketException) as exc:
                print(f"retrying {hour}: {exc}", file=sys.stderr)
                await asyncio.sleep(5)
        else:
            continue
        row = {"hour": hour, "postMinutes": POST_MINUTES, "posts": posts, "langs": dict(langs.most_common(12)),
               "likeMinutes": LIKE_MINUTES, "likes": likes}
        with out.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(datetime.fromtimestamp(hour, timezone.utc).isoformat(), posts, langs.get("en", 0), likes)


def _get(method: str, **params) -> dict:
    url = f"{APPVIEW}/{method}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return json.load(resp)
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def _paged(method: str, key: str, **params):
    cursor = None
    while True:
        page = _get(method, limit=100, **params, **({"cursor": cursor} if cursor else {}))
        yield from page.get(key, [])
        cursor = page.get("cursor")
        if not cursor or not page.get(key):
            return


def account(out: pathlib.Path) -> None:
    posts = []
    for item in _paged("app.bsky.feed.getAuthorFeed", "feed", actor=ACCOUNT, filter="posts_no_replies"):
        if "reason" in item:  # a repost of someone else's post
            continue
        post = item["post"]
        likes = [like["createdAt"] for like in _paged("app.bsky.feed.getLikes", "likes", uri=post["uri"])] \
            if post.get("likeCount") else []
        posts.append({"uri": post["uri"], "createdAt": post["record"]["createdAt"], "likes": likes,
                      "likeCount": post.get("likeCount", 0), "repostCount": post.get("repostCount", 0),
                      "replyCount": post.get("replyCount", 0), "quoteCount": post.get("quoteCount", 0)})
    out.write_text(json.dumps({"account": ACCOUNT, "fetchedAt": datetime.now(timezone.utc).isoformat(),
                               "posts": posts}, indent=1))
    print(f"{len(posts)} posts, {sum(len(p['likes']) for p in posts)} likes timed")


def _eastern_hour(moment: datetime) -> int:
    return moment.astimezone(EASTERN).hour


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _runs(hours: set[int]) -> list[dict]:
    """Contiguous [start, end) runs of Eastern hours, joined across midnight."""
    runs, h = [], 0
    while h < 24:
        if h in hours:
            start = h
            while h < 24 and h in hours:
                h += 1
            runs.append([start, h])
        h += 1
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == 24:
        runs[-1][1] = 24 + runs.pop(0)[1]
    return [{"start": s, "end": e % 24 if e > 24 else e} for s, e in runs]


def write(samples_path: pathlib.Path, account_path: pathlib.Path) -> None:
    rows = [json.loads(line) for line in samples_path.read_text().splitlines() if line.strip()]
    # Each Eastern hour's mean over every sample of it, as a share of the
    # mean hour. Each sample is placed by its own instant, so a sample from
    # either side of a DST change lands in the right local hour.
    by_hour: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        by_hour[datetime.fromtimestamp(row["hour"], timezone.utc).astimezone(EASTERN).hour].append(row)
    if len(by_hour) < 24:
        sys.exit(f"only {len(by_hour)} of 24 Eastern hours sampled yet")
    profile = {}
    for metric, value in (("englishPosts", lambda r: r["langs"].get("en", 0) / r["postMinutes"]),
                          ("allPosts", lambda r: r["posts"] / r["postMinutes"]),
                          ("likes", lambda r: r["likes"] / r["likeMinutes"])):
        means = [statistics.fmean(value(r) for r in by_hour[h]) for h in range(24)]
        overall = statistics.fmean(means)
        profile[metric] = [round(m / overall, 3) for m in means]
    samples_per_hour = [len(by_hour[h]) for h in range(24)]
    window_hours = {h for h in range(24) if profile["englishPosts"][h] > 1}

    acct = json.loads(account_path.read_text())
    like_hours = Counter(_eastern_hour(_instant(t)) for p in acct["posts"] for t in p["likes"])
    post_hours = Counter(_eastern_hour(_instant(p["createdAt"])) for p in acct["posts"])
    inside = [p for p in acct["posts"] if _eastern_hour(_instant(p["createdAt"])) in window_hours]
    outside = [p for p in acct["posts"] if _eastern_hour(_instant(p["createdAt"])) not in window_hours]

    def per_post(group):
        return {"posts": len(group), "likes": sum(p["likeCount"] for p in group),
                "reposts": sum(p["repostCount"] for p in group),
                "postsWithAnyInteraction": sum(1 for p in group if p["likeCount"] or p["repostCount"]
                                               or p["replyCount"] or p["quoteCount"])}

    first = min(r["hour"] for r in rows)
    last = max(r["hour"] for r in rows)
    OUTPUT.write_text(json.dumps({
        "_source": (
            f"backend/scripts/fetch_bluesky_activity.py, generated {datetime.now(timezone.utc).date().isoformat()}. "
            f"Jetstream ({JETSTREAM}) replayed for {len(rows)} hours from "
            f"{datetime.fromtimestamp(first, timezone.utc).isoformat()} to "
            f"{datetime.fromtimestamp(last, timezone.utc).isoformat()}: posts created in {POST_MINUTES} minutes "
            f"from :{OFFSET_MINUTES} of each hour, by declared language, and likes in {LIKE_MINUTES}. "
            "Window = the Eastern hours whose English post volume (mean over every sample of that hour) is "
            "above the mean hour; English stands in for the U.S. Cross-checks: likes on "
            f"@{acct['account']}'s posts via public.api.bsky.app, fetched {acct['fetchedAt'][:10]}."
        ),
        "timezone": "America/New_York",
        "windows": _runs(window_hours),
        "samplesPerEasternHour": samples_per_hour,
        "hourlyRelativeVolume": profile,
        "accountCrossCheck": {
            "likesByEasternHour": [like_hours.get(h, 0) for h in range(24)],
            "postsByEasternHour": [post_hours.get(h, 0) for h in range(24)],
            "postsSentInsideWindows": per_post(inside),
            "postsSentOutsideWindows": per_post(outside),
        },
    }, indent=2) + "\n")
    print(f"windows {_runs(window_hours)} from {len(rows)} hourly samples; wrote {OUTPUT}")


if __name__ == "__main__":
    command, *paths = sys.argv[1:]
    if command == "sample":
        asyncio.run(sample(pathlib.Path(paths[0])))
    elif command == "account":
        account(pathlib.Path(paths[0]))
    elif command == "write":
        write(pathlib.Path(paths[0]), pathlib.Path(paths[1]))
    else:
        sys.exit(__doc__)
