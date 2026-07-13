"""
LLM prompt templates for the pipeline.
Each function returns a dict with promptVersion, systemPrompt, userPrompt.
"""


def explore_document_summary_prompt(doc: dict) -> dict:
    title = doc.get("title", "")
    body = doc.get("body", "")[:2000]
    doc_type = doc.get("doc_type", "document")
    chamber = doc.get("chamber", "")
    politician = doc.get("politician_name", "")
    date = doc.get("date", "")

    return {
        "promptVersion": "explore-doc-summary-v3",
        "systemPrompt": (
            "You are a nonpartisan civic analyst summarizing government documents "
            "for a public transparency platform. Rules:\n"
            "1. Summarize what the document DOES — its purpose, provisions, and scope.\n"
            "2. Every sentence must add information not already visible in the title.\n"
            "3. Never restate the document title or type.\n"
            "4. Be factual — no editorializing, no filler.\n"
            "5. Focus on substance: what changes, who is affected, and why it matters.\n"
            "Return ONLY valid JSON."
        ),
        "userPrompt": f"""Summarize this government document for a citizen.

Document type: {doc_type}
Chamber: {chamber}
Date: {date}
Speaker/Author: {politician}
Title: {title}

Content:
{body}

Return JSON:
{{
  "summary": "<2-3 sentences: what does this document do? What is its purpose and substance?>",
  "keyPoints": ["<specific factual point from the content>", "<another specific point>"],
  "impact": "<1 sentence: concrete consequence for ordinary people, or empty string if none>"
}}""",
    }

def promise_evidence_gate_prompt(promise_text: str, candidate_text: str, candidate_kind: str) -> dict:
    """Judge whether a borderline-similarity vote/bill is genuine evidence for a promise.

    Used only in the embedding-similarity gray zone: below this, generic
    legislative-register text of any topic already clears a high bar
    purely by shared vocabulary (see policy_alignment.py calibration
    notes), so a fixed threshold alone can't separate "genuinely relates
    to this promise" from "written in the same bureaucratic register."
    """
    return {
        "promptVersion": "promise-evidence-gate-v1",
        "systemPrompt": (
            "You are a political data scientist checking evidence quality. "
            "Respond in JSON only."
        ),
        "userPrompt": f"""Campaign promise:
"{promise_text}"

Candidate {candidate_kind} (possible evidence):
"{candidate_text[:500]}"

Does this {candidate_kind} genuinely relate to whether this specific promise was kept? \
Superficial overlap (both are legislation, both use similar bureaucratic phrasing) is NOT \
enough — the subject matter must actually match the promise's substance.

Return JSON: {{"relates": true/false, "reason": "one short sentence"}}""",
    }


def promise_decomposition_prompt(promise_text: str) -> dict:
    return {
        "promptVersion": "promise-decomp-v1",
        "systemPrompt": (
            "You are a political data scientist. Your task is to decompose a "
            "vague campaign promise into specific, searchable policy sub-topics "
            "and keywords. These will be used for embedding-based retrieval of "
            "related legislative votes.\n"
            "Rules:\n"
            "1. Identify the core policy area (e.g., Healthcare, Energy).\n"
            "2. Generate 3-5 granular sub-topics (e.g., 'prescription drug prices').\n"
            "3. Generate a list of 5-10 specific technical keywords or bill title "
            "fragments likely to appear in related legislation.\n"
            "Return ONLY valid JSON."
        ),
        "userPrompt": f"""Decompose this campaign promise:
"{promise_text}"

Return JSON:
{{
  "category": "<Main Category>",
  "subTopics": ["<sub-topic 1>", "<sub-topic 2>", "..."],
  "keywords": ["<word1>", "<word2>", "..."],
  "searchQuery": "<An optimized 1-sentence description of the promise's intent for embedding similarity>"
}}""",
    }
