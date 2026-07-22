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
