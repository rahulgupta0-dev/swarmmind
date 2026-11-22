"""Evaluation harness for SwarmMind v0.4.

The harness runs a small golden set through a **local, offline mock** of the
Lemonade chat-completion API (so no server or network is required) and scores the
resulting reports with **deterministic-primary** metrics:

* valid citation IDs (precision of ``[S#]`` markers against the source registry),
* source retrievability (a URL/path is present and, for local paths, resolvable),
* quoted-span match (cited snippets contain an expected key phrase),
* unsupported-claim count, and
* source diversity (unique sources / distinct source kinds).

An **optional secondary** LLM-as-judge can score faithfulness and answer
relevance. It uses a *different* model than the generator, is blinded to the
generator's identity and randomizes source order. Its scores are always labelled
``approximate`` and are never substituted for the deterministic metrics. The
generator is never used as its own judge.

Public API: :func:`compute_metrics`, :func:`run_eval`, :func:`render_report`.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from swarmmind.config import Config
from swarmmind.core.synthesis import Synthesis

__all__ = ["compute_metrics", "run_eval", "render_report", "load_golden"]

DEFAULT_GOLDEN_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "eval" / "golden.json"
)
_CITATION_RE = re.compile(r"\[(S\d+)\]")
_JUDGE_SYSTEM_MARKER = "blind evaluation judge"
_EVAL_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Golden set
# ---------------------------------------------------------------------------


def load_golden(path: str | Path | None = None) -> dict[str, Any]:
    """Load and lightly validate the golden-set JSON file."""
    golden_path = Path(path) if path else DEFAULT_GOLDEN_PATH
    data = json.loads(golden_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("queries"), list):
        raise ValueError(f"Invalid golden set: {golden_path}")
    if not data["queries"]:
        raise ValueError(f"Golden set is empty: {golden_path}")
    return data


# ---------------------------------------------------------------------------
# Deterministic metrics
# ---------------------------------------------------------------------------


def _as_source_dict(raw: Any) -> dict[str, Any]:
    """Coerce a registry entry (dict, model or legacy string) to a dict."""
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    if isinstance(raw, str):
        return {"id": "", "kind": "web", "title": raw, "url": raw, "snippet": ""}
    if isinstance(raw, dict):
        return raw
    return {}


def _report_text(report: dict[str, Any]) -> str:
    """Concatenate every free-text field that may contain citations."""
    parts: list[str] = []
    for key in ("executive_summary", "conclusion"):
        value = report.get(key)
        if isinstance(value, str):
            parts.append(value)
    for section in report.get("sections") or []:
        if isinstance(section, dict):
            for key in ("heading", "content"):
                if isinstance(section.get(key), str):
                    parts.append(section[key])
    for contradiction in report.get("contradictions") or []:
        if isinstance(contradiction, str):
            parts.append(contradiction)
    return "\n".join(parts)


def _is_local_path(url: str) -> bool:
    """Return True when *url* looks like a filesystem path, not a web URL."""
    return not url.lower().startswith(("http://", "https://", "file://", "data:"))


def compute_metrics(
    report: dict[str, Any],
    golden_entry: dict[str, Any] | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Compute deterministic metrics for one report against a golden entry.

    The function is pure: it accepts synthetic reports directly, which makes it
    straightforward to unit-test each metric in isolation.

    Args:
        report: A v0.4 report dict (``sources``, ``claims``, ``sections``...).
        golden_entry: Optional golden-set entry supplying expected key phrases
            and a local base directory for retrievability checks.
        base_dir: Directory that relative local source paths resolve against.

    Returns:
        A dict containing a structured breakdown plus flat summary fields used
        by :func:`render_report`. Ratios are within ``[0, 1]``; a ratio of 1.0
        with a zero denominator means "nothing to penalise".
    """
    if not isinstance(report, dict):
        report = {}

    raw_sources = report.get("sources") or []
    if not isinstance(raw_sources, list):
        raw_sources = [raw_sources]
    sources = [_as_source_dict(raw) for raw in raw_sources]
    sources = [src for src in sources if src]

    known_ids = {
        str(src["id"])
        for src in sources
        if src.get("id") not in (None, "")
    }

    # ---- Valid citation IDs -------------------------------------------------
    markers = _CITATION_RE.findall(_report_text(report))
    for section in report.get("sections") or []:
        if not isinstance(section, dict):
            continue
        entries = section.get("sources") or []
        if not isinstance(entries, list):
            entries = [entries]
        for entry in entries:
            value = entry if isinstance(entry, str) else (
                str(entry["id"]) if isinstance(entry, dict) and entry.get("id") else ""
            )
            markers.extend(_CITATION_RE.findall(f"[{value}]") if value else [])

    valid = [mid for mid in markers if mid in known_ids]
    unknown_ids = sorted({mid for mid in markers if mid not in known_ids})
    precision = len(valid) / len(markers) if markers else 1.0

    # ---- Source retrievability ---------------------------------------------
    retrievable = 0
    for src in sources:
        url = str(src.get("url") or "").strip()
        if not url:
            continue
        if base_dir and _is_local_path(url):
            candidate = Path(base_dir) / url
            if candidate.exists():
                retrievable += 1
        else:
            retrievable += 1
    retrievability_ratio = retrievable / len(sources) if sources else 0.0

    # ---- Quoted-span match --------------------------------------------------
    phrases: list[str] = []
    if golden_entry:
        raw_phrases = golden_entry.get("key_phrases")
        if isinstance(raw_phrases, list):
            phrases = [str(p) for p in raw_phrases if str(p).strip()]
        if not phrases:
            for src in golden_entry.get("sources") or []:
                if isinstance(src, dict):
                    phrases.extend(
                        str(p) for p in (src.get("key_phrases") or []) if str(p).strip()
                    )
    cited_ids = set(valid)
    snippets: list[str] = []
    for src in sources:
        sid = str(src.get("id") or "")
        if not cited_ids or sid in cited_ids:
            snippets.append(str(src.get("snippet") or ""))
    haystack = "\n".join(snippets).lower()
    matched = [p for p in phrases if p.lower() in haystack]
    span_ratio = len(matched) / len(phrases) if phrases else 1.0

    # ---- Unsupported claims -------------------------------------------------
    claims = report.get("claims") or []
    if not isinstance(claims, list):
        claims = [claims]
    unsupported = 0
    for claim in claims:
        if isinstance(claim, dict):
            status = str(claim.get("status", "unverified"))
            ids = claim.get("source_ids") or []
        elif hasattr(claim, "model_dump"):
            data = claim.model_dump()
            status = str(data.get("status", "unverified"))
            ids = data.get("source_ids") or []
        else:
            status, ids = "unverified", []
        if isinstance(ids, str):
            ids = [ids]
        has_source = any(str(i) in known_ids for i in ids)
        if status not in ("supported", "corroborated") or not has_source:
            unsupported += 1
    unsupported_ratio = unsupported / len(claims) if claims else 0.0

    # ---- Source diversity ---------------------------------------------------
    kinds = sorted({
        str(src.get("kind") or "model") for src in sources
    })
    unique_sources = len(known_ids) if known_ids else len(sources)

    return {
        "citation_ids": {
            "total": len(markers),
            "valid": len(valid),
            "unknown_ids": unknown_ids,
            "precision": precision,
        },
        "source_retrievability": {
            "total": len(sources),
            "retrievable": retrievable,
            "ratio": retrievability_ratio,
        },
        "quoted_span_match": {
            "total_phrases": len(phrases),
            "matched": len(matched),
            "missing_phrases": [p for p in phrases if p not in matched],
            "ratio": span_ratio,
        },
        "unsupported_claims": {
            "count": unsupported,
            "total_claims": len(claims),
            "ratio": unsupported_ratio,
        },
        "source_diversity": {
            "unique_sources": unique_sources,
            "distinct_kinds": len(kinds),
            "kinds": kinds,
        },
        # Flat fields (convenient for aggregation / rendering).
        "valid_citation_ratio": precision,
        "source_retrievability_ratio": retrievability_ratio,
        "quoted_span_match_ratio": span_ratio,
        "unsupported_claim_count": unsupported,
        "unsupported_claim_ratio": unsupported_ratio,
        "unique_sources": unique_sources,
        "distinct_source_kinds": len(kinds),
    }


# ---------------------------------------------------------------------------
# Local, offline Lemonade stand-in
# ---------------------------------------------------------------------------


class LocalEvalClient:
    """Offline stand-in for :class:`LemonadeClient.chat_completion`.

    It generates a deterministic, well-formed synthesis report from the source
    registry embedded in the synthesis user message, and answers the secondary
    judge prompt with bounded, deterministic scores. No HTTP is performed.
    """

    def __init__(
        self,
        entries: list[dict[str, Any]],
        generator_model: str,
        judge_model: str | None = None,
    ) -> None:
        self.entries = entries
        self.generator_model = generator_model
        self.judge_model = judge_model
        self.calls: list[dict[str, str]] = []
        self._by_query = {
            str(entry.get("query", "")): entry for entry in entries
        }

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return an OpenAI-shaped completion dict (no network)."""
        system = "\n".join(
            str(m.get("content", ""))
            for m in messages
            if m.get("role") == "system"
        )
        user = "\n".join(
            str(m.get("content", ""))
            for m in messages
            if m.get("role") == "user"
        )

        if _JUDGE_SYSTEM_MARKER in system:
            self.calls.append({"model": model, "role": "judge"})
            content = self._judge_content(user)
        else:
            self.calls.append({"model": model, "role": "generator"})
            content = json.dumps(self._generator_report(user))
        return {"choices": [{"message": {"content": content}}]}

    def _entry_for(self, user: str) -> dict[str, Any]:
        match = re.search(r"Original Query:\s*(.+)", user)
        query = match.group(1).strip() if match else ""
        return self._by_query.get(query, self.entries[0])

    def _generator_report(self, user: str) -> dict[str, Any]:
        entry = self._entry_for(user)
        ids = re.findall(r"^\[(S\d+)\|", user, flags=re.MULTILINE)
        if not ids:
            ids = [f"S{i + 1}" for i, _ in enumerate(entry.get("sources") or [])]
        if not ids:
            ids = ["S1"]
        markers = " ".join(f"[{sid}]" for sid in ids)
        query = str(entry.get("query", ""))
        return {
            "title": f"Research Report: {query[:60]}",
            "executive_summary": (
                f"Local evaluation summary for this query, drawn from the "
                f"retrieved source registry {markers}."
            ),
            "sections": [
                {
                    "heading": "Findings from retrieved sources",
                    "content": f"The retrieved evidence is reported below {markers}.",
                    "sources": ids,
                }
            ],
            "conclusion": f"The cited sources support this summary {markers}.",
            "contradictions": [],
            "follow_up_questions": [
                f"Can you provide more detail on: {query}?",
                "Are there alternative sources for this topic?",
            ],
        }

    def _judge_content(self, user: str) -> str:
        match = re.search(r"Query:\s*(.+)", user)
        query = match.group(1).strip() if match else ""
        seed = sum(ord(ch) for ch in query)
        scores = {
            "faithfulness": 3 + (seed % 3),
            "answer_relevance": 4 + (seed % 2),
            "notes": "Approximate blind judgement; not ground truth.",
        }
        return json.dumps(scores)


# ---------------------------------------------------------------------------
# Secondary LLM-as-judge (always approximate, never the generator)
# ---------------------------------------------------------------------------


def _pick_judge_model(
    config: Config, generator_model: str, requested: str | None,
) -> tuple[str | None, str | None]:
    """Choose a judge model that is guaranteed to differ from the generator."""
    if requested:
        if requested == generator_model:
            return None, (
                "requested judge model equals generator model; the generator "
                "is never used as its own judge"
            )
        return requested, None
    candidates = [
        config.models.worker,
        config.models.omni,
        config.models.conductor,
    ]
    for candidate in candidates:
        if candidate and candidate != generator_model:
            return candidate, None
    return None, "no different model is configured for the judge role"


def _blinded_payload(
    report: dict[str, Any], entry: dict[str, Any], seed: int, query_id: str,
) -> str:
    """Render a blinded, order-randomized view for the judge."""
    rng = random.Random(f"{seed}:{query_id}")
    sources = [_as_source_dict(raw) for raw in (report.get("sources") or [])]
    rng.shuffle(sources)
    labels = [chr(ord("A") + i) for i in range(len(sources))]
    source_block = "\n".join(
        f"Doc {label}: {src.get('title', 'Untitled')}: "
        f"{' '.join(str(src.get('snippet', '')).split())}"
        for label, src in zip(labels, sources)
    ) or "(no sources supplied)"
    sections = [s for s in (report.get("sections") or []) if isinstance(s, dict)]
    rng.shuffle(sections)
    answer_parts = []
    for section in sections:
        text = _CITATION_RE.sub("", str(section.get("content", "")))
        answer_parts.append(text.strip())
    answer = "\n".join(p for p in answer_parts if p) or "(empty answer)"
    return (
        f"Query: {entry.get('query', report.get('query', ''))}\n\n"
        f"--- Documents (order randomized, IDs hidden) ---\n{source_block}\n\n"
        f"--- Anonymous answer (citation markers removed) ---\n{answer}"
    )


async def _judge_one(
    client: Any,
    report: dict[str, Any],
    entry: dict[str, Any],
    judge_model: str,
    seed: int,
) -> dict[str, Any]:
    """Score one report with the blinded secondary judge."""
    payload = _blinded_payload(report, entry, seed, str(entry.get("id", "?")))
    messages = [
        {
            "role": "system",
            "content": (
                "You are a blind evaluation judge. Score faithfulness to the "
                "documents and answer relevance to the query, each from 0 to 5. "
                "Do not mention which system produced the answer. Respond with "
                'JSON only: {"faithfulness": int, "answer_relevance": int, '
                '"notes": str}.'
            ),
        },
        {"role": "user", "content": payload},
    ]
    result: dict[str, Any] = {
        "judge_model": judge_model,
        "approximate": True,
        "blinded": True,
        "order_randomized": True,
        "seed": seed,
    }
    try:
        response = await client.chat_completion(
            model=judge_model, messages=messages, stream=False,
        )
        content = response["choices"][0]["message"]["content"]
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].replace("```", "").strip()
        parsed = json.loads(cleaned)
        result["faithfulness"] = min(5.0, max(0.0, float(parsed.get("faithfulness", 0))))
        result["answer_relevance"] = min(
            5.0, max(0.0, float(parsed.get("answer_relevance", 0))),
        )
        result["notes"] = str(parsed.get("notes", ""))
    except Exception as exc:  # pragma: no cover - defensive, judge is optional
        result["error"] = str(exc)
        result["faithfulness"] = None
        result["answer_relevance"] = None
    return result


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def run_eval(
    *,
    config: Config | None = None,
    client: Any | None = None,
    golden_path: str | Path | None = None,
    enable_judge: bool = False,
    judge_model: str | None = None,
    seed: int = 0,
) -> dict[str, Any]:
    """Run the golden set and return metrics, optional judge scores, manifest.

    Args:
        config: Application config (default: load from disk/env). Used for the
            generator model, backend snapshot and judge-model selection.
        client: Optional chat-completion client. By default an offline
            :class:`LocalEvalClient` is used, so the command works with no
            Lemonade server and no network.
        golden_path: Path to golden-set JSON (defaults to ``tests/eval/golden.json``).
        enable_judge: Enable the *secondary* LLM-as-judge. Off by default.
        judge_model: Judge model name; must differ from the generator model.
        seed: Seed for blinded order randomization (recorded in the manifest).

    Returns:
        ``{"manifest": ..., "aggregate": ..., "results": [...], "judge": ...}``.
    """
    config = config if config is not None else Config()  # type: ignore[call-arg]
    path = Path(golden_path) if golden_path else DEFAULT_GOLDEN_PATH
    golden = load_golden(path)
    entries: list[dict[str, Any]] = golden["queries"]
    base_dir = path.parent
    generator_model = config.models.conductor

    judge_selected: str | None = None
    judge_reason: str | None = None
    if enable_judge:
        judge_selected, judge_reason = _pick_judge_model(
            config, generator_model, judge_model,
        )
    else:
        judge_reason = "disabled by default; secondary signal only"

    eval_client = client if client is not None else LocalEvalClient(
        entries, generator_model, judge_selected,
    )

    async def _generate() -> list[dict[str, Any]]:
        synthesis = Synthesis(eval_client, generator_model)
        results: list[dict[str, Any]] = []
        for entry in entries:
            sources = []
            for index, source in enumerate(entry.get("sources") or []):
                record = dict(source)
                record["id"] = f"S{index + 1}"
                sources.append(record)
            worker_outputs = [{
                "findings": " ".join(
                    str(src.get("snippet", "")) for src in entry.get("sources") or []
                ),
                "sources_cited": sources,
                "key_points": [],
                "confidence": "high",
                "gaps": [],
            }]
            report = await synthesis.synthesize(
                worker_outputs, str(entry.get("query", "")),
            )
            item: dict[str, Any] = {
                "query_id": entry.get("id", "?"),
                "query": entry.get("query", ""),
                "report": report,
                "metrics": compute_metrics(report, entry, base_dir),
                "stop_reason": report.get("stop_reason", "n/a"),
                "rounds_used": report.get("rounds_used", 1),
            }
            if judge_selected:
                item["judge"] = await _judge_one(
                    eval_client, report, entry, judge_selected, seed,
                )
            results.append(item)
        return results

    results = asyncio.run(_generate())

    flat_keys = [
        "valid_citation_ratio",
        "source_retrievability_ratio",
        "quoted_span_match_ratio",
        "unsupported_claim_count",
        "unsupported_claim_ratio",
        "unique_sources",
        "distinct_source_kinds",
    ]
    aggregate: dict[str, Any] = {
        "n_queries": len(results),
        **{
            key: _mean([float(r["metrics"][key]) for r in results])
            for key in flat_keys
        },
    }

    manifest: dict[str, Any] = {
        "eval_version": _EVAL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator_model": generator_model,
        "judge_model": judge_selected,
        "judge_enabled": judge_selected is not None,
        "judge_skipped_reason": judge_reason,
        "backends": {
            role: (config.backend_for(role) or "auto")
            for role in ("conductor", "worker", "embeddings", "image", "tts")
        },
        "config_snapshot": config.model_dump(),
        "stop_reasons": sorted({str(r["stop_reason"]) for r in results}),
        "rounds_used": sorted({int(r["rounds_used"]) for r in results}),
        "seed": seed,
        "client": "LocalEvalClient" if client is None else type(client).__name__,
        "golden_set": {
            "path": str(path),
            "version": golden.get("version", "unknown"),
            "count": len(entries),
        },
    }

    judge_section: dict[str, Any] = {
        "enabled": judge_selected is not None,
        "approximate": True,
        "skipped_reason": judge_reason,
        "model": judge_selected,
    }
    if judge_selected:
        judge_rows = [r["judge"] for r in results if "judge" in r]
        judge_section["aggregate"] = {
            "faithfulness": _mean([
                float(r["faithfulness"]) for r in judge_rows
                if r.get("faithfulness") is not None
            ]),
            "answer_relevance": _mean([
                float(r["answer_relevance"]) for r in judge_rows
                if r.get("answer_relevance") is not None
            ]),
        }

    return {
        "manifest": manifest,
        "aggregate": aggregate,
        "results": results,
        "judge": judge_section,
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _pct(value: float) -> str:
    return f"{value:.0%}"


def render_report(result: dict[str, Any]) -> str:
    """Render an eval result as a Markdown table with honest caveats."""
    manifest = result.get("manifest") or {}
    aggregate = result.get("aggregate") or {}
    results = result.get("results") or []
    judge = result.get("judge") or {}

    def _cell(value: object) -> str:
        """Escape a value so it cannot break a Markdown table cell."""
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    lines: list[str] = [
        "# SwarmMind Evaluation Report",
        "",
        f"**Generated:** {manifest.get('generated_at', 'n/a')}  ",
        f"**Golden set:** {manifest.get('golden_set', {}).get('count', '?')} queries "
        f"(v{manifest.get('golden_set', {}).get('version', '?')})  ",
        f"**Generator model:** `{manifest.get('generator_model', 'n/a')}`  ",
        f"**Stop reason:** `{', '.join(manifest.get('stop_reasons', [])) or 'n/a'}`  ",
        f"**Client:** `{manifest.get('client', 'n/a')}`",
        "",
        "## Deterministic metrics (primary)",
        "",
        "| Query | Valid citations | Retrievable sources | Quoted-span match | "
        "Unsupported claims | Sources / kinds |",
        "|-------|-----------------|---------------------|-------------------|"
        "--------------------|-----------------|",
    ]
    for item in results:
        metrics = item.get("metrics") or {}
        lines.append(
            f"| {_cell(item.get('query_id', '?'))}: {_cell(item.get('query', '')[:42])} "
            f"| {_pct(float(metrics.get('valid_citation_ratio', 0.0)))} "
            f"| {_pct(float(metrics.get('source_retrievability_ratio', 0.0)))} "
            f"| {_pct(float(metrics.get('quoted_span_match_ratio', 0.0)))} "
            f"| {int(metrics.get('unsupported_claim_count', 0))} "
            f"| {int(metrics.get('unique_sources', 0))} / "
            f"{int(metrics.get('distinct_source_kinds', 0))} |"
        )
    lines.append(
        f"| **Aggregate ({int(aggregate.get('n_queries', 0))} queries)** "
        f"| {_pct(float(aggregate.get('valid_citation_ratio', 0.0)))} "
        f"| {_pct(float(aggregate.get('source_retrievability_ratio', 0.0)))} "
        f"| {_pct(float(aggregate.get('quoted_span_match_ratio', 0.0)))} "
        f"| {float(aggregate.get('unsupported_claim_count', 0.0)):.1f} "
        f"| {float(aggregate.get('unique_sources', 0.0)):.1f} / "
        f"{float(aggregate.get('distinct_source_kinds', 0.0)):.1f} |"
    )
    lines.append("")

    if judge.get("enabled"):
        agg = judge.get("aggregate") or {}
        lines += [
            "## Secondary LLM-as-judge — APPROXIMATE",
            "",
            f"Judge model `{judge.get('model', 'n/a')}` (different from the "
            "generator), blinded and order-randomized. These scores are "
            "**approximate opinions, not ground truth**.",
            "",
            "| Query | Faithfulness (0–5) | Answer relevance (0–5) |",
            "|-------|---------------------|------------------------|",
        ]
        for item in results:
            j = item.get("judge") or {}
            lines.append(
                f"| {item.get('query_id', '?')} "
                f"| {j.get('faithfulness', 'n/a')} "
                f"| {j.get('answer_relevance', 'n/a')} |"
            )
        lines.append(
            f"| **Aggregate** | {float(agg.get('faithfulness', 0.0)):.2f} "
            f"| {float(agg.get('answer_relevance', 0.0)):.2f} |"
        )
    else:
        lines += [
            "## Secondary LLM-as-judge — not run",
            "",
            f"Reason: {judge.get('skipped_reason', 'disabled')}. The generator is "
            "never used as its own judge.",
        ]

    lines += [
        "",
        "## Caveats",
        "",
        "- This is a **small golden set** (start at 8 queries). Do not treat it "
        "as a leaderboard or a statistically strong claim.",
        "- With the default local mock client, the harness validates the "
        "measurement pipeline end-to-end; it does **not** measure the quality "
        "of a real generator model. Re-run against real Lemonade models for "
        "model-quality numbers.",
        "- Deterministic metrics check structure (citation validity, "
        "retrievability, quoted spans, claim support, diversity) — they do not "
        "prove semantic truth of the answer.",
        "- LLM-as-judge scores are approximate, model-dependent and biased; "
        "they are secondary and never replace the deterministic metrics.",
        "- Combine with `swarmmind benchmark` (throughput/TTFT) for a complete, "
        "honest deep-dive — no speed-vs-quality leaderboard claims are made.",
        "- Web search / page fetching are opt-in (see `Config.web`); the default "
        "eval path is fully offline and local.",
        "",
    ]
    return "\n".join(lines)
