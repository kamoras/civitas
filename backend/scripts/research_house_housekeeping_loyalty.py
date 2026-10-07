"""Measure what v6.28's House fixes change, on live Clerk data.

1. Recent votes (default). Until v6.28 the House built its recent votes
   inline and decided "voted with party" from partyLeaning, which
   refine_with_vote_data sets from the roll call's split even on
   housekeeping questions (motions to recommit, ordering the previous
   question, motions to table), where stamp_roll_call_outcome deliberately
   leaves partySplit None. Those stored flags drive the scorecard's
   per-vote badges, its "against party" filter and counts, and key-vote
   selection. This takes the House's latest 120 roll calls of a year (the
   window house_pipeline uses), counts the housekeeping ones, and compares
   each member's break rate on them under the old label and the new one.
   Constituent Alignment does not read these flags when the party-line
   record exists (score_calculator.party_break_rate); see 2.

2. Motions to commit (--commit). Constituent Alignment reads the
   party-line record over the whole Congress (party_line_record.py), which
   already left housekeeping questions out but not the House's motion to
   commit, the motion to recommit's twin. This lists every House motion to
   commit in a Congress, its party split and who broke on it.

Run from the repo (network required):
    cd backend && PYTHONPATH=. python3 scripts/research_house_housekeeping_loyalty.py [year]
    cd backend && PYTHONPATH=. python3 scripts/research_house_housekeeping_loyalty.py --commit [congress]
"""

import asyncio
import collections
import statistics
import sys

import httpx

from app.contact import BOT_USER_AGENT
from app.pipeline.fetch.congress import parse_house_vote_xml
from app.pipeline.transform.normalize_votes import (
    _determine_party_alignment,
    compute_party_split,
    compute_party_vote_split,
    is_housekeeping,
)

WINDOW = 120  # house_pipeline: fetch_recent_house_roll_calls(..., count=120)


def _url(year: int, roll: int) -> str:
    return f"https://clerk.house.gov/evs/{year}/roll{roll:03d}.xml"


async def _latest_roll(client: httpx.AsyncClient, year: int) -> int:
    lo, hi = 0, 2000
    while lo < hi:
        mid = (lo + hi + 1) // 2
        r = await client.get(_url(year, mid))
        if r.status_code == 200 and b"<rollcall-vote" in r.content:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _normalized(vote: str) -> str:
    v = vote.upper()
    if v in ("YEA", "AYE", "YES"):
        return "Yea"
    if v in ("NAY", "NO"):
        return "Nay"
    return "Not Voting"


async def main(year: int) -> None:
    headers = {"User-Agent": BOT_USER_AGENT}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers) as client:
        last = await _latest_roll(client, year)
        rolls = []
        for n in range(max(1, last - WINDOW + 1), last + 1):
            r = await client.get(_url(year, n))
            if r.status_code == 200:
                rc = parse_house_vote_xml(r.text, year, n)
                if rc:
                    rolls.append(rc)

    housekeeping = [rc for rc in rolls if is_housekeeping(rc.get("question"), "house")]
    print(f"{year}: rolls {rolls[0]['rollNumber']}-{rolls[-1]['rollNumber']}, {len(rolls)} parsed")
    print(f"housekeeping: {len(housekeeping)}",
          collections.Counter(rc.get("question") for rc in housekeeping).most_common())

    old = collections.defaultdict(lambda: [0, 0])
    new = collections.defaultdict(lambda: [0, 0])
    party_of: dict[str, str] = {}
    padded = collections.Counter()
    for rc in rolls:
        old_label = (compute_party_vote_split(rc) or {}).get("label")
        new_label = None if is_housekeeping(rc.get("question"), "house") else compute_party_split(rc)
        for m in rc["members"]:
            party = m["party"]
            if party not in ("R", "D"):
                continue
            party_of[m["bioguideId"]] = party
            vote = _normalized(m["voteCast"])
            for label, acc in ((old_label, old), (new_label, new)):
                aligned = _determine_party_alignment(party, vote, label)
                if aligned is not None:
                    acc[m["bioguideId"]][0 if aligned else 1] += 1
            if is_housekeeping(rc.get("question"), "house"):
                padded[_determine_party_alignment(party, vote, old_label)] += 1
    print(f"housekeeping member-votes the old rule counted: with party {padded[True]}, "
          f"against {padded[False]}")

    def rate(acc: dict, bio: str) -> float:
        with_, against = acc[bio]
        return against / (with_ + against)

    for party in ("R", "D"):
        ids = [b for b, p in party_of.items() if p == party and sum(new[b]) > 0]
        before = [rate(old, b) for b in ids]
        after = [rate(new, b) for b in ids]
        change = [a - b for b, a in zip(before, after)]
        print(f"{party}: {len(ids)} members, recent-vote break rate {statistics.mean(before):.2%} -> "
              f"{statistics.mean(after):.2%} (mean change {statistics.mean(change):+.2%}, "
              f"largest {max(change, key=abs):+.2%})")


async def motions_to_commit(congress: int) -> None:
    first_year = 1789 + (congress - 1) * 2
    headers = {"User-Agent": BOT_USER_AGENT}
    party_line = 0
    found = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers) as client:
        for year in (first_year, first_year + 1):
            last = await _latest_roll(client, year)
            for n in range(1, last + 1):
                r = await client.get(_url(year, n))
                if r.status_code != 200:
                    continue
                rc = parse_house_vote_xml(r.text, year, n)
                if not rc or rc["congress"] != congress:
                    continue
                label = compute_party_split(rc)
                if label in ("R", "D"):
                    party_line += 1
                question = rc.get("question") or ""
                if is_housekeeping(question, "house") and not is_housekeeping(question):
                    breaks = [
                        f"{m['lastName']} ({m['party']}-{m['state']})" for m in rc["members"]
                        if m["party"] in ("R", "D")
                        and _determine_party_alignment(m["party"], _normalized(m["voteCast"]), label) is False
                    ]
                    found.append((year, n, question, rc.get("documentName"), label, breaks))
    print(f"{congress}th Congress House: {party_line} roll calls split on party lines (any question)")
    print(f"motions to commit: {len(found)}")
    for year, n, question, doc, label, breaks in found:
        print(f"  {year} roll {n}: {question} ({doc}), split {label}, breaks {len(breaks)} {breaks}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--commit":
        asyncio.run(motions_to_commit(int(sys.argv[2]) if len(sys.argv) > 2 else 119))
    else:
        asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 2026))
