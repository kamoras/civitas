"""
LLM prompt templates for the pipeline.
Each function returns a dict with promptVersion, systemPrompt, userPrompt.
"""


# Section markers for explore_document_summary_prompt's plain-text output.
# Streaming this generation to the browser (github issue: "explore doc ai
# summary should stream") ruled out JSON: a client can't render partial
# JSON — {"summary": "The bill est is neither valid JSON nor readable
# text — so the whole response would still have to be buffered before
# anything appears, defeating the point. Plain text with markers streams
# character-by-character as-is; the reader (explore.py) and frontend both
# split on these same two constants instead of parsing structured output.
SUMMARY_KEY_POINTS_MARKER = "KEY POINTS:"
SUMMARY_IMPACT_MARKER = "IMPACT:"


def explore_document_summary_prompt(doc: dict) -> dict:
    title = doc.get("title", "")
    body = doc.get("body", "")[:2000]
    doc_type = doc.get("doc_type", "document")
    chamber = doc.get("chamber", "")
    politician = doc.get("politician_name", "")
    date = doc.get("date", "")

    return {
        "promptVersion": "explore-doc-summary-v4",
        "systemPrompt": (
            "You are a nonpartisan civic analyst summarizing government documents "
            "for a public transparency platform. Rules:\n"
            "1. Summarize what the document DOES — its purpose, provisions, and scope.\n"
            "2. Every sentence must add information not already visible in the title.\n"
            "3. Never restate the document title or type.\n"
            "4. Be factual — no editorializing, no filler.\n"
            "5. Focus on substance: what changes, who is affected, and why it matters.\n"
            "6. Output PLAIN TEXT in exactly the format below — no JSON, no markdown."
        ),
        "userPrompt": f"""Summarize this government document for a citizen.

Document type: {doc_type}
Chamber: {chamber}
Date: {date}
Speaker/Author: {politician}
Title: {title}

Content:
{body}

Respond in EXACTLY this plain-text format (omit the IMPACT line entirely if there is no concrete consequence for ordinary people):
SUMMARY: <2-3 sentences: what does this document do? What is its purpose and substance?>
{SUMMARY_KEY_POINTS_MARKER}
- <specific factual point from the content>
- <another specific point>
{SUMMARY_IMPACT_MARKER} <1 sentence: concrete consequence for ordinary people>""",
    }


def parse_explore_document_summary(text: str) -> dict:
    """Split the plain-text SUMMARY/KEY POINTS/IMPACT format back into fields.

    Shared by explore.py's streaming endpoint (re-parsed on every chunk to
    derive what's safe to show so far) and its cache-write path (parsed once
    at the end, stored in the same {summary, keyPoints, impact} shape the
    old JSON-based cache entries used, so cache rows before and after this
    format change stay compatible).
    """
    summary_part, _, rest = text.partition(SUMMARY_KEY_POINTS_MARKER)
    key_points_part, _, impact_part = rest.partition(SUMMARY_IMPACT_MARKER)

    summary = summary_part.split("SUMMARY:", 1)[-1].strip()

    key_points = [
        line.strip().lstrip("-").strip()
        for line in key_points_part.splitlines()
        if line.strip().startswith("-")
    ]

    impact = impact_part.strip()

    return {"summary": summary, "keyPoints": key_points, "impact": impact}
