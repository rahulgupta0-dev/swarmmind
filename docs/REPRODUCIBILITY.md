# Reproducibility (v0.4)

Every `swarmmind eval` run returns a **run manifest** so a result can be
reproduced and audited. The manifest is available programmatically in
`run_eval()["manifest"]` and is summarized at the top of the Markdown report
produced by `render_report()` / `swarmmind eval --report`.

## Manifest fields

| Field | Contents |
|---|---|
| `eval_version` | Eval harness schema version (`"1.0"`). |
| `generated_at` | UTC timestamp of the run (ISO-8601, seconds precision). |
| `generator_model` | Lemonade model used for synthesis (default `models.conductor`). |
| `judge_model` | Different model used for the optional secondary judge, or `null`. |
| `judge_enabled` | Whether the judge ran. |
| `judge_skipped_reason` | Why the judge did not run (disabled, or no distinct model). |
| `backends` | Configured backend per role, or `"auto"` where hardware decides. |
| `config_snapshot` | Full `Config.model_dump()` at run time (models, RAG, execution, web, UI). |
| `stop_reasons` | Distinct `stop_reason` values from the reports (`single_round`). |
| `rounds_used` | Distinct round counts (currently `[1]`). |
| `seed` | Seed for the judge's blinded order randomization. |
| `client` | Client class used (`LocalEvalClient` for the default offline run). |
| `golden_set` | JSON path, version and query count. |

The manifest deliberately records **models and backends by name** — Lemonade
routing is by model variant, not a request-body backend field. Two runs with the
same manifest and the same golden-set file should produce the same deterministic
metrics; LLM-judge scores are approximate and may vary with model versions even
under the same manifest.

## One-command re-run

```bash
# Deterministic primary metrics, offline (no server, no network)
swarmmind eval --report

# Deterministic + approximate secondary judge, fixed randomization seed
swarmmind eval --report --judge

# Persist the artifact (Markdown report)
swarmmind eval --report --output docs/eval-run.md

# Full regression check (tests + suite)
timeout 120 .venv/bin/python -m pytest tests/test_eval.py -q
```

From Python, pin the inputs explicitly:

```python
from swarmmind.eval import run_eval, render_report
from swarmmind.config import Config

cfg = Config()
result = run_eval(config=cfg, enable_judge=True, judge_model=cfg.models.worker, seed=0)
print(result["manifest"]["generated_at"])
print(render_report(result))
```

To reproduce against real models instead of the offline mock, pass a connected
`client=` (a `LemonadeClient`-compatible object with `chat_completion`) to
`run_eval(...)` and record its model versions in the report notes.

## Environment and config snapshot

- Python ≥ 3.11; dependencies come from `pyproject.toml` (no new packages for
  evaluation). Use `.venv/bin/python -m pytest`.
- `Config` loads `~/.swarmmind/config.toml`, `SWARMMIND_*` environment variables
  and defaults (in that precedence). The manifest's `config_snapshot` captures
  the resolved values, so a re-run only needs the same TOML/env to match.
- The golden set and its local source documents live under `tests/eval/` and are
  versioned with the repository; the manifest records the golden-set version.

## Privacy note

SwarmMind is **local-first**. The eval path uses `LocalEvalClient` and the local
source files under `tests/eval/`; it makes **no network calls**. Web search and
page fetching are **opt-in** via `Config.web` (`fetch_pages`, `max_pages`,
`timeout_seconds`) and are not used by the default evaluation. When enabled for
a research query (not eval), external URLs are contacted and should be
considered external data disclosure. The config snapshot in the manifest
contains host/port and model names but no API keys or document contents.
