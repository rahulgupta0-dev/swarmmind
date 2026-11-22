"""Basic tests for SwarmMind scaffolding."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from swarmmind import __version__
from swarmmind.config import Config, ExecutionConfig
from swarmmind.lemonade.client import LemonadeClient


class TestConfig:
    """Configuration loading and defaults."""

    def test_version(self) -> None:
        assert __version__ == "0.3.0"

    def test_default_config(self) -> None:
        """Config should load with sensible defaults (user config is isolated by conftest)."""
        config = Config()  # type: ignore[call-arg]
        assert config.lemonade.host == "localhost"
        assert config.lemonade.port == 13305
        assert config.rag.chunk_size == 512
        assert config.rag.chunk_overlap == 64
        assert config.rag.top_k == 5
        assert config.models.conductor == "Qwen3.6-35B-A3B-GGUF"
        assert config.models.worker == "Gemma-4-12B-it"
        assert config.models.embeddings == "nomic-embed-text-v1-GGUF"
        assert config.models.image == "Flux-2-Klein-4B"
        assert config.models.tts == "kokoro-v1"
        assert config.ui.theme == "light"
        assert config.ui.panel_layout == "balanced"

    def test_get_lemonade_base_url(self) -> None:
        config = Config()  # type: ignore[call-arg]
        assert config.get_lemonade_base_url() == "http://localhost:13305"

    def test_custom_lemonade_host(self) -> None:
        config = Config(lemonade={"host": "192.168.1.100", "port": 8080})  # type: ignore[call-arg]
        assert config.get_lemonade_base_url() == "http://192.168.1.100:8080"


class TestLemonadeClient:
    """LemonadeClient instantiation (no server needed)."""

    def test_instantiation(self) -> None:
        client = LemonadeClient("http://localhost:13305")
        assert client.base_url == "http://localhost:13305"
        assert client.api_key == "not-needed"

    def test_custom_url(self) -> None:
        client = LemonadeClient("http://192.168.1.50:13305", api_key="test-key")
        assert client.base_url == "http://192.168.1.50:13305"
        assert client.api_key == "test-key"


# ===================================================================
# Evidence-ledger wiring (WP2)
# ===================================================================


def _make_mock_orchestrator(
    config: Config | None = None,
    chroma_store: Any = None,
) -> Any:
    """Build an Orchestrator with hardware detection stubbed out."""
    from swarmmind.core.orchestrator import Orchestrator

    cfg = config or Config(execution=ExecutionConfig(mode="sequential"))
    client = MagicMock()
    client.health_check = AsyncMock(return_value={"status": "ok"})
    client.load_model = AsyncMock(return_value={})

    with patch("swarmmind.core.orchestrator.detect_hardware") as mock_hw, patch(
        "swarmmind.core.orchestrator.recommend_backends", return_value={},
    ):
        mock_hw.return_value = MagicMock(
            os="linux", cpu_name="CPU", gpus=[], npus=[], total_ram_gb=16,
        )
        return Orchestrator(cfg, client, chroma_store=chroma_store)


def _assert_report_contract(report: dict[str, Any]) -> None:
    assert "title" in report
    assert "sections" in report
    assert report["stop_reason"] == "single_round"
    assert report["rounds_used"] == 1
    assert isinstance(report["sources"], list)
    assert isinstance(report["grounding"], dict)


@pytest.mark.asyncio
async def test_rag_sources_get_stable_ids_and_prefixed_context() -> None:
    store = MagicMock()
    store.search.return_value = [
        {
            "text": "RISC-V is an open ISA",
            "metadata": {"source_id": "doc-7", "chunk_index": 3},
            "distance": 0.12,
        },
        {
            "text": "Second chunk of text",
            "metadata": {"source_id": "doc-8", "chunk_index": 0},
            "distance": 0.2,
        },
    ]
    orch = _make_mock_orchestrator(chroma_store=store)
    orch._conductor.decompose_query = AsyncMock(return_value=[
        {"worker_type": "rag", "task": "Explain", "context": ""},
    ])

    seen: dict[str, str] = {}

    async def fake_run(worker_type: str, task: str, context: str, additional: str) -> dict:
        seen["additional"] = additional
        return {
            "findings": "f",
            "key_points": ["RISC-V is an open ISA"],
            "confidence": "high",
        }

    orch._worker.run = fake_run
    orch._synthesis.synthesize = AsyncMock(return_value={
        "title": "Report",
        "sections": [{"heading": "S", "content": "c", "sources": ["S1", "S999"]}],
        "claims": [{"text": "RISC-V is an open ISA", "source_ids": ["S1"], "status": "supported"}],
    })

    report = await orch.run("query", project_context={"id": "proj"}, web_search_enabled=False)

    assert "[S1]" in seen["additional"]
    assert "[S2]" in seen["additional"]
    assert report["sections"][0]["sources"] == ["S1"]  # S999 filtered out
    assert [s["id"] for s in report["sources"]] == ["S1", "S2"]
    rag = report["sources"][0]
    assert rag["kind"] == "rag" and rag["doc_id"] == "doc-7" and rag["chunk"] == 3
    assert rag["title"] == "doc-7"
    assert report["grounding"]["supported"] == 1
    _assert_report_contract(report)


@pytest.mark.asyncio
async def test_web_sources_use_fetch_pages_and_cite_snippets() -> None:
    config = Config(
        execution=ExecutionConfig(mode="sequential"),
        web={"fetch_pages": True, "max_pages": 2, "timeout_seconds": 1.5},  # type: ignore[call-arg]
    )
    orch = _make_mock_orchestrator(config=config)
    orch._conductor.decompose_query = AsyncMock(return_value=[
        {"worker_type": "web", "task": "Search", "context": ""},
    ])

    ddg = [
        {"title": "DDG A", "url": "https://a.example", "snippet": "DDG snippet A"},
        {"title": "DDG B", "url": "https://b.example", "snippet": "DDG snippet B"},
    ]

    async def fake_run(worker_type: str, task: str, context: str, additional: str) -> dict:
        seen_web["additional"] = additional
        return {
            "findings": "f",
            "key_points": ["Fetched page A text", "DDG snippet B"],
            "confidence": "high",
        }

    seen_web: dict[str, str] = {}
    orch._worker.run = fake_run
    orch._synthesis.synthesize = AsyncMock(return_value={"title": "R", "sections": []})

    with patch("swarmmind.core.orchestrator.search_web", return_value=ddg), patch(
        "swarmmind.core.orchestrator.fetch_pages", new_callable=AsyncMock,
    ) as mock_fetch:
        mock_fetch.return_value = [
            {
                "kind": "web", "title": "Page A", "url": "https://a.example",
                "snippet": "Fetched page A text", "fetch_ok": True,
            },
            {
                "kind": "web", "title": "DDG B", "url": "https://b.example",
                "snippet": "DDG snippet B", "fetch_ok": False,
            },
        ]
        result = await orch.run("query")

    mock_fetch.assert_awaited_once()
    assert mock_fetch.await_args.kwargs["max_pages"] == 2
    assert mock_fetch.await_args.kwargs["timeout_seconds"] == 1.5
    assert "[S1]" in seen_web["additional"] and "Fetched page A text" in seen_web["additional"]
    assert "[S2]" in seen_web["additional"] and "DDG snippet B" in seen_web["additional"]

    # Locate the worker output via synthesis call args.
    outputs = orch._synthesis.synthesize.await_args.args[0]
    cited = outputs[0]["sources_cited"]
    assert [s["id"] for s in cited] == ["S1", "S2"]
    statuses = {c["text"]: c["status"] for c in outputs[0]["claims"]}
    assert statuses["Fetched page A text"] == "supported"
    assert statuses["DDG snippet B"] == "unverified"
    _assert_report_contract(result)


@pytest.mark.asyncio
async def test_web_fetch_disabled_falls_back_to_ddg_snippet() -> None:
    config = Config(
        execution=ExecutionConfig(mode="sequential"),
        web={"fetch_pages": False, "max_pages": 5, "timeout_seconds": 0.1},  # type: ignore[call-arg]
    )
    orch = _make_mock_orchestrator(config=config)
    orch._conductor.decompose_query = AsyncMock(return_value=[
        {"worker_type": "web", "task": "Search", "context": ""},
    ])
    orch._worker.run = AsyncMock(return_value={
        "findings": "f", "key_points": ["DDG snippet A"], "confidence": "medium",
    })
    orch._synthesis.synthesize = AsyncMock(return_value={"title": "R", "sections": []})

    with patch("swarmmind.core.orchestrator.search_web", return_value=[
        {"title": "DDG A", "url": "https://a.example", "snippet": "DDG snippet A"},
    ]), patch("swarmmind.core.orchestrator.fetch_pages", new_callable=AsyncMock) as mock_fetch:
        await orch.run("query")

    mock_fetch.assert_not_awaited()
    outputs = orch._synthesis.synthesize.await_args.args[0]
    assert outputs[0]["sources_cited"][0]["snippet"] == "DDG snippet A"
    assert outputs[0]["claims"][0]["status"] == "unverified"


@pytest.mark.asyncio
async def test_legacy_mock_output_without_claims_still_works() -> None:
    orch = _make_mock_orchestrator()
    orch._conductor.decompose_query = AsyncMock(return_value=[
        {"worker_type": "analysis", "task": "Think", "context": ""},
    ])
    orch._worker.run = AsyncMock(return_value={
        "findings": "found",
        "key_points": ["Legacy claim"],
        "sources_cited": ["https://legacy.example"],
        "confidence": "medium",
    })
    orch._synthesis.synthesize = AsyncMock(return_value={"title": "R", "sections": []})

    report = await orch.run("query", web_search_enabled=False)

    outputs = orch._synthesis.synthesize.await_args.args[0]
    assert outputs[0]["sources_cited"][0]["kind"] == "model"
    assert outputs[0]["claims"][0]["status"] == "unverified"
    _assert_report_contract(report)


@pytest.mark.asyncio
async def test_timeout_branch_still_gets_sources_and_claims() -> None:
    orch = _make_mock_orchestrator()
    orch._conductor.decompose_query = AsyncMock(return_value=[
        {"worker_type": "analysis", "task": "Think", "context": ""},
    ])

    async def slow_run(*args: Any, **kwargs: Any) -> dict:
        await asyncio.sleep(0.2)
        return {"findings": "too late"}

    orch._worker.run = slow_run
    orch._synthesis.synthesize = AsyncMock(return_value={"title": "R", "sections": []})

    with patch("swarmmind.core.orchestrator._WORKER_TIMEOUT", 0.01):
        await orch.run("query", web_search_enabled=False)

    outputs = orch._synthesis.synthesize.await_args.args[0]
    assert "timed out" in outputs[0]["findings"]
    assert outputs[0]["sources_cited"]
    assert "claims" in outputs[0]


@pytest.mark.asyncio
async def test_worker_exception_branch_still_gets_sources_and_claims() -> None:
    orch = _make_mock_orchestrator()
    orch._conductor.decompose_query = AsyncMock(return_value=[
        {"worker_type": "analysis", "task": "Think", "context": ""},
    ])
    orch._worker.run = AsyncMock(side_effect=RuntimeError("kaboom"))
    orch._synthesis.synthesize = AsyncMock(return_value={"title": "R", "sections": []})

    await orch.run("query", web_search_enabled=False)

    outputs = orch._synthesis.synthesize.await_args.args[0]
    assert "error" in outputs[0]["findings"]
    assert outputs[0]["sources_cited"]
    assert "claims" in outputs[0]


@pytest.mark.asyncio
async def test_all_existing_progress_callbacks_are_preserved() -> None:
    orch = _make_mock_orchestrator()
    orch._conductor.decompose_query = AsyncMock(return_value=[
        {"worker_type": "analysis", "task": "Think", "context": ""},
    ])
    orch._worker.run = AsyncMock(return_value={"findings": "f", "confidence": "high"})
    orch._synthesis.synthesize = AsyncMock(return_value={"title": "R", "sections": []})

    events: list[str] = []
    await orch.run(
        "query",
        web_search_enabled=False,
        progress_callback=lambda status, detail: events.append(status),
    )

    for expected in (
        "preflight", "hardware_detected", "conducting", "decomposed",
        "working", "worker_progress", "completed_workers", "synthesising", "done",
    ):
        assert expected in events
