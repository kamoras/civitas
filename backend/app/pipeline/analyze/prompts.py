"""
All LLM prompt templates for the pipeline.
Each function returns a dict with promptVersion, systemPrompt, userPrompt.
"""

import json
from typing import Any


def bill_classification_prompt(bill: dict) -> dict:
    summary_line = f"Summary: {bill.get('summary', '')}" if bill.get("summary") else ""
    full_text_line = (
        f"Full text (excerpt): {bill['fullText'][:4000]}"
        if bill.get("fullText")
        else ""
    )

    return {
        "promptVersion": "bill-classify-v1",
        "systemPrompt": (
            "You are a nonpartisan congressional analyst. Classify bills by their "
            "corporate vs. consumer impact. Be factual and balanced. Return ONLY valid "
            "JSON with no additional text."
        ),
        "userPrompt": f"""Analyze this bill and return a JSON object:

Bill: {bill['billId']} - {bill['billName']}
Congress: {bill['congress']}
{summary_line}
{full_text_line}

Return this exact JSON structure:
{{
  "billId": "{bill['billId']}",
  "billName": "{bill['billName']}",
  "congress": {bill['congress']},
  "date": "<date of final Senate vote if known, or empty string>",
  "description": "<1-2 sentence neutral description of what the bill does>",
  "corporateInterest": "<1-2 sentences: which industries had a stake and why>",
  "publicImpact": "<1-2 sentences: concrete impact on ordinary people>",
  "affectedIndustries": ["<IndustryCode values from: PHARMA, INSURANCE, OIL_GAS, DEFENSE, FINANCE, REAL_ESTATE, TECH, TELECOM, AGRIBUSINESS, ENERGY, CONSTRUCTION, TRANSPORT, LAWYERS, LOBBYISTS, GAMBLING, GUNS, TOBACCO, CRYPTO, PRIVATE_PRISON, OTHER>"],
  "classification": "<one of: pro-corporate, pro-consumer, mixed>"
}}""",
    }


def cross_reference_prompt(senator: dict, donors: list, key_votes: list) -> dict:
    votes_data = [
        {
            "billId": v["billId"],
            "billName": v["billName"],
            "vote": v["vote"],
            "corporateInterest": v.get("corporateInterest", ""),
            "affectedIndustries": v.get("affectedIndustries", []),
        }
        for v in key_votes
    ]

    return {
        "promptVersion": "cross-ref-v1",
        "systemPrompt": (
            "You are a factual campaign finance analyst. Given a senator's donor list "
            "and their votes on key bills, identify connections between donors and "
            "relevant votes. Be strictly factual \u2014 correlation is not causation. Only "
            "identify connections where a donor's industry directly relates to a bill's "
            "subject matter. Return ONLY valid JSON."
        ),
        "userPrompt": f"""Senator: {senator['name']} ({senator['party']}-{senator['state']})

Top donors:
{json.dumps(donors, indent=2)}

Key votes:
{json.dumps(votes_data, indent=2)}

For each key vote, identify which donors (if any) have a direct industry connection to the bill. Return a JSON array:
[
  {{
    "billId": "<bill ID>",
    "relevantDonors": ["<donor names from the list above that are in affected industries>"],
    "relevantDonorTotal": <sum of those donors' contributions>,
    "explanation": "<1 factual sentence about the connection, if any>"
  }}
]

Only include donors whose industry directly relates to the bill. If no donors are relevant to a bill, use empty arrays and 0. Do not invent connections.""",
    }


def lobbying_match_prompt(senator: dict, donors: list, key_votes: list) -> dict:
    votes_data = [
        {
            "billId": v["billId"],
            "billName": v["billName"],
            "vote": v["vote"],
            "corporateInterest": v.get("corporateInterest", ""),
        }
        for v in key_votes[:10]
    ]

    return {
        "promptVersion": "lobbying-match-v1",
        "systemPrompt": (
            "You are a factual lobbying analyst. Given a senator's top donors and their "
            "votes on key bills, generate lobbying match records that show the "
            "relationship between donations and legislative activity. Be neutral and "
            "factual. Correlation does not imply causation. Return ONLY valid JSON."
        ),
        "userPrompt": f"""Senator: {senator['name']} ({senator['party']}-{senator['state']})

Top donors:
{json.dumps(donors[:8], indent=2)}

Key votes:
{json.dumps(votes_data, indent=2)}

Generate 2-4 lobbying match records for the most notable donor-vote relationships. Return a JSON array:
[
  {{
    "lobbyistOrg": "<donor/org name from the donor list>",
    "industry": "<IndustryCode: PHARMA, OIL_GAS, FINANCE, DEFENSE, TECH, etc.>",
    "lobbyingSpend": <estimated lobbying spend based on org size \u2014 use realistic figures>,
    "donationToSenator": <actual donation amount from the donor list>,
    "billsInfluenced": ["<bill IDs from the key votes list>"],
    "senatorVoteAligned": <true if senator voted in the direction the org would prefer, false otherwise>,
    "description": "<2-3 factual sentences describing the org's lobbying interest, donation, and the senator's vote. Do not imply causation.>"
  }}
]

Only use donors and bills from the data provided. Do not fabricate organizations or amounts.""",
    }


def flip_flop_prompt(senator: dict, key_votes: list) -> dict:
    votes_data = [
        {
            "billId": v["billId"],
            "billName": v["billName"],
            "vote": v["vote"],
            "description": v.get("description", ""),
        }
        for v in key_votes
    ]

    return {
        "promptVersion": "flipflop-v1",
        "systemPrompt": (
            'You analyze legislative consistency. A "flip-flop" means voting '
            "differently on substantially similar legislation across sessions, or "
            "publicly advocating one position while voting the opposite way. Be fair "
            "\u2014 changing one's mind based on new evidence is not necessarily a "
            "flip-flop. Return ONLY valid JSON."
        ),
        "userPrompt": f"""Senator: {senator['name']} ({senator['party']}-{senator['state']}), {senator.get('yearsInOffice', 0)} years in office

Voting record on key bills:
{json.dumps(votes_data, indent=2)}

Analyze for consistency and return:
{{
  "flipFlopScore": <0-100, where 0 is perfectly consistent and 100 is completely inconsistent>,
  "examples": ["<brief factual description of any inconsistencies found>"],
  "reasoning": "<1-2 sentences explaining the score>"
}}

If there are no clear inconsistencies, return a low score with an empty examples array.""",
    }


def nickname_prompt(senator: dict) -> dict:
    funding = senator.get("funding", {})
    industry_breakdown = funding.get("industryBreakdown", [])
    top_industry = industry_breakdown[0]["name"] if industry_breakdown else "Unknown"
    pac_funding = funding.get("totalFromPACs")
    pac_str = f"${pac_funding / 1_000_000:.1f}M" if pac_funding else "Unknown"
    small_donor_pct = funding.get("smallDonorPercentage", 0)
    corp_score = senator.get("representationScore", senator.get("corruptionScore", {})).get("constituentFunding", 0)

    return {
        "promptVersion": "nickname-v1",
        "systemPrompt": (
            "You are a punk zine editor creating factual but edgy nicknames for "
            "politicians. Nicknames should be 2-4 words, reference the senator's "
            "actual data (top industry, notable votes, years in office), and be "
            "irreverent but not libelous. Think punk rock, not defamation."
        ),
        "userPrompt": f"""Generate a punk nickname for this senator:

Name: {senator['name']} ({senator['party']}-{senator['state']})
Years in office: {senator.get('yearsInOffice', 0)}
Top funding industry: {top_industry}
PAC funding: {pac_str}
Small donor %: {small_donor_pct}%
Corporate influence score: {corp_score}/100

Return: {{ "punkNickname": "<2-4 word nickname>" }}""",
    }


def industry_classification_prompt(org_names: list[str]) -> dict:
    org_list = "\n".join(f'{i + 1}. "{name}"' for i, name in enumerate(org_names))

    return {
        "promptVersion": "industry-classify-v1",
        "systemPrompt": (
            "Classify each organization into exactly one industry code. Be accurate. "
            "If uncertain, use OTHER. Return ONLY valid JSON."
        ),
        "userPrompt": f"""Classify these organizations into industry codes.

Valid codes: PHARMA, INSURANCE, OIL_GAS, DEFENSE, FINANCE, REAL_ESTATE, TECH, TELECOM, AGRIBUSINESS, ENERGY, CONSTRUCTION, TRANSPORT, LAWYERS, LOBBYISTS, GAMBLING, GUNS, TOBACCO, CRYPTO, PRIVATE_PRISON, OTHER

Organizations:
{org_list}

Return a JSON array:
[{{"name": "<org name>", "industry": "<IndustryCode>"}}]""",
    }
