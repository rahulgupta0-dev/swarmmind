# Upstream Watch — Lemonade (fetched 2026-11-22)

> Research-only. All items below were verified from GitHub on **2026-11-22**.
> **RC features are release-candidate and may change before stable.** No AMD
> hardware was available here, so no NPU/GPU behaviour was validated locally.

## Dated upstream items

| Date | Item (link) | Verified facts |
|---|---|---|
| 2026-11-18 | Stable **v2026.47.1** ([release](https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.47.1)) | MTP speculative decoding auto for HRX; conversation-scoped image references (multi-image editing / direct Vision-LLM analysis). **Breaking:** empty `LEMONADE_ADMIN_API_KEY` no longer bypasses auth (a valid `LEMONADE_API_KEY` is required); `/files` lists one entry per shard for multi-shard GGUF. |
| 2026-11-20 | RC **candidate-v2026.48.3** ([release](https://github.com/lemonade-sdk/lemonade/releases/tag/candidate-v2026.48.3)) | `images`/`audio` params on `/api/generate`; `/api/show` reports speech/decision/audio capabilities; `/api/chat` + `/api/generate` reject non-LLM models with 400; FLM NPU backend v1.0.13 with AMD-signed Windows binaries enables embedding models on `/v1/embeddings`; TheNoise v0.15.0. |
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
