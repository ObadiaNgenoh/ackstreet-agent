"""Web tools: search the internet and fetch page text.

Search strategy (in order of preference):

1. ``TAVILY_API_KEY``  — richest results, returns extracted content
2. ``SERPER_API_KEY``  — Google results, titles + snippets
3. ``BRAVE_API_KEY``   — Brave Search API
4. DuckDuckGo HTML    — no key required, best-effort fallback

If none of the keyed backends is configured the DuckDuckGo fallback is used,
so web search still works on a bare install.
"""

from __future__ import annotations

import html
import os
import re
from typing import Any, Dict, List
from urllib.parse import quote_plus, urlparse

import httpx

from ..providers.base import ssl_verify
from .base import Tool, ToolResult

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 AckstreetAgent/0.1"
)

TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
WHITESPACE_RE = re.compile(r"[ \t]*\n[ \t]*")
MULTI_BLANK_RE = re.compile(r"\n{3,}")


def html_to_text(markup: str) -> str:
    """Very small HTML-to-text extractor.

    Deliberately dependency-free: strips scripts and styles, converts block
    tags to newlines, then removes remaining tags and unescapes entities.
    """
    markup = SCRIPT_RE.sub(" ", markup)
    markup = re.sub(r"<!--.*?-->", " ", markup, flags=re.DOTALL)
    for block in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"):
        markup = re.sub(rf"</?{block}[^>]*>", "\n", markup, flags=re.IGNORECASE)
    text = TAG_RE.sub(" ", markup)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = WHITESPACE_RE.sub("\n", text)
    text = MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()


def extract_title(markup: str) -> str:
    match = TITLE_RE.search(markup)
    if not match:
        return ""
    return html.unescape(TAG_RE.sub("", match.group(1))).strip()[:200]


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the internet and return titled results with URLs and snippets. "
        "Use this whenever you need current information, documentation or facts you are "
        "not certain about. Follow up with fetch_url to read a promising result in full."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "num_results": {
                "type": "integer",
                "description": "How many results to return (default 5, max 10).",
            },
        },
        "required": ["query"],
    }

    def _timeout(self) -> float:
        return float(self.config.get("tools", "web_timeout", 30))

    # -- backends ----------------------------------------------------------

    def _tavily(self, query: str, count: int) -> List[Dict[str, str]]:
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            return []
        with httpx.Client(timeout=self._timeout(), verify=ssl_verify(), follow_redirects=True) as client:
            response = client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": count,
                    "search_depth": "basic",
                    "include_answer": True,
                },
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Tavily HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        results: List[Dict[str, str]] = []
        if data.get("answer"):
            results.append({"title": "Direct answer", "url": "", "snippet": data["answer"]})
        for item in data.get("results", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": (item.get("content") or "")[:600],
                }
            )
        return results

    def _serper(self, query: str, count: int) -> List[Dict[str, str]]:
        api_key = os.environ.get("SERPER_API_KEY", "")
        if not api_key:
            return []
        with httpx.Client(timeout=self._timeout(), verify=ssl_verify(), follow_redirects=True) as client:
            response = client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "num": count},
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Serper HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        results: List[Dict[str, str]] = []
        answer = (data.get("answerBox") or {}).get("answer") or (data.get("answerBox") or {}).get("snippet")
        if answer:
            results.append({"title": "Direct answer", "url": "", "snippet": answer})
        for item in data.get("organic", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                }
            )
        return results

    def _brave(self, query: str, count: int) -> List[Dict[str, str]]:
        api_key = os.environ.get("BRAVE_API_KEY", "")
        if not api_key:
            return []
        with httpx.Client(timeout=self._timeout(), verify=ssl_verify(), follow_redirects=True) as client:
            response = client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
                params={"q": query, "count": count},
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Brave HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        return [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("description", ""),
            }
            for item in (data.get("web", {}) or {}).get("results", [])
        ]

    def _duckduckgo(self, query: str, count: int) -> List[Dict[str, str]]:
        """Keyless fallback scraping the DuckDuckGo HTML endpoint."""
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        with httpx.Client(timeout=self._timeout(), verify=ssl_verify(), follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": DEFAULT_UA})
        if response.status_code >= 400:
            raise RuntimeError(f"DuckDuckGo HTTP {response.status_code}")

        results: List[Dict[str, str]] = []
        pattern = re.compile(
            r'<a rel="nofollow" class="result__a" href="(?P<url>[^"]+)">(?P<title>.*?)</a>'
            r'.*?(?:class="result__snippet"[^>]*>(?P<snippet>.*?)</a>)?',
            re.DOTALL,
        )
        for match in pattern.finditer(response.text):
            raw_url = html.unescape(match.group("url"))
            # DuckDuckGo wraps targets in a redirect parameter.
            if "uddg=" in raw_url:
                raw_url = raw_url.split("uddg=", 1)[1].split("&", 1)[0]
                raw_url = html.unescape(raw_url)
            title = html.unescape(TAG_RE.sub("", match.group("title") or "")).strip()
            snippet = html.unescape(TAG_RE.sub("", match.group("snippet") or "")).strip()
            if title and raw_url.startswith("http"):
                results.append({"title": title, "url": raw_url, "snippet": snippet})
            if len(results) >= count:
                break
        return results

    # -- interface ---------------------------------------------------------

    def run(self, query: str, num_results: int = 5, **_: Any) -> ToolResult:
        if not self.config.get("tools", "allow_web", True):
            return ToolResult.failure("web access is disabled (tools.allow_web = false in config)")

        query = (query or "").strip()
        if not query:
            return ToolResult.failure("query must not be empty")

        count = max(1, min(int(num_results or 5), 10))
        errors: List[str] = []
        results: List[Dict[str, str]] = []
        backend = ""

        for name, backend_fn in (
            ("tavily", self._tavily),
            ("serper", self._serper),
            ("brave", self._brave),
            ("duckduckgo", self._duckduckgo),
        ):
            try:
                found = backend_fn(query, count)
            except Exception as exc:  # noqa: BLE001 - try the next backend
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            if found:
                results, backend = found[:count], name
                break

        if not results:
            detail = "; ".join(errors) if errors else "no backend returned results"
            return ToolResult.failure(
                f"search failed for '{query}' ({detail}). "
                "Set TAVILY_API_KEY or SERPER_API_KEY in the environment for reliable search."
            )

        lines = [f'Search results for "{query}" via {backend}:']
        for index, item in enumerate(results, start=1):
            lines.append(f"\n{index}. {item['title']}")
            if item["url"]:
                lines.append(f"   {item['url']}")
            if item["snippet"]:
                lines.append(f"   {item['snippet']}")
        return ToolResult.success(
            "\n".join(lines), backend=backend, count=len(results)
        )


class FetchUrlTool(Tool):
    name = "fetch_url"
    description = (
        "Fetch a URL and return its readable text (HTML is converted to plain text). "
        "Use after web_search to read a promising page in full, or to read API responses "
        "and raw files."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL to fetch."},
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters of text to return (default from config).",
            },
        },
        "required": ["url"],
    }

    def run(self, url: str, max_chars: int | None = None, **_: Any) -> ToolResult:
        if not self.config.get("tools", "allow_web", True):
            return ToolResult.failure("web access is disabled (tools.allow_web = false in config)")

        url = (url or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ToolResult.failure(f"only http(s) URLs are supported, got: {url!r}")

        limit = int(max_chars or self.output_limit)
        timeout = float(self.config.get("tools", "web_timeout", 30))

        try:
            with httpx.Client(
                timeout=timeout, verify=ssl_verify(), follow_redirects=True, headers={"User-Agent": DEFAULT_UA}
            ) as client:
                response = client.get(url)
        except httpx.ConnectError as exc:
            return ToolResult.failure(f"cannot connect to {url}: {exc}")
        except httpx.TimeoutException:
            return ToolResult.failure(f"timed out after {timeout}s fetching {url}")
        except httpx.HTTPError as exc:
            return ToolResult.failure(f"request failed for {url}: {exc}")

        if response.status_code >= 400:
            return ToolResult.failure(f"HTTP {response.status_code} fetching {url}")

        content_type = response.headers.get("content-type", "")
        raw = response.text

        if "html" in content_type.lower() or raw.lstrip().lower().startswith(("<!doctype html", "<html")):
            title = extract_title(raw)
            text = html_to_text(raw)
            header = f"{url}" + (f"\nTitle: {title}" if title else "")
            body = f"{header}\n---\n{text}"
        else:
            body = f"{url}\n[{content_type or 'unknown'}]\n---\n{raw}"

        if len(body) > limit:
            body = f"{body[:limit]}\n... [truncated; {len(body) - limit} more characters]"
        return ToolResult.success(body, url=url, status=response.status_code)


__all__ = ["FetchUrlTool", "WebSearchTool", "extract_title", "html_to_text"]
