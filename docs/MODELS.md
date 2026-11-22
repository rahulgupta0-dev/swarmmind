# Models: the SwarmMind ↔ Lemonade boundary

> **Short version:** SwarmMind never downloads or loads a model. It sends *model
> name strings* from `~/.swarmmind/config.toml` to Lemonade's OpenAI-compatible
> `/v1/*` endpoints. **Any model registered in Lemonade that matches the role's
> endpoint can be used** — GGUF (llama.cpp), FLM (Ryzen AI NPU), ONNX, or an
> Omni collection.

This guide shows how to register models in Lemonade with the `lemonade` CLI,
pin them in SwarmMind's config, and verify the result. Commands were checked
against the Lemonade CLI and custom-model docs (links at the bottom). They are
**not validated on AMD hardware here** — see [Caveats](#caveats).

## The boundary

```
config.toml model string  ──▶  SwarmMind client  ──▶  POST /v1/<role endpoint>
                                                        │
                                                        ▼
                                       Lemonade: name → registered model → backend
```

SwarmMind resolves a role to a name, sends that name as the `model` field in an
OpenAI-compatible request, and Lemonade decides which checkpoint/recipe/backend
serves it. Installing or swapping a model is therefore a **Lemonade** operation
(`lemonade pull`, `lemonade load`, `lemonade list`); SwarmMind just needs the
name. `POST /v1/load` is used to pre-load conductor/worker models, and
`GET /v1/models` / `/v1/system-info` are used for diagnostics and backend
auto-detection.

## Model roles and endpoint requirements

Each role in `[models]` must point at a model registered in Lemonade with a
matching capability. `models.omni` is optional.

| `config.toml` key | Default | SwarmMind endpoint(s) | Required Lemonade model type |
|---|---|---|---|
| `models.conductor` | `Qwen3.6-35B-A3B-GGUF` | `POST /v1/chat/completions` | Chat/instruct LLM (tool-calling helps) |
| `models.worker` | `Gemma-4-12B-it` | `POST /v1/chat/completions` | Chat/instruct LLM |
| `models.embeddings` | `nomic-embed-text-v1-GGUF` | `POST /v1/embeddings` | Embedding model (`embeddings` label) |
| `models.image` | `Flux-2-Klein-4B` | `POST /v1/images/generations`, `/v1/images/edits` | Image-generation model (`image` label) |
| `models.tts` | `kokoro-v1` | `POST /v1/audio/speech` | TTS model (`tts`/`speech` label) |
| `models.router` | `routing.router` | `POST /v1/classify`, `/v1/chat/completions` | Router/ONNX classifier (or Omni planner) |
| `models.omni` | *(empty)* | routes image/tts/vision via the Omni collection | `collection.omni`, when set |

The **Vision worker** calls `analyze_image`, which goes through
`/v1/chat/completions` with image content — so its model needs multimodal
(`vision`) support, not just text chat.

## Registering models in Lemonade

Run a Lemonade server first (`lemonade-server start`), then use the CLI. Names
you pass to SwarmMind must match the IDs listed by `lemonade list`.

### (a) Hugging Face GGUF — `unsloth/Inkling-2-Mini-GGUF`

This repo is an Unsloth dynamic GGUF quant of Thinking Machines' Inkling 2 Mini:
**26.2B total / 3.4B active MoE** (≈5.4B effective active), text + image
multimodal, Apache-2.0, up to 1M context. Recommended quant **UD-Q4_K_XL ≈17.6 GB**;
smaller 2-bit ≈9 GB, larger 8-bit ≈24 GB; the mmproj vision projector is ≈639 MB.
Unsloth recommends 16–24 GB RAM for full precision; llama.cpp runs it on AMD.

```bash
# Specific quant variant (recommended for this model):
lemonade pull unsloth/Inkling-2-Mini-GGUF:UD-Q4_K_XL

# Or pull the repo and choose a variant interactively:
lemonade pull unsloth/Inkling-2-Mini-GGUF
# Full URLs also work:
# lemonade pull https://huggingface.co/unsloth/Inkling-2-Mini-GGUF

# Show registered models that are not yet downloaded:
# (append ?show_all=true to the model listing)
curl "http://localhost:13305/v1/models?show_all=true"

# Private/gated repos:
export HF_TOKEN=hf_...          # then run lemonade pull as above
```

### (b) Custom `user.*` GGUF

Use explicit checkpoints + recipe when you want a stable name of your own:

```bash
lemonade pull user.inkling-mini \
  --checkpoint main unsloth/Inkling-2-Mini-GGUF:UD-Q4_K_XL \
  --recipe llamacpp
```

Repeatable `--label` values include `coding`, `embeddings`, `hot`, `mtp`,
`reasoning`, `reranking`, `tool-calling`, `vision` (and `chat`).

### (c) Vision model with mmproj

Multimodal GGUFs need a separate projector checkpoint (`mmproj`), which supported
repos auto-detect; add it explicitly for full control:

```bash
lemonade pull user.inkling-vision \
  --checkpoint main   unsloth/Inkling-2-Mini-GGUF:UD-Q4_K_XL \
  --checkpoint mmproj unsloth/Inkling-2-Mini-GGUF:<mmproj-file.gguf> \
  --recipe llamacpp \
  --label vision
```

Use the exact `mmproj-*.gguf` filename from the repo (or let Lemonade auto-detect
it). Without the projector, image input will not work.

### (d) FLM NPU model (Ryzen AI / FastFlowLM)

FLM is Lemonade's NPU recipe (`flm`). Register/pull an FLM model by its repo and
pin the backend to the NPU:

```bash
lemonade backends install flm:npu          # install the NPU backend if needed
lemonade pull <flm-model-repo>             # e.g. an amd/* NPU-ready checkpoint
# Or manually: lemonade pull user.my-flm --checkpoint main <org/repo> --recipe flm
lemonade list
```

Available FLM models depend on your Ryzen AI hardware and installed backend;
check `lemonade list` for the exact IDs.

### (e) Omni collection

An Omni collection bundles already-registered components (planner LLM, image,
ASR/TTS, vision) behind one name:

```bash
lemonade pull user.MyKit \
  --recipe collection.omni \
  --components Qwen3-0.6B-GGUF Whisper-Tiny SD-Turbo
lemonade load user.MyKit
```

## Pin models in `~/.swarmmind/config.toml`

Example using the Inkling models above:

```toml
[models]
conductor = "user.inkling-mini"        # or "unsloth/Inkling-2-Mini-GGUF:UD-Q4_K_XL"
worker = "user.inkling-mini"
embeddings = "nomic-embed-text-v1-GGUF"
image = "Flux-2-Klein-4B"
tts = "kokoro-v1"
router = "routing.router"
omni = "user.MyKit"                    # optional Omni collection

[models.context]
conductor = 131072                     # 128K default; Inkling supports up to 1M
worker = 131072

[models.backends]
conductor = "rocm"                     # rocm | vulkan | cpu | ryzenai | fastflowlm | auto
worker = "rocm"
embeddings = "ryzenai"
image = "rocm"
tts = "cpu"

[execution]
mode = "sequential"                    # low-RAM systems (one LLM call at a time)
max_concurrent = 1
```

Backends can be `rocm`, `vulkan`, `cpu`, `ryzenai`, `fastflowlm`, or `auto`.
Leave unset/`auto` to let SwarmMind pick from `/v1/system-info`; use `sequential`
mode on ~8–16 GB systems.

## Verify

```bash
swarmmind doctor     # server health, models, capabilities/backends, configured models
swarmmind ask "What is AMD Ryzen AI?" --no-web
```

`doctor` lists the models Lemonade exposes and checks the configured model
names; fix any `FAIL`/`WARN` before running a query. A successful
`swarmmind ask` (against a real server) proves the conductor/worker names resolve
and the chat endpoint responds. `swarmmind config show` prints the active config.

## Caveats

- **Context & memory:** context windows cost real VRAM/RAM (Inkling's 1M context
  is not free). Start with 128K, and use `sequential` mode on low-RAM machines.
  Quant size scales ~9 GB (2-bit) → 17.6 GB (4-bit) → 24 GB (8-bit).
- **Vision needs mmproj + the Vision worker:** a vision-capable chat model must
  be registered with `mmproj` and used by the Vision worker; text-only models
  cannot analyze images.
- **Chat roles need chat-capable models:** `conductor`/`worker` go to
  `/v1/chat/completions` — base/completion-only or embedding models will fail.
- **Embeddings are a different endpoint:** `models.embeddings` is sent to
  `/v1/embeddings`, not `/v1/chat/completions`; use an embedding-labelled model.
  FLM NPU embeddings are an **RC** feature in recent Lemonade releases — confirm
  support in your version before pinning `embeddings` to an FLM backend.
- **No local validation:** these commands were documented from Lemonade docs,
  not run against AMD hardware here. Exact quant filenames, FLM model IDs and
  backend names are hardware/version dependent — confirm with `lemonade list`,
  `lemonade backends`, and `lemonade pull --help`.

## Sources

- <https://lemonade-server.ai/docs/guide/configuration/custom-models/>
- <https://lemonade-server.ai/docs/guide/cli/>
- <https://huggingface.co/unsloth/Inkling-2-Mini-GGUF>
- [SwarmMind compatibility](COMPATIBILITY.md) · [Troubleshooting](TROUBLESHOOTING.md)
