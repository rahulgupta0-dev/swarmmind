# NOTE: Lemonade routing is by model name only. The `backend` field is NOT
# supported in the request body — real Lemonade ignores it. Backend selection
# is done by choosing the appropriate model variant (recipe) when pulling into
# Lemonade, not by injecting request-body fields.
"""Cross-backend benchmark for AMD Lemonade multi-model concurrent capabilities.

This module provides the core benchmarking logic that demonstrates the
multi-backend, multi-model concurrent capabilities of the AMD Lemonade stack.
It's designed to be used both as a standalone script and as a library called
by the SwarmMind CLI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

from pydantic import BaseModel, Field

from swarmmind.config import Config
from swarmmind.core.hardware import (
    SystemHardware,
    is_amd_optimized_target,
    recommend_backends,
)
from swarmmind.lemonade.client import LemonadeClient

logger = logging.getLogger(__name__)


# =============================================================================
# Models
# =============================================================================


class BenchmarkResult(BaseModel):
    """A single benchmark measurement for one model/backend combination.

    Attributes:
        role: The role this model serves (e.g. ``"conductor"``, ``"worker"``).
        model: The model identifier used in Lemonade API calls.
        backend: The backend identifier (e.g. ``"rocm"``, ``"vulkan"``,
            ``"cpu"``).
        load_seconds: Wall time for loading the model. ``None`` if the load
            endpoint isn't available or the model was already loaded.
        first_token_seconds: Time-to-first-token from the start of the chat
            completion request. ``None`` if streaming wasn't used.
        total_tokens: Total number of tokens generated.
        tokens_per_second: Throughput (tokens / wall_seconds).
        wall_seconds: End-to-end wall time.
        success: Whether the measurement completed without failure.
        error: Error message if *success* is ``False``.
    """

    role: str
    model: str
    backend: str
    load_seconds: float | None = None
    first_token_seconds: float | None = None
    total_tokens: int = 0
    tokens_per_second: float = 0.0
    wall_seconds: float = 0.0
    success: bool = True
    error: str | None = None


class BenchmarkReport(BaseModel):
    """Complete benchmark result with pre-rendered outputs.

    Attributes:
        hardware: The detected host hardware.
        results: All benchmark measurements.
        amd_optimized: Whether the host is an AMD-optimized target (ROCm
            GPU or Ryzen AI NPU present).
        generated_at: Timestamp of report generation.
    """

    hardware: SystemHardware
    results: list[BenchmarkResult]
    amd_optimized: bool
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    # ------------------------------------------------------------------
    # Output rendering
    # ------------------------------------------------------------------

    def to_markdown(self) -> str:
        """Render the report as a Markdown string."""
        lines: list[str] = []
        lines.append("# SwarmMind Benchmark Report")
        lines.append("")
        lines.append(
            f"**Generated:** {self.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        )
        lines.append("")

        if self.amd_optimized:
            lines.append(
                "> 🟢 **AMD-Optimized Target** — "
                "ROCm GPU and/or Ryzen AI NPU detected\n",
            )
        else:
            lines.append(
                "> ⚪ **Fallback Hardware** — "
                "No AMD ROCm/NPU optimizations detected\n",
            )

        # Hardware summary
        lines.append("## Hardware\n")
        lines.append(f"- **OS:** {self.hardware.os}")
        lines.append(
            f"- **CPU:** {self.hardware.cpu_name} "
            f"({self.hardware.cpu_cores_physical}C/"
            f"{self.hardware.cpu_cores_logical}T)",
        )
        lines.append(
            f"- **RAM:** {self.hardware.total_ram_gb:.1f} GB total, "
            f"{self.hardware.available_ram_gb:.1f} GB available",
        )
        if self.hardware.gpus:
            for gpu in self.hardware.gpus:
                lines.append(
                    f"- **GPU:** {gpu.name} ({gpu.vendor}, "
                    f"{gpu.vram_gb:.1f} GB, {gpu.backend})",
                )
        if self.hardware.npus:
            for npu in self.hardware.npus:
                lines.append(
                    f"- **NPU:** {npu.name} ({npu.vendor}, "
                    f"{npu.backend}, "
                    f"available={'yes' if npu.available else 'no'})",
                )
        lines.append("")

        # Results table
        lines.append("## Results\n")
        lines.append(
            "| Role | Model | Backend | Load (s) | TTFT (s) | "
            "Tokens | Tok/s | Wall (s) | Success |",
        )
        lines.append(
            "|------|-------|---------|----------|----------|"
            "--------|-------|----------|---------|",
        )
        for r in self.results:
            load_s = (
                f"{r.load_seconds:.2f}" if r.load_seconds is not None else "—"
            )
            ttft_s = (
                f"{r.first_token_seconds:.3f}"
                if r.first_token_seconds is not None
                else "—"
            )
            tokens = str(r.total_tokens)
            tok_s = (
                f"{r.tokens_per_second:.1f}"
                if r.tokens_per_second > 0
                else "—"
            )
            wall_s = f"{r.wall_seconds:.2f}"
            success = "✅" if r.success else "❌"
            lines.append(
                f"| {r.role} | {r.model} | {r.backend} | "
                f"{load_s} | {ttft_s} | {tokens} | {tok_s} | "
                f"{wall_s} | {success} |",
            )

        # Error rows
        errors = [r for r in self.results if not r.success and r.error]
        if errors:
            lines.append("")
            lines.append("## Errors\n")
            for e in errors:
                lines.append(
                    f"- **{e.role}/{e.model} ({e.backend}):** {e.error}",
                )

        return "\n".join(lines)

    def to_json(self) -> str:
        """Render the report as a JSON string."""
        raw = json.loads(self.model_dump_json())
        return json.dumps(raw, indent=2, default=str)

    def save(self, path: Path) -> None:
        """Write the benchmark report as Markdown with a JSON sidecar.

        The Markdown file is written to *path*; the JSON sidecar is written
        to the same path with the extension changed to ``.json``.
        """
        path.write_text(self.to_markdown(), encoding="utf-8")
        json_path = path.with_suffix(".json")
        json_path.write_text(self.to_json(), encoding="utf-8")


# =============================================================================
# Internal helpers
# =============================================================================


async def _measure_load_and_chat(
    client: LemonadeClient,
    role: str,
    model: str,
    backend: str,
    prompt: str,
) -> BenchmarkResult:
    """Time model load + first chat completion for a single model/backend.

    Phase B measurement: first loads the model (recording load time), then
    runs one streaming chat completion (recording TTFT and tokens/sec).
    """
    load_seconds: float | None = None
    first_token_seconds: float | None = None
    # Build the extra kwargs that the Lemonade API may accept
    extra_kwargs: dict[str, Any] = {}
    if backend and backend != "auto":
        extra_kwargs["backend"] = backend

    # ---- Load ----
    try:
        load_start = time.monotonic()
        await client.load_model(model)
        load_end = time.monotonic()
        load_seconds = load_end - load_start
    except Exception:
        load_seconds = None

    # ---- Chat (streaming) ----
    messages = [{"role": "user", "content": prompt}]
    total_tokens: int = 0
    chat_start: float = time.monotonic()
    wall_seconds: float = 0.0
    tokens_per_second: float | None = None
    first_token_seconds: float | None = None
    first_token_time: float | None = None
    try:
        stream: AsyncGenerator[dict[str, Any], None] = (
            await client.chat_completion(
                model=model,
                messages=messages,
                stream=True,
                **extra_kwargs,
            )
        )

        async for chunk in stream:
            if first_token_time is None:
                first_token_time = time.monotonic()
                first_token_seconds = first_token_time - chat_start
            for choice in chunk.get("choices", []):
                content = choice.get("delta", {}).get("content", "")
                if content:
                    total_tokens += len(content.split())

        chat_end = time.monotonic()
        wall_seconds = chat_end - chat_start

        if wall_seconds > 0 and total_tokens > 0:
            tokens_per_second = total_tokens / wall_seconds
    except Exception as exc:
        return BenchmarkResult(
            role=role,
            model=model,
            backend=backend,
            load_seconds=load_seconds,
            total_tokens=total_tokens,
            wall_seconds=wall_seconds,
            success=False,
            error=str(exc),
        )

    return BenchmarkResult(
        role=role,
        model=model,
        backend=backend,
        load_seconds=load_seconds,
        first_token_seconds=first_token_seconds,
        total_tokens=total_tokens,
        tokens_per_second=tokens_per_second,
        wall_seconds=wall_seconds,
        success=True,
    )


async def _run_parallel_swarm_test(
    client: LemonadeClient,
    model: str,
    backend: str,
    prompt: str,
    concurrency: int,
) -> BenchmarkResult:
    """Run *concurrency* concurrent chat completions — the AMD showcase.

    Phase C: the key test that demonstrates the benefit of AMD's
    split-hardware architecture (NPU + iGPU + dGPU) for multi-model
    concurrent inference.
    """

    messages = [{"role": "user", "content": prompt}]
    total_tokens = 0
    wall_start = time.monotonic()

    async def _one_stream() -> int:
        """Run one streaming completion, return token count."""
        count = 0
        try:
            stream: AsyncGenerator[dict[str, Any], None] = (
                await client.chat_completion(
                    model=model,
                    messages=messages,
                    stream=True,
                )
            )
            async for chunk in stream:
                for choice in chunk.get("choices", []):
                    content = choice.get("delta", {}).get("content", "")
                    if content:
                        count += len(content.split())
        except Exception as exc:
            logger.warning("Parallel stream failed: %s", exc)
        return count

    task_results = await asyncio.gather(
        *[_one_stream() for _ in range(concurrency)],
        return_exceptions=True,
    )

    wall_end = time.monotonic()
    wall_seconds = wall_end - wall_start

    for r in task_results:
        if isinstance(r, int):
            total_tokens += r

    return BenchmarkResult(
        role="parallel_swarm",
        model=model,
        backend=backend,
        total_tokens=total_tokens,
        tokens_per_second=(
            total_tokens / wall_seconds if wall_seconds > 0 else 0.0
        ),
        wall_seconds=wall_seconds,
    )


async def _run_direct_comparison(
    client: LemonadeClient,
    model: str,
    available_backends: list[str],
    prompt: str,
) -> list[BenchmarkResult]:
    """Run *model* across multiple backends for comparison (Phase D)."""
    results: list[BenchmarkResult] = []
    for backend in available_backends:
        logger.info("Direct comparison: %s on %s", model, backend)
        result = await _measure_load_and_chat(
            client,
            "comparison",
            model,
            backend,
            prompt,
        )
        results.append(result)
    return results


def _get_backend_for_role(
    role: str,
    config: Config,
    backend_plan: dict[str, Any],
) -> str:
    """Determine the backend to use for *role*.

    Precedence: user config override > hardware recommendation > ``"auto"``.
    """
    user_backend = config.backend_for(role)
    if user_backend:
        return user_backend
    rec = backend_plan.get(role)
    if rec and rec.recommended_backends:
        return rec.recommended_backends[0].value
    return "auto"


# =============================================================================
# Public API
# =============================================================================


async def run_benchmark(
    client: LemonadeClient,
    hardware: SystemHardware,
    config: Config,
    prompt: str,
    *,
    parallel_concurrency: int = 4,
    iterations: int = 3,
) -> BenchmarkReport:
    """Run a complete cross-backend benchmark.

    Four phases:

    1. **Phase A** — Inspect hardware & plan backends.
    2. **Phase B** — Cold-load test: time load + first token per role.
    3. **Phase C** — Parallel swarm test: *parallel_concurrency* concurrent
       chat completions (the AMD-stack showcase).
    4. **Phase D** — Direct backend comparison when multiple backends are
       available (excludes ``"auto"`` which is not a concrete backend).

    Args:
        client: A connected :class:`LemonadeClient`.
        hardware: A :class:`SystemHardware` describing the host.
        config: Application config with model names.
        prompt: The prompt for all chat completion measurements.
        parallel_concurrency: Number of concurrent streams in Phase C.
        iterations: Number of measurement iterations.

    Returns:
        A :class:`BenchmarkReport` with pre-rendered outputs.
    """
    results: list[BenchmarkResult] = []

    # ---- Phase A ----
    amd_optimized = is_amd_optimized_target(hardware)
    backend_plan = recommend_backends(hardware)
    logger.info(
        "Phase A: hardware=%s, amd_optimized=%s",
        hardware.cpu_name,
        amd_optimized,
    )

    # ---- Phase B: Cold-load per role ----
    role_model_map: dict[str, str] = {
        "conductor": config.models.conductor,
        "worker": config.models.worker,
        "embeddings": config.models.embeddings,
        "image": config.models.image,
        "tts": config.models.tts,
    }

    for role, model_name in role_model_map.items():
        if not model_name:
            continue
        backend = _get_backend_for_role(role, config, backend_plan)
        logger.info("Phase B: %s -> %s on %s", role, model_name, backend)
        result = await _measure_load_and_chat(
            client,
            role,
            model_name,
            backend,
            prompt,
        )
        results.append(result)

    # ---- Phase C: Parallel swarm (the AMD showcase) ----
    worker_model = config.models.worker
    worker_backend = _get_backend_for_role("worker", config, backend_plan)

    logger.info(
        "Phase C: parallel_swarm concurrency=%d model=%s backend=%s",
        parallel_concurrency,
        worker_model,
        worker_backend,
    )

    # Best-effort load before parallel test
    try:
        await client.load_model(worker_model)
    except Exception:
        pass

    parallel_result = await _run_parallel_swarm_test(
        client,
        worker_model,
        worker_backend,
        prompt,
        parallel_concurrency,
    )
    results.append(parallel_result)

    # ---- Phase D: Direct backend comparison ----
    # Exclude "auto" from comparison — it's not a concrete backend.
    available_backends: list[str] = []
    if "worker" in backend_plan:
        available_backends = [
            b.value
            for b in backend_plan["worker"].recommended_backends
            if b.value != "auto"
        ]

    if len(available_backends) > 1:
        logger.info(
            "Phase D: comparison for %s across %s",
            worker_model,
            available_backends,
        )
        comp_results = await _run_direct_comparison(
            client,
            worker_model,
            available_backends,
            prompt,
        )
        results.extend(comp_results)
    else:
        logger.info("Phase D skipped: single backend %s", available_backends)

    return BenchmarkReport(
        hardware=hardware,
        results=results,
        amd_optimized=amd_optimized,
    )


# =============================================================================
# CLI / standalone entry points
# =============================================================================


async def _benchmark_async(
    prompt: str,
    iterations: int,
    concurrency: int,
    out: Path | None,
) -> None:
    """Async entry point shared by the Click CLI and standalone runner."""
    config = Config()  # type: ignore[call-arg]
    client = LemonadeClient(config.get_lemonade_base_url())
    try:
        from swarmmind.core.hardware import detect_hardware

        hardware = await detect_hardware(client)
        report = await run_benchmark(
            client=client,
            hardware=hardware,
            config=config,
            prompt=prompt,
            parallel_concurrency=concurrency,
            iterations=iterations,
        )

        if out:
            report.save(out)
            print(f"Benchmark report saved to {out}")
            print(f"JSON sidecar:   {out.with_suffix('.json')}")

        print(report.to_markdown())
    finally:
        await client.close()


def main() -> None:
    """Standalone CLI entry point (argparse).

    Usage::

        python -m swarmmind.benchmark --prompt "..." --concurrency 4
    """
    import argparse

    ap = argparse.ArgumentParser(
        description=(
            "SwarmMind Benchmark — measure AMD cross-backend "
            "inference performance"
        ),
    )
    ap.add_argument(
        "--prompt",
        default="Explain the AMD Ryzen AI NPU in 2 sentences.",
        help="Prompt to use for all benchmark measurements.",
    )
    ap.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Number of measurement iterations.",
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Number of concurrent chat completions in the swarm test.",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("swarmmind_benchmark.md"),
        help="Output Markdown file path.",
    )
    args = ap.parse_args()
    asyncio.run(
        _benchmark_async(
            args.prompt,
            args.iterations,
            args.concurrency,
            args.out,
        ),
    )


if __name__ == "__main__":
    main()
