"""Lightweight web search for AI context enrichment.

Uses DuckDuckGo Instant Answer API (free, no API key, HTML scrape fallback).
Only triggered for benchmark/industry/analyst queries — not every question.
"""
import logging
from typing import List, Dict

import requests

logger = logging.getLogger(__name__)

TRIGGER_KEYWORDS = frozenset([
    "benchmark", "industry", "gartner", "forrester", "idc", "market",
    "analyst", "best practice", "trends", "competitors", "peer", "compare",
    "magic quadrant", "wave report", "average", "typical", "standard",
])


def should_search(question: str) -> bool:
    """Return True if the question warrants a web search."""
    lower = question.lower()
    return any(kw in lower for kw in TRIGGER_KEYWORDS)


def search_context(question: str, max_results: int = 3) -> List[Dict]:
    """Search for relevant context snippets. Returns list of {title, snippet, url}."""
    results = []
    try:
        # DuckDuckGo Instant Answer API (no auth required)
        query = f"enterprise architecture {question[:100]}"
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            timeout=5,
            headers={"User-Agent": "Entelim-Platform/1.0"},
        )
        if resp.status_code == 200:
            data = resp.json()
            # Abstract result
            if data.get("Abstract"):
                results.append({
                    "title": data.get("Heading", "Summary"),
                    "snippet": data["Abstract"][:300],
                    "url": data.get("AbstractURL", ""),
                })
            # Related topics
            for topic in data.get("RelatedTopics", [])[:max_results - 1]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append({
                        "title": topic.get("FirstURL", "").split("/")[-1].replace("_", " "),
                        "snippet": topic["Text"][:200],
                        "url": topic.get("FirstURL", ""),
                    })
    except Exception as exc:
        logger.debug("web_search: search failed: %s", exc)
    return results[:max_results]


def format_search_context(results: List[Dict]) -> str:
    """Format search results for LLM prompt injection."""
    if not results:
        return ""
    lines = ["Web search context (recent public sources):"]
    for r in results:
        lines.append(f"  - {r['title']}: {r['snippet']}")
    return "\n".join(lines)
