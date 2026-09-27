"""A bill's full public record, for its detail page: any bill, not only
one a current member sponsored.

From Congress.gov: the bill, its CRS summaries, every action, cosponsors
and text versions. From the roll calls congress_activity.py stored: every
recorded vote on it, with each member's position. Members are linked to
their Civitas pages where the site has one.

A part Congress.gov could not return is named in `unavailable` rather
than shown empty, and only a successful answer is cached, so an outage
never reads as a bill with no actions or no cosponsors.
"""

import html as html_lib
import re
import unicodedata

import httpx
from sqlalchemy.orm import Session

from app.models import RollCall, RollCallPosition, Representative, Senator
from app.config import settings
from app.pipeline.cache import api_cache_get, api_cache_set
from app.pipeline.fetch.congress import CONGRESS_API_BASE, _rate_limiter
from app.pipeline.fetch.http_utils import fetch_with_retry
from app.services.congress_service import bill_days, bill_label

# Congress.gov type path segment for each site bill-id prefix.
_TYPE_PATH = {
    "HR": "hr", "S": "s", "HRES": "hres", "SRES": "sres", "HJRES": "hjres",
    "SJRES": "sjres", "HCONRES": "hconres", "SCONRES": "sconres",
}
_CACHE_TIER = "congress"
# A bill's record changes a few times a day at most while it moves.
_CACHE_HOURS = 6
_PARTS = {
    "bill": "",
    "summaries": "/summaries",
    "actions": "/actions?limit=250",
    "cosponsors": "/cosponsors?limit=250",
    "text": "/text",
}


def parse_bill_id(bill_id: str) -> tuple[str, int] | None:
    """"S.4668" -> ("s", 4668); None for anything that is not a bill id.
    Split, not matched: the id comes from the URL (CodeQL py/polynomial-redos)."""
    if not bill_id or len(bill_id) > 16 or bill_id.count(".") != 1:
        return None
    prefix, number = bill_id.split(".")
    if prefix not in _TYPE_PATH or not number.isdigit():
        return None
    return _TYPE_PATH[prefix], int(number)


NOT_FOUND = object()


async def _congress_get(client: httpx.AsyncClient, url: str):
    """Congress.gov JSON, NOT_FOUND for a 404, or None when the fetch
    failed. The key goes only in the request URL, never the logged one."""
    full = str(httpx.URL(url).copy_merge_params({"api_key": settings.DATA_GOV_API_KEY, "format": "json"}))
    resp = await fetch_with_retry(client, _rate_limiter, "GET", url, request_url=full,
                                  expected_statuses=(404,), log_label="Congress API")
    if resp is None:
        return None
    if resp.status_code == 404:
        return NOT_FOUND
    try:
        return resp.json()
    except ValueError:
        return None


async def fetch_bill_record(client: httpx.AsyncClient, db: Session, congress: int, bill_id: str) -> dict:
    """{bill, summaries, actions, cosponsors, text, unavailable: [...],
    not_found}: not_found when Congress.gov has no such bill."""
    type_path, number = parse_bill_id(bill_id)
    out: dict = {"unavailable": [], "not_found": False}
    for part, suffix in _PARTS.items():
        key = f"bill-record-{part}-{congress}-{type_path}-{number}"
        cached = api_cache_get(db, _CACHE_TIER, key, max_age_hours=_CACHE_HOURS)
        if cached is not None:
            out[part] = cached.get("value")
            continue
        data = await _congress_get(client, f"{CONGRESS_API_BASE}/bill/{congress}/{type_path}/{number}{suffix}")
        if data is NOT_FOUND:
            if part == "bill":
                out["not_found"] = True
                return out
            data = {}
        if data is None:
            out[part] = None
            out["unavailable"].append(part)
            continue
        value = {
            "bill": data.get("bill"),
            "summaries": data.get("summaries"),
            "actions": data.get("actions"),
            "cosponsors": data.get("cosponsors"),
            "text": data.get("textVersions"),
        }[part]
        out[part] = value
        api_cache_set(db, _CACHE_TIER, key, {"value": value}, normal_ttl_hours=_CACHE_HOURS)
    return out


# ── Shaping ───────────────────────────────────────────────────────

def summary_paragraphs(summary_html: str) -> list[str]:
    """CRS summary HTML -> plain paragraphs. The page renders text, never
    another site's markup."""
    parts = re.split(r"</p>|<br\s*/?>", summary_html or "", flags=re.IGNORECASE)
    out = []
    for p in parts:
        text = " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", p)).split())
        if text:
            out.append(text)
    return out


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower().strip()


class MemberLinks:
    """Civitas page ids for members named by bioguide id (House votes,
    sponsors) or by last name and state (Senate votes carry a LIS id)."""

    def __init__(self, db: Session):
        self.by_bioguide: dict[str, str] = {}
        self.senators_by_state: dict[str, list[tuple[str, str]]] = {}
        for s in db.query(Senator):
            if s.bioguide_id:
                self.by_bioguide[s.bioguide_id] = s.id
            self.senators_by_state.setdefault(s.state, []).append((_fold(s.name), s.id))
        for r in db.query(Representative):
            if r.bioguide_id:
                self.by_bioguide[r.bioguide_id] = r.id

    def page(self, *, bioguide: str | None = None, last_name: str = "", state: str = "") -> str | None:
        """The member's Civitas page ("/politicians/ted-cruz"), or None for
        a member the site has no page for (a former member)."""
        found = self.by_bioguide.get(bioguide or "")
        if found is None and last_name and state:
            last = _fold(last_name)
            matches = [sid for name, sid in self.senators_by_state.get(state, []) if name.endswith(last)]
            found = matches[0] if len(matches) == 1 else None
        return f"/politicians/{found}" if found else None


_YEA = {"yea", "aye"}
_NAY = {"nay", "no"}


def _bucket(position: str) -> str:
    p = (position or "").strip().lower()
    if p in _YEA:
        return "yea"
    if p in _NAY:
        return "nay"
    if p == "present":
        return "present"
    return "notVoting"


def party_breakdown(positions: list[RollCallPosition]) -> list[dict]:
    """Per party: yeas, nays, present, not voting, counted from positions."""
    parties: dict[str, dict] = {}
    for p in positions:
        row = parties.setdefault(p.party or "?", {"party": p.party or "?", "yea": 0, "nay": 0, "present": 0, "notVoting": 0})
        row[_bucket(p.position)] += 1
    order = {"R": 0, "D": 1, "I": 2, "ID": 2}
    return sorted(parties.values(), key=lambda r: (order.get(r["party"], 9), r["party"]))


def vote_detail(db: Session, rc: RollCall, links: MemberLinks | None = None) -> dict:
    links = links or MemberLinks(db)
    positions = db.query(RollCallPosition).filter_by(roll_call_id=rc.id).all()
    members = [{
        "lastName": p.last_name, "firstName": p.first_name, "party": p.party, "state": p.state,
        "position": p.position, "bucket": _bucket(p.position),
        "page": (links.page(bioguide=p.member_id) if rc.chamber == "house"
                 else links.page(last_name=p.last_name, state=p.state)),
    } for p in positions]
    members.sort(key=lambda m: (m["lastName"], m["state"]))
    return {
        "chamber": rc.chamber, "congress": rc.congress, "session": rc.session, "number": rc.number,
        "date": rc.date, "question": rc.question, "title": rc.title, "result": rc.result,
        "rejected": rc.rejected, "majorityRequirement": rc.majority_requirement,
        "yeas": rc.yeas, "nays": rc.nays, "present": rc.present, "notVoting": rc.not_voting,
        "billId": rc.bill_id, "billLabel": bill_label(rc.bill_id), "sourceUrl": rc.source_url,
        "parties": party_breakdown(positions), "members": members,
    }


def _person(p: dict, links: MemberLinks) -> dict:
    return {
        "name": p.get("fullName") or "", "party": p.get("party") or "", "state": p.get("state") or "",
        "district": p.get("district"), "bioguideId": p.get("bioguideId"),
        "isOriginalCosponsor": p.get("isOriginalCosponsor"), "sponsorshipDate": p.get("sponsorshipDate"),
        "page": links.page(bioguide=p.get("bioguideId")),
    }


def shape_record(db: Session, congress: int, bill_id: str, raw: dict) -> dict:
    links = MemberLinks(db)
    bill = raw.get("bill") or {}
    summaries = sorted(raw.get("summaries") or [], key=lambda s: s.get("updateDate") or s.get("actionDate") or "")
    latest_summary = summaries[-1] if summaries else None
    votes = db.query(RollCall).filter(RollCall.bill_id == bill_id, RollCall.congress == congress) \
        .order_by(RollCall.date.desc(), RollCall.number.desc()).all()
    vote_summaries = []
    for rc in votes:
        positions = db.query(RollCallPosition).filter_by(roll_call_id=rc.id).all()
        vote_summaries.append({
            "chamber": rc.chamber, "session": rc.session, "number": rc.number, "date": rc.date,
            "question": rc.question, "title": rc.title, "result": rc.result, "rejected": rc.rejected,
            "yeas": rc.yeas, "nays": rc.nays, "present": rc.present, "notVoting": rc.not_voting,
            "parties": party_breakdown(positions),
        })
    return {
        "billId": bill_id,
        "billLabel": bill_label(bill_id),
        "congress": congress,
        "title": bill.get("title"),
        "introducedDate": bill.get("introducedDate"),
        "originChamber": bill.get("originChamber"),
        "policyArea": (bill.get("policyArea") or {}).get("name"),
        "latestAction": bill.get("latestAction"),
        "sponsors": [_person(p, links) for p in bill.get("sponsors") or []],
        "cosponsors": [_person(p, links) for p in raw.get("cosponsors") or []],
        "cboCostEstimates": [
            {"title": c.get("title"), "description": " ".join((c.get("description") or "").split()),
             "pubDate": c.get("pubDate"), "url": c.get("url")}
            for c in bill.get("cboCostEstimates") or []
        ],
        "summary": ({
            "actionDesc": latest_summary.get("actionDesc"), "actionDate": latest_summary.get("actionDate"),
            "paragraphs": summary_paragraphs(latest_summary.get("text") or ""),
        } if latest_summary else None),
        "actions": [
            {"date": a.get("actionDate"), "text": a.get("text"), "type": a.get("type"),
             "rollCalls": [{"chamber": (v.get("chamber") or "").lower(), "number": v.get("rollNumber"),
                            "session": v.get("sessionNumber")} for v in a.get("recordedVotes") or []]}
            for a in raw.get("actions") or []
        ],
        "textVersions": [
            {"type": t.get("type"), "date": (t.get("date") or "")[:10],
             "formats": {f.get("type"): f.get("url") for f in t.get("formats") or []}}
            for t in raw.get("text") or []
        ],
        "votes": vote_summaries,
        "days": bill_days(db, bill_id),
        "congressGovUrl": (
            f"https://www.congress.gov/bill/{congress}th-congress/"
            f"{_CONGRESS_GOV_TYPE.get(parse_bill_id(bill_id)[0], 'bill')}/{parse_bill_id(bill_id)[1]}"
        ),
        "unavailable": raw.get("unavailable") or [],
    }


_CONGRESS_GOV_TYPE = {
    "hr": "house-bill", "s": "senate-bill", "hres": "house-resolution", "sres": "senate-resolution",
    "hjres": "house-joint-resolution", "sjres": "senate-joint-resolution",
    "hconres": "house-concurrent-resolution", "sconres": "senate-concurrent-resolution",
}
