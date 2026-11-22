# SwarmMind v0.4 — Final Execution Plan (QA-reviewed)

> This supersedes `docs/IMPROVEMENT-PLAN-v0.4.md` (research draft). Two QA agents reviewed that draft and both returned **APPROVE WITH FIXES**. This file is the authoritative implementation spec.
> Base: v0.3.0 (`ca593a8`) · Baseline tests: **166 passed, 1 xfailed** in `.venv` · Python 3.13 environment, no `uv`; use `.venv/bin/python -m pytest`.

---

## 1. Scope decisions after QA

**In first release:**
- Real source IDs and citations end-to-end (backend → report → UI → export).
- Page fetching with honest snippet fallback.
- Deterministic citation validation + grounding summary (no LLM self-judge in the critical path).
- Source-linked, accessible UI/UX improvements and a run manifest.
- One visible Omni Model path (image + narration) with raw-component fallback.
- Independent evaluation harness with deterministic-primary metrics and a small golden set (start at 8 queries, grow to 20).

**Deferred (documented as roadmap, not implemented now):** multi-round LLM critic/gap loop, QueryDoc + selective propagation, DAG-style branching, batch verification, 3-attempt refinement. Reason: laptop-class local models, latency/VRAM cost, and risk of self-preference bias. Add `stop_reason: "single_round"` and `rounds_used: 1` to the report so the door stays open.

---

## 2. Exact cross-package contracts

### `swarmmind/core/evidence.py` (WP1)
```python
class SourceRecord(BaseModel):
    id: str                      # "S1", "S2" ... stable within a run
    kind: Literal["rag", "web", "image", "model"]
    title: str
    url: str = ""                # URL, file path, or "" for model knowledge
    snippet: str = ""            # exact supporting text, capped ~1200 chars
    doc_id: str = ""             # RAG source id
    chunk: int = -1

class Claim(BaseModel):
    text: str
    source_ids: list[str] = Field(default_factory=list)
    status: Literal["supported", "unverified", "contradicted"] = "unverified"

class EvidenceLedger:
    def add_sources(self, sources: list[SourceRecord | dict]) -> None: ...
    def get(self, source_id: str) -> SourceRecord | None: ...
    def as_prompt_block(self, max_chars: int = 6000) -> str: ...
    def validate_citations(self, report: dict) -> dict: ...
    def grounding_summary(self, report: dict) -> dict: ...
```
- Dedupe by `url` (web) or `doc_id:chunk` (RAG); never reassign an existing ID.
- `validate_citations` MUST accept both `SourceRecord` dicts and plain strings (legacy mocks use URL strings) and MUST filter unknown IDs from `sections[].sources`.
- `grounding_summary` returns `{"total_claims": int, "supported": int, "unverified": int, "contradicted": int, "citation_coverage": float}`.

### Worker output (WP2)
Existing keys preserved. Add:
```python
{
  ...,
  "sources_cited": [SourceRecord-dicts],   # primary; strings tolerated by validator
  "claims": [Claim-dicts],                 # NEW, compact
}
```
- RAG IDs from `ChromaStore.search()` metadata (`source_id`, `chunk_index`); metadata has no `title`/`url`, so synthesize title from `source_id`.
- Web IDs from fetched pages; fallback to snippet marked `unverified`.
- Analysis/vision with no external source emit `kind="model"` and `status="unverified"`.
- All new keys accessed with `.get()` in orchestrator — existing mocks in `tests/test_execution_mode.py` and `tests/test_hardware.py` must keep passing.

### Report output (WP3)
All existing keys preserved. Add:
```python
{
  ...,
  "sources": [SourceRecord-dicts],
  "grounding": { ...grounding_summary... },
  "stop_reason": "single_round",
  "rounds_used": 1,
}
```

### Lemonade client Omni methods (WP5)
```python
async def generate_image(self, prompt: str, model: str | None = None, size: str = "1024x1024") -> dict
async def edit_image(self, base64_image: str, prompt: str, model: str | None = None) -> dict
async def text_to_speech(self, text: str, model: str | None = None, voice: str = "af") -> bytes
async def transcribe_audio(self, audio_bytes: bytes, model: str | None = None) -> dict
async def analyze_image(self, base64_image: str, prompt: str, model: str | None = None) -> dict
def is_omni_model(self, model_name: str) -> bool
```
Config: `ModelsConfig.omni: str = ""` (optional collection name, e.g. `LMX-Omni-5.5B-Lite`); new `WebConfig(fetch_pages=True, max_pages=5, timeout_seconds=5.0)` exposed as `Config.web`. All fields have defaults so old config files still load (`extra="ignore"`).

---

## 3. Work packages and waves

| WP | Wave | Scope | Exclusive write ownership | Depends on |
|---|---|---|---|---|
| **WP1** | 1 | Evidence core: SourceRecord, Claim, EvidenceLedger, validation, grounding summary | `swarmmind/core/evidence.py`, `tests/test_evidence.py` | — |
| **WP5** | 1 | Omni config + client methods | `swarmmind/config.py`, `swarmmind/lemonade/client.py`, `tests/test_lemonade_v11_5_features.py` | — |
| **WP2** | 2 | Workers + `fetch_pages` + orchestrator source wiring | `swarmmind/core/workers.py`, `swarmmind/core/orchestrator.py`, `tests/test_workers.py`, `tests/test_web_fetch.py`, `tests/test_orchestrator.py` | WP1, WP5 (WebConfig) |
| **WP3** | 2 | Citation-aware synthesis, **UI/UX overhaul**, CLI eval/report commands, exports with sources | `swarmmind/core/synthesis.py`, `swarmmind/ui/app.py`, `swarmmind/ui/panels/chat.py`, `swarmmind/ui/panels/studio.py`, `swarmmind/ui/panels/sources.py`, `swarmmind/cli/main.py`, `tests/test_synthesis.py`, `tests/test_ui_citations.py` | WP1, WP5 (client contract) |
| **WP4** | 3 | Evaluation harness + golden set + docs | `swarmmind/eval.py`, `tests/eval/**`, `tests/test_eval.py`, `README.md`, `docs/EVALUATION.md`, `docs/REPRODUCIBILITY.md` | WP1–WP3 |

**Hard rule:** agents may only write files in their ownership column. Never git commit or push. No new runtime dependencies. Python ≥3.11 typing. Use `.venv/bin/python -m pytest` for tests.

---

## 4. UI/UX requirements (WP3) — "better UI and UX"

1. **Clickable citations**: `[S1]` markers in the chat report become links that scroll to / expand the matching source card; source cards show title, kind badge, URL or file path, and the exact snippet used.
2. **Grounding badge**: compact summary ("9/11 claims supported · 2 unverified") with colour + text label (`aria`-friendly). Unverified/model-knowledge claims visually distinct.
3. **Sources-before-answer**: while workers run, show source cards as they arrive (phase indicators + worker status already exist; extend them).
4. **Grounded empty/error states**: if no sources are configured, say "Add a PDF, URL, or note to ground your research" and offer a one-click jump to Sources. If evidence is insufficient, produce a grounded refusal instead of a confident answer.
5. **Fix source-status refresh**: ingestion status currently updates only on next interaction; add a lightweight refresh (`st.rerun()` or timed poll) so `pending → processing → ready/error` is visible live.
6. **Run manifest + re-run**: show models, backends, `stop_reason`, rounds, timestamp, config snapshot; add "Re-run this query" prefill.
7. **Export quality**: Markdown and HTML include inline citations plus a Sources appendix and the manifest.
8. **Privacy toggle**: web search / page fetch opt-in in Settings so the "100% local" claim stays honest; show a small "external lookup used" notice when enabled.
9. **Accessibility & polish**: keyboard-accessible citation markers, readable contrast, clear loading/empty states, responsive panel layout.

---

## 5. Evaluation requirements (WP4)

- Deterministic-primary metrics: valid citation IDs, URL/file retrievability, quoted-span match, unsupported-claim count, source diversity.
- Secondary LLM-as-judge (faithfulness, answer relevance) uses a **different** Lemonade model, blinded and order-randomized; report it as approximate.
- Golden set starts at 8 queries with local source docs; publish the set and rubric in `docs/EVALUATION.md`.
- `swarmmind eval --report` prints a Markdown table; combine with `benchmark.py` results into one honest "deep-dive" article (no leaderboard claims, include caveats).
- Reproducibility: capture manifest with every eval run.

---

## 6. Validation gates

1. Full suite: **≥166 passed, 1 xfailed**, zero new failures.
2. Mock-server test: report has ≥1 valid `[S1]`-style citation and zero unknown IDs when sources exist.
3. Backward compat: legacy mocks with `sources_cited` as strings and no `claims` still pass.
4. UI/export: citations clickable in chat and present in MD/HTML export; grounding badge renders with 0 sources.
5. Omni: methods exist, `is_omni_model()` works, Studio diagram/narration buttons work with raw-component fallback.
6. Docs: stale root context files updated to v0.3.0/v0.4 reality (coordinator owns these two files).

---

## 7. Known environment notes for agents

- `.venv` exists with all deps; use `.venv/bin/python -m pytest`.
- Earlier full-run hangs were caused by running pytest in a chained command; a plain `timeout 90 .venv/bin/python -m pytest -q` completes in ~9s.
- `test_integration.py` uses `sources_cited: ["https://…"]` strings — validator must tolerate them.
- `report_json` DB column already exists (v0.3.0), so new report fields need no migration.
