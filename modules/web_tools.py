"""
Web search for agent tools.

Provider priority (fully configured, nothing hardcoded):
  1. Tavily   - if TAVILY_API_KEY (env or Docker secret) is set
  2. SerpAPI  - if SERPAPI_API_KEY (env or Docker secret) is set
  3. DuckDuckGo HTML - keyless fallback so search works out of the box

All providers are called over plain REST (urllib) so no extra pip
dependencies are required. Every path catches its own errors and returns a
readable message instead of raising, so an agent tool can never crash the
agent loop.
"""

import json
import os
import re
import urllib.parse
import urllib.request

from modules.secrets import get_secret

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_MAX_RESULTS = 5


def _http_get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _http_post_json(url: str, payload: dict, timeout: int = 20) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _format_results(rows) -> str:
    """Rows: list of dicts with title/url/snippet (missing fields tolerated)."""
    if not rows:
        return "No web results found for this query."
    lines = []
    for i, row in enumerate(rows, 1):
        title = re.sub(r"<[^>]+>", "", row.get("title", "")).strip()
        url = row.get("url") or row.get("link", "")
        snippet = re.sub(r"<[^>]+>", "", row.get("snippet", "") or row.get("content", "")).strip()
        lines.append(f"{i}. {title}\n   {url}\n   {snippet}")
    return "\n\n".join(lines)


def _search_tavily(query: str, key: str, max_results: int) -> str:
    data = _http_post_json(
        "https://api.tavily.com/search",
        {
            "api_key": key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        },
    )
    rows = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in data.get("results", [])
    ]
    return _format_results(rows)


def _search_serpapi(query: str, key: str, max_results: int) -> str:
    params = urllib.parse.urlencode(
        {"engine": "google", "q": query, "api_key": key, "num": max_results}
    )
    data = json.loads(_http_get(f"https://serpapi.com/search.json?{params}"))
    rows = [
        {"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
        for r in data.get("organic_results", [])[:max_results]
    ]
    return _format_results(rows)


def _search_duckduckgo(query: str, max_results: int) -> str:
    """Keyless DuckDuckGo HTML search (no account, works for any user)."""
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
    html = _http_get(url)

    # Result links carry titles + hrefs; snippets follow in their own <a>.
    link_re = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
    snippet_re = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)

    titles = [(href, re.sub(r"<[^>]+>", "", text).strip()) for href, text in link_re.findall(html)]
    snippets = [re.sub(r"<[^>]+>", "", s).strip() for s in snippet_re.findall(html)]

    rows = []
    for i, (href, title) in enumerate(titles[:max_results]):
        snippet = snippets[i] if i < len(snippets) else ""
        rows.append({"title": title, "url": href, "snippet": snippet})
    return _format_results(rows)


def web_search(query: str, max_results: int = _MAX_RESULTS) -> str:
    """Search the web and return a readable, numbered result list.

    Never raises: any provider failure becomes a message the agent can
    respond to (and the agent still answers from its own knowledge).
    """
    query = (query or "").strip()
    if not query:
        return "Please provide a search query."

    tavily_key = get_secret("TAVILY_API_KEY", default="")
    if tavily_key:
        try:
            return _search_tavily(query, tavily_key, max_results)
        except Exception as e:
            return f"Tavily web search failed ({e}); falling back to DuckDuckGo.\n" + _safe_ddg(query, max_results)

    serpapi_key = get_secret("SERPAPI_API_KEY", default="")
    if serpapi_key:
        try:
            return _search_serpapi(query, serpapi_key, max_results)
        except Exception as e:
            return f"SerpAPI web search failed ({e}); falling back to DuckDuckGo.\n" + _safe_ddg(query, max_results)

    return _safe_ddg(query, max_results)


def _safe_ddg(query: str, max_results: int) -> str:
    try:
        return _search_duckduckgo(query, max_results)
    except Exception as e:
        return (
            f"Web search is currently unavailable ({e}). "
            "No results could be fetched right now."
        )


# Groq function-calling schema for the web search tool.
WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the internet for current information on a topic. Use this "
            "when the user asks about recent events, facts outside the "
            "uploaded documents, or anything the agent may not know."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query (concise, specific).",
                }
            },
            "required": ["query"],
        },
    },
}
