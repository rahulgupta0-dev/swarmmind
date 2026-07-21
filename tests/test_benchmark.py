"""Tests for the cross-backend benchmark module.

These tests run *offline* — a dedicated mock client (following the
``MockClient`` pattern from ``test_hardware.py``) replaces the real
:class:`LemonadeClient` so we never touch a real server.  Coverage:

* :class:`BenchmarkReport` model: markdown rendering, JSON round-trip,
  file save, AMD-optimised / fallback banners.
* :func:`run_benchmark` against AMD-optimised and fallback hardware,
  error tolerance (load / chat failures), parallel concurrency
  verification, and Phase D skip/include logic.
* CLI command registration: verifying that ``main`` Click group gains
  a ``benchmark`` subcommand.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest

from swarmmind.config import Config
from swarmmind.core.hardware import GPUInfo, NPUInfo, SystemHardware


# ---------------------------------------------------------------------------
# Mock client
# ---------------------------------------------------------------------------


class MockBenchmarkClient:
    """Stand-in for :class:`LemonadeClient` used by benchmark tests.

    Records call counts and simulates streaming responses with
    configurable delays so that timing measurements are deterministic.

    Methods
    -------
    ``load_model`` — returns immediately (or after *load_delay*).
    ``chat_completion`` — returns an async generator when ``stream=True``
    that yields one word at a time (with *stream_delay* seconds between
    words).
    """

    def __init__(
        self,
        load_delay: float = 0.0,
        stream_delay: float = 0.001,
        fail_load: bool = False,
        fail_chat: bool = False,
    ) -> None:
        self.load_calls = 0
        self.chat_calls = 0
        self.load_delay = load_delay
        self.stream_delay = stream_delay
        self.fail_load = fail_load
        self.fail_chat = fail_chat
        # Track active concurrent streams for parallel concurrency tests
        self._active_chat_count = 0
        self._max_concurrent = 0

    async def load_model(self, model: str) -> dict[str, Any]:
        """Simulate model load (instant or delayed)."""
        self.load_calls += 1
        await asyncio.sleep(self.load_delay)
        if self.fail_load:
            raise RuntimeError("Load failed (mock)")
        return {"status": "ok", "model": model}

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any] | AsyncGenerator[dict[str, Any], None]:
        """Simulate chat completion with optional streaming."""
        self.chat_calls += 1
        if stream:
            return self._mock_stream(model)
        return {
            "id": "mock-cmpl",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Mock response.",
                    },
                    "finish_reason": "stop",
                },
            ],
        }

    async def _mock_stream(
        self,
        model: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield word-by-word SSE chunks like the real /v1/chat/completions."""
        self._active_chat_count += 1
        self._max_concurrent = max(
            self._max_concurrent,
            self._active_chat_count,
        )
        try:
            words = [
                "AMD",
                " Ryzen",
                " AI",
                " NPU",
                " is",
                " a",
                " dedicated",
                " AI",
                " accelerator",
                ".",
            ]
            for word in words:
                await asyncio.sleep(self.stream_delay)
                if self.fail_chat:
                    raise RuntimeError("Chat failed (mock)")
                yield {
                    "id": "mock-cmpl",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": word},
                            "finish_reason": None,
                        }
                    ],
                }
            yield {
                "id": "mock-cmpl",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            }
        finally:
            self._active_chat_count -= 1


# ---------------------------------------------------------------------------
# Hardware profiles
# ---------------------------------------------------------------------------

AMD_OPTIMIZED_HW = SystemHardware(
    os="Linux",
    cpu_name="AMD Ryzen AI MAX+ 395",
    cpu_cores_physical=16,
    cpu_cores_logical=32,
    cpu_arch="x86_64",
    gpus=[
        GPUInfo(
            name="AMD Radeon 8060S",
            vendor="AMD",
            vram_gb=16.0,
            backend="rocm",
        ),
    ],
    npus=[
        NPUInfo(
            name="AMD Ryzen AI NPU",
            vendor="AMD",
            backend="ryzenai",
            available=True,
        ),
    ],
    total_ram_gb=64.0,
    available_ram_gb=48.0,
)

FALLBACK_HW = SystemHardware(
    os="Linux",
    cpu_name="Intel Core i7-12700",
    cpu_cores_physical=8,
    cpu_cores_logical=16,
    cpu_arch="x86_64",
    gpus=[],
    npus=[],
    total_ram_gb=16.0,
    available_ram_gb=8.0,
)


# =========================================================================
# Tests: BenchmarkReport model
# =========================================================================


class TestBenchmarkReport:
    """``BenchmarkReport`` model structure and output rendering."""

    def test_report_amd_optimized_banner(self) -> None:
        """AMD-optimized hardware banner appears in markdown."""
        from swarmmind.benchmark import BenchmarkReport

        report = BenchmarkReport(
            hardware=AMD_OPTIMIZED_HW,
            results=[],
            amd_optimized=True,
        )
        md = report.to_markdown()
        assert "AMD-Optimized Target" in md
        assert "ROCm GPU and/or Ryzen AI NPU detected" in md

    def test_report_fallback_banner(self) -> None:
        """Fallback hardware banner appears in markdown."""
        from swarmmind.benchmark import BenchmarkReport

        report = BenchmarkReport(
            hardware=FALLBACK_HW,
            results=[],
            amd_optimized=False,
        )
        md = report.to_markdown()
        assert "Fallback Hardware" in md
        assert "No AMD ROCm/NPU optimizations detected" in md

    def test_report_to_json_roundtrip(self) -> None:
        """JSON output round-trips through Pydantic."""
        from swarmmind.benchmark import BenchmarkReport, BenchmarkResult

        result = BenchmarkResult(
            role="conductor",
            model="test-model",
            backend="rocm",
            load_seconds=0.5,
            first_token_seconds=0.12,
            total_tokens=50,
            tokens_per_second=100.0,
            wall_seconds=0.5,
        )
        report = BenchmarkReport(
            hardware=AMD_OPTIMIZED_HW,
            results=[result],
            amd_optimized=True,
        )
        data = json.loads(report.to_json())
        assert data["amd_optimized"] is True
        assert data["hardware"]["cpu_name"] == "AMD Ryzen AI MAX+ 395"
        assert len(data["results"]) == 1
        assert data["results"][0]["role"] == "conductor"
        assert data["results"][0]["load_seconds"] == 0.5

    def test_report_save_creates_files(self, tmp_path: Path) -> None:
        """``.save()`` writes both Markdown and JSON sidecar."""
        from swarmmind.benchmark import BenchmarkReport, BenchmarkResult

        report = BenchmarkReport(
            hardware=FALLBACK_HW,
            results=[
                BenchmarkResult(
                    role="worker",
                    model="m",
                    backend="cpu",
                    total_tokens=10,
                    wall_seconds=1.0,
                    tokens_per_second=10.0,
                ),
            ],
            amd_optimized=False,
        )

        md_path = tmp_path / "benchmark.md"
        report.save(md_path)

        assert md_path.exists()
        assert md_path.read_text(encoding="utf-8").startswith(
            "# SwarmMind Benchmark Report",
        )
        assert (tmp_path / "benchmark.json").exists()

    def test_report_markdown_contains_table(self) -> None:
        """Markdown includes a results table with error rows."""
        from swarmmind.benchmark import BenchmarkReport, BenchmarkResult

        report = BenchmarkReport(
            hardware=FALLBACK_HW,
            results=[
                BenchmarkResult(
                    role="conductor",
                    model="qwen",
                    backend="rocm",
                    load_seconds=2.0,
                    first_token_seconds=0.5,
                    total_tokens=100,
                    tokens_per_second=200.0,
                    wall_seconds=0.5,
                    success=True,
                ),
                BenchmarkResult(
                    role="worker",
                    model="qwen-small",
                    backend="vulkan",
                    total_tokens=0,
                    wall_seconds=0.0,
                    success=False,
                    error="Model not found",
                ),
            ],
            amd_optimized=False,
        )
        md = report.to_markdown()
        assert "| Role | Model | Backend | Load (s) | TTFT (s)" in md
        assert "conductor" in md
        assert "❌" in md
        assert "Model not found" in md

    def test_empty_results_table(self) -> None:
        """Even with no results the table header is present."""
        from swarmmind.benchmark import BenchmarkReport

        report = BenchmarkReport(
            hardware=FALLBACK_HW,
            results=[],
            amd_optimized=False,
        )
        md = report.to_markdown()
        assert "| Role | Model | Backend |" in md

    def test_hardware_summary_includes_gpu_npu(self) -> None:
        """GPU and NPU details appear in the markdown hardware summary."""
        from swarmmind.benchmark import BenchmarkReport

        report = BenchmarkReport(
            hardware=AMD_OPTIMIZED_HW,
            results=[],
            amd_optimized=True,
        )
        md = report.to_markdown()
        assert "Radeon" in md
        assert "Ryzen AI NPU" in md
        assert "64.0 GB" in md


# =========================================================================
# Tests: run_benchmark
# =========================================================================


class TestRunBenchmark:
    """Integration-level tests of ``run_benchmark``."""

    @pytest.mark.asyncio
    async def test_basic_amd_optimized(self) -> None:
        """AMD-optimized hardware produces an optimised report."""
        from swarmmind.benchmark import run_benchmark

        client = MockBenchmarkClient()
        config = Config()  # type: ignore[call-arg]

        report = await run_benchmark(
            client=client,  # type: ignore[arg-type]
            hardware=AMD_OPTIMIZED_HW,
            config=config,
            prompt="test prompt",
        )

        assert report.amd_optimized is True
        # Phase B: 5 roles + Phase C: 1 + Phase D: 2 backends = 8
        assert len(report.results) == 8
        for r in report.results:
            assert isinstance(r.role, str)
            assert isinstance(r.model, str)
            if r.success:
                assert r.wall_seconds >= 0

    @pytest.mark.asyncio
    async def test_fallback_hardware(self) -> None:
        """Non-AMD hardware: not optimised; all backends fallback."""
        from swarmmind.benchmark import run_benchmark

        client = MockBenchmarkClient()
        config = Config()  # type: ignore[call-arg]

        report = await run_benchmark(
            client=client,  # type: ignore[arg-type]
            hardware=FALLBACK_HW,
            config=config,
            prompt="test",
        )

        assert report.amd_optimized is False

    @pytest.mark.asyncio
    async def test_error_tolerance_load_failure(self) -> None:
        """Load failures don't abort the benchmark."""
        from swarmmind.benchmark import run_benchmark

        client = MockBenchmarkClient(fail_load=True)
        config = Config()  # type: ignore[call-arg]

        report = await run_benchmark(
            client=client,  # type: ignore[arg-type]
            hardware=AMD_OPTIMIZED_HW,
            config=config,
            prompt="test",
        )

        assert len(report.results) > 0
        # Load failures set load_seconds=None but chats still succeed
        load_failed = [
            r
            for r in report.results
            if r.role in {"conductor", "worker", "embeddings", "image", "tts"}
        ]
        assert all(r.load_seconds is None for r in load_failed)

    @pytest.mark.asyncio
    async def test_error_tolerance_chat_failure(self) -> None:
        """Chat failures produce error results but don't abort."""
        from swarmmind.benchmark import run_benchmark

        client = MockBenchmarkClient(fail_chat=True)
        config = Config()  # type: ignore[call-arg]

        report = await run_benchmark(
            client=client,  # type: ignore[arg-type]
            hardware=FALLBACK_HW,
            config=config,
            prompt="test",
        )

        failures = [r for r in report.results if not r.success]
        assert len(failures) >= 1
        for f in failures:
            assert f.error is not None

    @pytest.mark.asyncio
    async def test_parallel_concurrency_actually_parallel(self) -> None:
        """The parallel swarm test runs streams concurrently.

        Each stream has 10 words at 0.05 s per word.  Sequential would be
        10 * 4 * 0.05 = 2 s.  Parallel should be ~0.5 s.
        """
        from swarmmind.benchmark import run_benchmark

        client = MockBenchmarkClient(stream_delay=0.05)
        config = Config()  # type: ignore[call-arg]

        report = await run_benchmark(
            client=client,  # type: ignore[arg-type]
            hardware=FALLBACK_HW,
            config=config,
            prompt="test",
            parallel_concurrency=4,
        )

        parallel_results = [
            r for r in report.results if r.role == "parallel_swarm"
        ]
        assert len(parallel_results) == 1
        pr = parallel_results[0]
        # Wall time should be well under sequential 2 s
        assert pr.wall_seconds < 1.5, (
            f"Expected parallel wall time < 1.5 s, got {pr.wall_seconds:.2f}"
        )

        # At least 2 streams ran concurrently at some point
        assert client._max_concurrent >= 2, (
            f"Expected max_concurrent >= 2, got {client._max_concurrent}"
        )

    @pytest.mark.asyncio
    async def test_direct_comparison_skipped_single_backend(self) -> None:
        """Phase D is skipped when only one concrete backend is available.

        ``FALLBACK_HW`` (no GPU/NPU) yields only ``[cpu, auto]`` for the
        worker role.  After filtering out ``auto``, only ``[cpu]`` remains
        so the comparison is skipped.
        """
        from swarmmind.benchmark import run_benchmark

        client = MockBenchmarkClient()
        config = Config()  # type: ignore[call-arg]

        report = await run_benchmark(
            client=client,  # type: ignore[arg-type]
            hardware=FALLBACK_HW,
            config=config,
            prompt="test",
        )

        comparison_results = [
            r for r in report.results if r.role == "comparison"
        ]
        assert len(comparison_results) == 0

    @pytest.mark.asyncio
    async def test_direct_comparison_included_multi_backend(self) -> None:
        """Phase D runs when multiple concrete backends are available.

        ``AMD_OPTIMIZED_HW`` (ROCm + Vulkan-capable) yields
        ``[rocm, vulkan, auto]``` for the worker role.  After filtering
        out ``auto``, two concrete backends remain -> Phase D runs.
        """
        from swarmmind.benchmark import run_benchmark

        client = MockBenchmarkClient()
        config = Config()  # type: ignore[call-arg]

        report = await run_benchmark(
            client=client,  # type: ignore[arg-type]
            hardware=AMD_OPTIMIZED_HW,
            config=config,
            prompt="test",
        )

        comparison_results = [
            r for r in report.results if r.role == "comparison"
        ]
        assert len(comparison_results) > 0
        # Each comparison result should have a concrete backend
        for cr in comparison_results:
            assert cr.backend != "auto"

    @pytest.mark.asyncio
    async def test_report_has_hardware_summary(self) -> None:
        """The report's hardware matches what was passed in."""
        from swarmmind.benchmark import run_benchmark

        client = MockBenchmarkClient()
        config = Config()  # type: ignore[call-arg]

        report = await run_benchmark(
            client=client,  # type: ignore[arg-type]
            hardware=AMD_OPTIMIZED_HW,
            config=config,
            prompt="test",
        )

        assert report.hardware.cpu_name == "AMD Ryzen AI MAX+ 395"
        assert len(report.hardware.gpus) == 1
        assert len(report.hardware.npus) == 1

    @pytest.mark.asyncio
    async def test_cold_load_records_load_time(self) -> None:
        """Cold-load tests record non-None load_seconds on success."""
        from swarmmind.benchmark import run_benchmark

        client = MockBenchmarkClient(load_delay=0.01)
        config = Config()  # type: ignore[call-arg]

        report = await run_benchmark(
            client=client,  # type: ignore[arg-type]
            hardware=FALLBACK_HW,
            config=config,
            prompt="test",
        )

        role_results = [
            r
            for r in report.results
            if r.role in {"conductor", "worker", "embeddings", "image", "tts"}
        ]
        for r in role_results:
            assert r.load_seconds is not None
            assert r.load_seconds >= 0.01  # at least the delay

    @pytest.mark.asyncio
    async def test_cold_load_records_first_token_time(self) -> None:
        """Cold-load tests record ttft during the chat phase."""
        from swarmmind.benchmark import run_benchmark

        client = MockBenchmarkClient()
        config = Config()  # type: ignore[call-arg]

        report = await run_benchmark(
            client=client,  # type: ignore[arg-type]
            hardware=FALLBACK_HW,
            config=config,
            prompt="test",
        )

        role_results = [
            r
            for r in report.results
            if r.role in {"conductor", "worker", "embeddings", "image", "tts"}
        ]
        for r in role_results:
            assert r.first_token_seconds is not None
            assert r.first_token_seconds >= 0


# =========================================================================
# Tests: CLI wiring
# =========================================================================


class TestCLIIntegration:
    """The ``swarmmind benchmark`` Click command is registered correctly."""

    def test_benchmark_command_registered(self) -> None:
        """The ``main`` Click group has a ``benchmark`` subcommand."""
        from swarmmind.cli.main import main

        commands = list(main.commands.keys())
        assert "benchmark" in commands

    def test_benchmark_command_params(self) -> None:
        """The benchmark command has expected Click options."""
        from swarmmind.cli.main import main

        cmd = main.commands.get("benchmark")
        assert cmd is not None, "benchmark command not found"

        param_names = {p.name for p in cmd.params}
        assert "prompt" in param_names
        assert "iterations" in param_names
        assert "concurrency" in param_names
        assert "out" in param_names

    def test_cli_module_imports_cleanly(self) -> None:
        """Importing ``swarmmind.cli.main`` does not raise."""
        import swarmmind.cli.main  # noqa: F811
