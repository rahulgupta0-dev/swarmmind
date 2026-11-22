"""Tests for the newcomer-friendly ``swarmmind doctor`` command.

All network access is faked: the CLI's client factory is replaced with a
synchronous-to-async fake, so these tests stay fast and fully offline.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import swarmmind.cli.main as cli_main


class _FakeClient:
    """Minimal async stand-in for LemonadeClient's doctor probes."""

    def __init__(self, health, models=None, info=None, models_error=None) -> None:
        self.health = health
        self.models = models or []
        self.info = info or {}
        self.models_error = models_error

    async def health_check(self) -> dict:
        return self.health

    async def get_models(self):
        if self.models_error:
            raise self.models_error
        return self.models

    async def system_info(self) -> dict:
        return self.info


def _fake_config(api_key: str = "", host: str = "localhost", port: int = 13305,
                 fetch_pages: bool = True, omni: str = "LMX-Omni-5.5B-Lite"):
    return SimpleNamespace(
        lemonade=SimpleNamespace(host=host, port=port, api_key=api_key),
        web=SimpleNamespace(fetch_pages=fetch_pages),
        models=SimpleNamespace(omni=omni),
        get_lemonade_base_url=lambda: f"http://{host}:{port}",
    )


@pytest.fixture
def cli(tmp_path, monkeypatch) -> CliRunner:
    """Isolate the data dir; default config is a fake with no API key."""
    monkeypatch.setattr(cli_main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cli_main, "_get_config", lambda: _fake_config())
    monkeypatch.setattr(
        cli_main, "_doctor_client",
        lambda config: _FakeClient(health={"status": "ok", "code": 200}),
    )
    return CliRunner()


def test_doctor_healthy(cli, monkeypatch) -> None:
    """All checks pass and the checklist includes exact next steps."""
    monkeypatch.setattr(
        cli_main, "_doctor_client",
        lambda config: _FakeClient(
            health={"status": "ok", "code": 200},
            models=[{"id": "Qwen3.6-35B-A3B-GGUF"}, {"id": "LMX-Omni-5.5B-Lite"}],
            info={"backend": "rocm", "devices": [{"name": "Radeon", "type": "gpu"}]},
        ),
    )
    result = cli.invoke(cli_main.main, ["doctor"])
    assert result.exit_code == 0, result.output
    assert result.output.count("[PASS]") == 7
    assert "[PASS] Hardware / backend: Backend rocm, devices Radeon" in result.output
    assert "Summary: 7 passed, 0 warning(s), 0 failed" in result.output
    assert "Next steps:" in result.output
    for command in (
        "swarmmind doctor",
        'swarmmind ask "What is AMD Ryzen AI?"',
        "swarmmind web",
        "bash tests/smoke_test.sh",
    ):
        assert command in result.output
    assert "Traceback" not in result.output


def test_doctor_unreachable_server(cli, monkeypatch) -> None:
    """An unreachable Lemonade is a FAIL with a copy-pasteable fix."""
    monkeypatch.setattr(
        cli_main, "_doctor_client",
        lambda config: _FakeClient(health={"status": "error", "detail": "Connection refused"}),
    )
    result = cli.invoke(cli_main.main, ["doctor"])
    assert result.exit_code == 1
    assert "[FAIL] Lemonade server" in result.output
    assert "Connection refused" in result.output
    assert "lemonade-server start" in result.output
    assert "python tests/mock_lemonade_server.py" in result.output
    assert "[WARN] Models / Omni: Skipped" in result.output
    assert "[WARN] Hardware / backend: Skipped" in result.output
    assert "Traceback" not in result.output


def test_doctor_missing_import_is_reported(cli, monkeypatch) -> None:
    """A missing critical package fails doctor and prints the install hint."""
    real_import = cli_main._import_for_doctor

    def fake_import(name):
        if name == "chromadb":
            raise ImportError("No module named 'chromadb'")
        return real_import(name)

    monkeypatch.setattr(cli_main, "_import_for_doctor", fake_import)
    result = cli.invoke(cli_main.main, ["doctor"])
    assert result.exit_code == 1
    assert "[FAIL] Critical imports" in result.output
    assert "chromadb" in result.output
    assert 'pip install -e ".[dev]"' in result.output


def test_doctor_json_stdout_is_pure_and_masks_api_key(cli, monkeypatch) -> None:
    """--json emits only parseable JSON on stdout; warnings go to stderr."""
    secret = "lemonade-super-secret-key"
    monkeypatch.setattr(cli_main, "_get_config", lambda: _fake_config(api_key=secret))
    monkeypatch.setattr(
        cli_main, "_doctor_client",
        lambda config: _FakeClient(health={"status": "ok", "code": 200}, models=[], info={}),
    )
    result = cli.invoke(cli_main.main, ["doctor", "--json"])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.lstrip().startswith("{")
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["summary"] == {"PASS": 5, "WARN": 2, "FAIL": 0}
    assert [c["name"] for c in payload["checks"]] == [
        "python", "imports", "config", "data_dir",
        "lemonade_health", "models", "system_info",
    ]
    config_check = payload["checks"][2]
    assert config_check["api_key_present"] is True
    assert config_check["api_key_masked"] == "****ey"
    assert config_check["host"] == "localhost" and config_check["port"] == 13305
    assert config_check["web_fetch_pages"] is True
    assert config_check["models_omni"] == "LMX-Omni-5.5B-Lite"
    assert payload["next_steps"][0] == "swarmmind doctor"
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "WARN models: 0 model(s) visible" in result.stderr
    assert "WARN models:" not in result.stdout


def test_doctor_json_exit_code_one_on_failure(cli, monkeypatch) -> None:
    """--json still exits 1 when a check fails; the failure stays out of stdout."""
    monkeypatch.setattr(
        cli_main, "_doctor_client",
        lambda config: _FakeClient(health={"status": "error", "detail": "down"}),
    )
    result = cli.invoke(cli_main.main, ["doctor", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "fail"
    assert payload["summary"]["FAIL"] == 1
    assert "lemonade-server start" in result.stderr


def test_doctor_help_documents_json_flag(cli) -> None:
    result = cli.invoke(cli_main.main, ["doctor", "--help"])
    assert result.exit_code == 0, result.output
    assert "--json" in result.output
    assert "actionable next steps" in result.output
