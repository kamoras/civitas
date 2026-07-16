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
    """Classify how a vote/bill relates to whether a campaign promise was kept.

    Single combined judgment covering BOTH relevance and fulfillment degree,
    rather than two separate questions — "is this related" and "does this
    fulfill it" aren't independent (a candidate that isn't related trivially
    doesn't fulfill anything either), and asking a small local model two
    separate binary questions about the same text pair risks inconsistent
    answers across the two calls. One 5-way classification is a more
    natural task and only costs one LLM round-trip.

    Two failure modes this replaces:
    1. Relevance alone (v1): generic legislative-register text of any topic
       clears a high embedding-similarity bar purely by shared vocabulary
       (see policy_alignment.py calibration notes), so a fixed threshold
       can't separate "genuinely relates" from "written in the same
       bureaucratic register."
    2. Relevance without fulfillment (found via external audit, 2026-07):
       a genuinely on-topic, directionally-aligned vote was credited as
       full "kept" evidence with no check on whether it actually delivered
       the SCOPE of what was promised — a promise to "expand Medicare to
       all Americans" satisfied by a narrow, incremental drug-pricing
       amendment the member voted yes on. Related and directionally
       favorable is not the same as fulfilled.
    """
    return {
        "promptVersion": "promise-evidence-gate-v2",
        "systemPrompt": (
            "You are a political data scientist checking evidence quality and "
            "fulfillment for a campaign promise tracker. Respond in JSON only."
        ),
        "userPrompt": f"""Campaign promise:
"{promise_text}"

Candidate {candidate_kind} (possible evidence):
"{candidate_text[:500]}"

Classify how this {candidate_kind} relates to whether the promise was kept. Superficial \
overlap (both are legislation, both use similar bureaucratic phrasing) is NOT enough — \
the subject matter must actually match the promise's substance, and matching substance is \
not the same as fulfilling it.

- "unrelated": not meaningfully about the same subject as the promise, even if it shares \
generic legislative language.
- "contradicts": the senator's action works against what was promised (e.g. voted against \
something that would have delivered on it, or supported something that undermines it).
- "related_neutral": genuinely about the same topic, but doesn't clearly show the promise \
was kept or broken either way.
- "fulfills_partially": meaningfully advances what was promised, but doesn't deliver its \
full scope (e.g. an incremental or narrower measure than what was promised).
- "fulfills_fully": substantively delivers what was SPECIFICALLY promised, not just a \
related topic.

Return JSON: {{"relationship": "unrelated"|"contradicts"|"related_neutral"|"fulfills_partially"|"fulfills_fully", "reason": "one short sentence"}}""",
    }


def promise_extraction_prompt(platform_text: str, senator_name: str) -> dict:
    """Extract concrete campaign promises from a senator's platform text.

    Deliberately its own single-purpose call rather than one field in a
    larger prompt that also generates a voting summary, PAC analysis, and
    key-vote reasoning in the same response — small local models
    (LFM2.5-1.2B-Instruct here) do measurably better on one focused
    extraction task than on a bundled multi-field JSON response, and
    promise extraction is the one field downstream fulfillment scoring
    depends on entirely, so it's worth the extra call.

    No fixed count requested: the old "extract 4-8 commitments" instruction
    pressured the model to pad thin platform text with vague restatements
    of policy areas ("Healthcare" reworded as a pseudo-promise) rather than
    honestly returning fewer. Real platform text often supports very few
    (or zero) genuinely concrete, specific commitments — an honest empty
    list is more useful downstream than an inflated one that can never be
    fairly evaluated for fulfillment.
    """
    return {
        "promptVersion": "promise-extraction-v1",
        "systemPrompt": (
            "You are a political data scientist extracting concrete campaign "
            "commitments from a candidate's platform text for a fact-checking "
            "pipeline. Rules:\n"
            "1. Only extract commitments EXPLICITLY stated in the text — never infer, "
            "generalize, or invent one because it seems plausible for this senator.\n"
            "2. A real commitment names a specific policy action or outcome, not a bare "
            "topic. 'Healthcare' or 'Supports veterans' is NOT a commitment. "
            "'Expand Medicare drug price negotiation to all Part D drugs' IS one.\n"
            "3. If the text only contains vague priorities or topic lists with no specific "
            "commitments, return an empty list — do not pad it by rewording topics as if "
            "they were promises.\n"
            "4. Use the candidate's own framing and wording where possible.\n"
            "5. Return ONLY valid JSON, no markdown."
        ),
        "userPrompt": f"""Platform text for {senator_name}:
{platform_text[:3000]}

Extract every concrete, specific policy commitment explicitly stated in this text. Return \
as many or as few as the text actually supports — do not force a target count.

Return JSON:
{{"promises": ["<specific commitment 1>", "<specific commitment 2>", "..."]}}""",
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
