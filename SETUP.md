# 🐝 SwarmMind — Judge Setup Guide

> **AMD Lemonade Developer Challenge 2026 Submission**  
> Quick start guide for judges evaluating SwarmMind

---

## 📋 Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | Required for type hints and async features |
| AMD Lemonade | Latest | Core inference engine |
| Node.js | 18+ (optional) | Only for community skills |

---

## 🚀 Quick Setup (5 minutes)

### Step 1: Install AMD Lemonade

```bash
# Install Lemonade SDK
pip install lemonade-sdk

# Start the Lemonade server (default port: 13305)
lemonade-server start

# Verify it's running
curl http://localhost:13305/v1/health
```

### Step 2: Clone and Install SwarmMind

```bash
# Clone the repository
git clone https://github.com/rahulgupta0-dev/swarmmind.git
cd swarmmind

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

### Step 3: Verify Installation

```bash
# Check CLI is available
swarmmind --help

# Run the smoke test (requires Lemonade server running)
bash tests/smoke_test.sh

# Run the full test suite
python -m pytest tests/ -v
```

### Step 4: Launch the Web UI

```bash
# Start the Streamlit web UI
swarmmind web

# Open http://localhost:8501 in your browser
```

---

## 🧪 Running Tests

### Quick Test (No Server Required)

```bash
# Run all offline tests (150 tests, ~20 seconds)
python -m pytest tests/ -v

# Run just the TDD audit tests (28 tests)
python -m pytest tests/test_pyproject_toml.py tests/test_async_utils.py tests/test_lemonade_client.py tests/test_license_consistency.py -v
```

### Full Smoke Test (Server Required)

```bash
# Start Lemonade server first
lemonade-server start

# Run the 6-step smoke test
bash tests/smoke_test.sh
```

**Expected Output:**
```
============================================
  Lemonade SwarmMind — Smoke Test
============================================

🔍 [1/6] Checking Lemonade server...
       http://localhost:13305/v1/health → OK (200)

🔍 [2/6] Checking loaded models...
       Found 5 models: Qwen3.6-35B-A3B-GGUF, Gemma-4-12B-it...

🔍 [3/6] Testing chat completion...
       Response: "Hello"

🔍 [4/6] Testing embeddings...
       Embedding dims: 768

🔍 [5/6] Testing CLI pipeline...
       [Report output]

🔍 [6/6] Testing Streamlit web UI starts...
       Icons OK
       UI panels import OK

============================================
  ✅ Smoke Test Complete!
============================================
```

---

## 🎯 Testing the CLI

### Basic Research Query

```bash
# Ask a research question
swarmmind ask "What are the advantages of AMD Ryzen AI NPU?"

# Ask without web search
swarmmind ask "Explain ROCm architecture" --no-web

# Ask with sequential execution (safer on low-RAM systems)
swarmmind ask "Compare RAG vs fine-tuning" --sequential

# Ask with project context
swarmmind ask "Compare RAG vs fine-tuning" --project-id <project-id>
```

### Project Management

```bash
# Create a project
swarmmind project create "AMD Research"

# List projects
swarmmind project list

# Add a source
swarmmind source add <project-id> web https://amd.com/ryzen-ai

# List sources
swarmmind source list <project-id>
```

### Configuration

```bash
# Show current config
swarmmind config show

# View hardware detection
# (Launch web UI and check the sidebar)
swarmmind web
```

---

## 🎬 Running the Demo

### Generate Demo Video

```bash
# Capture screenshots and generate demo video
python scripts/demo_capture.py

# Edit and polish the video
python scripts/demo_edit.py

# Output: demo_output/video/swarmmind_demo.mp4
```

### Demo Requirements

- Lemonade server running on localhost:13305
- Playwright installed (`pip install playwright`)
- FFmpeg installed (`apt install ffmpeg` or `brew install ffmpeg`)
- Streamlit running on localhost:8501

---

## 🔧 Configuration

### Default Config Location

```
~/.swarmmind/config.toml
```

### Example Config

```toml
[lemonade]
host = "localhost"
port = 13305

[models]
conductor = "Qwen3.6-35B-A3B-GGUF"
worker = "Gemma-4-12B-it"
embeddings = "nomic-embed-text-v1-GGUF"

[rag]
chunk_size = 512
chunk_overlap = 64
top_k = 5

[execution]
mode = "parallel"        # "parallel" (fast) or "sequential" (low-RAM)
max_concurrent = 4        # Limit parallel workers (1-16)

[ui]
theme = "dark"
panel_layout = "balanced"
```

### Execution Mode

Choose how worker agents run based on your hardware:

| Mode | Speed | RAM Usage | Best For |
|---|---|---|---|
| `parallel` (default) | ⚡ Fast | Higher | 32 GB+ RAM |
| `sequential` | 🐢 Slower | Lower | 8-16 GB RAM |

```bash
# CLI flag
swarmmind ask "query" --sequential

# Config file
[execution]
mode = "sequential"
```

**Web UI:** Toggle in Settings panel (left sidebar).

### Hardware Auto-Detection

SwarmMind automatically detects your AMD hardware:

| Hardware | Backend | Auto-Assignment |
|---|---|---|
| AMD Ryzen AI NPU (XDNA 2) | ryzenai | Embeddings |
| AMD Radeon GPU (ROCm) | rocm | Conductor & Workers |
| AMD CPU (llama.cpp) | cpu | Fallback |

---

## 🐛 Troubleshooting

### "Cannot reach Lemonade server"

```bash
# Check if Lemonade is running
curl http://localhost:13305/v1/health

# Start Lemonade if not running
lemonade-server start

# Check Lemonade logs
lemonade-server logs
```

### "ModuleNotFoundError: No module named 'streamlit'"

```bash
# Install streamlit
pip install streamlit

# Or install all dev dependencies
pip install -e ".[dev]"
```

### "pytest.mark.asyncio errors"

```bash
# Ensure pytest-asyncio is installed
pip install pytest-asyncio>=0.24

# Run with asyncio mode
python -m pytest tests/ -v --asyncio-mode=auto
```

### Tests Timing Out

```bash
# Run tests in smaller batches
python -m pytest tests/test_orchestrator.py -v
python -m pytest tests/test_integration.py -v

# Or skip slow tests
python -m pytest tests/ -v -m "not slow"
```

---

## 📊 Test Coverage

| Test File | Tests | Description |
|---|---|---|
| `test_orchestrator.py` | 6 | Config loading, LemonadeClient |
| `test_rag.py` | 3 | ChromaDB operations |
| `test_metrics.py` | 9 | Metrics snapshot, UI components |
| `test_benchmark.py` | 20 | Benchmark report, CLI wiring |
| `test_hardware.py` | 31 | Hardware detection, backend routing |
| `test_pyproject_toml.py` | 5 | Package structure validation |
| `test_async_utils.py` | 9 | Async utility consolidation |
| `test_lemonade_client.py` | 11 | Client bug fix, streaming |
| `test_license_consistency.py` | 3 | License consistency |
| `test_execution_mode.py` | 11 | Parallel/sequential execution modes |
| `test_integration.py` | 53 | End-to-end integration |
| **TOTAL** | **161** | **160 pass + 1 xfail** |

---

## 🎯 What to Look For

### Architecture Quality

- ✅ Clean separation of concerns (core/, ui/, rag/, lemonade/)
- ✅ Async-first design with proper error handling
- ✅ Hardware auto-detection and backend routing
- ✅ Modular worker system (RAG, Web, Analysis, Code)

### Code Quality

- ✅ TDD methodology (28 new tests, all passing)
- ✅ Type hints throughout
- ✅ Comprehensive docstrings
- ✅ Graceful error handling in CLI

### AMD Integration

- ✅ Uses 8 Lemonade API endpoints
- ✅ Auto-detects Ryzen AI NPU, ROCm GPU
- ✅ Recommends optimal backends per role
- ✅ 100% local, zero cloud dependency

### Documentation

- ✅ Clear README with architecture diagram
- ✅ CLI reference with all 11 commands
- ✅ Configuration guide
- ✅ This judge setup guide

---

## 📞 Support

If you encounter any issues:

1. Check the [Troubleshooting](#-troubleshooting) section
2. Run the smoke test: `bash tests/smoke_test.sh`
3. Check Lemonade status: `curl http://localhost:13305/v1/health`
4. Review the [CHANGELOG.md](CHANGELOG.md) for recent fixes

---

*Built with ❤️ for the AMD Lemonade Developer Challenge 2026*
