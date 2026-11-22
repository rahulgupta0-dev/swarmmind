# SwarmMind Evaluation (v0.4)

SwarmMind's evaluation harness measures **answer quality** with
deterministic, structural metrics first and treats model-based judgement as a
clearly-labelled secondary signal. It is deliberately honest: the golden set is
small, the default generator is a local mock, and no leaderboard claims are made.

Module: `swarmmind/eval.py` · Golden set: `tests/eval/golden.json` · Tests: `tests/test_eval.py`

## Why deterministic-primary?

Local laptop-class models are non-deterministic and prone to **self-preference
bias** when grading their own output. A judge that is the same model as the
generator is not an independent measurement. The harness therefore computes
metrics that can be reproduced and audited from the report JSON alone, and only
optionally adds an LLM judge that is a **different** model, blinded and
order-randomized. The judge's scores are always marked `approximate`.

## The golden set

`tests/eval/golden.json` starts with **8 offline queries**. Each entry contains:

- `id`, `query` — the test question;
- `sources` — local `SourceRecord`-shaped entries with `title`, `kind`, `url`,
  `snippet` and `key_phrases`;
- `key_phrases` — phrases expected to appear verbatim in the cited snippets.

The source text also lives in `tests/eval/sources/*.txt`, and each source's
`url` is a relative path to that file. This makes source retrievability a real,
filesystem-backed check and keeps the whole run offline (no DuckDuckGo, no page
fetch, no Lemonade server required). Grow the set toward 20 queries by adding
entries with local docs; keep phrases verbatim from the snippets.

## Metrics (primary, deterministic)

For each query the harness computes (all ratios in `[0, 1]`):

| Metric | Definition | Failure mode it catches |
|---|---|---|
| **Valid citation IDs** | precision of `[S#]` markers (in text and `sections[].sources`) against `report["sources"]`; unknown IDs are listed separately | Hallucinated or stale citation IDs (FM6) |
| **Source retrievability** | fraction of registry entries with a non-empty URL/path; for local paths the file must resolve under the golden-set directory | Fake or dead sources |
| **Quoted-span match** | fraction of expected `key_phrases` found in the cited snippets (case-insensitive) | Citing a source that does not support the claim |
| **Unsupported-claim count** | claims whose status is not supported/corroborated **or** whose `source_ids` do not intersect known IDs | Ungrounded assertions (FM7) |
| **Source diversity** | number of unique cited sources and distinct source kinds (`rag`/`web`/`image`/`model`) | Single-source or single-type answers |

`run_eval()` aggregates these across the golden set. Ratios of `1.0` with a
zero denominator mean "nothing to penalise" (e.g. a report with no citation
markers has no invalid citation IDs).

## Secondary LLM-as-judge (approximate)

Enable with `swarmmind eval --report --judge` or `run_eval(enable_judge=True)`:

- the **judge model is different** from the generator (auto-selected as
  `models.worker`/`models.omni`, or explicitly via `judge_model=`); if the only
  available model equals the generator, judging is **skipped**, never substituted;
- the report is presented **blinded**: no generator identity, source IDs
  replaced by `Doc A/B/…`, citation markers stripped;
- source and section order are **randomized** with a recorded `seed`;
- the judge returns faithfulness and answer relevance scores on a 0–5 scale.

These scores are labelled `approximate: true` in both the result dict and the
Markdown report. They are opinions from another local model, not ground truth,
and never replace the deterministic metrics.

## How to run

```bash
# Offline, deterministic metrics only
swarmmind eval --report

# Write Markdown to a file
swarmmind eval --output eval-report.md

# Add the approximate secondary judge (needs a second Lemonade model)
swarmmind eval --report --judge

# Tests (fast, offline)
.venv/bin/python -m pytest tests/test_eval.py -q

# As a library
.venv/bin/python -c \
  "from swarmmind.eval import run_eval, render_report; print(render_report(run_eval()))"
```

`swarmmind eval` uses `LocalEvalClient` by default: a deterministic stand-in for
`LemonadeClient.chat_completion` that drives the real `Synthesis` code path
(registry building, citation validation, grounding summary). This validates the
**measurement pipeline end-to-end**, not the quality of a real generator model.
To evaluate real models, pass a configured `client=` to `run_eval(...)`.

## Token accounting on FLM 1.0.11+ (verified 2026-11-22)

Upstream v2026.46.2 / PR
[#4037](https://github.com/lemonade-sdk/lemonade/pull/4037) changed FLM 1.0.11
so streaming token usage is emitted **only** when the request sets
`stream_options.include_usage`; without it, `/api/v1/stats` records no token
counts or TPS. SwarmMind's concurrent code action adds that flag to streaming
chat calls and makes `swarmmind benchmark` consume real usage chunks rather than
the previous `len(content.split())` word-count estimate — otherwise token
counts and tokens/sec would be empty/misleading on FLM 1.0.11+. These are
throughput measurements, not a quality or leaderboard result.

For the verifiable "deep-dive performance eval" artifact, the top next action
on real AMD hardware is `lemonade bench --submit` (PR
[#3284](https://github.com/lemonade-sdk/lemonade/pull/3284); CLI docs
https://lemonade-server.ai/docs/guide/cli/), which uploads results and prints a
bench.lemonade-server.ai claim link. **Not run here** (no AMD hardware/server),
so no leaderboard claim is made. See [CHALLENGE-STATUS.md](CHALLENGE-STATUS.md).

## Limitations and caveats

1. Eight queries is a smoke test, not a benchmark; no statistical significance
   or leaderboard claims.
2. The local mock generator produces perfect citations by construction, so
   aggregate scores near 1.0 validate the harness rather than a model.
3. Deterministic metrics check structure, not semantic truth.
4. LLM-as-judge is model-dependent, biased and approximate; it is secondary.
5. `stop_reason` is `single_round` and `rounds_used` is `1` (deferred
   multi-round/critic loop); see `docs/EXECUTION-PLAN.md`.
6. Web search and page fetching are opt-in (`Config.web`); the default eval path
   is fully local. Combine with `swarmmind benchmark` for throughput/TTFT to get
   an honest deep-dive — never a speed-vs-quality ranking.
7. The run manifest in `docs/REPRODUCIBILITY.md` records models, backends,
   config snapshot and timestamp for every run.

## API

- `run_eval(config=None, client=None, golden_path=None, enable_judge=False,
  judge_model=None, seed=0) -> dict` — returns
  `{"manifest", "aggregate", "results", "judge"}`.
- `render_report(result) -> str` — Markdown table + caveats.
- `compute_metrics(report, golden_entry=None, base_dir=None) -> dict` — pure
  metric function, usable with synthetic reports in tests.
- `load_golden(path=None) -> dict` — load the golden set.
