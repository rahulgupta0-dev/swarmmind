"""Query conductor — decomposes user queries into sub-tasks for workers."""

from __future__ import annotations

import json
import logging
from typing import Any

from swarmmind.lemonade.client import LemonadeClient

logger = logging.getLogger(__name__)

CONDUCTOR_SYSTEM_PROMPT = """You are a research query conductor. Your job is to analyse the user's research
question and decompose it into 2-5 parallel sub-tasks that specialised workers
can tackle independently.

Each sub-task must specify:
  - worker_type: one of "rag", "web", "analysis", "code"
  - task: a concise description of what to research
  - context: background information that helps the worker (may be empty)
  - reasoning: why this sub-task is necessary

Respond **only** with a valid JSON array of objects. Example:
[
  {
    "worker_type": "web",
    "task": "Search for recent papers on RISC-V vector extensions",
    "context": "Focus on 2024-2025 publications",
    "reasoning": "Need up-to-date information from the web"
  },
  {
    "worker_type": "analysis",
    "task": "Analyse the architectural differences between RISC-V and ARM SVE",
    "context": "Compare ISA features, performance, and ecosystem maturity",
    "reasoning": "Leverage LLM knowledge for architectural comparison"
  }
]

Output ONLY the JSON array — no markdown, no explanation."""


class Conductor:
    """Decomposes a user's research query into structured worker tasks.

    Args:
        client: An initialised :class:`LemonadeClient`.
        model: The LLM model to use for decomposition.
    """

    def __init__(self, client: LemonadeClient, model: str) -> None:
        self._client = client
        self._model = model

    async def decompose_query(
        self,
        query: str,
        project_context: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """Break *query* into a list of worker-task dicts.

        Each returned dict has keys ``worker_type``, ``task``, ``context``,
        ``reasoning``.

        Falls back to 3 default workers if the LLM response cannot be parsed.
        """
        messages = [
            {"role": "system", "content": CONDUCTOR_SYSTEM_PROMPT},
            {"role": "user", "content": self._build_user_message(query, project_context)},
        ]

        try:
            response = await self._client.chat_completion(
                model=self._model,
                messages=messages,
                stream=False,
            )
            content = response["choices"][0]["message"]["content"].strip()
            return self._parse_response(content)
        except Exception as exc:
            logger.warning("Conductor LLM call failed: %s — using fallback workers", exc)
            return self._fallback(query)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_user_message(
        self,
        query: str,
        project_context: dict[str, Any] | None,
    ) -> str:
        parts = [f"Research Query: {query}"]
        if project_context:
            parts.append(f"\nProject Context:\n{json.dumps(project_context, indent=2)}")
        return "\n".join(parts)

    def _parse_response(self, content: str) -> list[dict[str, str]]:
        """Try to extract and validate a JSON array from the LLM response."""
        # Strip markdown code fences if present
        cleaned = content.strip()
        if cleaned.startswith("```"):
            # Remove opening fence (possibly with language spec)
            cleaned = cleaned.split("\n", 1)[-1]
            # Remove closing fence
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()

        tasks: list[dict[str, str]] = json.loads(cleaned)

        if not isinstance(tasks, list):
            raise ValueError("Response is not a JSON array")

        valid_types = {"rag", "web", "analysis", "code"}
        for t in tasks:
            if t.get("worker_type") not in valid_types:
                t["worker_type"] = "analysis"
            for key in ("task", "context", "reasoning"):
                t.setdefault(key, "")

        return tasks

    def _fallback(self, query: str) -> list[dict[str, str]]:
        """Return a safe default decomposition when the LLM fails."""
        return [
            {
                "worker_type": "rag",
                "task": f"Search local knowledge base for: {query}",
                "context": "Use indexed project documents as primary source.",
                "reasoning": "Retrieve relevant information from ingested sources.",
            },
            {
                "worker_type": "web",
                "task": f"Search the web for recent information on: {query}",
                "context": "Focus on authoritative and up-to-date sources.",
                "reasoning": "Supplement local knowledge with web search results.",
            },
            {
                "worker_type": "analysis",
                "task": f"Analyse and synthesise findings for: {query}",
                "context": "Combine RAG and web results with internal knowledge.",
                "reasoning": "Provide deep analysis and identify gaps.",
            },
        ]
