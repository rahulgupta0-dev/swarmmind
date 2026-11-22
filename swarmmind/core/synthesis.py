"""Synthesis engine — merges worker outputs into a structured research report.

The v0.4 synthesiser is citation-aware: every source a worker cites is collected
into a bounded registry, the LLM is asked to cite those IDs inline (``[S1]``),
and the parsed report is validated deterministically before it is returned.
Unknown citation IDs are dropped and a compact ``claims`` / ``grounding``
summary is attached so downstream UI and exports can explain *why* an answer
is (or is not) grounded.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from swarmmind.core.evidence import EvidenceLedger
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

CITATION RULES (mandatory):
- A "Source Registry" listing valid IDs (S1, S2, ...) is provided in the user
  message. You may cite ONLY those IDs — never invent or guess an ID.
- Cite evidence inline using bracket markers, e.g. "Vector extensions improve
  throughput [S1][S3]." Put the marker immediately after the claim it supports.
- Every section MUST include a "sources" array containing the same IDs you
  cited in that section, e.g. "sources": ["S1", "S3"].
- If you have no source for a statement, say so plainly (e.g. "based on model
  knowledge") and leave that section's "sources" array empty. Do NOT present
  ungrounded speculation as a sourced fact.
- Never cite a source whose snippet does not actually support the claim.

Respond **only** with a valid JSON object. Example:
{
  "title": "The Impact of RISC-V Vector Extensions on HPC",
  "executive_summary": "RISC-V vector extensions improve throughput [S1].",
  "sections": [
    {"heading": "Architecture Overview", "content": "... [S1]", "sources": ["S1"]}
  ],
  "conclusion": "RISC-V V-extension ... [S1]",
  "contradictions": ["Some sources suggest ..."],
  "follow_up_questions": ["How does ...?"]
}

Output ONLY the JSON object — no markdown, no explanation."""

_CITATION_RE = re.compile(r"\[(S\d+)\]")
_REGISTRY_MAX_CHARS = 6000

_ALLOWED_SOURCE_FIELDS = ("id", "kind", "title", "url", "snippet", "doc_id", "chunk")
_VALID_KINDS = frozenset({"rag", "web", "image", "model"})


# ---------------------------------------------------------------------------
# Source registry helpers (module-level so tests can use them directly)
# ---------------------------------------------------------------------------


def _normalize_source(raw: Any) -> dict[str, Any] | None:
    """Coerce one worker-supplied source into a canonical dict (or ``None``).

    Accepts ``SourceRecord`` models, dicts and legacy URL strings. Missing
    fields receive safe defaults and unexpected keys are ignored.
    """
    if raw is None:
        return None
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    if isinstance(raw, str):
        raw = {"kind": "web", "title": raw, "url": raw, "snippet": ""}
    if not isinstance(raw, dict):
        return None

    record: dict[str, Any] = {key: raw.get(key) for key in _ALLOWED_SOURCE_FIELDS}
    kind = record.get("kind")
    if kind not in _VALID_KINDS:
        kind = "model"
    record["kind"] = kind

    sid = record.get("id")
    record["id"] = str(sid).strip() if sid is not None and str(sid).strip() else ""

    for key in ("title", "url", "snippet", "doc_id"):
        value = record.get(key)
        record[key] = "" if value is None else str(value)
    if not record["title"]:
        record["title"] = (
            record["url"]
            or (f"{record['doc_id']}:{record['chunk']}" if record["doc_id"] or record.get("chunk", -1) != -1 else "")
            or "Untitled source"
        )
    try:
        record["chunk"] = int(record.get("chunk", -1))
    except (TypeError, ValueError):
        record["chunk"] = -1
    return record


def build_source_registry(
    worker_outputs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Collect and dedupe all ``sources_cited`` from *worker_outputs*.

    Dedupe is by explicit ``id`` (the IDs assigned upstream by the evidence
    ledger), with a URL/doc_id fallback for legacy sources that carry no ID.
    Returns ``(registry, known_ids)`` where ``known_ids`` is the set of IDs the
    LLM is allowed to cite.
    """
    registry: list[dict[str, Any]] = []
    known_ids: set[str] = set()
    seen_urls: set[str] = set()
    seen_rag_keys: set[str] = set()
    next_number = 1

    for worker in worker_outputs:
        if not isinstance(worker, dict):
            continue
        raw_sources = worker.get("sources_cited") or []
        if not isinstance(raw_sources, list):
            raw_sources = [raw_sources]
        for raw in raw_sources:
            record = _normalize_source(raw)
            if record is None:
                continue

            sid = record["id"]
            url = record["url"]
            rag_key = (
                f"{record['doc_id']}:{record['chunk']}"
                if record["kind"] == "rag" and (record["doc_id"] or record["chunk"] != -1)
                else ""
            )
            if sid and sid in known_ids:
                continue  # dedupe by stable id
            if not sid and url and url in seen_urls:
                continue
            if not sid and rag_key and rag_key in seen_rag_keys:
                continue

            if not sid:
                while f"S{next_number}" in known_ids:
                    next_number += 1
                sid = f"S{next_number}"
                next_number += 1
                record["id"] = sid

            known_ids.add(sid)
            if url:
                seen_urls.add(url)
            if rag_key:
                seen_rag_keys.add(rag_key)
            registry.append(record)

    return registry, known_ids


def _source_ids_for_worker(
    raw_sources: Any, registry: list[dict[str, Any]],
) -> list[str]:
    """Map a worker's ``sources_cited`` to IDs present in *registry*."""
    if not isinstance(raw_sources, list):
        raw_sources = [raw_sources] if raw_sources else []
    ids: list[str] = []
    by_id = {str(r.get("id", "")): r for r in registry}
    for raw in raw_sources:
        record = _normalize_source(raw)
        if record is None:
            continue
        sid = record["id"]
        if sid in by_id:
            ids.append(sid)
            continue
        for reg in registry:
            if record["url"] and reg.get("url") == record["url"]:
                ids.append(str(reg["id"]))
                break
            if (
                record["doc_id"]
                and reg.get("doc_id") == record["doc_id"]
                and reg.get("chunk") == record["chunk"]
            ):
                ids.append(str(reg["id"]))
                break
    return list(dict.fromkeys(ids))


def render_registry_block(
    registry: list[dict[str, Any]], max_chars: int = _REGISTRY_MAX_CHARS,
) -> str:
    """Render a bounded, citation-labelled source block for the prompt."""
    if max_chars <= 0 or not registry:
        return "(no sources were supplied for this run)"
    lines = []
    for record in registry:
        title = " ".join(str(record.get("title", "")).split())
        snippet = " ".join(str(record.get("snippet", "")).split())
        lines.append(
            f"[{record.get('id', '?')}|{record.get('kind', 'model')}] {title}: {snippet}"
        )
    block = "\n".join(lines)
    if len(block) <= max_chars:
        return block
    marker = "\n... [truncated]"
    keep = max_chars - len(marker)
    return block[:keep] + marker if keep > 0 else marker[:max_chars]


def _coerce_source_ids(entries: Any, known_ids: set[str] | None) -> list[str]:
    """Return a deduped list of valid source IDs from a ``sources`` field."""
    if entries is None:
        return []
    if not isinstance(entries, list):
        entries = [entries]
    ids: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            value = entry.strip().lstrip("[").rstrip("]")
        elif isinstance(entry, dict):
            value = str(entry.get("id") or entry.get("url") or "").strip()
        elif hasattr(entry, "id"):
            value = str(entry.id).strip()
        else:
            value = ""
        if not value:
            continue
        if known_ids is not None and value not in known_ids:
            continue  # unknown ID -> drop
        if value not in ids:
            ids.append(value)
    return ids


def _strip_unknown_citations(text: str, known_ids: set[str] | None) -> str:
    """Remove ``[S#]`` markers that reference an unknown ID."""
    if not isinstance(text, str) or known_ids is None:
        return text
    return _CITATION_RE.sub(
        lambda match: match.group(0) if match.group(1) in known_ids else "",
        text,
    )


def _load_json_report(content: str) -> dict[str, Any]:
    """Parse the LLM JSON with one in-process retry, then fail cleanly."""
    cleaned = content.strip() if isinstance(content, str) else ""
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        # Single retry: salvage a JSON object embedded in surrounding prose.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("Synthesis response did not contain a JSON object")
        try:
            parsed = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("Malformed synthesis JSON response") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Synthesis JSON response was not an object")
    return parsed


def _grounding_summary(claims: list[dict[str, Any]], registry: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the deterministic grounding summary using the WP1 contract."""
    try:
        return EvidenceLedger().grounding_summary({
            "claims": claims,
            "sources": registry,
        })
    except Exception:  # pragma: no cover - defensive; never break a report
        return {
            "total_claims": len(claims),
            "supported": sum(1 for c in claims if c.get("status") == "supported"),
            "unverified": sum(1 for c in claims if c.get("status") == "unverified"),
            "contradicted": 0,
            "citation_coverage": 0.0,
        }


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
            A dict with all legacy report keys plus ``sources``, ``claims``,
            ``grounding``, ``stop_reason``, ``rounds_used``, ``query`` and
            ``timestamp``.
        """
        outputs = worker_outputs if isinstance(worker_outputs, list) else []
        registry, known_ids = build_source_registry(outputs)
        messages = [
            {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
            {"role": "user", "content": self._build_user_message(
                outputs, original_query, conversation_history, registry=registry)},
        ]

        try:
            response = await self._client.chat_completion(
                model=self._model,
                messages=messages,
                stream=False,
            )
            content = response["choices"][0]["message"]["content"].strip()
            return self._parse_response(
                content, original_query,
                known_source_ids=known_ids, sources=registry,
            )
        except Exception as exc:
            # A malformed LLM response is an expected, handled path — a full
            # traceback here would flood CLI output during normal fallback.
            logger.warning("Synthesis failed, using fallback report: %s", exc)
            return self._fallback_report(outputs, original_query)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_user_message(
        self,
        worker_outputs: list[dict[str, Any]],
        original_query: str,
        conversation_history: list[dict[str, Any]] | None = None,
        registry: list[dict[str, Any]] | None = None,
    ) -> str:
        parts = [f"Original Query: {original_query}"]

        if conversation_history:
            hist_lines = ["\n--- Prior Conversation (for continuity) ---"]
            for i, turn in enumerate(conversation_history, 1):
                if not isinstance(turn, dict):
                    continue
                prev_q = turn.get("query", "")
                prev_rep = turn.get("report")
                if isinstance(prev_rep, dict):
                    prev_sum = str(prev_rep.get("executive_summary", ""))[:500]
                    prev_conc = str(prev_rep.get("conclusion", ""))[:500]
                    hist_lines.append(f"Turn {i}: Q: {prev_q}")
                    hist_lines.append(f"  Summary: {prev_sum}")
                    hist_lines.append(f"  Conclusion: {prev_conc}")
                else:
                    hist_lines.append(f"Turn {i}: Q: {prev_q} (no report)")
            parts.append("\n".join(hist_lines))

        if registry is None:
            registry, _ = build_source_registry(worker_outputs)

        parts.append("")
        parts.append("--- Source Registry (cite ONLY these IDs) ---")
        parts.append(render_registry_block(registry))
        parts.append("")
        parts.append("--- Worker Outputs ---\n")

        for i, wo in enumerate(worker_outputs, 1):
            if not isinstance(wo, dict):
                continue
            parts.append(f"Worker {i}:")
            parts.append(f"  Findings: {wo.get('findings', 'N/A')}")
            parts.append(f"  Key Points: {json.dumps(wo.get('key_points', []), default=str)}")
            parts.append(f"  Confidence: {wo.get('confidence', 'N/A')}")
            parts.append(f"  Gaps: {json.dumps(wo.get('gaps', []), default=str)}")
            cited = _source_ids_for_worker(wo.get("sources_cited") or [], registry)
            if cited:
                parts.append(f"  Sources: {', '.join(f'[{sid}]' for sid in cited)}")
            parts.append("")

        parts.append(
            "Remember: cite inline as [S1], list the same IDs in each section's "
            '"sources" array, and never invent an ID.'
        )
        return "\n".join(parts)

    def _parse_response(
        self,
        content: str,
        original_query: str,
        *,
        known_source_ids: set[str] | None = None,
        sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Extract and validate the JSON report from the LLM response."""
        report = _load_json_report(content)
        registry = sources if isinstance(sources, list) else []
        known = known_source_ids if known_source_ids is not None else {
            str(src.get("id")) for src in registry if isinstance(src, dict) and src.get("id")
        }

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

        # Validate section citations and attach one claim per section.
        sections = report.get("sections")
        if not isinstance(sections, list):
            sections = []
        claims: list[dict[str, Any]] = []
        valid_sections: list[dict[str, Any]] = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            source_ids = _coerce_source_ids(section.get("sources"), known)
            section["sources"] = source_ids
            for text_key in ("heading", "content"):
                if text_key in section:
                    section[text_key] = _strip_unknown_citations(section[text_key], known)
            heading = str(section.get("heading", "")).strip()
            body = str(section.get("content", "")).strip()
            claims.append({
                "text": heading or (body[:200] or f"Finding {len(claims) + 1}"),
                "source_ids": source_ids,
                "status": "supported" if source_ids else "unverified",
            })
            valid_sections.append(section)
        report["sections"] = valid_sections

        for text_key in ("executive_summary", "conclusion"):
            report[text_key] = _strip_unknown_citations(report.get(text_key, ""), known)
        if isinstance(report.get("contradictions"), list):
            report["contradictions"] = [
                _strip_unknown_citations(c, known) for c in report["contradictions"]
            ]

        report["claims"] = claims
        report["sources"] = registry
        report["grounding"] = _grounding_summary(claims, registry)
        report["stop_reason"] = "single_round"
        report["rounds_used"] = 1
        report["query"] = original_query
        report["timestamp"] = datetime.now().astimezone().isoformat(timespec="seconds")
        return report

    def _fallback_report(
        self,
        worker_outputs: list[dict[str, Any]],
        original_query: str,
    ) -> dict[str, Any]:
        """Build a minimal, honestly-grounded report when LLM synthesis fails."""
        outputs = worker_outputs if isinstance(worker_outputs, list) else []
        registry, _ = build_source_registry(outputs)
        sections = []
        for i, wo in enumerate(outputs, 1):
            if not isinstance(wo, dict):
                continue
            sections.append({
                "heading": f"Worker {i} Findings",
                "content": wo.get("findings", "No findings available."),
                "sources": _source_ids_for_worker(wo.get("sources_cited") or [], registry),
            })

        claims: list[dict[str, Any]] = []
        for section in sections:
            ids = section.get("sources") or []
            claims.append({
                "text": section.get("heading", "Finding"),
                "source_ids": ids,
                "status": "supported" if ids else "unverified",
            })

        summary = (
            "Synthesis was automatically generated from individual worker outputs."
            if registry else
            "No sources were available for this query. The findings below come "
            "from model knowledge only and are unverified — add a PDF, URL, or "
            "note and re-run the query to ground the answer."
        )

        return {
            "title": f"Research Report: {original_query[:60]}",
            "executive_summary": summary,
            "sections": sections,
            "conclusion": "Review individual worker sections for detailed findings.",
            "contradictions": [],
            "follow_up_questions": [
                f"Can you provide more detail on: {original_query}?",
                "What are the key sources used?",
                "Are there alternative perspectives on this topic?",
            ],
            "claims": claims,
            "sources": registry,
            "grounding": _grounding_summary(claims, registry),
            "stop_reason": "single_round",
            "rounds_used": 1,
            "query": original_query,
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
