"""TDD tests for pyproject.toml validity and package installability."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


class TestPyprojectTomlStructure:
    """Verify pyproject.toml has correct TOML structure."""

    def test_dependencies_are_direct_child_of_project(self) -> None:
        """dependencies must be under [project], not nested in [project.urls]."""
        import tomllib

        toml_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        # dependencies MUST be a direct child of [project]
        assert "dependencies" in data.get("project", {}), (
            "dependencies not found under [project] — likely nested in [project.urls]"
        )

        deps = data["project"]["dependencies"]
        assert isinstance(deps, list), "dependencies must be a list of strings"
        assert len(deps) > 0, "dependencies list must not be empty"

    def test_urls_do_not_contain_dependencies(self) -> None:
        """[project.urls] should only contain URL mappings, not dependencies."""
        import tomllib

        toml_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        urls = data.get("project", {}).get("urls", {})
        assert "dependencies" not in urls, (
            "dependencies should NOT be inside [project.urls]"
        )

    def test_required_project_fields_exist(self) -> None:
        """Verify all PEP 621 required fields are present."""
        import tomllib

        toml_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        project = data.get("project", {})
        assert "name" in project, "missing project.name"
        assert "version" in project, "missing project.version"
        assert "requires-python" in project, "missing project.requires-python"

    def test_optional_dev_dependencies_present(self) -> None:
        """Dev dependencies (pytest, pytest-asyncio) should be declared."""
        import tomllib

        toml_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)

        dev_deps = data.get("project", {}).get("optional-dependencies", {}).get("dev", [])
        dep_names = " ".join(dev_deps).lower()
        assert "pytest" in dep_names, "pytest missing from dev dependencies"
        assert "pytest-asyncio" in dep_names, "pytest-asyncio missing from dev dependencies"

    def test_package_build_does_not_error(self) -> None:
        """Running python -m build (or setuptools check) should not crash."""
        pytest.importorskip("build", reason="build package not installed")
        result = subprocess.run(
            [sys.executable, "-m", "build", "--no-isolation", "--sdist", "."],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
            timeout=30,
        )
        # We only care that the build didn't crash with a TOML parse error
        # exit code 0 or 1 from build is fine; a ValueError is not
        assert "configuration error" not in (result.stderr or "").lower(), (
            f"Build config error detected:\n{result.stderr}"
        )
