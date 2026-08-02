"""Synthesis engine — merges worker outputs into a structured research report."""

from __future__ import annotations

import json
import logging
from typing import Any

from swarmmind.lemonade.client import LemonadeClient

logger = logging.getLogger(__name__)

SYNTHESIS_SYSTEM_PROMPT = """You are a research synthesiser. Your job is to merge findings from multiple
specialist research workers into a single cohesive report.

Given the original query and a set of worker outputs (each containing findings,
key points, sources, and confidence levels), produce a structured report with:

1. **Title** — A concise, descriptive title.
2. **Executive Summary** — 2-3 paragraphs summarising the overall answer.
3. **Sections** — Organised by theme, each section covering relevant findings
   from across the workers.
4. **Conclusion** — The final takeaway.
5. **Contradictions** — Note any conflicting information across workers.
6. **Follow-up Questions** — 3-5 suggested questions the user might ask next.

Respond **only** with a valid JSON object. Example:
{
  "title": "The Impact of RISC-V Vector Extensions on HPC",
  "executive_summary": "RISC-V vector extensions ...",
  "sections": [
    {"heading": "Architecture Overview", "content": "...", "sources": ["..."]}
  ],
  "conclusion": "RISC-V V-extension ...",
  "contradictions": ["Some sources suggest ..."],
  "follow_up_questions": ["How does ...?"]
}

Output ONLY the JSON object — no markdown, no explanation."""


class Synthesis:
    """Merges outputs from multiple workers into a structured research report.

    Args:
        client: An initialised :class:`LemonadeClient`.
        model: The LLM model to use for synthesis.
    """

    def __init__(self, client: LemonadeClient, model: str) -> None:
        self._client = client
        self._model = model

    async def synthesize(
        self,
        worker_outputs: list[dict[str, Any]],
        original_query: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Combine *worker_outputs* into a single structured report.

        Args:
            worker_outputs: A list of dicts as returned by ``Worker.run()``.
            original_query: The user's original research query.
            conversation_history: Optional prior turns so the report can
                reference earlier findings (multi-turn support).

        Returns:
            A dict with keys ``title``, ``executive_summary``, ``sections``,
            ``conclusion``, ``contradictions``, ``follow_up_questions``.
        """
        messages = [
            {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
            {"role": "user", "content": self._build_user_message(
                worker_outputs, original_query, conversation_history)},
        ]

        try:
            response = await self._client.chat_completion(
                model=self._model,
                messages=messages,
                stream=False,
            )
            content = response["choices"][0]["message"]["content"].strip()
            return self._parse_response(content, original_query)
        except Exception as exc:
            logger.exception("Synthesis failed: %s", exc)
            return self._fallback_report(worker_outputs, original_query)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_user_message(
        self,
        worker_outputs: list[dict[str, Any]],
        original_query: str,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> str:
        parts = [f"Original Query: {original_query}"]

        if conversation_history:
            hist_lines = ["\n--- Prior Conversation (for continuity) ---"]
            for i, turn in enumerate(conversation_history, 1):
                prev_q = turn.get("query", "")
                prev_rep = turn.get("report")
                if isinstance(prev_rep, dict):
                    prev_sum = prev_rep.get("executive_summary", "")[:500]
                    prev_conc = prev_rep.get("conclusion", "")[:500]
                    hist_lines.append(f"Turn {i}: Q: {prev_q}")
                    hist_lines.append(f"  Summary: {prev_sum}")
                    hist_lines.append(f"  Conclusion: {prev_conc}")
                else:
                    hist_lines.append(f"Turn {i}: Q: {prev_q} (no report)")
            parts.append("\n".join(hist_lines))

        parts.append("")
        parts.append("--- Worker Outputs ---\n")

        for i, wo in enumerate(worker_outputs, 1):
            parts.append(f"Worker {i}:")
            parts.append(f"  Findings: {wo.get('findings', 'N/A')}")
            parts.append(f"  Key Points: {json.dumps(wo.get('key_points', []))}")
            parts.append(f"  Confidence: {wo.get('confidence', 'N/A')}")
            parts.append(f"  Gaps: {json.dumps(wo.get('gaps', []))}")
            parts.append("")

        return "\n".join(parts)

    def _parse_response(self, content: str, original_query: str) -> dict[str, Any]:
        """Extract and validate the JSON report from the LLM response."""
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()

        report: dict[str, Any] = json.loads(cleaned)

        # Ensure all required keys exist
        defaults: dict[str, Any] = {
            "title": f"Research Report: {original_query[:60]}",
            "executive_summary": "",
            "sections": [],
            "conclusion": "",
            "contradictions": [],
            "follow_up_questions": [],
        }
        for key, default in defaults.items():
            report.setdefault(key, default)

        return report

    def _fallback_report(
        self,
        worker_outputs: list[dict[str, Any]],
        original_query: str,
    ) -> dict[str, Any]:
        """Build a minimal report when the LLM synthesis fails."""
        sections = []
        for i, wo in enumerate(worker_outputs, 1):
            sections.append({
                "heading": f"Worker {i} Findings",
                "content": wo.get("findings", "No findings available."),
                "sources": wo.get("sources_cited", []),
            })

        return {
            "title": f"Research Report: {original_query[:60]}",
            "executive_summary": "Synthesis was automatically generated from individual worker outputs.",
            "sections": sections,
            "conclusion": "Review individual worker sections for detailed findings.",
            "contradictions": [],
            "follow_up_questions": [
                f"Can you provide more detail on: {original_query}?",
                "What are the key sources used?",
                "Are there alternative perspectives on this topic?",
            ],
        }
