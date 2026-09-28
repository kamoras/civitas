"""Check each model-written Bluesky issue post against the articles it cites.

Until 2026-09-27 (PR #660) an issue's Bluesky post was written by a language
model from the issue's news articles. The articles themselves were never
stored, so the only record of what the model was given is each issue's
source URLs. This reads every such post from the account's public feed,
fetches the articles its issue cites, and sorts the post into:

  flagged       it states something the articles do not: the platform's own
                grounding and editorializing checks (analyze/grounding.py),
                any proper name, acronym or number the articles never
                mention, or a sentence with fewer than MIN_SUPPORT of its
                content words together in one or two adjacent article
                sentences
  supported     none of those
  unverifiable  at least one of the articles could not be read (AP,
                Politico and The Hill refuse automated reading) and the post
                was not clean against the rest, so what it says may be in
                the unread one

MIN_SUPPORT was set by hand-labelling a random 25 posts (it agreed on 24)
and checked on a fresh 15 flagged and 15 passed.

Usage:
    python backend/scripts/audit_bluesky_posts.py [--cache DIR] [--out results.json]

The flagged posts are withdrawn through app/data/retractions.json.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import html
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.pipeline.analyze.grounding import grounding_violations, hedge_and_editorializing_violations  # noqa: E402

ACCOUNT = "did:plc:s2jly6odtnwefp2aaofjzz7e"
FEED = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
ISSUE_API = "https://civitas-research.org/api/action/issues/{}"
# Issue posts before PR #660 reached production were model-written.
VERBATIM_SINCE = "2026-09-27T20:05"
USER_AGENT = "civitas-research.org post audit (curl/8.5.0)"
MIN_SUPPORT = 0.40

STOP = set("""a an the of to in on for and or but with by at from as is are was were be been has have had it its this
that these those their his her they he she will would could should may might can not no new more also after before
over amid into about than while during which who whom what when where why how all any some several many most other
such so up out just only under between recent recently""".split())
# Capitalized for reasons other than being a name.
NOT_NAMES = set("""Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May June July
August September October November December U.S US American Americans Yesterday On The A An In This That These It He
She They His Her Their Its As At After Before While With For And But Or If When Meanwhile Also Separately""".split())


def _get(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        return urllib.request.urlopen(req, timeout=30).read()
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return None


def issue_posts() -> list[dict]:
    posts, cursor = [], None
    while True:
        query = {"actor": ACCOUNT, "limit": 100, "filter": "posts_no_replies"}
        if cursor:
            query["cursor"] = cursor
        page = json.loads(_get(f"{FEED}?{urllib.parse.urlencode(query)}"))
        for item in page["feed"]:
            post = item["post"]
            link = ((post["record"].get("embed") or {}).get("external") or {}).get("uri") or ""
            if item.get("reason") or "/issue/" not in link or post["record"]["createdAt"] >= VERBATIM_SINCE:
                continue
            posts.append({"uri": post["uri"], "at": post["record"]["createdAt"], "text": post["record"]["text"],
                          "issue": link.rstrip("/").split("/issue/")[1].split("?")[0]})
        cursor = page.get("cursor")
        if not cursor:
            return posts


def article_text(url: str, cache: pathlib.Path) -> str:
    """The article's title, description and paragraphs; "" if unreadable."""
    path = cache / (hashlib.sha1(url.encode()).hexdigest() + ".html")
    if not path.exists():
        raw = _get(url)
        time.sleep(1.0)
        if raw is None:
            return ""
        path.write_bytes(raw)
    page = path.read_text(errors="replace")
    paras = re.findall(r"<p[^>]*>(.*?)</p>", page, re.S | re.I)
    title = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    desc = re.search(r'<meta[^>]+(?:name|property)="(?:og:)?description"[^>]+content="([^"]*)"', page, re.I)
    text = " ".join(html.unescape(m.group(1)) for m in (title, desc) if m) + " "
    text += " ".join(html.unescape(re.sub(r"<[^>]+>", " ", p)) for p in paras)
    return re.sub(r"\s+", " ", text).strip()


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 2}


def support(sentence: str, source_sentences: list[str]) -> float:
    """The largest share of the sentence's content words found together in
    one or two adjacent source sentences."""
    words = _words(sentence)
    if not words:
        return 1.0
    pairs = [" ".join(source_sentences[i:i + 2]) for i in range(len(source_sentences))]
    return max((len(words & _words(p)) / len(words) for p in pairs), default=0.0)


def names(text: str) -> set[str]:
    """Capitalized words that do not open a sentence, and acronyms, without
    a possessive, split at hyphens."""
    out = set()
    for sentence in _sentences(text):
        for i, word in enumerate(re.findall(r"[A-Za-z][A-Za-z'\-.]*", sentence)):
            word = re.sub(r"['’]s$", "", word).strip(".'-")
            if len(word) < 2 or word in NOT_NAMES:
                continue
            if word.isupper() or (i > 0 and word[0].isupper()):
                out.update(p for p in word.split("-") if len(p) > 1 and p[0].isupper())
    return out


def screen(post_text: str, sources: list[str]) -> list[str]:
    text = re.sub(r"https?://\S+", "", post_text).strip()
    text = re.sub(r"^(Yesterday|On [A-Z][a-z]+ \d{1,2}):\s*", "", text)  # added by code, not the model
    source = " ".join(sources)
    reasons = grounding_violations(text, source) + hedge_and_editorializing_violations(text)
    lower = source.lower()
    # A demonym counts when its stem is there ("African" -> "Africa").
    missing = sorted(n for n in names(text) if n.lower() not in lower and n.lower()[:max(4, len(n) - 3)] not in lower)
    if missing:
        reasons.append("names not in the articles: " + ", ".join(missing))
    source_sentences = _sentences(source)
    weak = [s for s in _sentences(text) if support(s, source_sentences) < MIN_SUPPORT]
    if weak:
        reasons.append("sentences the articles do not support: " + " | ".join(weak))
    return reasons


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=pathlib.Path, default=pathlib.Path("/tmp/bsky-audit"))
    ap.add_argument("--out", type=pathlib.Path, default=pathlib.Path("bsky_audit.json"))
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)

    posts = issue_posts()
    issues = {}
    for issue_id in {p["issue"] for p in posts}:
        raw = _get(ISSUE_API.format(issue_id))
        issues[issue_id] = json.loads(raw).get("sourceUrls", []) if raw else []

    results = []
    for post in posts:
        read = [article_text(u, args.cache) for u in issues[post["issue"]]]
        sources = [t for t in read if len(t) > 300]  # shorter is an error or consent page
        reasons = screen(post["text"], sources) if sources else ["no article could be read"]
        verdict = ("supported" if not reasons
                   else "flagged" if sources and len(sources) == len(read) else "unverifiable")
        results.append({**post, "verdict": verdict, "reasons": reasons})

    args.out.write_text(json.dumps(results, indent=1))
    print(collections.Counter(r["verdict"] for r in results))


if __name__ == "__main__":
    main()
