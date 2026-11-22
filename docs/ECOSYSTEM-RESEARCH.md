# Ecosystem Research — AMD / Lemonade / GAIA (as of 2026-11-22)

> Research-only document. **Nothing here has been implemented.** Ideas are ranked for a
> project at v0.4 state (see `docs/EXECUTION-PLAN.md`): grounded citations, Omni client
> methods, deterministic eval harness all landed; the multi-round LLM critic loop is
> *deliberately deferred* and is **not** proposed below. All claims were checked against
linked primary sources; release-note text for the 48.x RCs is AI-generated and marked
"subject to human revision" upstream — treat it as directional.

## 1. Dated release timeline

| Date | Release / event | Verified facts relevant to SwarmMind | Source |
|---|---|---|---|
| 2026-10-29 → **2026-11-04** | Lemonade **v2026.45.3** (RC→GA) | Full HTTP-server rewrite: route classes, consistent errors, generated API docs. GET on POST-only paths (`slots/{id}`, `jobs/{id}/*`, `models/register`) now **405 not 404**; missing `model` field yields the same `"Missing model field in request"` on all model routes (`chat/completions`, `completions`, `embeddings`, `responses`, `rerank`); malformed form-encoded fields yield **400 not 500**; streams carry a cancel token (disconnect cancels upstream). New K2-Horizon family + Flint. GUI3 redesign, Chinese locale, mobile web UI. | [GA release](https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.45.3) · [PR #3771](https://github.com/lemonade-sdk/lemonade/pull/3771) · [server spec](https://lemonade-server.ai/docs/dev/specs/http-server/) |
| **2026-11-16 → 11-18** | Lemonade **v2026.47.1** (RC→stable, current "Latest") | MTP speculative decoding auto-enabled on supported HRX models; **conversation-scoped image references** for multi-image editing / direct Vision-LLM analysis; **auth change**: empty `LEMONADE_ADMIN_API_KEY` no longer bypasses auth — a valid `LEMONADE_API_KEY` is required for API and metrics routes; `/files` now returns one entry per shard for multi-shard GGUF. Also gfx117x ROCm targets, FLM 1.0.11 decision models, LFM3-1.7B / LFM3-VL-2B. | [stable](https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.47.1) · [RC](https://github.com/lemonade-sdk/lemonade/releases/tag/candidate-v2026.47.1) |
| **2026-11-17** | **GAIA v0.26.0** | Faster first replies (dynamic tool loading on by default + cached follow-ups); Gmail send/draft/reply with approval, Google + Outlook calendar; long-recording/video-link transcript + report + subtitles; Discord bot; read-aloud speaker button; plainer model-server errors; tighter file/credential access; live CPU/GPU/NPU strip; terminal UI renamed `gaia-cli` (old Python CLI name now `gaia`). | [release notes](https://amd-gaia.ai/docs/releases/v0.26.0) · [GitHub tag](https://github.com/amd/gaia/releases/tag/v0.26.0) |
| **2026-11-18** | Lemonade **v2026.48.0 RC** | Ollama-compat gateway: `images`/`audio` on `/api/generate`, `speech`/`decision`/`audio` on `/api/show`, `/api/chat` + `/api/generate` return **400** for non-LLM models instead of 500; failed streams end with an error line. | [RC](https://github.com/lemonade-sdk/lemonade/releases/tag/candidate-v2026.48.0) |
| **2026-11-19** | **AMD × Unsloth Studio / Desktop NPU** | Lemonade *embedded SDK* + FastFlowLM bring XDNA 2 NPU inference to Unsloth Desktop; OpenAI-compatible "Unsloth Desktop API" opens NPU inference to 100+ agentic apps (Open WebUI, Cline, Hermes Agent, LangChain, Copilot…). Q4NX 4-bit models, streaming + vision + tools. Linux supported (kernel ≥7.0 `amdxdna` or `amdxdna-dkms`). Managed runtime is local-only, rotated API key, pinned SHA-256 downloads; `UNSLOTH_STUDIO_ENABLE_NPU=0` disables. | [AMD blog](https://www.amd.com/en/blogs/2026/unlocking-the-amd-ryzen-ai-npu-for-unsloth-desktop.html) · [newsroom](https://newsroom.amd.com/news/amd-unsloth-bring-npu-support-agentic-ai-applications/) · [Unsloth guide](https://unsloth.ai/docs/get-started/install/amd/npu) · [Phoronix](https://www.phoronix.com/news/Unsloth-Desktop-Ryzen-AI-NPU) |
| **2026-11-20** | Lemonade **v2026.48.3 RC** | **FLM NPU backend → v1.0.13 with AMD-signed Windows binaries, enabling embedding models on `/v1/embeddings`**; same `/api/*` 400 changes as 48.0; TheNoise → v0.15.0. Windows RC installers themselves are unsigned. | [RC](https://github.com/lemonade-sdk/lemonade/releases/tag/candidate-v2026.48.3) · [FLM v1.0.13](https://github.com/ROCm/FastFlowLM/releases/tag/v1.0.13) |

**Correction to the brief:** the `/api/generate`, `/api/show`, `/api/chat` changes in 48.x are
**Ollama-compatible gateway endpoints**, not SwarmMind's `/v1/*` OpenAI endpoints. SwarmMind is
only indirectly affected. The FLM-1.0.13 embeddings change *is* on `/v1/embeddings` and is
currently **RC-only** — verify against a stable release before relying on it.

## 2. GAIA v0.26.0 — what matters to SwarmMind

GAIA is a **separate AMD product**, not a Lemonade dependency: SwarmMind has no GAIA
integration, so there is no compatibility break here. Its value is as a **product bar** and a
source of small, copyable UX patterns:

- **Read-aloud on every reply** — SwarmMind already has `LemonadeClient.text_to_speech()`
  (`swarmmind/lemonade/client.py:263`) but never surfaces audio in the UI. A speaker button
  in `swarmmind/ui/panels/chat.py` calling `st.audio(bytes)` is a direct, low-risk win.
- **"See what is happening"** — a live CPU/GPU/NPU strip. SwarmMind already calls
  `system_stats()` in the client but the Studio panel shows only model names
  (`swarmmind/ui/panels/studio.py:171`). Surfacing NPU/GPU utilization would make the AMD
  angle visible to judges.
- **Connectors as sources** — GAIA's Gmail/calendar/Discord/recording support suggests the
  same *shape* for SwarmMind's source ingestion: new `kind` values beyond the current
  `rag | web | image | model` (`swarmmind/core/evidence.py`) — e.g. `email`, `calendar`,
  `transcript` — all flowing through the existing `SourceRecord`/citation path.
- **Plainer errors + tighter file access** — mirrors SwarmMind's `403 origin diagnostics`
  in `health_check()`. Extending that pattern to 400/401/405 from the route-class server is
  cheap and judge-visible.
- **Dynamic tool loading** — do **not** copy the multi-round/agentic-loop direction; SwarmMind's
  deferred critic-loop decision stands.

## 3. Unsloth Studio + FLM — what matters to SwarmMind

The strategic signal: **Lemonade is now an embedded SDK inside a mainstream desktop app**,
and the NPU path is a first-class, OpenAI-compatible endpoint. SwarmMind already detects
FastFlowLM and recommends it for embeddings (`swarmmind/core/hardware.py:687-699`), and
`ModelsConfig.backends["embeddings"]` accepts `"fastflowlm"`. So the *plumbing exists*; what's
missing is a supported model and honest docs. Practical implications:

- NPU embeddings are feasible via `/v1/embeddings` once FLM 1.0.13 is stable — SwarmMind's
  `embeddings()` call at `client.py:159` needs **no protocol change**, just a model name and a
  backend pin. This is the single biggest AMD-judging win available.
- Unsloth's managed server rotates its API key on each start — so a configurable API key in
  SwarmMind would let the same binary work against both a plain `lemonade-server` and an
  Unsloth-managed instance.
- Be honest in docs: Q4NX NPU models, not arbitrary GGUF; XDNA 2 only (Ryzen AI 300/400,
  Strix Halo); NPU chat via `/v1/chat/completions`, embeddings via `/v1/messages` in the
  Unsloth guide. Large models need substantial shared memory (35B ≈ 28 GB NPU / 64 GB RAM).

## 4. Ranked feature ideas

| # | Idea | User value | AMD-judging value | Effort | Now / roadmap | Exact file / component |
|---|---|---|---|---|---|---|
| 1 | **NPU embeddings via FLM 1.0.13** — document + pin a supported embedding model; add a hardware probe note | Lower-power RAG on Ryzen AI; frees GPU | High — demonstrates real NPU use, not just detection | S | **Now** (docs + config default only; wait for stable FLM before claiming working model) | `swarmmind/core/hardware.py:687`, `swarmmind/config.py:58,83`, `README.md` backend table |
| 2 | **Configurable LEMONADE_API_KEY** — add `LemonadeConfig.api_key`, pass to all three `LemonadeClient(...)` constructors, fall back to `SWARMMIND_LEMONADE_API_KEY` | Works against auth-enabled / Unsloth-managed servers | High — v47.1 breaking change + ecosystem interop | S | **Now** (compat) | `swarmmind/config.py:10`, `swarmmind/lemonade/client.py:31`, `swarmmind/cli/main.py:47`, `swarmmind/ui/app.py:538`, `swarmmind/benchmark.py:536` |
| 3 | **Clearer 400/401/405 error handling** — map route-class server errors to one plain sentence + fix-settings hint, extend current 403 diagnostic | No cryptic 500s; faster self-service | Medium — shows deliberate ecosystem tracking | S | **Now** | `swarmmind/lemonade/client.py:365-415` (`health_check`), `swarmmind/ui/panels/studio.py` status area |
| 4 | **Read-aloud of reports** via existing `text_to_speech()` + `st.audio` | Accessibility; matches GAIA flagship parity | Medium — visible Omni/TTS demo | S | **Now** | `swarmmind/ui/panels/chat.py` (report render), `swarmmind/lemonade/client.py:263` |
| 5 | **Conversation-scoped image references for Vision worker** — accept multiple images per query, pass as a `content` array | Multi-image comparison research | High — directly mirrors v47.1 feature | M | **Roadmap** (small v0.5 scope) | `swarmmind/lemonade/client.py:302` (`analyze_image`), `swarmmind/core/workers.py:116-124`, `swarmmind/ui/panels/chat.py:208`, `swarmmind/core/orchestrator.py:398` |
| 6 | **GAIA-style connectors for source ingestion** — `email`/`calendar`/`transcript` SourceRecord kinds + CLI `source add` options | Wider research surface beyond PDF/URL | Medium — local-first ingestion story | M | **Roadmap** | `swarmmind/core/evidence.py`, `swarmmind/rag/pipeline.py`, `swarmmind/cli/main.py` |
| 7 | **Live hardware strip in Studio** (CPU/GPU/NPU from `system_stats`) | Transparency; shows why backend chosen | Medium | S | **Now** | `swarmmind/ui/panels/studio.py`, `swarmmind/lemonade/client.py:466` |
| 8 | **Handle multi-shard `/files` entries** — tolerate >1 response entry if model inventory is consumed | Avoids v47.1 regression | Low (we don't consume `/files` today) | S | **Document-only** now | `swarmmind/lemonade/client.py` (`get_models`) — no current `/files` call |

Do **not** implement: the multi-round critic loop (deferred by spec), Ollama-gateway
`/api/*` changes (unused), TheNoise model swaps (no dependency), `gaia-cli` rename (no
dependency).

## 5. Compatibility actions — now vs document-only

**Take now (concrete changes):**
1. Add `api_key: str = ""` to `LemonadeConfig`; thread it into the three `LemonadeClient(...)`
   call sites. Existing default stays `"not-needed"` so plain servers are unaffected.
   Without this, a user who sets `LEMONADE_API_KEY` per v47.1 gets 401.
2. Add a single `classify_api_error()` helper in `swarmmind/lemonade/client.py` covering
   400 ("Missing model field in request" / invalid form field), 401/403 (auth / origins),
   405 (POST-only path) and emit one plain sentence; reuse in `health_check()` and Studio.
3. Update `SETUP.md` / `README.md` config example with the auth note and a stable-version
   recommendation (v2026.47.1 is the latest stable; 48.x is RC).
4. Add a docs note that Unsloth-managed Lemonade rotates its key — users must read the key
   printed by `unsloth run` and set `SWARMMIND_LEMONADE_API_KEY`.

**Document-only for now:**
- FLM 1.0.13 NPU embeddings: record as *available in RC* (`candidate-v2026.48.3`), do not
  hard-code an unverified model name as a default.
- `/files` multi-shard and Ollama-gateway 400s: note in a compatibility appendix; SwarmMind
  neither calls `/files` nor the `/api/*` gateway.
- GAIA `gaia-cli` rename, dynamic tool settings removal, `use_chatgpt`/`use_openai` args:
  irrelevant to SwarmMind; mention only to explain why no action is needed.

## 6. Sources

- GAIA v0.26.0 notes: https://amd-gaia.ai/docs/releases/v0.26.0 · https://github.com/amd/gaia/releases/tag/v0.26.0
- Lemonade releases: https://github.com/lemonade-sdk/lemonade/releases · v2026.45.3 https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.45.3 · v2026.47.1 https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.47.1 · v2026.48.0 RC https://github.com/lemonade-sdk/lemonade/releases/tag/candidate-v2026.48.0 · v2026.48.3 RC https://github.com/lemonade-sdk/lemonade/releases/tag/candidate-v2026.48.3
- HTTP server spec / PR #3771: https://lemonade-server.ai/docs/dev/specs/http-server/ · https://github.com/lemonade-sdk/lemonade/pull/3771
- AMD × Unsloth: https://www.amd.com/en/blogs/2026/unlocking-the-amd-ryzen-ai-npu-for-unsloth-desktop.html · https://newsroom.amd.com/news/amd-unsloth-bring-npu-support-agentic-ai-applications/ · https://unsloth.ai/docs/get-started/install/amd/npu · https://www.phoronix.com/news/Unsloth-Desktop-Ryzen-AI-NPU
- FastFlowLM v1.0.13: https://github.com/ROCm/FastFlowLM/releases/tag/v1.0.13 · https://www.amd.com/en/developer/resources/technical-articles/2026/fastflow-now-runs-on-amd-gpus.html
