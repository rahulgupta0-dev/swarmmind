"""Specialised research workers for the SwarmMind agent pool."""

from __future__ import annotations

import logging
from typing import Any

from duckduckgo_search import DDGS

from swarmmind.lemonade.client import LemonadeClient

logger = logging.getLogger(__name__)

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
            ``confidence``, ``gaps``.
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
            "- Gaps or uncertainties"
        )

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
                "confidence": "medium",
                "gaps": [],
            }
        except Exception as exc:
            logger.exception("Worker '%s' failed: %s", worker_type, exc)
            return {
                "findings": f"Worker {worker_type} encountered an error: {exc}",
                "key_points": [],
                "sources_cited": [],
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
