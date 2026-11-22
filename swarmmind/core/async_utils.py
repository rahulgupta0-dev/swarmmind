"""Helper utilities for safely running asyncio loops within Streamlit."""

from __future__ import annotations

import asyncio
import atexit
import threading
import time
from typing import Any, Coroutine, TypeVar

_T = TypeVar("_T")

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()


def _start_background_loop() -> None:
    """Create and run a fresh event loop on the background thread."""
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


def _ensure_loop() -> None:
    """Start the background loop exactly once, in a thread-safe manner."""
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is None:
            _loop_thread = threading.Thread(
                target=_start_background_loop, daemon=True,
            )
            _loop_thread.start()
        # Wait for the loop to be created and running before handing out work.
        while _loop is None or not _loop.is_running():
            time.sleep(0.01)


@atexit.register
def _shutdown_background_loop() -> None:
    """Stop the persistent loop so the interpreter can exit cleanly."""
    loop = _loop
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(loop.stop)


def run_async(coro: Coroutine[Any, Any, _T]) -> _T:
    """Safely run async functions in Streamlit using a persistent background loop."""
    _ensure_loop()

    # If we are already in the background loop (e.g. nested calls), we can't block.
    # But Streamlit scripts run in their own threads, so this is safe.
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if current_loop is _loop:
        raise RuntimeError("run_async called from within the background loop!")

    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result()
