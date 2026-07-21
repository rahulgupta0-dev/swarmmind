"""Shared test fixtures for the SwarmMind test suite.

Provides config isolation (temporarily hides ~/.swarmmind/config.toml)
and default pytest timeout settings for async tests.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Generator

import pytest


# ---------------------------------------------------------------------------
# Config isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path) -> Generator[None, None, None]:
    """Temporarily hide the user's ~/.swarmmind/config.toml during tests.

    This ensures tests always see default config values, regardless of
    what the developer has configured on their machine.

    Uses atomic rename for safety — if the test crashes between move and
    restore, the backup is still in place for the next session.
    """
    config_dir = Path.home() / ".swarmmind"
    config_file = config_dir / "config.toml"
    backup_file = config_dir / "config.toml.bak"

    # If a user config exists, temporarily move it aside
    had_config = config_file.exists()
    if had_config:
        config_file.rename(backup_file)

    try:
        yield
    finally:
        # Restore the original config file
        if had_config and backup_file.exists():
            backup_file.rename(config_file)


# ---------------------------------------------------------------------------
# Pytest configuration
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    """Register the 'slow' marker for tests that contact external services."""
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (may contact external services with short timeouts)",
    )
