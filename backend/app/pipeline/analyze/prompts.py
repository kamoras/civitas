"""
LLM prompt templates for the pipeline.
Each function returns a dict with promptVersion, systemPrompt, userPrompt.
"""


def explore_document_summary_prompt(query: str, doc: dict) -> dict:
    title = doc.get("title", "")
    body = doc.get("body", "")[:2000]
    doc_type = doc.get("doc_type", "document")
    chamber = doc.get("chamber", "")
    politician = doc.get("politician_name", "")
    date = doc.get("date", "")

    return {
        "promptVersion": "explore-doc-summary-v2",
        "systemPrompt": (
            "You are a nonpartisan civic analyst. Rules:\n"
            "1. Every sentence must add information not already visible in the title.\n"
            "2. Never restate the document title or type.\n"
            "3. Be factual — no editorializing, no filler.\n"
            "4. If the document is only weakly related to the search, say so honestly.\n"
            "Return ONLY valid JSON."
        ),
        "userPrompt": f"""A citizen searched for: "{query}"

This government document matched their search.

Document type: {doc_type}
Chamber: {chamber}
Date: {date}
Speaker/Author: {politician}
Title: {title}

Content:
{body}

Return JSON:
{{
  "relevance": "<2-3 sentences: what specific part of this document connects to '{query}'? Be concrete.>",
  "keyPoints": ["<specific factual point from the content>", "<another specific point>"],
  "impact": "<1 sentence: concrete consequence for ordinary people, or empty string if none>"
}}""",
    }
