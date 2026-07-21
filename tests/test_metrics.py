"""Tests for the metrics snapshot helper and UI component imports.

Tests cover:

* :func:`fetch_snapshot` with mocked client (happy path, failures,
  partial failures, non-dict responses, empty responses).
* :class:`MetricsSnapshot` model defaults and JSON round-trip.
* Module-level import smoke test for
  :mod:`swarmmind.ui.components.metrics`.

Streamlit component rendering tests are intentionally skipped since
they require a running Streamlit server.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Mock clients
# ---------------------------------------------------------------------------


class MockClient:
    """Minimal mock :class:`LemonadeClient` that returns canned dicts."""

    def __init__(
        self,
        stats: dict[str, Any] | None = None,
        sys_stats: dict[str, Any] | None = None,
        sys_info: dict[str, Any] | None = None,
    ) -> None:
        self._stats = stats or {}
        self._sys_stats = sys_stats or {}
        self._sys_info = sys_info or {}

    async def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)

    async def system_stats(self) -> dict[str, Any]:
        return dict(self._sys_stats)

    async def system_info(self) -> dict[str, Any]:
        return dict(self._sys_info)


class FailingClient:
    """Mock whose endpoints all raise — tests defensive behaviour."""

    async def get_stats(self) -> dict[str, Any]:
        msg = "Connection refused"
        raise RuntimeError(msg)

    async def system_stats(self) -> dict[str, Any]:
        msg = "Connection refused"
        raise RuntimeError(msg)

    async def system_info(self) -> dict[str, Any]:
        msg = "Connection refused"
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Tests: MetricsSnapshot model
# ---------------------------------------------------------------------------


class TestMetricsSnapshot:
    """Pydantic model — defaults, fields, and serialisation."""

    def test_defaults(self) -> None:
        """Fresh snapshot has empty dicts and a ``fetched_at``."""
        from swarmmind.core.metrics import MetricsSnapshot

        snap = MetricsSnapshot()
        assert snap.stats == {}
        assert snap.system_stats == {}
        assert snap.system_info == {}
        assert isinstance(snap.fetched_at, datetime)

    def test_json_round_trip(self) -> None:
        """``model_dump`` → ``model_validate`` preserves all fields."""
        from swarmmind.core.metrics import MetricsSnapshot

        now = datetime(2026, 7, 17, 14, 0, 0)
        snap = MetricsSnapshot(
            stats={"models": {"test": {"tokens_per_second": 42.0}}},
            system_stats={"vram_used_gb": 8.0, "vram_total_gb": 24.0},
            system_info={"cpu": {"name": "AMD EPYC"}},
            fetched_at=now,
        )
        data = snap.model_dump()
        restored = MetricsSnapshot.model_validate(data)
        assert restored.stats == snap.stats
        assert restored.system_stats == snap.system_stats
        assert restored.system_info == snap.system_info
        assert restored.fetched_at == snap.fetched_at

    def test_model_dump_json(self) -> None:
        """JSON serialisation round-trip works end to end."""
        from swarmmind.core.metrics import MetricsSnapshot

        snap = MetricsSnapshot(
            stats={"foo": "bar"},
            system_stats={"npu_percent": 15.0},
        )
        json_str = snap.model_dump_json()
        reloaded = MetricsSnapshot.model_validate_json(json_str)
        assert reloaded.stats == snap.stats
        assert reloaded.system_stats == snap.system_stats
        assert reloaded.system_info == snap.system_info


# ---------------------------------------------------------------------------
# Tests: fetch_snapshot
# ---------------------------------------------------------------------------


class TestFetchSnapshot:
    """``fetch_snapshot`` defensive behaviour with various mock clients."""

    @pytest.mark.asyncio
    async def test_populates_all_fields(self) -> None:
        """Happy path — all three endpoints return data."""
        from swarmmind.core.metrics import fetch_snapshot

        client = MockClient(
            stats={"models": {"qwen": {"tokens_per_second": 45.2}}},
            sys_stats={"vram_used_gb": 8.0, "vram_total_gb": 24.0},
            sys_info={"cpu": {"name": "AMD EPYC"}, "memory": {"total_gb": 64.0}},
        )
        snap = await fetch_snapshot(client)  # type: ignore[arg-type]

        assert snap.stats == {"models": {"qwen": {"tokens_per_second": 45.2}}}
        assert snap.system_stats == {"vram_used_gb": 8.0, "vram_total_gb": 24.0}
        assert snap.system_info == {"cpu": {"name": "AMD EPYC"}, "memory": {"total_gb": 64.0}}
        assert isinstance(snap.fetched_at, datetime)

    @pytest.mark.asyncio
    async def test_all_endpoints_fail(self) -> None:
        """All endpoints raise — snapshot fields are empty dicts."""
        from swarmmind.core.metrics import fetch_snapshot

        client = FailingClient()
        snap = await fetch_snapshot(client)  # type: ignore[arg-type]

        assert snap.stats == {}
        assert snap.system_stats == {}
        assert snap.system_info == {}
        assert isinstance(snap.fetched_at, datetime)

    @pytest.mark.asyncio
    async def test_partial_failure(self) -> None:
        """Only one endpoint fails — the other two are populated."""
        from swarmmind.core.metrics import fetch_snapshot

        class PartialFailClient:
            async def get_stats(self) -> dict[str, Any]:
                return {"models": {"test": {"tps": 30.0}}}

            async def system_stats(self) -> dict[str, Any]:
                msg = "timeout"
                raise RuntimeError(msg)

            async def system_info(self) -> dict[str, Any]:
                return {"cpu": {"name": "test"}}

        snap = await fetch_snapshot(PartialFailClient())  # type: ignore[arg-type]

        assert snap.stats == {"models": {"test": {"tps": 30.0}}}
        assert snap.system_stats == {}
        assert snap.system_info == {"cpu": {"name": "test"}}

    @pytest.mark.asyncio
    async def test_non_dict_responses(self) -> None:
        """Non-dict responses are treated as empty dicts."""
        from swarmmind.core.metrics import fetch_snapshot

        class NonDictClient:
            async def get_stats(self) -> list[dict[str, Any]]:
                return [{"model": "test", "tps": 10}]  # list, not dict

            async def system_stats(self) -> str:
                return "error"

            async def system_info(self) -> None:
                return None

        snap = await fetch_snapshot(NonDictClient())  # type: ignore[arg-type]

        assert snap.stats == {}
        assert snap.system_stats == {}
        assert snap.system_info == {}

    @pytest.mark.asyncio
    async def test_empty_responses(self) -> None:
        """Empty dicts from all endpoints — valid snapshot with empty fields."""
        from swarmmind.core.metrics import fetch_snapshot

        client = MockClient(stats={}, sys_stats={}, sys_info={})
        snap = await fetch_snapshot(client)  # type: ignore[arg-type]

        assert snap.stats == {}
        assert snap.system_stats == {}
        assert snap.system_info == {}


# ---------------------------------------------------------------------------
# Smoke test: UI components import
# ---------------------------------------------------------------------------


class TestMetricsComponentsImport:
    """Verify that the metrics UI module can be imported without crashes.

    Actual Streamlit rendering is not exercised here (requires a running
    Streamlit server).  This merely confirms the module syntax is valid
    and the public API symbols exist.
    """

    def test_module_import(self) -> None:
        """``swarmmind.ui.components.metrics`` imports without error."""
        from swarmmind.ui.components import metrics  # noqa: PLC0415, F811

        assert hasattr(metrics, "render_hardware_card")
        assert hasattr(metrics, "render_stats_dashboard")
        assert hasattr(metrics, "render_backend_recommendations_card")

    def helper_functions_exist(self) -> None:
        """Internal helpers exist (not strictly required but good to know)."""
        from swarmmind.ui.components.metrics import (  # noqa: PLC0415
            _extract_model_stats,
            _safe_float,
        )

        assert callable(_extract_model_stats)
        assert callable(_safe_float)
