"""Tests for the evidence core (WP1 of the v0.4 execution plan)."""

from __future__ import annotations

import pytest

from swarmmind.core.evidence import Claim, EvidenceLedger, SourceRecord


def _web(url: str, title: str = "Page", snippet: str = "snippet") -> SourceRecord:
    return SourceRecord(kind="web", title=title, url=url, snippet=snippet)


def _rag(doc_id: str, chunk: int, snippet: str = "text") -> SourceRecord:
    return SourceRecord(kind="rag", title=doc_id, doc_id=doc_id, chunk=chunk, snippet=snippet)


# ---------------------------------------------------------------------------
# Stable IDs and deduplication
# ---------------------------------------------------------------------------


def test_stable_id_assignment() -> None:
    ledger = EvidenceLedger()
    ledger.add_sources([
        _web("https://a.example"),
        _web("https://b.example"),
        _web("https://c.example"),
    ])
    ids = [s.id for s in (ledger.get(f"S{i}") for i in (1, 2, 3))]
    assert ids == ["S1", "S2", "S3"]


def test_dedupe_web_by_url() -> None:
    ledger = EvidenceLedger()
    ledger.add_sources([_web("https://a.example", title="First"), _web("https://b.example")])
    ledger.add_sources([_web("https://a.example", title="Duplicate")])

    assert ledger.get("S1") is not None
    assert ledger.get("S1").title == "First"  # first content wins
    assert ledger.get("S4") is None  # duplicate did not consume a new ID
    ledger.add_sources([_web("https://c.example")])
    assert ledger.get("S3") is not None  # next fresh ID is S3, never reassigned


def test_dedupe_rag_by_doc_id_and_chunk() -> None:
    ledger = EvidenceLedger()
    ledger.add_sources([_rag("doc1", 0), _rag("doc1", 1), _rag("doc2", 0)])
    ledger.add_sources([_rag("doc1", 0)])  # duplicate of S1

    assert ledger.get("S1") is not None and ledger.get("S1").chunk == 0
    assert ledger.get("S2") is not None and ledger.get("S2").chunk == 1
    assert ledger.get("S3") is not None and ledger.get("S3").doc_id == "doc2"
    assert ledger.get("S4") is None


def test_never_reassign_existing_id_to_different_content() -> None:
    ledger = EvidenceLedger()
    ledger.add_sources([_web("https://a.example")])
    # Caller-supplied id must be ignored; different content gets a fresh ID.
    ledger.add_sources([{"id": "S1", "kind": "web", "title": "Other", "url": "https://b.example"}])

    assert ledger.get("S1").url == "https://a.example"
    assert ledger.get("S2").url == "https://b.example"


def test_get_returns_none_for_unknown() -> None:
    ledger = EvidenceLedger()
    assert ledger.get("S99") is None


def test_get_returns_defensive_copy() -> None:
    ledger = EvidenceLedger()
    ledger.add_sources([_web("https://a.example")])
    record = ledger.get("S1")
    assert record is not None
    record.title = "Mutated"
    assert ledger.get("S1").title == "Page"


# ---------------------------------------------------------------------------
# Prompt block
# ---------------------------------------------------------------------------


def test_as_prompt_block_contains_id_kind_title_and_snippet() -> None:
    ledger = EvidenceLedger()
    ledger.add_sources([_web("https://a.example", title="Alpha", snippet="exact words")])
    block = ledger.as_prompt_block()
    assert "[S1|web]" in block
    assert "Alpha" in block
    assert "exact words" in block


def test_as_prompt_block_truncates_to_budget() -> None:
    ledger = EvidenceLedger()
    ledger.add_sources([_web("https://a.example", title="Alpha", snippet="word " * 500)])
    block = ledger.as_prompt_block(max_chars=120)
    assert len(block) <= 120
    assert "truncated" in block
    assert ledger.as_prompt_block(max_chars=0) == ""


# ---------------------------------------------------------------------------
# Citation validation
# ---------------------------------------------------------------------------


def test_validate_citations_with_source_dicts() -> None:
    ledger = EvidenceLedger()
    report = {
        "title": "T",
        "sources": [
            {"id": "S1", "kind": "web", "title": "Alpha", "url": "https://a.example"},
            {"id": "S2", "kind": "rag", "title": "Beta", "doc_id": "d1", "chunk": 0},
        ],
        "sections": [{"heading": "H", "sources": ["S1", "S2"]}],
        "claims": [{"text": "c", "source_ids": ["S1"], "status": "supported"}],
    }
    out = ledger.validate_citations(report)

    assert [s["id"] for s in out["sources"]] == ["S1", "S2"]
    assert out["sources"][0]["kind"] == "web"
    assert out["sections"][0]["sources"] == ["S1", "S2"]
    assert out["grounding"]["supported"] == 1
    assert out["grounding"]["citation_coverage"] == 1.0


def test_validate_citations_with_legacy_url_strings() -> None:
    ledger = EvidenceLedger()
    report = {
        "sources": ["https://a.example", "https://b.example"],
        "sections": [{"sources": ["https://a.example"]}],
    }
    out = ledger.validate_citations(report)

    assert [s["url"] for s in out["sources"]] == ["https://a.example", "https://b.example"]
    assert out["sections"][0]["sources"] == ["S1"]  # legacy URL rewritten to stable ID


def test_validate_citations_filters_unknown_ids() -> None:
    ledger = EvidenceLedger()
    ledger.add_sources([_web("https://a.example")])
    report = {
        "sources": [{"id": "S1", "kind": "web", "title": "Alpha", "url": "https://a.example"}],
        "sections": [{"sources": ["S1", "S99", "https://unknown.example"]}],
    }
    out = ledger.validate_citations(report)
    assert out["sections"][0]["sources"] == ["S1"]


# ---------------------------------------------------------------------------
# Grounding summary
# ---------------------------------------------------------------------------


def test_grounding_summary_zero_claims() -> None:
    summary = EvidenceLedger().grounding_summary({"claims": []})
    assert summary == {
        "total_claims": 0,
        "supported": 0,
        "unverified": 0,
        "contradicted": 0,
        "citation_coverage": 0.0,
    }


def test_grounding_summary_mixed_claims() -> None:
    ledger = EvidenceLedger()
    ledger.add_sources([_web("https://a.example"), _web("https://b.example")])
    report = {
        "claims": [
            Claim(text="cited", source_ids=["S1"], status="supported").model_dump(),
            {"text": "uncited", "source_ids": ["S99"], "status": "unverified"},
            {"text": "no citation", "source_ids": [], "status": "contradicted"},
        ],
    }
    summary = ledger.grounding_summary(report)
    assert summary["total_claims"] == 3
    assert summary["supported"] == 1
    assert summary["unverified"] == 1
    assert summary["contradicted"] == 1
    assert summary["citation_coverage"] == pytest.approx(1 / 3)


# ---------------------------------------------------------------------------
# Mutation safety
# ---------------------------------------------------------------------------


def test_validate_citations_does_not_corrupt_report_keys() -> None:
    ledger = EvidenceLedger()
    report = {
        "title": "Keep me",
        "custom": {"nested": [1, 2, 3]},
        "grounding": {"stale": True},
        "sources": ["https://a.example"],
        "sections": [{"sources": ["https://a.example", "S404"]}],
    }

    out = ledger.validate_citations(report)

    # Unrelated keys survive untouched.
    assert out["title"] == "Keep me"
    assert out["custom"] == {"nested": [1, 2, 3]}
    # Owned keys are replaced with correct values, not silently corrupted.
    assert "stale" not in out["grounding"]
    assert out["sections"][0]["sources"] == ["S1"]
    assert out["sources"][0]["id"] == "S1"
    # Input is not mutated by validation.
    assert report["grounding"] == {"stale": True}
    assert report["sources"] == ["https://a.example"]
    assert report["sections"][0]["sources"] == ["https://a.example", "S404"]


def test_validate_citations_repeated_calls_do_not_leak() -> None:
    first = EvidenceLedger().validate_citations({
        "sources": ["https://a.example"],
        "sections": [{"sources": ["S1"]}],
    })
    second = EvidenceLedger().validate_citations({
        "title": "Second",
        "sections": [{"sources": []}],
    })
    assert first["sources"][0]["url"] == "https://a.example"
    assert "sources" not in second or second.get("sources") == []
    assert second["title"] == "Second"
    assert second["sections"][0]["sources"] == []
