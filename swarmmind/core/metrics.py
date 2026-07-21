"""One-shot metrics snapshot helper for AMD Lemonade SwarmMind.

This provides a lightweight :class:`MetricsSnapshot` model and a single
async helper, :func:`fetch_snapshot`, that combines stats, system-stats,
and system-info queries into one point-in-time view.

The *polling* loop lives in the UI layer (see
:mod:`swarmmind.ui.components.metrics`).  This module stays pure data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from swarmmind.lemonade.client import LemonadeClient

logger = __import__("logging").getLogger(__name__)


class MetricsSnapshot(BaseModel):
    """A point-in-time snapshot of Lemonade server stats and system metrics.

    Each field is best-effort — if the corresponding endpoint is
    unreachable or returns malformed data the field is an empty dict.
    """

    stats: dict[str, Any] = Field(default_factory=dict)
    """Per-model performance counters from ``GET /v1/stats``."""

    system_stats: dict[str, Any] = Field(default_factory=dict)
    """Live resource usage from ``GET /v1/system-stats``."""

    system_info: dict[str, Any] = Field(default_factory=dict)
    """Static hardware description from ``GET /v1/system-info``."""

    fetched_at: datetime = Field(default_factory=datetime.now)
    """When the snapshot was taken (server local time)."""


async def fetch_snapshot(
    client: LemonadeClient,
) -> MetricsSnapshot:
    """Fetch a one-shot snapshot combining stats + system_stats + system_info.

    Each endpoint is best-effort — if one fails, the corresponding field
    on the returned snapshot is an empty dict and nothing is raised.

    Args:
        client: An initialised :class:`LemonadeClient`.

    Returns:
        A fully populated :class:`MetricsSnapshot` (fields may be empty
        dicts if endpoints are unreachable).
    """
    stats: dict[str, Any] = {}
    system_stats: dict[str, Any] = {}
    system_info: dict[str, Any] = {}

    try:
        result = await client.get_stats()
        if isinstance(result, dict):
            stats = result
    except Exception as exc:
        logger.debug("fetch_snapshot: get_stats failed: %s", exc)

    try:
        result = await client.system_stats()
        if isinstance(result, dict):
            system_stats = result
    except Exception as exc:
        logger.debug("fetch_snapshot: system_stats failed: %s", exc)

    try:
        result = await client.system_info()
        if isinstance(result, dict):
            system_info = result
    except Exception as exc:
        logger.debug("fetch_snapshot: system_info failed: %s", exc)

    return MetricsSnapshot(
        stats=stats,
        system_stats=system_stats,
        system_info=system_info,
        fetched_at=datetime.now(),
    )
