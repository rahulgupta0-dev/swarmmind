"""Tests for worker prompts and citation plumbing."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from swarmmind.core.workers import WORKER_SYSTEM_PROMPTS, Worker


def _worker_with_response(content: str) -> tuple[Worker, MagicMock]:
    client = MagicMock()
    client.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": content}}],
    })
    return Worker(client, "test-model"), client


def test_all_worker_prompts_require_inline_s1_citations() -> None:
    assert set(WORKER_SYSTEM_PROMPTS) >= {"rag", "web", "analysis", "code", "vision"}
    for name, prompt in WORKER_SYSTEM_PROMPTS.items():
        assert "[S1]" in prompt, name
        assert "cite" in prompt.lower(), name
        assert "Never invent" in prompt, name


def test_run_signature_keeps_four_positional_args() -> None:
    params = list(inspect.signature(Worker.run).parameters.values())
    assert [p.name for p in params[1:5]] == [
        "worker_type", "task", "context", "additional_context",
    ]
    # The fourth argument must be optional so legacy 4-arg mocks/callers work.
    assert params[4].default == ""


@pytest.mark.asyncio
async def test_worker_run_passes_citation_rules_to_model() -> None:
    content = (
        "Findings here.\n"
        "- RISC-V is an open ISA [S1]\n"
        "- Web result confirms it [S2]\n"
        "Confidence: high"
    )
    worker, client = _worker_with_response(content)

    result = await worker.run(
        "rag", "Explain RISC-V", "[S1] doc chunk text", "[S2] web snippet",
    )

    messages = client.chat_completion.await_args.kwargs["messages"]
    system_prompt = messages[0]["content"]
    user_message = messages[1]["content"]
    assert "[S1]" in system_prompt
    assert "Inline citations" in user_message
    assert "[S1] doc chunk text" in user_message
    assert "[S2] web snippet" in user_message
    assert result["key_points"] == [
        "RISC-V is an open ISA [S1]", "Web result confirms it [S2]",
    ]
    assert result["claims"] == []
    assert result["sources_cited"] == []


@pytest.mark.asyncio
async def test_worker_run_error_branch_has_evidence_keys() -> None:
    client = MagicMock()
    client.chat_completion = AsyncMock(side_effect=RuntimeError("boom"))
    worker = Worker(client, "test-model")

    result = await worker.run("analysis", "task", "", "")

    assert "encountered an error" in result["findings"]
    assert result["key_points"] == []
    assert result["sources_cited"] == []
    assert result["claims"] == []
    assert result["confidence"] == "low"


@pytest.mark.asyncio
async def test_vision_worker_uses_multimodal_message() -> None:
    worker, client = _worker_with_response("A diagram.")

    await worker.run("vision", "Describe", "", "data:image/png;base64,AAAA")

    content: Any = client.chat_completion.await_args.kwargs["messages"][1]["content"]
    assert isinstance(content, list)
    assert content[1]["image_url"]["url"].startswith("data:image")
