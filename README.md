# 🐝 SwarmMind — Multi-Agent AI Research on AMD Lemonade 🍋

> **AMD Lemonade Developer Challenge 2026 Submission**  
> A local-first, multi-agent research swarm **Powered by Lemonade Omni Models**  
> *Built as a deep ecosystem contribution to push local, multi-agent AI forward on AMD hardware.*

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![AMD Lemonade](https://img.shields.io/badge/AMD-Lemonade-ED1C24.svg)](https://github.com/lemonade-sdk/lemonade)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

---

### 🎥 [Watch the SwarmMind Demo Video Here!](https://arorlgyiqvelbjrtshjn.supabase.co/storage/v1/object/public/pranav/1784638692_swarmmind_demo_1080p.mp4)

---

## 🎯 What is SwarmMind?

SwarmMind is a **multi-agent AI research assistant** that decomposes complex research queries into parallel sub-tasks, runs specialised worker agents concurrently, and synthesises a structured report — all running **100% locally** on AMD hardware via the [Lemonade SDK](https://github.com/lemonade-sdk/lemonade).

### The Swarm Architecture

```
User Query
    │
    ▼
┌─────────────┐
│  Conductor  │  ← Decomposes query into 2-5 parallel tasks
│  (LLM)      │    using AMD Lemonade chat/completions
└──────┬──────┘
       │
   ┌───┴────────────────────┐
   ▼         ▼              ▼
┌──────┐  ┌──────┐  ┌──────────┐
│  RAG │  │ Web  │  │ Analysis │  ← Workers run in parallel
│Worker│  │Worker│  │  Worker  │    (asyncio.gather)
└──┬───┘  └──┬───┘  └────┬─────┘
   └──────────┴───────────┘
              │
              ▼
        ┌──────────┐
        │Synthesis │  ← Merges all outputs into
        │  (LLM)   │    structured report + follow-ups
        └──────────┘
```

---

## ✨ Features

- **🧠 Multi-Agent Orchestration** — Conductor decomposes queries; parallel workers research independently
- **🔍 RAG (Retrieval-Augmented Generation)** — ChromaDB for private document search
- **🌐 Web Search** — DuckDuckGo integration for real-time web results
- **⚡ Parallel or Sequential Execution** — Choose parallel (fast) or sequential (low-RAM) worker execution
- **🖥️ AMD Hardware Detection** — Auto-detects Ryzen AI NPU, ROCm GPU, and recommends optimal backends
- **🎨 Lemonade Omni Models** — Native multimodal processing! Leverages Qwen3.6-35B-A3B for Vision, Flux for Diagrams, and Kokoro for TTS narration.
- **📊 Structured Reports** — Executive summary, sections, contradictions, follow-up questions
- **📤 Export** — Markdown and HTML report export
- **🖥️ Professional UI** — Dark glassmorphism design with 3-panel layout

---

## 🚀 Quick Start

> **Judge?** See the [Setup Guide](SETUP.md) for detailed instructions.

### Prerequisites

1. **AMD Lemonade** installed and running:
   ```bash
   pip install lemonade-sdk
   lemonade-server start
   ```

2. **Python 3.11+** with uv or pip

### Installation

```bash
git clone https://github.com/rahulgupta0-dev/swarmmind.git
cd swarmmind

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"

# Verify installation
swarmmind --help
```

### Run

```bash
# Launch the Streamlit web UI
swarmmind web

# Or run a CLI query
swarmmind ask "What is AMD Ryzen AI?"

# Or run the smoke test (requires Lemonade server)
bash tests/smoke_test.sh
```

Open http://localhost:8501 in your browser.

---

## 🔧 Configuration

SwarmMind auto-detects your AMD hardware via Lemonade's /v1/system-info endpoint:

| Hardware | Backend | Use Case |
|---|---|---|
| AMD Ryzen AI NPU (XDNA 2) | ryzenai | Conductor (low-latency) |
| AMD Radeon GPU (ROCm) | rocm | Workers (high-throughput) |
| AMD CPU (llama.cpp) | cpu | Fallback |

---

## 🏗️ Architecture

```
swarmmind/
├── core/
│   ├── orchestrator.py  # Main pipeline coordinator
│   ├── conductor.py     # Query decomposition (LLM)
│   ├── workers.py       # RAG / Web / Analysis / Code workers
│   ├── synthesis.py     # Multi-worker report synthesis (LLM)
│   └── hardware.py      # AMD hardware detection & backend routing
├── lemonade/
│   └── client.py        # Async Lemonade API client
├── rag/
│   └── chroma_store.py  # ChromaDB RAG implementation
├── data/
│   └── database.py      # SQLite project/conversation storage
└── ui/
    ├── app.py           # Streamlit app entry point
    └── panels/
        ├── chat.py      # Research query & results panel
        ├── sources.py   # Project & source management
        └── studio.py    # Export, notes & Lemonade status
```

---

## 📝 AMD Lemonade Integration

SwarmMind uses the following Lemonade endpoints:

| Endpoint | Purpose |
|---|---|
| GET /v1/health | Connection health check |
| POST /v1/chat/completions | All LLM inference (conductor, workers, synthesis) |
| POST /v1/load | Pre-load conductor model |
| POST /v1/embeddings | RAG document embedding |
| GET /v1/system-info | AMD hardware detection (NPU/GPU/CPU) |
| GET /v1/stats | Token throughput metrics |

---

## 💻 CLI Reference

| Command | Description |
|---|---|
| `swarmmind ask <query>` | Run a research query from the terminal |
| `swarmmind ask <query> --no-web` | Disable web search for this query |
| `swarmmind ask <query> --sequential` | Run workers sequentially (safer on low-RAM systems) |
| `swarmmind ask <query> --project-id <id>` | Scope query to a specific project's sources |
| `swarmmind project create <name>` | Create a new research project |
| `swarmmind project list` | List all projects |
| `swarmmind source add <project> <type> <uri>` | Add a source (pdf, youtube, web, text) |
| `swarmmind source list <project>` | List sources in a project |
| `swarmmind report list <project>` | List past reports for a project |
| `swarmmind config show` | Show current configuration |
| `swarmmind benchmark` | Run AMD cross-backend benchmark |
| `swarmmind web` | Launch Streamlit web UI |

---

## ⚙️ Configuration

SwarmMind stores configuration at `~/.swarmmind/config.toml`:

```toml
[lemonade]
host = "localhost"
port = 13305

[models]
conductor = "Qwen3.6-35B-A3B-GGUF"
worker = "Gemma-4-12B-it"
embeddings = "nomic-embed-text-v1-GGUF"
image = "Flux-2-Klein-4B"
tts = "kokoro-v1"

[rag]
chunk_size = 512
chunk_overlap = 64
top_k = 5

[execution]
mode = "parallel"        # "parallel" (fast) or "sequential" (low-RAM)
max_concurrent = 4        # Limit parallel workers (1-16)
```

### Execution Mode

SwarmMind supports two worker execution modes to accommodate different hardware:

| Mode | Speed | RAM Usage | Best For |
|---|---|---|---|
| `parallel` (default) | ⚡ Fast — workers run simultaneously | Higher — multiple LLM calls at once | 32 GB+ RAM (Strix Halo, high-end GPUs) |
| `sequential` | 🐢 Slower — one worker at a time | Lower — single LLM call at a time | 8-16 GB RAM (laptops, older hardware) |

```bash
# CLI: Force sequential mode
swarmmind ask "Compare RAG vs fine-tuning" --sequential

# Config: Set via config.toml
[execution]
mode = "sequential"
max_concurrent = 1
```

In the **Web UI**, toggle execution mode in the **Settings** panel (left sidebar).

Hardware auto-detection routes each model role to the best available backend:

| Hardware | Backend | Use Case |
|---|---|---|
| AMD Ryzen AI NPU (XDNA 2) | ryzenai | Embeddings (low-power, steady-state) |
| AMD Radeon GPU (ROCm) | rocm | Conductor & Workers (high-throughput) |
| AMD CPU (llama.cpp) | cpu | Fallback / TTS |

Pin backends manually in `config.toml`:

```toml
[models.backends]
conductor = "rocm"
worker = "rocm"
embeddings = "ryzenai"
image = "rocm"
tts = "cpu"
```

---

## 📄 License

Apache 2.0 — see [LICENSE](LICENSE) for full terms.

---

## 📚 Documentation

| Document | Description |
|---|---|
| [Setup Guide](SETUP.md) | Judge setup instructions, troubleshooting |
| [CHANGELOG.md](CHANGELOG.md) | TDD audit fixes and methodology |
| [README.md](README.md) | Architecture, features, CLI reference |

---

*Built with ❤️ for the AMD Lemonade Developer Challenge 2026*
