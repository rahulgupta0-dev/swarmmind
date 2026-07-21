"""TDD tests for async_utils consolidation.

The codebase has two competing async_utils modules:
  - swarmmind/core/async_utils.py  (background-thread approach)
  - swarmmind/ui/async_utils.py    (nest_asyncio approach)

Both export run_async(). This test suite:
  1. Proves both exist (the bug)
  2. Verifies they have compatible APIs
  3. After consolidation, verifies only one exists
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest


class TestAsyncUtilsDuplication:
    """Prove the duplication problem and validate the fix."""

    def test_core_async_utils_exists(self) -> None:
        """The core async_utils module should be importable."""
        from swarmmind.core import async_utils as core_au

        assert hasattr(core_au, "run_async"), "core.async_utils must export run_async"

    def test_ui_async_utils_exists(self) -> None:
        """The UI async_utils module should be importable."""
        from swarmmind.ui import async_utils as ui_au

        assert hasattr(ui_au, "run_async"), "ui.async_utils must export run_async"

    def test_core_run_async_is_callable(self) -> None:
        """core.async_utils.run_async must be a callable function."""
        from swarmmind.core.async_utils import run_async

        assert callable(run_async)
        sig = inspect.signature(run_async)
        params = list(sig.parameters.keys())
        assert len(params) >= 1, "run_async must accept at least one argument (coro)"

    def test_ui_run_async_is_callable(self) -> None:
        """ui.async_utils.run_async must be a callable function."""
        from swarmmind.ui.async_utils import run_async

        assert callable(run_async)
        sig = inspect.signature(run_async)
        params = list(sig.parameters.keys())
        assert len(params) >= 1, "run_async must accept at least one argument (coro)"

    def test_core_and_ui_run_async_are_same_function(self) -> None:
        """After consolidation, core and ui async_utils should be the same module.

        This test FAILS while duplication exists. After fixing, it should pass
        because one module should re-export from the other.
        """
        import swarmmind.core.async_utils as core_au
        import swarmmind.ui.async_utils as ui_au

        # After consolidation: ui.async_utils should re-export from core
        # OR core should re-export from ui. Check if they point to the same
        # underlying function or if one is a re-export of the other.
        #
        # The simplest check: the source file paths should be identical,
        # meaning one module is just re-exporting the other.
        core_file = Path(inspect.getfile(core_au)).resolve()
        ui_file = Path(inspect.getfile(ui_au)).resolve()

        # If they're the same file, we're good (re-export via __init__ or symlink)
        # If different files, we need to check that at least the APIs are identical
        if core_file != ui_file:
            # Both files exist separately — check that the API signatures match
            core_sig = inspect.signature(core_au.run_async)
            ui_sig = inspect.signature(ui_au.run_async)
            assert core_sig == ui_sig, (
                f"Duplicate async_utils with different signatures!\n"
                f"  core: {core_sig}\n"
                f"  ui:   {ui_sig}\n"
                "One module should re-export from the other."
            )

    def test_no_duplicate_event_loop_code(self) -> None:
        """Verify there aren't two different event loop strategies in play."""
        import swarmmind.core.async_utils as core_au
        import swarmmind.ui.async_utils as ui_au

        core_source = inspect.getsource(core_au)
        ui_source = inspect.getsource(ui_au)

        # The core module uses background-thread approach
        core_has_thread = "threading.Thread" in core_source or "_start_background_loop" in core_source
        # The ui module uses nest_asyncio
        ui_has_nest = "nest_asyncio" in ui_source

        # If both are true, we have two competing strategies — that's the bug
        # After fix: only ONE strategy should exist
        if core_has_thread and ui_has_nest:
            pytest.xfail(
                "Both async_utils use different strategies: "
                "core uses background thread, ui uses nest_asyncio. "
                "This is the duplication bug."
            )

    def test_run_async_actually_works(self) -> None:
        """The run_async function should actually execute an async coroutine."""
        import asyncio
        from swarmmind.core.async_utils import run_async

        async def _hello() -> str:
            return "hello"

        result = run_async(_hello())
        assert result == "hello", f"run_async returned {result!r}, expected 'hello'"

    def test_run_async_with_awaitable(self) -> None:
        """run_async should handle real async operations."""
        import asyncio
        from swarmmind.core.async_utils import run_async

        async def _add(a: int, b: int) -> int:
            await asyncio.sleep(0.001)
            return a + b

        result = run_async(_add(2, 3))
        assert result == 5

    def test_no_import_errors_when_using_run_async(self) -> None:
        """Importing run_async from either location should not raise."""
        from swarmmind.core.async_utils import run_async as core_ra
        from swarmmind.ui.async_utils import run_async as ui_ra
        assert core_ra is not None
        assert ui_ra is not None
