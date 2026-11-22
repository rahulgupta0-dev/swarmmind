# Upstream Watch — Lemonade (fetched 2026-11-22)

> Research-only. All items below were verified from GitHub on **2026-11-22**.
> **RC features are release-candidate and may change before stable.** No AMD
> hardware was available here, so no NPU/GPU behaviour was validated locally.

## Dated upstream items

| Date | Item (link) | Verified facts |
|---|---|---|
| 2026-11-04 | Stable **v2026.46.2** ([release](https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.46.2)) | FLM 1.0.11 streaming token usage gated behind `stream_options.include_usage` (PR [#4037](https://github.com/lemonade-sdk/lemonade/pull/4037)); `/models` drops top-level `image_defaults`/`audio_defaults` in favour of `recipe_options` (PR [#4049](https://github.com/lemonade-sdk/lemonade/pull/4049)). |
| 2026-11-18 | Stable **v2026.47.1** ([release](https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.47.1)) | MTP speculative decoding auto for HRX; conversation-scoped image references (multi-image editing / direct Vision-LLM analysis). **Breaking:** empty `LEMONADE_ADMIN_API_KEY` no longer bypasses auth (a valid `LEMONADE_API_KEY` is required); `/files` lists one entry per shard for multi-shard GGUF. |
| 2026-11-20 | RC **candidate-v2026.48.3** ([release](https://github.com/lemonade-sdk/lemonade/releases/tag/candidate-v2026.48.3)) | `images`/`audio` params on `/api/generate`; `/api/show` reports speech/decision/audio capabilities; `/api/chat` + `/api/generate` reject non-LLM models with 400; FLM NPU backend **v1.0.13** with AMD-signed Windows binaries enables embedding models on `/v1/embeddings`; TheNoise v0.15.0. |
| 2026-10-28 | PR [#3284](https://github.com/lemonade-sdk/lemonade/pull/3284) — `lemonade bench --submit` (merged) | `bench --submit` is real: uploads results to the benchmark service, prints a claim link for bench.lemonade-server.ai; submission progress goes to stderr, so `bench --submit --json > results.json` works. Docs: https://lemonade-server.ai/docs/guide/cli/ |
| open | Issue [#4170](https://github.com/lemonade-sdk/lemonade/issues/4170) — `ryzenai -CPU/-Hybrid` models report device `"npu"`; Issue [#4226](https://github.com/lemonade-sdk/lemonade/issues/4226) — Strix Halo perf monitor shows RAM only | Upstream device/memory reporting inaccuracies on CPU-hybrid and Strix Halo hardware. |
| open | Issue [#4181](https://github.com/lemonade-sdk/lemonade/issues/4181) — "kokoro-v1: Unknown option 'voice'" | The 400 comes from the GUI model-options endpoint `/api/v1/models/kokoro-v1/options`. The issue states direct `/v1/audio/speech` requests **with `"voice":"bm_george"` work fine**. |
| open | PR [#4209](https://github.com/lemonade-sdk/lemonade/pull/4209) — opt-in web search in Lemonade chat | Disabled by default; only search queries leave the device; linked pages are not downloaded. |
| open | PR [#4202](https://github.com/lemonade-sdk/lemonade/pull/4202) — MCP server health, system info, backend management | Health / system-info / backend-management reporting. |
| open | PR [#4178](https://github.com/lemonade-sdk/lemonade/pull/4178) — one-shot & piped `lemonade chat` with `--json` | stdout carries only the answer; diagnostics go to stderr. |
| open | Issue [#4230](https://github.com/lemonade-sdk/lemonade/issues/4230) — kyojin backend containers (qwen3.8-flash / glm-5.3-flash on Strix Halo); Issue [#4184](https://github.com/lemonade-sdk/lemonade/issues/4184) — Project-Maya backend | Proposed backend/model ideas. |
| open | Issue [#4227](https://github.com/lemonade-sdk/lemonade/issues/4227) — duplicate `device` label in forwarded `/metrics` | Breaks Prometheus scrapes; upstream bug. |

## Mapping: upstream item → SwarmMind

| Upstream item | SwarmMind position / action |
|---|---|
| v2026.47.1 auth change | **Already done** — client reads `LEMONADE_API_KEY` (fallback `LEMONADE_ADMIN_API_KEY`) and sends Bearer only when present (`docs/COMPATIBILITY.md`). |
| v2026.47.1 multi-shard `/files` | **Not relevant** — we do not consume `/files`. |
| v2026.47.1 multi-image references | **Roadmap** — current Vision worker sends one image; multi-image is a v0.5 idea (`ECOSYSTEM-RESEARCH.md` idea #5). |
| RC 48.3 `/api/*` 400, `/api/show`, TheNoise | **Not relevant** — Ollama-compat gateway endpoints we do not call (and RC-only). |
| RC 48.3 FLM v1.0.13 `/v1/embeddings` | **Roadmap / RC-only** — embeddings plumbing exists; no supported model pinned; no local AMD validation. |
| #4181 kokoro `voice` | **Not affected** — the 400 is the GUI options endpoint; SwarmMind's TTS sends `voice` inside `/v1/audio/speech` (`client.py:352-368`), which the issue says works. No code change. |
| #4209 opt-in web search | **Already aligned** — search is opt-in and our page-fetch (`fetch_pages`) is a separate opt-in; we remain local-first. |
| #4202 health / system info / backends | **Done** — `swarmmind doctor` reports health/hardware **and** model capabilities (`vision`/`image`/`tts`/`omni`) and available backends (commit `62ec95d`, 2026-11-22). |
| #4178 `lemonade chat --json` | **Already aligned** — `swarmmind ask --json` writes only the report to stdout, diagnostics to stderr. |
| #4230 / #4184 backends | **Roadmap** — ideas only; not implementable here without AMD hardware. |
| #4227 `/metrics` duplicate label | **Not relevant** — we do not scrape `/metrics`. |
| #3284 `bench --submit` | **Top next action on real AMD hardware** — see `CHALLENGE-STATUS.md`. Not run here (no AMD machine / no server); no leaderboard claim. |

## 2026-11-22 — verified findings + SwarmMind impact

All items below re-verified today against upstream release notes / PRs / docs.
No AMD hardware or Lemonade server was available here; no NPU/GPU behaviour or
leaderboard result was validated locally.

1. **v2026.46.2 / PR [#4037](https://github.com/lemonade-sdk/lemonade/pull/4037) — streaming usage.** FLM 1.0.11 streams token usage **only** when `stream_options.include_usage` is set; without it `/api/v1/stats` records no token counts or TPS. The same release returns **400** for OpenAI multi-message conversation history sent to single-turn `*-flash` models, invalid `tool_choice`/`response_format`, forced tool calls on small Qwen3 models, and empty requests (no messages/prompt). **Impact — code action (concurrent):** client adds `stream_options.include_usage`; benchmark consumes real usage chunks instead of word-count estimates. **Flash-history 400 does not apply:** `conductor.py` and `synthesis.py` embed prior turns into a single user-prompt text block, not OpenAI multi-message history. Keep as a caveat only if we ever switch to API-level history.
2. **v2026.46.2 / PR [#4049](https://github.com/lemonade-sdk/lemonade/pull/4049) — `/models` fields.** Top-level `image_defaults`/`audio_defaults` removed; values nested under `recipe_options`. **Impact: not applicable** — SwarmMind does not read those keys.
3. **PR [#3284](https://github.com/lemonade-sdk/lemonade/pull/3284) — `lemonade bench --submit` (merged 2026-10-28).** Real upload + claim link for bench.lemonade-server.ai; progress on stderr. **Impact:** TOP recommended next action on a real AMD machine (save the claim link as a verifiable "deep-dive performance eval" artifact). **Not run here** — no AMD hardware/server, so no leaderboard claim (see `CHALLENGE-STATUS.md`).
4. **FLM v1.0.13 in RC v2026.48.3 — embeddings.** Enables `/v1/embeddings`; **RC-only**, supports the split-hardware thesis. **Impact:** our embeddings call is backend-agnostic; no AMD validation happened here.
5. **Issue [#4181](https://github.com/lemonade-sdk/lemonade/issues/4181) (kokoro) — corrected (re-stated).** The 400 comes from the GUI options endpoint `/api/v1/models/kokoro-v1/options`; the issue body says direct `/v1/audio/speech` with `voice` works. **Impact: no code change** — SwarmMind sends `voice` in the speech payload. Do **not** strip `voice`.
6. **Issues [#4170](https://github.com/lemonade-sdk/lemonade/issues/4170) / [#4226](https://github.com/lemonade-sdk/lemonade/issues/4226).** **Impact: no code change now** — SwarmMind detects hardware via `/v1/system-info` (not the health `device` field) and should treat unified memory conservatively; noted as a caveat.
