"""Action Center API — serves daily civic action issues."""

import asyncio
import json
import logging
import threading

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from datetime import date

from app.api.admin import require_admin
from app.database import get_db
from app.models import (
    ActionIssue, DailyTheme, ExploreDocument, NationalMonitor, MonitorUpdate, Senator,
)
from app.schemas import (
    ActionIssueSchema, ActionItemSchema, RelatedBillSchema,
    RelatedExploreDoc, RelatedSenator,
    NationalMonitorSchema, NationalMonitorDetailSchema, MonitorUpdateSchema,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/action")


def _parse_json_field(raw: str, default: list | None = None) -> list:
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else (default or [])
    except (json.JSONDecodeError, TypeError):
        return default or []


def _build_issue_response(issue: ActionIssue, db: Session) -> dict:
    explore_ids = _parse_json_field(issue.related_explore_ids)
    related_docs: list[dict] = []
    if explore_ids:
        docs = (
            db.query(ExploreDocument.id, ExploreDocument.title,
                     ExploreDocument.doc_type, ExploreDocument.date,
                     ExploreDocument.url)
            .filter(ExploreDocument.id.in_(explore_ids))
            .all()
        )
        related_docs = [
            RelatedExploreDoc(
                id=d.id, title=d.title, doc_type=d.doc_type,
                date=d.date, url=d.url,
            ).model_dump(by_alias=True)
            for d in docs
        ]

    senator_data = _parse_json_field(getattr(issue, "related_senators", "[]"))
    related_senators = [
        RelatedSenator(**s).model_dump(by_alias=True)
        for s in senator_data if isinstance(s, dict) and s.get("id")
    ]

    raw_actions = _parse_json_field(issue.actions)
    action_items: list[dict] = []
    for a in raw_actions:
        if isinstance(a, dict) and "text" in a:
            action_items.append(
                ActionItemSchema(
                    text=a["text"],
                    type=a.get("type", "general"),
                    url=a.get("url"),
                ).model_dump(by_alias=True)
            )
        elif isinstance(a, str):
            action_items.append(
                ActionItemSchema(text=a, type="general").model_dump(by_alias=True)
            )

    raw_bills = _parse_json_field(issue.related_bill_ids)
    related_bills: list[dict] = []
    for b in raw_bills:
        if isinstance(b, dict) and b.get("id") and b.get("url"):
            related_bills.append(
                RelatedBillSchema(
                    name=b.get("name", b["id"]),
                    id=b["id"],
                    url=b["url"],
                ).model_dump(by_alias=True)
            )

    return ActionIssueSchema(
        id=issue.id,
        date=issue.date,
        rank=issue.rank,
        title=issue.title,
        summary=issue.summary,
        facts=_parse_json_field(issue.facts),
        actions=action_items,
        source_urls=_parse_json_field(issue.source_urls),
        source_names=_parse_json_field(issue.source_names),
        policy_areas=_parse_json_field(issue.policy_areas),
        related_bills=related_bills,
        related_explore_docs=related_docs,
        related_senators=related_senators,
    ).model_dump(by_alias=True)


@router.get("/issues")
async def get_action_issues(
    date: str | None = Query(None, description="Date in YYYY-MM-DD format; defaults to most recent"),
    db: Session = Depends(get_db),
):
    """Return the current day's action issues (or most recent available)."""
    if date:
        issues = (
            db.query(ActionIssue)
            .filter(ActionIssue.date == date)
            .order_by(ActionIssue.rank)
            .all()
        )
    else:
        latest_date = (
            db.query(ActionIssue.date)
            .order_by(ActionIssue.date.desc())
            .limit(1)
            .scalar()
        )
        if not latest_date:
            return {"date": None, "issues": []}
        issues = (
            db.query(ActionIssue)
            .filter(ActionIssue.date == latest_date)
            .order_by(ActionIssue.rank)
            .all()
        )

    if not issues:
        return {"date": date, "issues": [], "theme": None}

    issue_date = issues[0].date
    theme_row = db.query(DailyTheme).filter(DailyTheme.date == issue_date).first()
    theme = None
    if theme_row:
        try:
            theme = json.loads(theme_row.theme_json)
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "date": issue_date,
        "issues": [_build_issue_response(i, db) for i in issues],
        "theme": theme,
    }


_BRANCH_CHAMBERS = {
    "senate": ["Senate"],
    "house": ["House"],
    "executive": ["Executive", "Regulatory"],
}


@router.get("/recent/{branch}")
async def get_recent_by_branch(
    branch: str,
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Return the most recent explore documents for a government branch."""
    chambers = _BRANCH_CHAMBERS.get(branch.lower())
    if not chambers:
        raise HTTPException(400, f"Unknown branch: {branch}. Use senate, house, or executive.")

    docs = (
        db.query(
            ExploreDocument.id, ExploreDocument.title, ExploreDocument.doc_type,
            ExploreDocument.date, ExploreDocument.url, ExploreDocument.chamber,
            ExploreDocument.summary, ExploreDocument.politician_name,
        )
        .filter(ExploreDocument.chamber.in_(chambers))
        .order_by(ExploreDocument.date.desc())
        .limit(limit * 3)
        .all()
    )

    seen_titles: set[str] = set()
    results: list[dict] = []
    for d in docs:
        key = d.title.strip().lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        results.append({
            "id": d.id,
            "title": d.title,
            "docType": d.doc_type,
            "date": d.date,
            "url": d.url or "",
            "chamber": d.chamber,
            "summary": (d.summary or "")[:300],
            "politicianName": d.politician_name or "",
        })
        if len(results) >= limit:
            break

    return {"branch": branch, "documents": results, "count": len(results)}


@router.get("/country-news")
async def get_country_news():
    """Return recent news articles grouped by country mentioned."""
    from app.pipeline.fetch.news_feeds import fetch_news_articles

    articles = fetch_news_articles()
    countries = _extract_country_mentions(articles)
    return {"countries": countries}


def _extract_country_mentions(articles: list) -> list[dict]:
    """Group articles by country mentions using simple name matching."""
    _COUNTRIES: dict[str, dict] = {
        "China": {"lat": 35.86, "lng": 104.19},
        "Russia": {"lat": 61.52, "lng": 105.32},
        "Ukraine": {"lat": 48.38, "lng": 31.17},
        "Iran": {"lat": 32.43, "lng": 53.69},
        "Israel": {"lat": 31.05, "lng": 34.85},
        "North Korea": {"lat": 40.34, "lng": 127.51},
        "South Korea": {"lat": 35.91, "lng": 127.77},
        "Taiwan": {"lat": 23.70, "lng": 120.96},
        "Japan": {"lat": 36.20, "lng": 138.25},
        "India": {"lat": 20.59, "lng": 78.96},
        "Mexico": {"lat": 23.63, "lng": -102.55},
        "Canada": {"lat": 56.13, "lng": -106.35},
        "United Kingdom": {"lat": 55.38, "lng": -3.44},
        "Germany": {"lat": 51.17, "lng": 10.45},
        "France": {"lat": 46.23, "lng": 2.21},
        "Brazil": {"lat": -14.24, "lng": -51.93},
        "Saudi Arabia": {"lat": 23.89, "lng": 45.08},
        "Turkey": {"lat": 38.96, "lng": 35.24},
        "Australia": {"lat": -25.27, "lng": 133.78},
        "Iraq": {"lat": 33.22, "lng": 43.68},
        "Syria": {"lat": 34.80, "lng": 38.99},
        "Afghanistan": {"lat": 33.94, "lng": 67.71},
        "Pakistan": {"lat": 30.38, "lng": 69.35},
        "Cuba": {"lat": 21.52, "lng": -77.78},
        "Venezuela": {"lat": 6.42, "lng": -66.59},
        "Poland": {"lat": 51.92, "lng": 19.15},
        "Italy": {"lat": 41.87, "lng": 12.57},
        "Spain": {"lat": 40.46, "lng": -3.75},
        "Nigeria": {"lat": 9.08, "lng": 8.68},
        "Egypt": {"lat": 26.82, "lng": 30.80},
        "South Africa": {"lat": -30.56, "lng": 22.94},
        "Colombia": {"lat": 4.57, "lng": -74.30},
        "Philippines": {"lat": 12.88, "lng": 121.77},
        "Vietnam": {"lat": 14.06, "lng": 108.28},
        "Indonesia": {"lat": -0.79, "lng": 113.92},
        "Thailand": {"lat": 15.87, "lng": 100.99},
        "Myanmar": {"lat": 21.91, "lng": 95.96},
        "Ethiopia": {"lat": 9.15, "lng": 40.49},
        "Kenya": {"lat": -0.02, "lng": 37.91},
        "Argentina": {"lat": -38.42, "lng": -63.62},
        "Chile": {"lat": -35.68, "lng": -71.54},
        "Peru": {"lat": -9.19, "lng": -75.02},
        "Palestine": {"lat": 31.95, "lng": 35.23},
        "Lebanon": {"lat": 33.85, "lng": 35.86},
        "Yemen": {"lat": 15.55, "lng": 48.52},
        "Somalia": {"lat": 5.15, "lng": 46.20},
        "Sudan": {"lat": 12.86, "lng": 30.22},
        "Libya": {"lat": 26.34, "lng": 17.23},
        "Haiti": {"lat": 18.97, "lng": -72.29},
        "Honduras": {"lat": 15.20, "lng": -86.24},
        "Guatemala": {"lat": 15.78, "lng": -90.23},
        "El Salvador": {"lat": 13.79, "lng": -88.90},
    }

    _ALIASES: dict[str, str] = {
        "UK": "United Kingdom", "Britain": "United Kingdom",
        "DPRK": "North Korea", "Pyongyang": "North Korea",
        "ROK": "South Korea", "Seoul": "South Korea",
        "Beijing": "China", "Chinese": "China",
        "Russian": "Russia", "Moscow": "Russia", "Kremlin": "Russia",
        "Iranian": "Iran", "Tehran": "Iran",
        "Israeli": "Israel", "Gaza": "Palestine", "West Bank": "Palestine",
        "Palestinian": "Palestine", "Taipei": "Taiwan", "Taiwanese": "Taiwan",
        "Kyiv": "Ukraine", "Ukrainian": "Ukraine",
        "Mexican": "Mexico",
    }

    from collections import defaultdict
    country_articles: dict[str, list[dict]] = defaultdict(list)

    for article in articles:
        text = f"{article.title} {article.summary}"
        for name, coords in _COUNTRIES.items():
            if name in text:
                country_articles[name].append({
                    "title": article.title,
                    "url": article.url,
                    "source": article.source_name,
                    "date": article.published.isoformat() if article.published else "",
                })
        for alias, canonical in _ALIASES.items():
            if alias in text and canonical not in [a.get("_matched") for a in country_articles.get(canonical, [])]:
                country_articles[canonical].append({
                    "title": article.title,
                    "url": article.url,
                    "source": article.source_name,
                    "date": article.published.isoformat() if article.published else "",
                })

    results: list[dict] = []
    for name, arts in country_articles.items():
        seen: set[str] = set()
        unique = []
        for a in arts:
            if a["title"] not in seen:
                seen.add(a["title"])
                unique.append(a)
        coords = _COUNTRIES[name]
        results.append({
            "country": name,
            "lat": coords["lat"],
            "lng": coords["lng"],
            "articleCount": len(unique),
            "articles": unique[:5],
        })

    results.sort(key=lambda c: c["articleCount"], reverse=True)
    return results


def _next_election_day(after: date) -> date:
    """Compute next federal election day (first Tue after first Mon in Nov, even years)."""
    year = after.year if after.month <= 10 else after.year + 1
    if year % 2 != 0:
        year += 1
    while True:
        nov1 = date(year, 11, 1)
        first_monday = nov1.day + (7 - nov1.weekday()) % 7
        if nov1.weekday() == 0:
            first_monday = 1
        election_day = date(year, 11, first_monday + 1)
        if election_day > after:
            return election_day
        year += 2


_CLASS_II_STATES = {
    "AK", "AL", "AR", "CO", "DE", "GA", "ID", "IL", "IA", "KS",
    "KY", "LA", "ME", "MA", "MI", "MN", "MS", "MT", "NE", "NH",
    "NJ", "NM", "NC", "OK", "OR", "RI", "SC", "SD", "TN", "TX",
    "VA", "WV", "WY",
}
_CLASS_III_STATES = {
    "AK", "AL", "AZ", "CA", "CO", "CT", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "MD", "MO", "NV", "NH",
    "NY", "NC", "ND", "OH", "OK", "OR", "PA", "SC", "SD", "UT",
    "VT", "WA", "WI",
}
_CLASS_I_STATES = {
    "AZ", "CA", "CT", "DE", "FL", "HI", "IN", "ME", "MD", "MA",
    "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NJ", "NM", "NY",
    "ND", "OH", "PA", "RI", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
}


def _seats_up_for_year(year: int) -> set[str]:
    """Return set of state abbreviations with Senate seats up in given year."""
    if (year - 2020) % 6 == 0:
        return _CLASS_II_STATES
    if (year - 2022) % 6 == 0:
        return _CLASS_III_STATES
    if (year - 2018) % 6 == 0:
        return _CLASS_I_STATES
    return set()


_HOUSE_DISTRICTS: dict[str, int] = {
    "AL": 7, "AK": 1, "AZ": 9, "AR": 4, "CA": 52, "CO": 8, "CT": 5,
    "DE": 1, "FL": 28, "GA": 14, "HI": 2, "ID": 2, "IL": 17, "IN": 9,
    "IA": 4, "KS": 4, "KY": 6, "LA": 6, "ME": 2, "MD": 8, "MA": 9,
    "MI": 13, "MN": 8, "MS": 4, "MO": 8, "MT": 2, "NE": 3, "NV": 4,
    "NH": 2, "NJ": 12, "NM": 3, "NY": 26, "NC": 14, "ND": 1, "OH": 15,
    "OK": 5, "OR": 6, "PA": 17, "RI": 2, "SC": 7, "SD": 1, "TN": 9,
    "TX": 38, "UT": 4, "VT": 1, "VA": 11, "WA": 10, "WV": 2, "WI": 8,
    "WY": 1,
}


@router.get("/elections")
async def get_election_info(db: Session = Depends(get_db)):
    """Return upcoming election info: dates, senate races, state data."""
    today = date.today()
    election_day = _next_election_day(today)
    days_until = (election_day - today).days
    el_year = election_day.year
    is_presidential = el_year % 4 == 0
    is_election_day = days_until == 0
    is_election_season = days_until <= 60

    seats_up = _seats_up_for_year(el_year)
    all_house = True

    senators = (
        db.query(Senator.id, Senator.name, Senator.state, Senator.party,
                 Senator.score_funding_independence, Senator.score_promise_persistence,
                 Senator.score_independent_voting, Senator.score_funding_diversity,
                 Senator.leadership_score, Senator.years_in_office)
        .all()
    )

    by_state: dict[str, list[dict]] = {}
    for s in senators:
        overall = round(
            (s.score_funding_independence + s.score_promise_persistence +
             s.score_independent_voting + s.score_funding_diversity) / 4, 1
        )
        entry = {
            "id": s.id, "name": s.name, "party": s.party,
            "overallScore": overall,
            "leadershipScore": round(s.leadership_score, 1) if s.leadership_score else None,
            "yearsInOffice": s.years_in_office,
            "upForElection": s.state in seats_up,
        }
        by_state.setdefault(s.state, []).append(entry)

    all_state_codes = set(by_state.keys()) | set(_HOUSE_DISTRICTS.keys())
    states: list[dict] = []
    for code in sorted(all_state_codes):
        sens = by_state.get(code, [])
        has_race = code in seats_up
        districts = _HOUSE_DISTRICTS.get(code, 0)
        states.append({
            "state": code,
            "hasSenateRace": has_race,
            "hasHouseRace": districts > 0,
            "houseDistricts": districts,
            "senators": sens,
        })

    return {
        "nextElection": {
            "date": election_day.isoformat(),
            "type": "Presidential General Election" if is_presidential
                    else "Midterm General Election",
            "year": el_year,
            "daysUntil": days_until,
            "isElectionDay": is_election_day,
            "isElectionSeason": is_election_season,
        },
        "senateSeatsUp": len(seats_up),
        "houseSeatsUp": 435,
        "states": states,
    }


@router.post("/refresh", dependencies=[Depends(require_admin)])
async def refresh_action_center(db: Session = Depends(get_db)):
    """Trigger an action center refresh (admin only). Runs in background."""
    from app.pipeline.analyze.action_center import refresh_action_issues

    def _run():
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(asyncio.sleep(0))
            refresh_action_issues()
        except Exception:
            logger.exception("Action center refresh failed")
        finally:
            loop.close()

    threading.Thread(target=_run, daemon=True, name="action-refresh").start()
    return {"message": "Action center refresh triggered"}


def _monitor_to_schema(m: NationalMonitor, include_updates: bool = False):
    """Convert a NationalMonitor ORM object to a schema dict."""
    base = NationalMonitorSchema(
        id=m.id,
        slug=m.slug,
        title=m.title,
        description=m.description,
        category=m.category,
        status=m.status,
        policy_areas=_parse_json_field(m.policy_areas),
        created_at=m.created_at.strftime("%Y-%m-%d"),
        updated_at=m.updated_at.strftime("%Y-%m-%d"),
        last_article_date=m.last_article_date,
        update_count=len(m.updates) if m.updates else 0,
    )
    if not include_updates:
        return base.model_dump(by_alias=True)

    updates = [
        MonitorUpdateSchema(
            id=u.id,
            date=u.date,
            summary=u.summary,
            source_url=u.source_url,
            source_name=u.source_name,
            article_title=u.article_title,
        ).model_dump(by_alias=True)
        for u in (m.updates or [])
    ]
    detail = NationalMonitorDetailSchema(
        **{k: getattr(base, k) for k in base.model_fields},
        updates=updates,
    )
    return detail.model_dump(by_alias=True)


@router.get("/monitors")
async def list_monitors(db: Session = Depends(get_db)):
    """List all active and watching national monitors."""
    monitors = (
        db.query(NationalMonitor)
        .filter(NationalMonitor.status.in_(["active", "watching"]))
        .order_by(NationalMonitor.updated_at.desc())
        .all()
    )
    return {"monitors": [_monitor_to_schema(m) for m in monitors]}


@router.get("/monitors/{slug}")
async def get_monitor(slug: str, db: Session = Depends(get_db)):
    """Get full detail for a national monitor including timeline."""
    monitor = (
        db.query(NationalMonitor)
        .filter(NationalMonitor.slug == slug)
        .first()
    )
    if not monitor:
        raise HTTPException(status_code=404, detail="Monitor not found")
    return _monitor_to_schema(monitor, include_updates=True)
