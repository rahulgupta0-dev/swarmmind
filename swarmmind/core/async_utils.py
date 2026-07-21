"""Helper utilities for safely running asyncio loops within Streamlit."""

import asyncio

import asyncio
import threading
import atexit

_loop = None
_loop_thread = None

def _start_background_loop():
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_forever()

def _ensure_loop():
    global _loop, _loop_thread
    if _loop is None:
        _loop_thread = threading.Thread(target=_start_background_loop, daemon=True)
        _loop_thread.start()
        # Wait for loop to be ready
        while _loop is None or not _loop.is_running():
            import time
            time.sleep(0.01)

def run_async(coro):
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
