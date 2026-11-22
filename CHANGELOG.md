# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-11-22

Focus: turn SwarmMind from a demo orchestrator into a trustworthy,
source-grounded deep-research tool. See `docs/EXECUTION-PLAN.md` for the
authoritative implementation spec.

### Added

- **Evidence ledger with stable source IDs** (`swarmmind/core/evidence.py`):
  `SourceRecord`/`Claim` models and an `EvidenceLedger` that assigns stable
  `S1`, `S2`, … IDs per run, deduplicates by URL or `doc_id:chunk`, validates
  citations (dropping unknown IDs), and produces a grounding summary.
- **Real page fetching**: the web worker can now fetch and extract page
  content (`WebConfig.fetch_pages`, `max_pages`, `timeout_seconds`) with an
  honest DuckDuckGo-snippet fallback when a page cannot be retrieved.
- **Citation-aware synthesis and grounding**: worker outputs carry
  `sources_cited` and compact `claims`; the synthesis prompt receives a
  bounded source block and reports include `sources`, `grounding`,
  `stop_reason: "single_round"` and `rounds_used: 1`.
- **Omni Model client methods**: `generate_image`, `edit_image`,
  `text_to_speech`, `transcribe_audio`, `analyze_image` and `is_omni_model`,
  with a configured `ModelsConfig.omni` collection and raw-component fallback.
- **UI/UX citation improvements**: clickable, source-linked citations in chat,
  an accessible grounding badge, grounded empty/error states, and a run
  manifest with re-run prefill; citations appear in Markdown/HTML exports.
- **Offline evaluation harness**: `tests/eval/` with deterministic-primary
  citation precision/recall and faithfulness metrics plus a small golden set
  (`docs/EVALUATION.md`, `docs/REPRODUCIBILITY.md`).
- **Lemonade v2026.47+ compatibility**: the HTTP client now reads
  `LEMONADE_API_KEY` (falling back to `LEMONADE_ADMIN_API_KEY`) from the
  environment and sends `Authorization: Bearer <key>` only when a key is
  present. See `docs/COMPATIBILITY.md`.

### Changed

- Reports now expose a stable report contract (sources + grounding fields)
  across the CLI, UI and export paths while preserving all existing keys.
- The Lemonade client uses one centralised `httpx.AsyncClient` factory for
  streaming and non-streaming requests.

### Fixed

- **Clean shutdown**: `close()` and the async context manager no longer rely
  on an uninitialised client attribute, and streaming uses a per-request
  client instead of the missing `self._client`.
- HTTP 400 responses for a missing `model` field or non-LLM model now raise
  an actionable hint instead of a cryptic raw server error.

### Deferred (roadmap, not implemented)

- The **multi-round LLM critic / gap-analysis loop**, QueryDoc selective
  propagation, DAG-style branching, batch verification and 3-attempt
  refinement are deferred. Reports intentionally record
  `stop_reason: "single_round"` and `rounds_used: 1` to keep that door open.
