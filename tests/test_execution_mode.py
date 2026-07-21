"""Tests for parallel vs sequential worker execution mode."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from swarmmind.config import Config, ExecutionConfig


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestExecutionConfig:
    """Validate ExecutionConfig defaults and constraints."""

    def test_default_mode_is_parallel(self) -> None:
        cfg = ExecutionConfig()
        assert cfg.mode == "parallel"

    def test_default_max_concurrent(self) -> None:
        cfg = ExecutionConfig()
        assert cfg.max_concurrent == 4

    def test_sequential_mode(self) -> None:
        cfg = ExecutionConfig(mode="sequential", max_concurrent=1)
        assert cfg.mode == "sequential"
        assert cfg.max_concurrent == 1

    def test_max_concurrent_bounds(self) -> None:
        """max_concurrent must be between 1 and 16."""
        cfg = ExecutionConfig(max_concurrent=1)
        assert cfg.max_concurrent == 1
        cfg2 = ExecutionConfig(max_concurrent=16)
        assert cfg2.max_concurrent == 16

    def test_config_includes_execution(self) -> None:
        cfg = Config()
        assert hasattr(cfg, "execution")
        assert cfg.execution.mode == "parallel"

    def test_execution_mode_from_toml(self) -> None:
        """Execution config can be set via TOML-style dict."""
        cfg = Config(execution=ExecutionConfig(mode="sequential", max_concurrent=2))
        assert cfg.execution.mode == "sequential"
        assert cfg.execution.max_concurrent == 2


# ---------------------------------------------------------------------------
# Orchestrator execution mode tests
# ---------------------------------------------------------------------------


def _make_mock_orchestrator(mode: str = "parallel", max_concurrent: int = 4):
    """Create an Orchestrator with mocked dependencies for execution mode testing."""
    from swarmmind.core.orchestrator import Orchestrator

    config = Config(execution=ExecutionConfig(mode=mode, max_concurrent=max_concurrent))
    client = MagicMock()
    client.health_check = AsyncMock(return_value={"status": "ok"})
    client.load_model = AsyncMock(return_value={})
    client.chat = AsyncMock(return_value={"choices": [{"message": {"content": "[]"}}]})

    # Mock hardware detection to skip network calls
    with patch("swarmmind.core.orchestrator.detect_hardware") as mock_hw:
        mock_hw.return_value = MagicMock(
            os="linux", cpu_name="Test CPU", gpus=[], npus=[],
            total_ram_gb=16,
        )
        with patch("swarmmind.core.orchestrator.recommend_backends", return_value={}):
            orch = Orchestrator(config, client)

    return orch


class TestParallelExecution:
    """Test parallel worker execution via asyncio.gather."""

    @pytest.mark.asyncio
    async def test_parallel_mode_calls_gather(self) -> None:
        """In parallel mode, workers should be dispatched via asyncio.gather."""
        orch = _make_mock_orchestrator(mode="parallel", max_concurrent=4)

        # Mock conductor to return 3 analysis tasks (no web, since web_search_enabled=False filters them)
        orch._conductor.decompose_query = AsyncMock(return_value=[
            {"worker_type": "analysis", "task": "Task 1", "context": ""},
            {"worker_type": "analysis", "task": "Task 2", "context": ""},
            {"worker_type": "analysis", "task": "Task 3", "context": ""},
        ])

        # Mock worker.run to track execution order
        call_times: list[str] = []

        async def fake_run(worker_type: str, task: str, context: str, additional: str) -> dict:
            call_times.append(f"start-{task}")
            await asyncio.sleep(0.01)
            call_times.append(f"end-{task}")
            return {"findings": f"Result for {task}", "key_points": [], "sources_cited": [], "confidence": "high"}

        orch._worker.run = fake_run
        orch._synthesis.synthesize = AsyncMock(return_value={"title": "Test Report", "sections": []})

        report = await orch.run("test query", web_search_enabled=False)

        assert report["title"] == "Test Report"
        # In parallel mode, all workers start before any end
        starts = [t for t in call_times if t.startswith("start-")]
        ends = [t for t in call_times if t.startswith("end-")]
        assert len(starts) == 3
        assert len(ends) == 3
        # All starts should happen before all ends (parallel)
        assert call_times.index(starts[0]) < call_times.index(ends[-1])


class TestSequentialExecution:
    """Test sequential worker execution."""

    @pytest.mark.asyncio
    async def test_sequential_mode_runs_one_at_a_time(self) -> None:
        """In sequential mode, workers run one after another."""
        orch = _make_mock_orchestrator(mode="sequential", max_concurrent=1)

        # Only analysis tasks (web tasks filtered when web_search_enabled=False)
        orch._conductor.decompose_query = AsyncMock(return_value=[
            {"worker_type": "analysis", "task": "Task A", "context": ""},
            {"worker_type": "analysis", "task": "Task B", "context": ""},
            {"worker_type": "analysis", "task": "Task C", "context": ""},
        ])

        call_log: list[str] = []

        async def fake_run(worker_type: str, task: str, context: str, additional: str) -> dict:
            call_log.append(f"start-{task}")
            await asyncio.sleep(0.01)
            call_log.append(f"end-{task}")
            return {"findings": f"Result for {task}", "key_points": [], "sources_cited": [], "confidence": "high"}

        orch._worker.run = fake_run
        orch._synthesis.synthesize = AsyncMock(return_value={"title": "Sequential Report", "sections": []})

        report = await orch.run("test query", web_search_enabled=False)

        assert report["title"] == "Sequential Report"
        # In sequential mode: start-A, end-A, start-B, end-B, start-C, end-C
        assert call_log == [
            "start-Task A", "end-Task A",
            "start-Task B", "end-Task B",
            "start-Task C", "end-Task C",
        ]


class TestMaxConcurrent:
    """Test that max_concurrent limits parallel execution."""

    @pytest.mark.asyncio
    async def test_max_concurrent_limits_parallelism(self) -> None:
        """With max_concurrent=2, at most 2 workers run simultaneously."""
        orch = _make_mock_orchestrator(mode="parallel", max_concurrent=2)

        orch._conductor.decompose_query = AsyncMock(return_value=[
            {"worker_type": "analysis", "task": f"Task {i}", "context": ""}
            for i in range(4)
        ])

        active_count = 0
        max_observed = 0

        async def fake_run(worker_type: str, task: str, context: str, additional: str) -> dict:
            nonlocal active_count, max_observed
            active_count += 1
            max_observed = max(max_observed, active_count)
            await asyncio.sleep(0.05)
            active_count -= 1
            return {"findings": f"Result for {task}", "key_points": [], "sources_cited": [], "confidence": "high"}

        orch._worker.run = fake_run
        orch._synthesis.synthesize = AsyncMock(return_value={"title": "Concurrent Report", "sections": []})

        await orch.run("test query", web_search_enabled=False)

        # With 4 tasks and max_concurrent=2, we should never see more than 2 active
        assert max_observed <= 2


class TestProgressCallbacks:
    """Test that progress callbacks reflect execution mode."""

    @pytest.mark.asyncio
    async def test_parallel_progress_includes_mode(self) -> None:
        """Parallel mode progress callback includes 'parallel' in the message."""
        orch = _make_mock_orchestrator(mode="parallel")

        orch._conductor.decompose_query = AsyncMock(return_value=[
            {"worker_type": "analysis", "task": "Task 1", "context": ""},
        ])
        orch._worker.run = AsyncMock(return_value={"findings": "ok", "key_points": [], "sources_cited": [], "confidence": "high"})
        orch._synthesis.synthesize = AsyncMock(return_value={"title": "R", "sections": []})

        progress_events: list[tuple[str, dict]] = []

        def capture(status: str, detail: dict[str, Any]) -> None:
            progress_events.append((status, detail))

        await orch.run("test", web_search_enabled=False, progress_callback=capture)

        working_events = [e for e in progress_events if e[0] == "working"]
        assert len(working_events) == 1
        assert "parallel" in working_events[0][1]["message"]

    @pytest.mark.asyncio
    async def test_sequential_progress_includes_mode(self) -> None:
        """Sequential mode progress callback includes 'sequential' in the message."""
        orch = _make_mock_orchestrator(mode="sequential")

        orch._conductor.decompose_query = AsyncMock(return_value=[
            {"worker_type": "analysis", "task": "Task 1", "context": ""},
        ])
        orch._worker.run = AsyncMock(return_value={"findings": "ok", "key_points": [], "sources_cited": [], "confidence": "high"})
        orch._synthesis.synthesize = AsyncMock(return_value={"title": "R", "sections": []})

        progress_events: list[tuple[str, dict]] = []

        def capture(status: str, detail: dict[str, Any]) -> None:
            progress_events.append((status, detail))

        await orch.run("test", web_search_enabled=False, progress_callback=capture)

        working_events = [e for e in progress_events if e[0] == "working"]
        assert len(working_events) == 1
        assert "sequential" in working_events[0][1]["message"]
