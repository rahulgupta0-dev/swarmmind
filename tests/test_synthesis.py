"""WP3 — citation-aware synthesis tests.

Covers the bounded source registry, deterministic citation validation, claim
generation and graceful handling of malformed LLM responses.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from swarmmind.core.synthesis import (
    SYNTHESIS_SYSTEM_PROMPT,
    Synthesis,
    build_source_registry,
    render_registry_block,
)


class FakeClient:
    """Minimal LemonadeClient stand-in returning a fixed chat response."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []

    async def chat_completion(self, model: str, messages: list[dict], stream: bool = False) -> dict:
        self.calls.append({"model": model, "messages": messages, "stream": stream})
        return {"choices": [{"message": {"content": self.content}}]}


class RaisingClient:
    async def chat_completion(self, **kwargs: Any) -> dict:
        raise RuntimeError("simulated LLM failure")


def _src(sid: str, title: str = "Doc", snippet: str = "supporting text") -> dict:
    return {
        "id": sid, "kind": "rag", "title": title, "url": f"file:///{sid}.pdf",
        "snippet": snippet, "doc_id": sid, "chunk": 0,
    }


def _synthesis(content: str = "{}") -> tuple[Synthesis, FakeClient]:
    client = FakeClient(content)
    return Synthesis(client, "test-model"), client


def test_system_prompt_requires_citations() -> None:
    assert "[S1]" in SYNTHESIS_SYSTEM_PROMPT
    assert '"sources"' in SYNTHESIS_SYSTEM_PROMPT
    assert "never invent" in SYNTHESIS_SYSTEM_PROMPT.lower()


def test_registry_dedupes_by_id_and_supports_legacy_strings() -> None:
    outputs = [{
        "sources_cited": [
            _src("S1", "First"), _src("S1", "Duplicate"), _src("S2", "Second"),
            "https://example.com",
        ],
    }]
    registry, known = build_source_registry(outputs)
    ids = [r["id"] for r in registry]
    assert ids == ["S1", "S2", "S3"]
    assert known == {"S1", "S2", "S3"}
    assert registry[-1]["url"] == "https://example.com"
    assert registry[-1]["kind"] == "web"


def test_user_message_includes_bounded_registry() -> None:
    synthesis, _ = _synthesis()
    outputs = [{"findings": "f", "sources_cited": [_src("S1"), _src("S1")]}]
    msg = synthesis._build_user_message(outputs, "query")
    assert "--- Source Registry (cite ONLY these IDs) ---" in msg
    assert msg.count("[S1|") == 1
    assert "[S1]" in msg  # worker citation list

    many = [{"sources_cited": [_src(f"S{i}", snippet="x" * 400) for i in range(1, 100)]}]
    big_msg = synthesis._build_user_message(many, "query")
    segment = big_msg.split("--- Source Registry (cite ONLY these IDs) ---", 1)[1]
    segment = segment.split("--- Worker Outputs ---", 1)[0]
    assert "[truncated]" in segment
    assert len(segment) <= 6100
    assert len(render_registry_block([])) < 100


def test_parse_validates_citations_drops_unknown_and_adds_claims() -> None:
    synthesis, _ = _synthesis()
    registry, known = build_source_registry([{"sources_cited": [_src("S1")]}])
    payload = {
        "title": "Report",
        "executive_summary": "Backed by evidence [S1], plus a typo [S9].",
        "sections": [
            {"heading": "Good [S1]", "content": "True [S1] and bogus [S9].", "sources": ["S1", "S9"]},
            {"heading": "No evidence", "content": "Guess [S9].", "sources": ["S9"]},
        ],
        "conclusion": "Done [S1]",
        "contradictions": ["Earlier work [S9]"],
        "follow_up_questions": ["Why?"],
    }
    report = synthesis._parse_response(
        json.dumps(payload), "q", known_source_ids=known, sources=registry,
    )
    assert report["sections"][0]["sources"] == ["S1"]
    assert report["sections"][1]["sources"] == []
    assert "[S9]" not in report["sections"][0]["content"]
    assert "[S9]" not in report["executive_summary"]
    assert "[S1]" in report["executive_summary"]

    claims = report["claims"]
    assert len(claims) == 2
    assert claims[0]["status"] == "supported"
    assert claims[0]["source_ids"] == ["S1"]
    assert claims[1]["status"] == "unverified"
    assert claims[1]["source_ids"] == []

    assert report["sources"] == registry
    assert set(report["grounding"]) == {
        "total_claims", "supported", "unverified", "contradicted", "citation_coverage",
    }
    assert report["grounding"]["total_claims"] == 2
    assert report["stop_reason"] == "single_round"
    assert report["rounds_used"] == 1
    assert report["query"] == "q"
    assert report["timestamp"]


@pytest.mark.asyncio
async def test_synthesize_salvages_json_embedded_in_prose() -> None:
    payload = {"title": "Salvaged", "sections": [{"heading": "A", "content": "x [S1]", "sources": ["S1"]}]}
    synthesis, _ = _synthesis(f"Sure, here is the JSON:\n{json.dumps(payload)}\nHope this helps.")
    report = await synthesis.synthesize(
        [{"sources_cited": [_src("S1")], "findings": "f"}], "query",
    )
    assert report["title"] == "Salvaged"
    assert report["sections"][0]["sources"] == ["S1"]


@pytest.mark.asyncio
async def test_malformed_json_falls_back_gracefully() -> None:
    synthesis, _ = _synthesis("this is definitely not JSON {{{")
    report = await synthesis.synthesize([], "empty query")
    for key in (
        "title", "executive_summary", "sections", "conclusion",
        "contradictions", "follow_up_questions", "claims", "sources",
        "grounding", "stop_reason", "rounds_used",
    ):
        assert key in report
    assert "No sources were available" in report["executive_summary"]
    assert report["grounding"]["total_claims"] == 0


@pytest.mark.asyncio
async def test_legacy_string_sources_are_tolerated() -> None:
    payload = {"title": "Legacy", "sections": [{"heading": "S", "content": "web [S1]", "sources": ["S1"]}]}
    synthesis, _ = _synthesis(json.dumps(payload))
    report = await synthesis.synthesize(
        [{"sources_cited": ["https://example.com/a"], "findings": "f"}], "web query",
    )
    assert report["sources"][0]["id"] == "S1"
    assert report["sources"][0]["url"] == "https://example.com/a"
    assert report["claims"][0]["status"] == "supported"


@pytest.mark.asyncio
async def test_llm_failure_fallback_keeps_all_keys() -> None:
    synthesis = Synthesis(RaisingClient(), "model")
    report = await synthesis.synthesize(
        [{"findings": "f", "sources_cited": [_src("S1")]}], "q",
    )
    assert report["sections"][0]["heading"] == "Worker 1 Findings"
    assert report["sections"][0]["sources"] == ["S1"]
    assert report["claims"][0]["status"] == "supported"
    assert report["stop_reason"] == "single_round"
