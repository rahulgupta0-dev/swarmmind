# ⚡ SwarmMind 5-Minute Quickstart

Go from clone to a cited, grounded report. Commands are copy-paste ready.

## 1. Clone and install

```bash
git clone https://github.com/rahulgupta0-dev/swarmmind.git
cd swarmmind
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
swarmmind --help                   # confirm the CLI is on your PATH
```

Requires Python 3.11+. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) if install fails.

## 2. Start a Lemonade server

**With AMD hardware** (Ryzen AI NPU / Radeon GPU):

```bash
pip install lemonade-sdk
lemonade-server start              # listens on http://localhost:13305
```

**No AMD hardware (or no installable Lemonade package)?** Use the bundled mock
server. It returns canned chat/embedding responses and exercises the full CLI
and UI pipeline without a GPU or NPU — no extra dependencies:

```bash
# Terminal 1 — leave this running
.venv/bin/python tests/mock_lemonade_server.py    # port 13305; override with MOCK_PORT
```

The mock is for development/demo only. It does **not** validate real AMD
hardware or model quality.

## 3. Check everything with the doctor

```bash
swarmmind doctor
```

It checks your Python version, config, server health, configured models and
local setup, and prints `PASS` / `FAIL` / `WARN` per check with suggested fixes.
Fix any `FAIL` before continuing.

## 4. Verify with the smoke test (PASS lines)

```bash
bash tests/smoke_test.sh
```

Expected output:

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

## 5. Create a project and add a source

```bash
swarmmind project create "Demo" --id demo
swarmmind source add demo text "The Oxylabs Research Swarm uses leaderless coordination." --name "Fact"
swarmmind source list demo         # optional: confirm status = ready
```

Expected:

```
Created project 'Demo' (id: demo)
Added source Fact (id: …) — status: ready, 1 chunk(s), 61 chars
```

## 6. Ask a question, grounded in your source

```bash
swarmmind ask "How does the Oxylabs Research Swarm coordinate?" --project-id demo --no-web
```

`--no-web` keeps the answer local to your sources. Add `--json` for pure JSON on
stdout (diagnostics go to stderr). A grounded report cites stable `[S#]` source
IDs and ends with a Sources appendix:

```
┌─ Mock grounded research report ─────────────────────────┐
Executive Summary
  This report cites the supplied sources [S1].

Evidence summary
  Key findings are grounded in [S1].

Conclusion
  Citations resolved to real source IDs.

Sources
  [S1] Fact (text) — "The Oxylabs Research Swarm uses leaderless coordination."
```

Against the mock server the text is canned; against a real server the model
writes the report. In both cases `[S1]` maps to a real source card in the UI.

## 7. Open the web UI

```bash
swarmmind web
# open http://localhost:8501
```

The UI shows clickable citations, a grounding badge, a Sources appendix with
exact snippets, and an opt-in **Read aloud** button (Lemonade TTS).

## Next steps

- [SETUP.md](../SETUP.md) — detailed setup and configuration
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — symptom → cause → fix
- [COMPATIBILITY.md](COMPATIBILITY.md) — Lemonade release matrix
- [EVALUATION.md](EVALUATION.md) — offline quality evaluation (`swarmmind eval`)
