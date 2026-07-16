"""Justice scorecard pipeline: fetch votes from Oyez, analyze, and persist."""

import json
import logging
from collections import defaultdict

import httpx
from sqlalchemy.orm import Session

from app.pipeline.analyze.justice_analyzer import analyze_justice_votes
from app.pipeline.analyze.ollama_client import call_llm
from app.pipeline.fetch.justice_votes import fetch_case_votes, fetch_current_justices
from app.services.justice_service import upsert_justice

logger = logging.getLogger(__name__)


async def run_justice_pipeline(db: Session) -> dict:
    """Fetch, analyze, and persist Supreme Court justice scorecards.

    Returns summary dict with counts.
    """
    logger.info("=== Justice pipeline starting ===")

    async with httpx.AsyncClient(
        headers={"User-Agent": "Civitas/1.0 (civic-transparency-tool) httpx/0.27"},
        follow_redirects=True,
    ) as client:
        justices = await fetch_current_justices(client)
        if not justices:
            logger.warning("No justices found, aborting pipeline")
            return {"justices": 0, "votes": 0}

        all_votes = await fetch_case_votes(client)

    case_votes: dict[str, list[dict]] = defaultdict(list)
    justice_votes: dict[str, list[dict]] = defaultdict(list)
    for v in all_votes:
        case_votes[v["case_id"]].append(v)
        justice_votes[v["justice_id"]].append(v)

    # Comparison blocs are derived from appointing party (data), never
    # hand-coded membership sets — see the note in justice_analyzer.
    party_map = {j["id"]: j.get("appointing_party", "") for j in justices}

    for j in justices:
        jid = j["id"]
        jvotes = justice_votes.get(jid, [])

        analysis = analyze_justice_votes(
            justice_id=jid,
            appointing_party=j.get("appointing_party", ""),
            votes=jvotes,
            all_case_votes=dict(case_votes),
            party_map=party_map,
        )

        summary = _generate_summary(j, analysis, jvotes, db)

        record = {
            "id": jid,
            "name": j["name"],
            "last_name": j.get("last_name", ""),
            "role_title": j.get("role_title", "Associate Justice"),
            "appointing_president": j.get("appointing_president", ""),
            "appointing_party": j.get("appointing_party", ""),
            "date_start": j.get("date_start"),
            "date_end": j.get("date_end"),
            "is_active": j.get("is_active", True),
            "thumbnail_url": j.get("thumbnail_url", ""),
            "score_consistency": analysis["score_consistency"],
            "score_independence": analysis["score_independence"],
            "score_bipartisan_agreement": analysis["score_bipartisan_agreement"],
            "score_judicial_restraint": analysis["score_judicial_restraint"],
            "cases_decided": analysis["cases_decided"],
            "majority_pct": analysis["majority_pct"],
            "dissent_pct": analysis["dissent_pct"],
            "unanimous_pct": analysis["unanimous_pct"],
            "authored_majority": analysis["authored_majority"],
            "authored_dissent": analysis["authored_dissent"],
            "authored_concurrence": analysis["authored_concurrence"],
            "close_case_majority_pct": analysis["close_case_majority_pct"],
            "cross_bloc_pct": analysis["cross_bloc_pct"],
            "agreement_matrix": json.dumps(analysis["agreement_matrix"]),
            "summary": summary,
        }

        upsert_justice(db, record, jvotes)
        logger.info(
            "  %s: consistency=%.1f, independence=%.1f, cases=%d",
            j["name"],
            analysis["score_consistency"],
            analysis["score_independence"],
            analysis["cases_decided"],
        )

    db.commit()
    logger.info("=== Justice pipeline complete: %d justices, %d votes ===", len(justices), len(all_votes))
    return {"justices": len(justices), "votes": len(all_votes)}


def _generate_summary(
    justice: dict,
    analysis: dict,
    votes: list[dict],
    db: Session,
) -> str:
    """Generate a narrative summary for a justice using the LLM.

    Feeds the LLM pre-computed statistics and notable case names so it
    can produce a natural-language profile without hallucinating data.
    """
    name = justice.get("name", "")
    party = justice.get("appointing_party", "?")
    president = justice.get("appointing_president", "unknown")
    role = justice.get("role_title", "Associate Justice")

    notable_cases = _select_notable_cases(votes)
    case_lines = "\n".join(
        f"- {c['case_name']} ({c['case_term']}): voted {c['vote']}, "
        f"{c['opinion_type']} opinion, {c['majority_votes']}-{c['minority_votes']} decision"
        for c in notable_cases
    ) or "No notable cases available."

    agreement = analysis.get("agreement_matrix", {})
    top_agree = sorted(agreement.items(), key=lambda x: x[1], reverse=True)[:3]
    low_agree = sorted(agreement.items(), key=lambda x: x[1])[:3]
    agree_lines = ", ".join(f"{k} ({v}%)" for k, v in top_agree) if top_agree else "N/A"
    disagree_lines = ", ".join(f"{k} ({v}%)" for k, v in low_agree) if low_agree else "N/A"

    prompt = (
        f"Justice {name}, {role}. Appointed by {president} ({party}).\n\n"
        f"VOTING STATISTICS (pre-computed, do NOT recalculate):\n"
        f"- Cases decided: {analysis['cases_decided']}\n"
        f"- Majority: {analysis['majority_pct']:.1f}%\n"
        f"- Dissent: {analysis['dissent_pct']:.1f}%\n"
        f"- Unanimous cases: {analysis['unanimous_pct']:.1f}%\n"
        f"- Cross-bloc rate: {analysis['cross_bloc_pct']:.1f}%\n"
        f"- Close-case majority: {analysis['close_case_majority_pct']:.1f}%\n"
        f"- Authored: {analysis['authored_majority']} majority, "
        f"{analysis['authored_dissent']} dissent, {analysis['authored_concurrence']} concurrence\n\n"
        f"SCORES (pre-computed, do NOT recalculate):\n"
        f"- Ideological Consistency: {analysis['score_consistency']:.1f}/100\n"
        f"- Independence: {analysis['score_independence']:.1f}/100\n"
        f"- Bipartisan Agreement: {analysis['score_bipartisan_agreement']:.1f}/100\n"
        f"- Judicial Restraint: {analysis['score_judicial_restraint']:.1f}/100\n\n"
        f"HIGHEST AGREEMENT: {agree_lines}\n"
        f"LOWEST AGREEMENT: {disagree_lines}\n\n"
        f"NOTABLE CASES:\n{case_lines}\n\n"
        f"Write a 3-4 sentence analytical summary of this justice's jurisprudential profile. "
        f"Focus on their independence from ideological expectations, voting patterns in "
        f"close decisions, and any notable patterns in opinion authorship. "
        f"Use the statistics above — do NOT invent or recalculate numbers. "
        f"Be factual, analytical, and non-partisan.\n\n"
        f'Return JSON: {{"summary": "your 3-4 sentence summary here"}}'
    )

    result = call_llm(
        prompt_version="justice-summary-v1",
        system_prompt=(
            "You are a legal analyst writing concise Supreme Court justice profiles "
            "for a civic transparency platform. Rules:\n"
            "1. Use ONLY the pre-computed statistics provided — never invent numbers.\n"
            "2. Be analytical, not editorializing. Describe patterns, not judgments.\n"
            "3. Reference specific case names when relevant.\n"
            "4. Write in third person. No bullet points — flowing prose only.\n"
            "5. Return valid JSON with a single 'summary' key."
        ),
        user_prompt=prompt,
        cache_key={
            "justiceId": justice.get("id", ""),
            "cases": analysis["cases_decided"],
            "consistency": round(analysis["score_consistency"], 1),
            "independence": round(analysis["score_independence"], 1),
            "v": 1,
        },
        db_session=db,
        max_tokens=400,
        num_ctx=2048,
    )

    if result and isinstance(result, dict):
        text = str(result.get("summary") or result.get("text") or "")
        if len(text) > 20:
            return text[:600]

    if result and isinstance(result, str) and len(result) > 20:
        return result[:600]

    logger.info("LLM summary unavailable for %s, using template fallback", name)
    return _fallback_summary(justice, analysis)


def _select_notable_cases(votes: list[dict], max_cases: int = 8) -> list[dict]:
    """Pick the most analytically interesting cases for the LLM prompt.

    Prioritizes close decisions, dissents, and authored opinions.
    """
    scored: list[tuple[float, dict]] = []
    for v in votes:
        score = 0.0
        if v.get("is_close"):
            score += 3.0
        if v.get("vote") == "minority":
            score += 2.0
        if v.get("opinion_type") in ("majority", "dissent"):
            score += 2.0
        if v.get("opinion_type") == "concurrence":
            score += 1.0
        if not v.get("is_unanimous"):
            score += 1.0
        if v.get("case_name"):
            scored.append((score, v))

    scored.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    result: list[dict] = []
    for _, v in scored:
        cid = v["case_id"]
        if cid in seen:
            continue
        seen.add(cid)
        result.append(v)
        if len(result) >= max_cases:
            break
    return result


def _fallback_summary(justice: dict, analysis: dict) -> str:
    """Template-based fallback when the LLM is unavailable."""
    name = justice.get("last_name") or justice.get("name", "")
    xb = analysis["cross_bloc_pct"]
    ind = analysis["score_independence"]
    dis = analysis["dissent_pct"]
    maj = analysis["majority_pct"]

    lines = [f"Justice {name}:"]

    if ind >= 50:
        lines.append(
            f"Demonstrates strong jurisprudential independence — crosses "
            f"expected ideological lines in {xb:.0f}% of split decisions."
        )
    elif ind >= 25:
        lines.append(
            f"Shows moderate independence from appointing-party bloc — "
            f"breaks ranks in {xb:.0f}% of split decisions."
        )
    elif ind >= 10:
        lines.append(
            f"Largely votes with expected ideological bloc, with occasional "
            f"crossover ({xb:.0f}% of split decisions)."
        )
    else:
        lines.append(
            f"Voting pattern is highly predictable along ideological lines "
            f"({xb:.0f}% cross-bloc rate)."
        )

    if dis > 25:
        lines.append(f"Frequent dissenter ({dis:.0f}% of cases).")
    elif maj > 90:
        lines.append(f"In the majority {maj:.0f}% of the time.")

    return " ".join(lines)
