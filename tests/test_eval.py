"""Offline tests for the SwarmMind evaluation harness (WP4).

Covers deterministic metric computation with synthetic reports (perfect,
missing citations, unknown IDs, zero claims), Markdown rendering, the
``swarmmind eval`` CLI command, and run-manifest presence. All tests avoid
network access and use the local mock Lemonade client.
"""

from __future__ import annotations

import re

import pytest
from click.testing import CliRunner

from swarmmind.cli.main import main
from swarmmind.eval import (
    DEFAULT_GOLDEN_PATH,
    compute_metrics,
    load_golden,
    render_report,
    run_eval,
)

_GOLDEN_ENTRY = {
    "id": "g-test",
    "query": "Where is the needle?",
    "key_phrases": ["needle"],
    "sources": [
        {
            "id": "S1",
            "kind": "web",
            "title": "Haystack",
            "url": "sources/haystack.txt",
            "snippet": "Inside this haystack there is a needle.",
        }
    ],
}


def _source(**overrides: object) -> dict:
    src: dict = {
        "id": "S1",
        "kind": "web",
        "title": "Haystack",
        "url": "sources/haystack.txt",
        "snippet": "Inside this haystack there is a needle.",
    }
    src.update(overrides)
    return src


def _report(*, markers: str = "[S1]", section_sources: list[str] | None = None,
            claim_ids: list[str] | None = None, status: str = "supported",
            with_claims: bool = True) -> dict:
    report = {
        "sources": [_source()],
        "executive_summary": f"A grounded finding {markers}.",
        "sections": [{
            "heading": "Finding",
            "content": f"Evidence {markers}.",
            "sources": section_sources if section_sources is not None else ["S1"],
        }],
        "claims": [],
    }
    if with_claims:
        report["claims"] = [{
            "text": "A claim",
            "source_ids": claim_ids if claim_ids is not None else ["S1"],
            "status": status,
        }]
    return report


# ---------------------------------------------------------------------------
# Deterministic metrics
# ---------------------------------------------------------------------------


def test_perfect_report_scores_full_marks() -> None:
    metrics = compute_metrics(_report(), _GOLDEN_ENTRY)
    assert metrics["citation_ids"]["precision"] == 1.0
    assert metrics["source_retrievability_ratio"] == 1.0
    assert metrics["quoted_span_match_ratio"] == 1.0
    assert metrics["unsupported_claim_count"] == 0
    assert metrics["unique_sources"] == 1
    assert metrics["distinct_source_kinds"] == 1


def test_missing_citations_flag_unsupported_claim() -> None:
    report = _report(markers="", section_sources=[], claim_ids=[], status="unverified")
    metrics = compute_metrics(report, _GOLDEN_ENTRY)
    # No markers at all -> nothing invalid, but the claim is unsupported.
    assert metrics["citation_ids"]["total"] == 0
    assert metrics["valid_citation_ratio"] == 1.0
    assert metrics["unsupported_claim_count"] == 1
    assert metrics["unsupported_claim_ratio"] == 1.0


def test_unknown_citation_ids_are_penalized() -> None:
    report = _report(
        markers="[S99]", section_sources=["S99"], claim_ids=["S99"],
    )
    metrics = compute_metrics(report, _GOLDEN_ENTRY)
    assert metrics["citation_ids"]["total"] == 3
    assert metrics["citation_ids"]["valid"] == 0
    assert metrics["citation_ids"]["unknown_ids"] == ["S99"]
    assert metrics["valid_citation_ratio"] == 0.0
    assert metrics["unsupported_claim_count"] == 1


def test_zero_claims_reports_zero_unsupported() -> None:
    metrics = compute_metrics(_report(with_claims=False), _GOLDEN_ENTRY)
    assert metrics["unsupported_claim_count"] == 0
    assert metrics["unsupported_claims"]["total_claims"] == 0
    assert metrics["valid_citation_ratio"] == 1.0


def test_retrievability_checks_local_paths() -> None:
    golden = load_golden()
    entry = golden["queries"][0]
    source = entry["sources"][0]
    report = {
        "sources": [dict(source, id="S1")],
        "sections": [{"content": "", "sources": ["S1"]}],
        "claims": [],
    }
    base = DEFAULT_GOLDEN_PATH.parent
    assert compute_metrics(report, entry, base)["source_retrievability_ratio"] == 1.0
    missing = {
        "sources": [dict(source, id="S1", url="sources/does-not-exist.txt")],
        "sections": [],
        "claims": [],
    }
    assert compute_metrics(missing, entry, base)["source_retrievability_ratio"] == 0.0


def test_unknown_key_phrase_lowers_span_match() -> None:
    entry = dict(_GOLDEN_ENTRY, key_phrases=["needle", "absent phrase"])
    metrics = compute_metrics(_report(), entry)
    assert metrics["quoted_span_match_ratio"] == pytest.approx(0.5)
    assert metrics["quoted_span_match"]["missing_phrases"] == ["absent phrase"]


# ---------------------------------------------------------------------------
# Golden set, run_eval and manifest
# ---------------------------------------------------------------------------


def test_golden_set_has_eight_offline_queries() -> None:
    golden = load_golden()
    assert len(golden["queries"]) == 8
    assert all(q.get("sources") for q in golden["queries"])


def test_run_eval_produces_metrics_and_manifest() -> None:
    result = run_eval()
    assert result["manifest"]["eval_version"] == "1.0"
    assert result["manifest"]["generator_model"]
    assert result["manifest"]["config_snapshot"]
    assert result["manifest"]["stop_reasons"] == ["single_round"]
    assert result["manifest"]["judge_enabled"] is False
    assert result["manifest"]["golden_set"]["count"] == 8
    assert len(result["results"]) == 8
    assert result["aggregate"]["n_queries"] == 8
    assert result["aggregate"]["valid_citation_ratio"] == 1.0
    assert result["aggregate"]["unsupported_claim_count"] == 0
    assert set(result["manifest"]["backends"]) >= {"conductor", "worker"}


def test_run_eval_judge_uses_a_different_blinded_model() -> None:
    result = run_eval(enable_judge=True, seed=7)
    manifest = result["manifest"]
    assert manifest["judge_enabled"] is True
    assert manifest["judge_model"] != manifest["generator_model"]
    assert result["judge"]["approximate"] is True
    for item in result["results"]:
        judge = item["judge"]
        assert judge["blinded"] is True
        assert judge["order_randomized"] is True
        assert judge["seed"] == 7
        assert 0 <= judge["faithfulness"] <= 5


def test_judge_model_equal_to_generator_is_never_used() -> None:
    result = run_eval(enable_judge=True, judge_model="Qwen3.6-35B-A3B-GGUF")
    assert result["manifest"]["judge_enabled"] is False
    assert "never used as its own judge" in result["manifest"]["judge_skipped_reason"]


# ---------------------------------------------------------------------------
# Rendering + CLI
# ---------------------------------------------------------------------------


def test_render_report_contains_tables_and_caveats() -> None:
    markdown = render_report(run_eval())
    assert "Deterministic metrics (primary)" in markdown
    assert "Valid citations" in markdown
    assert "g1:" in markdown
    assert "APPROXIMATE" not in markdown
    assert "not run" in markdown
    assert "## Caveats" in markdown
    assert "leaderboard" in markdown


def test_render_report_with_judge_is_labelled_approximate() -> None:
    markdown = render_report(run_eval(enable_judge=True))
    assert "APPROXIMATE" in markdown
    assert "Faithfulness (0" in markdown


def test_cli_eval_report_registers_and_prints() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["eval", "--report"])
    assert result.exit_code == 0, result.output
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "Deterministic metrics (primary)" in plain
    assert "Caveats" in plain


def test_cli_eval_writes_output_file(tmp_path) -> None:
    target = tmp_path / "eval.md"
    runner = CliRunner()
    result = runner.invoke(main, ["eval", "--output", str(target)])
    assert result.exit_code == 0, result.output
    assert target.exists()
    assert "Deterministic metrics (primary)" in target.read_text(encoding="utf-8")


def test_cli_help_lists_eval() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "eval" in result.output
