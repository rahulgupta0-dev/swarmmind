# Lemonade Server Compatibility (SwarmMind v0.4.0)

This document records the upstream Lemonade Server releases verified against
SwarmMind and states, for each change, whether SwarmMind has adapted or only
recommends a follow-up. SwarmMind talks to Lemonade through its
OpenAI-compatible `/v1/*` endpoints only; it does not depend on the desktop
GUI or the Ollama-compatible `/api/*` surface.

Sources: [Lemonade Server docs](https://lemonade-server.ai/docs/), upstream
[release notes](https://github.com/lemonade-sdk/lemonade/releases).

## Summary

| Release | Upstream change | SwarmMind status |
|---|---|---|
| v2026.45.3 | Consistent errors, 405/400 status codes, cancellation tokens/job routes | Partly adapted (friendly 400 hint); rest recommended |
| v2026.47.1 | Auth change; multi-shard `/files` | Auth implemented; `/files` not consumed |
| v2026.48.x (RC) | FLM NPU embeddings; multimodal fields; 400 for non-LLM models | Embeddings/400 handled; RC-only fields recommended only |

## v2026.45.3 — HTTP server rebuild (2026-11-04)

Source: <https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.45.3>

Verified changes:

- The HTTP server was rebuilt on a route-class architecture with consistent
  errors and generated API docs.
- `GET` requests to POST-only paths (`slots/{id}`, `jobs/{id}/pause`,
  `jobs/{id}/interrupt`, `jobs/{id}/resume`, `models/register`) now return
  **405** instead of 404 — the job pause/interrupt/resume routes provide the
  cancellation-token behaviour.
- A request without a `model` field now returns the same
  `"Missing model field in request"` error on every model route
  (`chat/completions`, `completions`, `embeddings`, `responses`, `rerank`).
- Form-encoded requests with invalid field types now return **400** instead
  of 500.

SwarmMind response:

- **Implemented.** `LemonadeClient._request()` detects a 400 whose body
  contains `"Missing model field"` (or a non-LLM-model message) and raises an
  actionable hint instead of the terse raw text. This directly matches the
  new uniform error string.
- **Implemented.** The client sends only the request shapes it needs; it
  never issues `GET` to a POST-only path, so the 405 change is harmless.
- **Recommended (not implemented).** SwarmMind has no client wrapper for the
  job cancellation-token endpoints (`jobs/{id}/interrupt` etc.). Streaming is
  cancelled by closing the `httpx` stream; a dedicated cancel API is roadmap.

## v2026.47.1 — Authentication and `/files` shards (2026-11-18)

Source: <https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.47.1>

Verified changes:

- Setting `LEMONADE_ADMIN_API_KEY` to an empty string **no longer bypasses
  authentication**; a valid `LEMONADE_API_KEY` is required for API and metrics
  routes.
- `/files` now returns one entry **per shard** for multi-shard GGUF models in
  `extra_models_dir`, instead of a single aggregated entry.

SwarmMind response:

- **Implemented.** `swarmmind/lemonade/client.py` reads `LEMONADE_API_KEY`
  (falling back to `LEMONADE_ADMIN_API_KEY`) from the environment and sends
  `Authorization: Bearer <key>` when a key is present. With no key it sends
  no `Authorization` header, preserving unauthenticated/local behaviour. No
  config fields were added.
- **Recommended (not implemented).** SwarmMind does not consume `/files`;
  model discovery uses `GET /v1/models`. Any future loader that reads
  `/files` must handle multiple shard entries rather than assuming one.

## v2026.48.x (release candidate) — FLM NPU embeddings and multimodal fields

Sources: <https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.48.0>
and <https://github.com/lemonade-sdk/lemonade/releases/tag/v2026.48.3>

Verified changes (RC notes; AI-generated upstream and subject to revision):

- FLM NPU backend updated to v1.0.13 with AMD-signed Windows binaries,
  enabling **embedding models on the `/v1/embeddings` endpoint**.
- Image and audio parameters were added to `/api/generate` for multimodal
  conversations, and `/api/show` now reports speech/decision/audio
  capabilities.
- `/api/chat` and `/api/generate` reject non-LLM models with a **400** error
  instead of loading and failing; backend errors end SSE streams with an
  error line.

SwarmMind response:

- **Compatible as-is.** `LemonadeClient.embeddings()` already posts to
  `/v1/embeddings`, which is the endpoint the FLM NPU backend now serves. No
  client change was required.
- **Implemented.** The friendly-400 handling above also covers non-LLM model
  rejections, so users get guidance instead of a raw exception.
- **Recommended (not implemented).** The new multimodal fields live on the
  Ollama-compatible `/api/*` endpoints. SwarmMind's vision path uses the
  OpenAI-compatible `/v1/chat/completions` `image_url` content part
  (`analyze_image`); native audio pass-through and capability discovery from
  `/api/show` are not implemented, and RC-only behaviour should not be relied
  on in stable deployments.

## Verification scope

The compatibility statements above are based on the linked upstream release
notes and on source inspection of `swarmmind/lemonade/client.py`. They were
not validated against a live Lemonade v2026.47+/v2026.48 server; tests use
mocks only.
