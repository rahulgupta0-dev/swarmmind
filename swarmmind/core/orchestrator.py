"""Main orchestrator — runs the full research flow end-to-end."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from swarmmind.config import Config
from swarmmind.core.conductor import Conductor
from swarmmind.core.hardware import (
    BackendRecommendation,
    SystemHardware,
    detect_hardware,
    has_amd_hardware,
    is_amd_optimized_target,
    recommend_backends,
)
from swarmmind.core.synthesis import Synthesis
from swarmmind.core.workers import Worker, search_web
from swarmmind.lemonade.client import LemonadeClient
from swarmmind.rag.chroma_store import ChromaStore

logger = logging.getLogger(__name__)

_WORKER_TIMEOUT = 60.0


class Orchestrator:
    """Coordinates the end-to-end multi-agent research pipeline.

    Flow::

    1. Pre-flight → health check, hardware detection
    2. Conductor → decompose query into sub-tasks
    3. Workers → execute sub-tasks in parallel or sequentially
       (configurable via ``config.execution.mode``)
    4. Synthesis → merge worker outputs into report

    Args:
    config: Application configuration.
    client: An initialised :class:`LemonadeClient`.
    chroma_store: Optional :class:`ChromaStore` instance for RAG workers.
    """

    def __init__(
        self,
        config: Config,
        client: LemonadeClient,
        chroma_store: Optional[ChromaStore] = None,
    ) -> None:
        self._config = config
        self._client = client
        self._chroma_store = chroma_store
        self._conductor = Conductor(client, config.models.conductor)
        self._synthesis = Synthesis(client, config.models.conductor)
        self._worker = Worker(client, config.models.worker)
        # Cached SystemHardware result, populated lazily by ``_detect_hardware``.
        self._hardware: Optional[SystemHardware] = None
        self._backend_recommendations: Optional[dict[str, BackendRecommendation]] = None

    # ------------------------------------------------------------------
    # Hardware detection (cached)
    # ------------------------------------------------------------------

    async def _detect_hardware(self) -> SystemHardware:
        """Detect the host hardware once and cache the result.

        Subsequent calls return the cached :class:`SystemHardware`. The
        detection is *always* best-effort — empty endpoints log a warning
        and return a populated-but-maybe-empty model.
        """
        if self._hardware is None:
            try:
                self._hardware = await detect_hardware(self._client)
            except Exception as exc:
                logger.warning("Hardware detection failed: %s — continuing with empty model", exc)
                self._hardware = SystemHardware()

            # Compute recommendations once and cache.
            try:
                self._backend_recommendations = recommend_backends(self._hardware)
            except Exception as exc:
                logger.warning("Backend recommendation failed: %s", exc)
                self._backend_recommendations = {}

            logger.info(
                "Hardware detected: %s (AMD=%s, optimized=%s)",
                self._hardware.os,
                has_amd_hardware(self._hardware),
                is_amd_optimized_target(self._hardware),
            )
        return self._hardware

    async def detect_hardware(self) -> SystemHardware:
        """Public façade for :meth:`_detect_hardware` (used by the UI)."""
        return await self._detect_hardware()

    def backend_recommendations(
        self,
    ) -> dict[str, BackendRecommendation]:
        """Return the cached per-role backend recommendations.

        If detection hasn't run yet, returns an empty dict.
        """
        return dict(self._backend_recommendations or {})

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def run(
        self,
        query: str,
        project_context: Optional[dict[str, Any]] = None,
        web_search_enabled: bool = True,
        vision_base64: Optional[str] = None,
        progress_callback: Optional[Callable[[str, dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        """Execute the full research pipeline.

        Args:
        query: The user's research question.
        project_context: Optional project metadata for context.
        web_search_enabled: Whether to allow web search workers.
        progress_callback: Optional callback invoked at each phase
        with ``(status, detail_dict)``.

        Returns:
        The final report dict from :class:`Synthesis.synthesize`.
        """
        # ------------------------------------------------------------------
        # 1. Pre-flight
        # ------------------------------------------------------------------
        self._emit(progress_callback, "preflight", {"message": "Checking Lemonade connection..."})
        health = await self._client.health_check()
        if health.get("status") == "forbidden" or health.get("code") == 403:
            raise PermissionError(
                f"Lemonade origin rejected (403 Forbidden) at {self._config.get_lemonade_base_url()}: "
                f"{health.get('detail', 'Origin not allowed. Set LEMONADE_ALLOWED_ORIGINS.')}"
            )
        if health.get("status") != "ok":
            raise ConnectionError(
                f"Cannot reach Lemonade at {self._config.get_lemonade_base_url()}: "
                f"{health.get('detail', 'unknown error')}"
            )

        # 1b. Hardware detection (best-effort, always after health check)
        try:
            hardware = await self._detect_hardware()
            self._emit(
                progress_callback,
                "hardware_detected",
                {
                    "os": hardware.os,
                    "cpu": hardware.cpu_name,
                    "gpus": [
                        {
                            "name": g.name,
                            "vendor": g.vendor,
                            "vram_gb": g.vram_gb,
                            "backend": g.backend,
                        }
                        for g in hardware.gpus
                    ],
                    "npus": [
                        {
                            "name": n.name,
                            "vendor": n.vendor,
                            "backend": n.backend,
                            "available": n.available,
                        }
                        for n in hardware.npus
                    ],
                    "total_ram_gb": hardware.total_ram_gb,
                    "amd_hardware": has_amd_hardware(hardware),
                    "amd_optimized": is_amd_optimized_target(hardware),
                    "backends": {
                        role: [b.value for b in rec.recommended_backends]
                        for role, rec in (self._backend_recommendations or {}).items()
                    },
                },
            )
        except Exception as exc:
            logger.warning("Hardware detection step failed: %s — continuing", exc)
            hardware = SystemHardware()

        # Try to load the conductor model (non-blocking — may already be loaded)
        try:
            await self._client.load_model(self._config.models.conductor)
        except Exception:
            logger.info(
                "Model %s may already be loaded or load failed — continuing",
                self._config.models.conductor,
            )

        # ------------------------------------------------------------------
        # 2. Conductor
        # ------------------------------------------------------------------
        self._emit(progress_callback, "conducting", {"message": "Decomposing query..."})
        tasks = await self._conductor.decompose_query(query, project_context or {})

        # Filter out web workers if disabled
        if not web_search_enabled:
            tasks = [t for t in tasks if t.get("worker_type") != "web"]

        if not tasks:
            raise RuntimeError("Conductor returned no tasks — cannot proceed.")

        self._emit(
            progress_callback,
            "decomposed",
            {"task_count": len(tasks), "tasks": tasks},
        )

        # ------------------------------------------------------------------
        # 3. Workers (parallel or sequential, configurable)
        # ------------------------------------------------------------------
        exec_mode = self._config.execution.mode
        max_concurrent = self._config.execution.max_concurrent
        self._emit(
            progress_callback,
            "working",
            {
                "message": f"Spawning {len(tasks)} workers ({exec_mode}"
                + (f", max {max_concurrent} concurrent)" if exec_mode == "parallel" else ")"),
            },
        )

        async def run_worker(task_def: dict[str, str]) -> dict[str, Any]:
            worker_type = task_def.get("worker_type", "analysis")
            task = task_def.get("task", query)
            context = task_def.get("context", "")
            additional = ""

            # Gather context for specific worker types
            if worker_type == "rag" and self._chroma_store and project_context:
                pid = project_context.get("id", "default")
                try:
                    results = self._chroma_store.search(pid, query, top_k=self._config.rag.top_k)
                    if results:
                        additional = "\n\n".join(
                            f"[Relevance {r['distance']:.3f}] {r['text']}" for r in results
                        )
                except Exception as exc:
                    logger.warning("RAG search failed: %s", exc)

            elif worker_type == "web" and web_search_enabled:
                try:
                    web_results = await asyncio.get_event_loop().run_in_executor(
                        None, search_web, task, 5,
                    )
                    if web_results:
                        additional = "\n\n".join(
                            f"[{r['title']}]({r['url']}): {r['snippet']}" for r in web_results
                        )
                except Exception as exc:
                    logger.warning("Web search failed: %s", exc)

            elif worker_type == "vision" and vision_base64:
                additional = vision_base64

            try:
                result = await asyncio.wait_for(
                    self._worker.run(worker_type, task, context, additional),
                    timeout=_WORKER_TIMEOUT,
                )
                result["worker_type"] = worker_type
                return result
            except asyncio.TimeoutError:
                return {
                    "worker_type": worker_type,
                    "findings": f"Worker timed out after {_WORKER_TIMEOUT}s.",
                    "key_points": [],
                    "sources_cited": [],
                    "confidence": "low",
                    "gaps": ["Timeout"],
                }

        if exec_mode == "sequential":
            # Sequential: one worker at a time (safe for low-RAM systems)
            worker_outputs: list[dict[str, Any]] = []
            for i, task in enumerate(tasks):
                self._emit(
                    progress_callback,
                    "worker_progress",
                    {"current": i + 1, "total": len(tasks), "worker_type": task.get("worker_type")},
                )
                worker_outputs.append(await run_worker(task))
        else:
            # Parallel: use asyncio.Semaphore to limit concurrency
            semaphore = asyncio.Semaphore(max_concurrent)

            async def run_worker_limited(task_def: dict[str, str]) -> dict[str, Any]:
                async with semaphore:
                    return await run_worker(task_def)

            worker_outputs = list(
                await asyncio.gather(
                    *[run_worker_limited(t) for t in tasks],
                    return_exceptions=False,
                )
            )

        self._emit(
            progress_callback,
            "completed_workers",
            {"worker_count": len(worker_outputs), "execution_mode": exec_mode},
        )

        # ------------------------------------------------------------------
        # 4. Synthesis
        # ------------------------------------------------------------------
        self._emit(progress_callback, "synthesising", {"message": "Synthesising results..."})
        report = await self._synthesis.synthesize(worker_outputs, query)

        self._emit(progress_callback, "done", {"message": "Research complete."})

        return report

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _emit(
        callback: Optional[Callable[[str, dict[str, Any]], None]],
        status: str,
        detail: dict[str, Any],
    ) -> None:
        """Call the progress callback if one was provided."""
        if callback:
            callback(status, detail)
