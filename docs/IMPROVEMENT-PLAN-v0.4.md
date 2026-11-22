# SwarmMind v0.4 — Grounded Deep Research Improvement Plan

> Status: **DRAFT for QA review** · Created 2026-11-22 · Base: v0.3.0 (`ca593a8`)
> Goal: turn SwarmMind from a demo-able orchestrator into a **trustworthy, source-grounded deep-research tool** and a stronger AMD Lemonade Challenge entry.

---

## 1. Objective

Three outcomes, in priority order:

1. **Trust** — every factual claim in a report can be traced to a real, retrievable source. Fix the broken citation layer.
2. **Depth** — replace the one-shot conductor→workers→synthesis pass with a bounded **evidence-led research loop** that finds gaps and runs targeted follow-ups.
3. **Proof** — measure quality (citation precision/recall, faithfulness) and AMD-relevant performance, then publish it.

Non-goals: no cloud LLM calls, no new heavyweight runtime dependencies, no rewrite of the Streamlit UI, no changes to the public API that break existing config files.

---

## 2. Research basis (what informed this design)

| Source | Finding applied here |
|---|---|
| [Dossier — ledger-driven branching search](https://dl.acm.org/doi/pdf/10.1145/3786335.3813122) | Use a **persistent Research Ledger** of provenance-tracked claims with statuses `verified / corroborated / unverified / contradicted / hypothesis`. Gap analysis reads the ledger and emits targeted sub-queries. |
| [Argus — evidence assembly](https://arxiv.org/html/2605.16217) | Deduplicate evidence **at source-URL level** so parallel workers cannot inflate support; label claims `supported / contradicted / unverified`; dispatch a **batch** of verification queries; stop on budget. |
| [Optimist Deep Research](https://arxiv.org/html/2610.23299) | A **critic** scores coverage, evidence quality, citation coverage, source diversity, contradictions, recency, gaps, search saturation — and returns *actions*, not a binary verdict. Terminate when no actionable intent remains, not only on a fixed count. |
| [DAGent — evaluate-then-grow](https://arxiv.org/abs/2609.39154) | Workers emit a compact **QueryDoc** (answer, explanation, steps, confidence, uncertainties) plus a full transcript; downstream stages receive only QueryDocs (selective propagation) to bound context. Cap refinement at 3 attempts. |
| [GPT Researcher](https://github.com/assafelovic/gpt-researcher/) + [benchmark](https://github.com/assafelovic/gpt-researcher/blob/main/deep_agents/BENCHMARK.md) | Planner/executor/publisher + **crawler that reads actual pages** and source-tracks each resource: ~2× verified citations vs. search snippets (18.6 → 35.2 effective citations/report). Directly justifies fetching page content instead of trusting DuckDuckGo snippets. |
| [ALCE](https://arxiv.org/abs/2305.14627) | Evaluate citation **recall** (cited passages entail the statement) and citation **precision** (no irrelevant citations) separately, using NLI-style verification. |
| [RAGAS](https://arxiv.org/html/2309.15217v1), [RAGAS factual correctness](https://docs.ragas.io/en/latest/concepts/metrics/available%5Fmetrics/factual%5Fcorrectness/), [GroUSE](https://arxiv.org/html/2409.06595) | Faithfulness = supported statements / all statements; answer relevance; context relevance; failure modes FM6 (missing/wrong citation) and FM7 (unsupported claim). |
| [AMD Lemonade Challenge](https://www.amd.com/en/developer/resources/technical-articles/2026/join-the-lemonade-developer-challenge.html) + [Omni Models docs](https://lemonade-server.ai/docs/dev/lemonade-omni/) | Challenge explicitly says "we would love to see what you can do with Omni Models" and lists a **deep-dive performance eval** as an example submission. Shipped omni collections expose `generate_image`, `edit_image`, `text_to_speech`, `transcribe_audio`, `analyze_image`. |

---

## 3. Current gaps (verified in code)

| Gap | Evidence |
|---|---|
| **Citations are non-functional** | `Worker.run()` hardcodes `"sources_cited": []` (`core/workers.py:119`, `:128`); `orchestrator.py:284` also sets `[]`. `Synthesis._build_user_message` never sends sources to the model. |
| **Synthesis cannot cite** | `SYNTHESIS_SYSTEM_PROMPT` asks for `sources: [...]` but no source IDs are supplied; `_parse_response()` only fills missing keys, does not validate IDs. |
| **Web worker reads snippets only** | `search_web()` returns DDG `title/href/body`; no page fetching or content extraction. |
| **No verification pass** | No check that a claim is supported by its cited text; no contradiction labelling beyond a free-text LLM field. |
| **One-shot research** | `Orchestrator.run()` does conductor → workers → synthesis once. No gap analysis, no follow-up searches. |
| **Analysis worker is ungrounded by design** | System prompt says "use your internal knowledge" — outputs must be explicitly marked as uncited/model-knowledge. |
| **Context can balloon** | Full worker findings are concatenated into the synthesis prompt; no compact per-worker summary. |
| **Benchmark is throughput-only** | `benchmark.py` measures load time / TTFT / tok-s; not answer quality. |
| **Stale planning docs** | Root `LEMONADE-SWARMMIND-CONTEXT.md` and `.tako/WORKING-CONTEXT.md` say "Pre-Planning"/"62 tests"; true state is v0.3.0 / 166 passed, 1 xfailed. |

---

## 4. Target design

### 4.1 Source IDs and the Research Ledger

New module `swarmmind/core/evidence.py` (pure stdlib + pydantic, no new deps):

```python
class SourceRecord(BaseModel):
    id: str            # "S1", "S2" ... stable within a run
    kind: Literal["rag", "web", "image", "model"]
    title: str
    url: str = ""      # file path / URL / "" for model knowledge
    snippet: str       # exact text that supports a claim (truncated)
    doc_id: str = ""   # RAG source id
    chunk: int = -1

class Claim(BaseModel):
    text: str
    source_ids: list[str]
    status: Literal["verified", "corroborated", "unverified",
                    "contradicted", "hypothesis"]
```

`EvidenceLedger` responsibilities:
- `add_sources(list[SourceRecord])` — dedupe by `url` (or `doc_id:chunk`), never reassign an existing ID.
- `validate_citations(report)` — drop/flag unknown IDs, compute citation coverage.
- `gap_analysis(query, claims)` — return list of `{"intent": ..., "query": ..., "target": claim_or_question}`.

### 4.2 Worker contract change

`Worker.run()` returns:
```python
{
  "findings": "…with [S1] inline…",
  "key_points": [...],
  "sources_cited": [SourceRecord-dicts actually used],
  "confidence": "high|medium|low",
  "gaps": [...],
  "claims": [Claim-dicts],          # NEW, compact
  "transcript_ref": "worker-3",     # NEW, for selective propagation
}
```
- RAG worker: source IDs come from `ChromaStore.search()` metadata (`source_id`, `chunk_index`).
- Web worker: source IDs come from fetched pages, not search snippets.
- Analysis worker: emits `kind="model"` sources and must label claims `unverified`; no fake URLs.

### 4.3 Bounded research loop (in `orchestrator.py`)

```
preflight → conductor → [worker round] → critic → (gaps & budget?) → targeted follow-up workers
                                      ↑___________________________________________|
                                → synthesis → validate citations → report
```
- `ResearchConfig` in `config.py`: `max_rounds: int = 3`, `max_followup_per_round: int = 2`, `max_pages_per_query: int = 5`, `max_seconds: int = 300`.
- Stop when: critic reports no actionable gap, OR `max_rounds` reached, OR time budget exhausted. Record `stop_reason` in the report.
- Worker refinements capped at 3 attempts per DAGent-style guidance.
- Synthesis receives **compact claims + source snippets** (not raw transcripts), bounded by source count.

### 4.4 Critic

`core/critic.py`: one LLM call per round. Input: original query, compact claims, source diversity report, gaps from previous round. Output JSON:
```json
{"verdict": "sufficient|continue", "actions": [
   {"type": "search|verify|resolve_contradiction", "query": "...", "target": "S3 or claim id"}
], "reason": "..."}
```
No action → stop. This is the Optimist/DAGent termination rule.

### 4.5 Page fetching

`workers.py`: add `fetch_pages(urls) -> list[SourceRecord]` using `httpx` (already a dependency) + `markitdown` (already a dependency). Guard: `http/https` only, timeout, max bytes, max pages, no local file paths from web results. Fall back to DDG snippet only when fetch fails, and mark the source `unverified`.

### 4.6 Omni alignment (phase 3)

Add `Config.models.omni: str = ""` (optional collection name). When set, `LemonadeClient.chat_completion` targets the collection name (server-side orchestration) and Studio exposes `analyze_image`, `text_to_speech`, `generate_image`, `edit_image` via the existing endpoints; raw component calls remain the fallback.

### 4.7 Evaluation

`tests/eval/` + `swarmmind/eval.py`:
- Golden set of ~20 queries with local source docs.
- Metrics (LLM-as-judge via Lemonade, no cloud): faithfulness (RAGAS style), citation recall/precision (ALCE style), answer relevance, source diversity, contradiction detection.
- `swarmmind eval --report` prints a Markdown table; reuse `benchmark.py` for backend performance and combine into one "deep-dive eval" article.

---

## 5. Work packages and file ownership (for multi-agent dispatch)

| WP | Scope | Owns (exclusive write) | Depends on |
|---|---|---|---|
| **WP1 Evidence core** | `SourceRecord`, `Claim`, `EvidenceLedger`, citation validation, gap analysis | `swarmmind/core/evidence.py`, `tests/test_evidence.py` | — |
| **WP2 Workers + web fetch** | Source IDs in RAG/web/vision workers, `fetch_pages`, worker contract | `swarmmind/core/workers.py`, `tests/test_workers.py`, `tests/test_web_fetch.py` | WP1 |
| **WP3 Critic + orchestrator loop** | `Critic`, bounded loop, config fields, compact context | `swarmmind/core/critic.py`, `swarmmind/core/orchestrator.py`, `swarmmind/config.py`, `tests/test_critic.py`, `tests/test_orchestrator.py` | WP1, WP2 |
| **WP4 Synthesis + UI + eval** | Citation-aware synthesis prompt/validation, UI grounding badge, eval harness, docs refresh | `swarmmind/core/synthesis.py`, `swarmmind/ui/panels/chat.py`, `swarmmind/ui/panels/studio.py`, `swarmmind/eval.py`, `tests/test_synthesis.py`, `tests/eval/**`, `docs/IMPROVEMENT-PLAN-v0.4.md`, root context docs | WP1–WP3 |
| **WP5 Omni integration** | Optional `collection.omni` mode + OmniRouter tools | `swarmmind/lemonade/client.py`, `tests/test_lemonade_v11_5_features.py` | WP1 |

Rules for every agent: **never write outside the owned file list**, do not git commit/push, no new runtime dependencies beyond existing `pyproject.toml`, keep Python ≥3.11 typing style, run the relevant tests, report changed files and test results.

Execution order: WP1 → (WP2 ∥ WP5) → WP3 → WP4. Agents for the same wave must not touch each other's files.

---

## 6. Validation gates

1. `pytest` full suite: ≥ current 166 passed, 1 xfailed; zero new failures.
2. Mock-server integration test: report contains ≥1 valid `[S#]` citation when RAG/web sources exist; zero unknown IDs.
3. Loop test: critic stop reason recorded; `max_rounds` and time budget enforced; no infinite loop when critic keeps asking.
4. `swarmmind eval --report` produces metrics with non-empty source citations.
5. Docs: root context files updated to v0.3.0/v0.4 reality.

---

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Page fetching is slow / blocked | 3–5s timeout, max pages, snippet fallback marked `unverified`. |
| Local model produces malformed JSON | Existing fallback paths + strict validation + retry once. |
| More rounds = higher VRAM/time | `max_rounds`, sequential mode reuse, time budget, compact context. |
| Parallel agents conflict | Strict file ownership in §5; integration done by coordinator only. |
| Fake citations from ungrounded analysis worker | Mark `model` sources, exclude from citation recall, show in UI as "model knowledge". |
| No venv in workspace | Coordinator installs deps (`uv sync --extra dev` or pip) before test gates. |

---

## 8. What QA agents must answer

1. Is the plan feasible with the current code and no new heavy deps?
2. Are the work packages correctly decomposed and file-ownership conflict-free?
3. Are there missing pieces (evaluation, safety, backwards compatibility, AMD judging angle)?
4. Is the priority order right for "next level + usefulness"?
5. What must be fixed before implementation starts?
