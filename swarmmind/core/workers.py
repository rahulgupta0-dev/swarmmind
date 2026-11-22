"""Specialised research workers for the SwarmMind agent pool."""

from __future__ import annotations

import html as html_lib
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from duckduckgo_search import DDGS

from swarmmind.lemonade.client import LemonadeClient

logger = logging.getLogger(__name__)

# Cap the number of bytes read from a single page and the snippet we keep.  A
# page fetch must never turn into an unbounded download.
_MAX_PAGE_BYTES = 512_000
_SNIPPET_CHARS = 1200

# Every worker is asked to cite inline.  Keeping this as a shared suffix makes
# the rule consistent across all worker types and easy to assert in tests.
_CITATION_RULE = (
    " Cite every factual claim inline with a source ID in the form [S1], using only "
    "IDs that appear in the Context (for example [S1], [S2]). Never invent an ID. "
    "If a claim is not backed by a listed source, state that it is unverified."
)

# System prompts for each worker type
WORKER_SYSTEM_PROMPTS: dict[str, str] = {
    "rag": (
        "You are a RAG research assistant. You have access to retrieved document chunks "
        "that provide specific information relevant to the query. Synthesise these into "
        "a coherent answer, citing sources where possible."
    ),
    "web": (
        "You are a web research assistant. You have access to search engine results "
        "relevant to the query. Summarise the findings, note source URLs, and highlight "
        "key facts and figures."
    ),
    "analysis": (
        "You are a deep-analysis research assistant. Use your internal knowledge to "
        "provide thorough analysis, compare perspectives, identify trends, and offer "
        "insights. You do not have access to external search or documents."
    ),
    "code": (
        "You are a code-analysis research assistant. You have access to code files and "
        "technical context. Analyse the code, explain its purpose, and identify patterns, "
        "bugs, or areas for improvement."
    ),
    "vision": (
        "You are a vision-language research assistant. You have access to user-uploaded images "
        "and contextual queries. Analyze the provided image deeply to answer the query, "
        "extracting text, describing UI elements, or summarizing visual data."
    ),
}

# Apply the inline-citation rule uniformly.  Done after the literal so the
# base prompts stay readable above.
WORKER_SYSTEM_PROMPTS = {
    name: prompt + _CITATION_RULE for name, prompt in WORKER_SYSTEM_PROMPTS.items()
}


class Worker:
    """A single research worker that executes a task for a specialised area.

    Args:
        client: An initialised :class:`LemonadeClient`.
        model: The LLM model to use.
    """

    def __init__(self, client: LemonadeClient, model: str) -> None:
        self._client = client
        self._model = model

    async def run(
        self,
        worker_type: str,
        task: str,
        context: str,
        additional_context: str = "",
    ) -> dict[str, Any]:
        """Execute the worker task and return structured findings.

        Args:
            worker_type: ``"rag"``, ``"web"``, ``"analysis"``, or ``"code"``.
            task: The task description from the conductor.
            context: Context passed by the conductor (or search results for RAG/web).
            additional_context: Extra context (e.g. RAG search snippets).

        Returns:
            A dict with keys ``findings``, ``key_points``, ``sources_cited``,
            ``claims``, ``confidence``, ``gaps``.  The orchestrator fills in
            the evidence-backed values for ``sources_cited`` and ``claims``.
        """
        system_prompt = WORKER_SYSTEM_PROMPTS.get(worker_type, WORKER_SYSTEM_PROMPTS["analysis"])

        user_message = f"## Task\n{task}\n\n## Context\n{context}\n"
        if additional_context:
            user_message += f"\n## Additional Context\n{additional_context}\n"

        user_message += (
            "\n\nPlease provide your findings as a structured response covering:\n"
            "- Key findings\n"
            "- Key points (bullet list)\n"
            "- Sources cited\n"
            "- Confidence level (high/medium/low)\n"
            "- Gaps or uncertainties\n"
            "- Inline citations: mark each claim with the matching [S#] ID shown "
            "in the Context above; do not use IDs that are not listed there"
        )

        if worker_type == "vision" and additional_context.startswith("data:image"):
            # Pass image directly as part of a multimodal message
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_message},
                        {"type": "image_url", "image_url": {"url": additional_context}}
                    ]
                }
            ]
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]

        try:
            response = await self._client.chat_completion(
                model=self._model,
                messages=messages,
                stream=False,
            )
            content = response["choices"][0]["message"]["content"]

            return {
                "findings": content,
                "key_points": self._extract_key_points(content),
                "sources_cited": [],
                "claims": [],
                "confidence": "medium",
                "gaps": [],
            }
        except Exception as exc:
            logger.exception("Worker '%s' failed: %s", worker_type, exc)
            return {
                "findings": f"Worker {worker_type} encountered an error: {exc}",
                "key_points": [],
                "sources_cited": [],
                "claims": [],
                "confidence": "low",
                "gaps": [str(exc)],
            }

    @staticmethod
    def _extract_key_points(text: str) -> list[str]:
        """Simple heuristic extraction of bullet points from LLM output."""
        points: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("* "):
                points.append(stripped[2:])
            elif stripped and stripped[0].isdigit() and ". " in stripped[:4]:
                points.append(stripped.split(". ", 1)[1])
        return points[:10]  # cap at 10 points


# ------------------------------------------------------------------
# Standalone web search helper
# ------------------------------------------------------------------

def search_web(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search the web using DuckDuckGo.

    Args:
        query: The search query.
        max_results: Maximum number of results to return.

    Returns:
        A list of dicts with keys ``title``, ``url``, ``snippet``.
    """
    results: list[dict[str, str]] = []
    try:
        with DDGS() as ddgs:
            for i, r in enumerate(ddgs.text(query, max_results=max_results)):
                if i >= max_results:
                    break
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
    except Exception as exc:
        logger.warning("Web search failed: %s", exc)

    return results


# ------------------------------------------------------------------
# Web page fetching (opt-in, size-bounded, http(s)-only)
# ------------------------------------------------------------------

_TAG_RE = re.compile(r"(?s)<[^>]+>")
_SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1\s*>")
_TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title\s*>")
_WS_RE = re.compile(r"\s+")


def _html_to_text(raw: bytes) -> str:
    """Extract readable text from an HTML byte payload (dependency-free)."""
    try:
        html = raw.decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - decode with errors= never raises
        html = ""
    html = _SCRIPT_RE.sub(" ", html)
    html = _TAG_RE.sub(" ", html)
    text = html_lib.unescape(html)
    return _WS_RE.sub(" ", text).strip()


def _html_title(raw: bytes) -> str:
    """Return the document ``<title>`` text, if any."""
    match = _TITLE_RE.search(raw.decode("utf-8", errors="replace"))
    if not match:
        return ""
    return _WS_RE.sub(" ", html_lib.unescape(match.group(1))).strip()


def _fallback_page(url: str, title: str, snippet: str) -> dict[str, Any]:
    """Build the honest, unverified record used when a page cannot be fetched."""
    return {
        "kind": "web",
        "title": title or url or "Untitled page",
        "url": url,
        "snippet": str(snippet)[:_SNIPPET_CHARS],
        "fetch_ok": False,
    }


async def fetch_pages(
    urls: list[str],
    *,
    max_pages: int,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """Fetch a bounded number of web pages and extract readable snippets.

    ``urls`` is normally the list of URL strings returned by :func:`search_web`;
    each entry may also be the full DDG result dict (``url``/``title``/
    ``snippet``) so the original search snippet can be retained as an honest
    fallback when the live page cannot be fetched.

    Only ``http``/``https`` URLs are requested — every other scheme is rejected
    without any network or filesystem access.  Each result carries
    ``fetch_ok``: ``True`` only when a real page was downloaded (and is therefore
    safe to mark as a supported source), ``False`` when the DDG snippet is used.

    Args:
        urls: URL strings and/or ``{"url", "title", "snippet"}`` result dicts.
        max_pages: Hard cap on the number of records returned.
        timeout_seconds: Per-request timeout passed to :class:`httpx.AsyncClient`.

    Returns:
        A list of web-page dicts with keys ``kind``, ``title``, ``url``,
        ``snippet`` and ``fetch_ok``, in input order.
    """
    if max_pages <= 0:
        return []

    # Normalise inputs first so security checks and fallbacks use the same data.
    candidates: list[dict[str, str]] = []
    for item in urls[:max_pages]:
        if isinstance(item, dict):
            url = str(item.get("url") or "")
            title = str(item.get("title") or "")
            snippet = str(item.get("snippet") or "")
        else:
            url = str(item or "")
            title, snippet = "", ""
        candidates.append({"url": url, "title": title, "snippet": snippet})

    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=True,
    ) as client:
        for candidate in candidates:
            url = candidate["url"]
            parsed = urlparse(url)

            # Security guard: never hand a non-http(s) URL to httpx.  Keep the
            # DDG snippet as a clearly-marked unverified fallback instead.
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                results.append(
                    _fallback_page(url, candidate["title"], candidate["snippet"])
                )
                continue

            try:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= _MAX_PAGE_BYTES:
                            break
                    raw = b"".join(chunks)[:_MAX_PAGE_BYTES]
                    final_url = str(response.url) or url

                snippet = _html_to_text(raw)[:_SNIPPET_CHARS]
                results.append({
                    "kind": "web",
                    "title": _html_title(raw) or candidate["title"] or final_url,
                    "url": final_url,
                    # A rendered-but-empty page still falls back to DDG text.
                    "snippet": snippet or candidate["snippet"][:_SNIPPET_CHARS],
                    "fetch_ok": True,
                })
            except Exception as exc:
                logger.warning("Page fetch failed for %s: %s", url, exc)
                results.append(
                    _fallback_page(url, candidate["title"], candidate["snippet"])
                )

    return results
