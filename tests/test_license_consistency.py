"""TDD tests for license consistency across project metadata.

The audit found:
- pyproject.toml says Apache-2.0
- README.md says MIT
- LICENSE file exists

This is a mismatch that should be fixed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestLicenseConsistency:
    """Verify all license references are consistent."""

    def _get_pyproject_license(self) -> str:
        import tomllib

        with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
            data = tomllib.load(f)
        lic = data.get("project", {}).get("license", "")
        # PEP 621: license can be a string or a dict like {text = "Apache-2.0"}
        if isinstance(lic, dict):
            return lic.get("text", "") or lic.get("file", "")
        return str(lic)

    def _get_readme_license_mentions(self) -> list[str]:
        readme = (PROJECT_ROOT / "README.md").read_text()
        # Find all license badge/text mentions
        matches = re.findall(
            r"License[:\s]*(?:MIT|Apache(?:\s*2\.0)?|BSD|GPL)",
            readme,
            re.IGNORECASE,
        )
        return matches

    def _get_license_file_header(self) -> str:
        license_file = PROJECT_ROOT / "LICENSE"
        if license_file.exists():
            return license_file.read_text()[:500]
        return ""

    def test_pyproject_has_license_field(self) -> None:
        """pyproject.toml should declare a license."""
        license_val = self._get_pyproject_license()
        assert license_val, "pyproject.toml missing project.license"

    def test_readme_license_matches_pyproject(self) -> None:
        """README license badge should match pyproject.toml license."""
        pyproject_license = self._get_pyproject_license()
        readme_mentions = self._get_readme_license_mentions()

        if not readme_mentions:
            pytest.skip("No explicit license mention in README badges")

        # Normalize for comparison
        pyproject_normalized = re.sub(r"[^a-z0-9]", "", pyproject_license.lower())
        for mention in readme_mentions:
            mention_normalized = re.sub(r"[^a-z0-9]", "", mention.lower())
            if mention_normalized in pyproject_normalized or pyproject_normalized in mention_normalized:
                return  # Match found

        pytest.fail(
            f"License mismatch: pyproject.toml says '{pyproject_license}' "
            f"but README mentions {readme_mentions}"
        )

    def test_license_file_matches_pyproject(self) -> None:
        """LICENSE file header should match pyproject.toml license."""
        pyproject_license = self._get_pyproject_license()
        license_header = self._get_license_file_header()

        if not license_header:
            pytest.skip("LICENSE file not found or empty")

        pyproject_lower = pyproject_license.lower()
        header_lower = license_header.lower()

        if "apache" in pyproject_lower:
            assert "apache" in header_lower, (
                f"pyproject.toml says Apache but LICENSE file doesn't mention Apache"
            )
        elif "mit" in pyproject_lower:
            assert "mit" in header_lower or "permission is hereby granted" in header_lower, (
                f"pyproject.toml says MIT but LICENSE file doesn't match"
            )
