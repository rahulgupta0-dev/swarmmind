"""Main orchestrator — runs the full research flow end-to-end."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable, Optional

from swarmmind.config import Config
from swarmmind.core.conductor import Conductor
from swarmmind.core.evidence import EvidenceLedger, SourceRecord
from swarmmind.core.hardware import (
    BackendRecommendation,
    SystemHardware,
    detect_hardware,
    has_amd_hardware,
    is_amd_optimized_target,
    recommend_backends,
)
from swarmmind.core.synthesis import Synthesis
from swarmmind.core.workers import Worker, fetch_pages, search_web
from swarmmind.lemonade.client import LemonadeClient
from swarmmind.rag.chroma_store import ChromaStore

logger = logging.getLogger(__name__)

_WORKER_TIMEOUT = 60.0

_TOKEN_RE = re.compile(r"\w{4,}")


def _source_ids(raw_sources: Any) -> list[str]:
    """Collect source IDs from a worker's ``sources_cited`` value.

    Tolerates ``SourceRecord`` models, canonical dicts and legacy URL strings
    so old mocks keep working.
    """
    if not isinstance(raw_sources, list):
        raw_sources = [raw_sources] if raw_sources else []
    ids: list[str] = []
    for raw in raw_sources:
        if isinstance(raw, SourceRecord):
            ids.append(raw.id)
        elif isinstance(raw, dict) and raw.get("id"):
            ids.append(str(raw["id"]))
        elif isinstance(raw, str) and raw:
            ids.append(raw)
    return ids


def _match_source_ids(text: str, records: list[SourceRecord]) -> list[str]:
    """Match a claim to the sources whose title/snippet share its keywords.

    Falls back to all used IDs when there is no lexical overlap, and caps the
    match at three IDs to keep claims compact.
    """
    if not records:
        return []
    tokens = {t.lower() for t in _TOKEN_RE.findall(str(text))}
    if not tokens:
        return [r.id for r in records[:3]]

    scored: list[tuple[int, str]] = []
    for record in records:
        haystack = {t.lower() for t in _TOKEN_RE.findall(
            f"{record.title} {record.snippet}"
        )}
        scored.append((len(tokens & haystack), record.id))
    best = max(score for score, _ in scored)
    if best <= 0:
        return [r.id for r in records[:3]]
    return [rid for score, rid in scored if score == best][:3]


def _build_claims(
    key_points: Any,
    records: list[SourceRecord],
    supported_ids: set[str],
) -> list[dict[str, Any]]:
    """Build compact Claim dicts from a worker's key points."""
    if not isinstance(key_points, list):
        key_points = [key_points] if key_points else []
    claims: list[dict[str, Any]] = []
    for point in key_points[:10]:
        ids = _match_source_ids(str(point), records)
        status = "supported" if any(sid in supported_ids for sid in ids) else "unverified"
        claims.append({"text": str(point), "source_ids": ids, "status": status})
    return claims


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
        conversation_history: Optional[list[dict[str, Any]]] = None,
        progress_callback: Optional[Callable[[str, dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        """Execute the full research pipeline.

        Args:
        query: The user's research question.
        project_context: Optional project metadata for context.
        web_search_enabled: Whether to allow web search workers.
        vision_base64: Optional base64-encoded image for the vision worker.
        conversation_history: Optional list of prior turn dicts with
        ``query`` and ``report`` keys, fed to conductor and synthesis
        so follow-ups can reference earlier findings.
        progress_callback: Optional callback invoked at each phase
        with ``(status, detail_dict)``.

        Returns:
        The final report dict from :class:`Synthesis.synthesize`.
        """
        # One ledger per run: IDs are stable across workers and the report.
        ledger = EvidenceLedger()

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
        tasks = await self._conductor.decompose_query(
            query,
            project_context or {},
            conversation_history=conversation_history,
        )

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
            used_records: list[SourceRecord] = []
            supported_ids: set[str] = set()

            # Gather context for specific worker types, registering every
            # retrieved item in the run's ledger so it gets a stable [S#] ID.
            if worker_type == "rag" and self._chroma_store and project_context:
                pid = project_context.get("id", "default")
                try:
                    results = self._chroma_store.search(pid, query, top_k=self._config.rag.top_k)
                    if results:
                        lines: list[str] = []
                        for r in results:
                            meta = r.get("metadata") or {}
                            doc_id = str(meta.get("source_id") or meta.get("source") or "")
                            raw_chunk = meta.get("chunk_index", meta.get("chunk", -1))
                            try:
                                chunk = int(raw_chunk)
                            except (TypeError, ValueError):
                                chunk = -1
                            snippet = str(r.get("text") or "")[:1200]
                            record = ledger.register({
                                "kind": "rag",
                                "title": doc_id or "Document chunk",
                                "snippet": snippet,
                                "doc_id": doc_id,
                                "chunk": chunk,
                            })
                            used_records.append(record)
                            supported_ids.add(record.id)
                            distance = r.get("distance", 0.0)
                            try:
                                relevance = f"{float(distance):.3f}"
                            except (TypeError, ValueError):
                                relevance = str(distance)
                            lines.append(f"[{record.id}] [Relevance {relevance}] {snippet}")
                        additional = "\n\n".join(lines)
                except Exception as exc:
                    logger.warning("RAG search failed: %s", exc)

            elif worker_type == "web" and web_search_enabled:
                try:
                    web_results = await asyncio.get_event_loop().run_in_executor(
                        None, search_web, task, 5,
                    )
                    if web_results:
                        # Web settings default safely for older config mocks.
                        web_cfg = getattr(self._config, "web", None)
                        fetch_enabled = bool(getattr(web_cfg, "fetch_pages", False))
                        max_pages = int(getattr(web_cfg, "max_pages", 5))
                        timeout_seconds = float(getattr(web_cfg, "timeout_seconds", 5.0))

                        if fetch_enabled:
                            pages = await fetch_pages(
                                web_results,
                                max_pages=max_pages,
                                timeout_seconds=timeout_seconds,
                            )
                        else:
                            # Opt-out: keep the DDG snippet, marked unverified.
                            pages = [
                                {
                                    "kind": "web",
                                    "title": str(r.get("title") or ""),
                                    "url": str(r.get("url") or ""),
                                    "snippet": str(r.get("snippet") or ""),
                                    "fetch_ok": False,
                                }
                                for r in web_results[:max_pages]
                            ]

                        lines = []
                        for page in pages:
                            record = ledger.register({
                                "kind": "web",
                                "title": page.get("title") or "Web result",
                                "url": page.get("url") or "",
                                "snippet": page.get("snippet") or "",
                            })
                            used_records.append(record)
                            if page.get("fetch_ok"):
                                supported_ids.add(record.id)
                            lines.append(
                                f"[{record.id}] {record.title}: {record.snippet}"
                            )
                        additional = "\n\n".join(lines)
                except Exception as exc:
                    logger.warning("Web search failed: %s", exc)

            elif worker_type == "vision" and vision_base64:
                additional = vision_base64

            try:
                result = await asyncio.wait_for(
                    self._worker.run(worker_type, task, context, additional),
                    timeout=_WORKER_TIMEOUT,
                )
            except asyncio.TimeoutError:
                result = {
                    "findings": f"Worker timed out after {_WORKER_TIMEOUT}s.",
                    "key_points": [],
                    "sources_cited": [],
                    "confidence": "low",
                    "gaps": ["Timeout"],
                }
            except Exception as exc:
                logger.exception("Worker '%s' failed: %s", worker_type, exc)
                result = {
                    "findings": f"Worker {worker_type} encountered an error: {exc}",
                    "key_points": [],
                    "sources_cited": [],
                    "confidence": "low",
                    "gaps": [str(exc)],
                }

            # Workers with no external evidence still get an explicit,
            # unverified model-knowledge source (stable URL keeps the ID
            # unchanged when the ledger re-registers it during validation).
            if not used_records:
                used_records.append(ledger.register({
                    "kind": "model",
                    "title": f"Model knowledge ({worker_type})",
                    "url": f"model://{worker_type}",
                    "snippet": "",
                }))

            result["worker_type"] = worker_type
            result["sources_cited"] = [r.model_dump() for r in used_records]
            result["claims"] = _build_claims(
                result.get("key_points"), used_records, supported_ids,
            )
            return result

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

        # Canonical ID order across all workers, for the report registry.
        used_ids: list[str] = []
        for output in worker_outputs:
            for sid in _source_ids(output.get("sources_cited")):
                if sid not in used_ids:
                    used_ids.append(sid)
        source_dicts = [
            record.model_dump()
            for record in (ledger.get(sid) for sid in used_ids)
            if record is not None
        ]

        self._emit(
            progress_callback,
            "completed_workers",
            {
                "worker_count": len(worker_outputs),
                "execution_mode": exec_mode,
                "sources": source_dicts,
            },
        )

        # ------------------------------------------------------------------
        # 4. Synthesis
        # ------------------------------------------------------------------
        self._emit(progress_callback, "synthesising", {"message": "Synthesising results..."})
        report = await self._synthesis.synthesize(
            worker_outputs, query, conversation_history=conversation_history,
        )

        # Attach the run's evidence, validate citation IDs and mark the
        # deliberately single-round pipeline.
        report["sources"] = source_dicts
        report = ledger.validate_citations(report)
        report["stop_reason"] = "single_round"
        report["rounds_used"] = 1

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
