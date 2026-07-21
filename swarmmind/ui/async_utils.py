"""Streamlit-safe async helpers — re-exported from core.async_utils.

This module previously contained a competing ``nest_asyncio``-based
implementation of ``run_async``.  It now re-exports the canonical
implementation from ``swarmmind.core.async_utils`` which uses a
persistent background-thread event loop.

Public API:
    * :func:`run_async` — sync-in-sync-out adapter for async coroutines.
"""

from __future__ import annotations

from swarmmind.core.async_utils import run_async  # noqa: F401

__all__: list[str] = ["run_async"]
