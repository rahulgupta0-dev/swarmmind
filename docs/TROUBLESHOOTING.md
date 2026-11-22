# 🛠️ SwarmMind Troubleshooting

**Start here:** run `swarmmind doctor`. It reports `PASS` / `FAIL` / `WARN` for
Python version, config, server health, configured models and local setup, with
a suggested fix for each `FAIL`. Most issues below are caught by it.

For release-specific behaviour see [COMPATIBILITY.md](COMPATIBILITY.md). For
`swarmmind eval` and grounding metrics see [EVALUATION.md](EVALUATION.md).

| Symptom | Cause | Exact fix |
|---|---|---|
| `Error: Cannot reach Lemonade server` / health check fails | Server not running or wrong port | Start it: `lemonade-server start`. Verify: `curl http://localhost:13305/v1/health`. If on another host/port, set `host`/`port` in `~/.swarmmind/config.toml` or `LEMONADE_URL`. No AMD box? Run `.venv/bin/python tests/mock_lemonade_server.py` instead. |
| Connection OK in CLI but browser/UI fails; `403` on `/v1/health` | Lemonade origin allowlist rejects the Streamlit origin | Add the UI origin to the server's allowlist: `export LEMONADE_ALLOWED_ORIGINS="http://localhost:8501"` (include other origins comma-separated), then restart Lemonade. |
| `400 Missing model field in request` or non-LLM model error | Lemonade v2026.45.3+ requires a `model` field and rejects non-LLM models on chat routes | SwarmMind already sends a friendly hint; fix the configured model: set a chat model (e.g. `Qwen3.6-35B-A3B-GGUF`) as `models.conductor`/`models.worker` in `config.toml`, and ensure `models.embeddings` points at an embedding model. Check with `swarmmind doctor`. See [COMPATIBILITY.md](COMPATIBILITY.md). |
| `401`/`403` auth failure after upgrading Lemonade | v2026.47.1+ requires a real Bearer token; empty `LEMONADE_ADMIN_API_KEY` no longer bypasses auth | Set `api_key` under `[lemonade]` in `~/.swarmmind/config.toml`, or `export LEMONADE_API_KEY="your-key"` (fallback: `LEMONADE_ADMIN_API_KEY`). Restart SwarmMind. Older unauthenticated servers need no key. |
| `kokoro-v1: Unknown option 'voice'` / `swarmmind` read-aloud fails | Upstream issue #4181 is a **GUI model-options endpoint** (`/api/v1/models/kokoro-v1/options`) bug; direct `/v1/audio/speech` with `voice` works | **Not a SwarmMind bug** — our TTS sends `voice` inside the `/v1/audio/speech` payload, which the issue confirms works. If read-aloud fails, check the configured omni/TTS model and server health with `swarmmind doctor` instead. See [UPSTREAM-WATCH.md](UPSTREAM-WATCH.md). |
| Model capabilities / "Omni" not visible for a model | Server does not report speech/decision/audio caps, or a backend/model is not loaded (many cap fields are still RC-only in upstream 48.x) | Run `swarmmind doctor` — it is being extended to report detected capabilities and backends alongside health/hardware. Check `[models]` backends in `config.toml` and `swarmmind doctor --json` for details. See [UPSTREAM-WATCH.md](UPSTREAM-WATCH.md). |
| Report has no `[S#]` citations / empty Sources appendix | Sources not ingested, no project scoped, or web + RAG both off | Confirm `swarmmind source list <project>` shows `status = ready`; add a source with `swarmmind source add <project> text "<fact>" --name "Fact"`; pass `--project-id <project>` to `ask`; avoid `--no-web` if you expect web sources. Run `swarmmind eval --report` to check citation health. |
| Source stuck in `processing` / never becomes `ready` | Ingestion failed silently (bad path, unsupported format, or server down) | Check `swarmmind source list <project>` for an error message; ensure the server is up (`swarmmind doctor`); re-add the source. For `text` type, pass the text itself, not a file path. Large files: try increasing `rag.chunk_size`. |
| `requires Python >=3.11` / syntax or install errors | Interpreter older than 3.11 | `python --version`; install Python 3.11+ and recreate the venv: `python3.11 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`. |
| Dependency install / build errors (`setuptools`, compiler, ChromaDB) | Stale pip or missing build tooling | `python -m pip install --upgrade pip setuptools wheel`, then retry `pip install -e ".[dev]"`. Use the platform installers `./install.sh` / `install.bat`. If a native wheel is unavailable, install Python 3.11+ (64-bit). See [SETUP.md](../SETUP.md). |
| `swarmmind web` opens nothing / browser stays blank | Streamlit not installed or port in use | Install: `pip install -e ".[dev]"` (includes Streamlit). Check port: `lsof -i :8501`; use another port: `streamlit run swarmmind/ui/app.py --server.port 8502`. Open `http://localhost:8501` manually. |
| `No AMD NPU/GPU detected` / all roles fall back to `cpu` | No Ryzen AI / ROCm device, or `/v1/system-info` unavailable | Expected on non-AMD machines — SwarmMind falls back to CPU llama.cpp; nothing to fix. On AMD hardware, confirm drivers are installed and the server exposes `/v1/system-info`. Pin backends manually in `config.toml` (`[models.backends]`), e.g. `conductor = "rocm"`. The mock server always reports no accelerator.

Still stuck? Run `bash tests/smoke_test.sh`, review [SETUP.md](../SETUP.md) and
[CHANGELOG.md](../CHANGELOG.md), and check the server logs (`lemonade-server logs`).
