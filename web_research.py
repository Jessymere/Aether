import json
import urllib.parse
import urllib.request
import re


def needs_web(query: str) -> bool:
    """Conservative heuristic for requests whose answer may be time-sensitive or source-dependent."""
    signals = r"\b(latest|current|today|recent|news|price|pricing|weather|official docs?|documentation|api|release|version|202[4-9])\b|https?://"
    return bool(re.search(signals, query, re.I))


def research(query: str, limit: int = 5) -> str:
    """Fetch a small public research brief from DuckDuckGo's no-key API."""
    url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "no_html": 1,
        "skip_disambig": 0,
    })
    request = urllib.request.Request(url, headers={"User-Agent": "LocalMultiAIBuilder/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode())
    except Exception as exc:
        return f"Web research unavailable: {exc}"

    lines = []
    if data.get("AbstractText"):
        lines.append(f"- {data['AbstractText']}\n  Source: {data.get('AbstractURL', '')}")
    for topic in data.get("RelatedTopics", []):
        if len(lines) >= limit:
            break
        if isinstance(topic, dict) and topic.get("Text"):
            lines.append(f"- {topic['Text']}\n  Source: {topic.get('FirstURL', '')}")
    return "\n".join(lines) or "No useful web results were returned. Verify important facts independently."
