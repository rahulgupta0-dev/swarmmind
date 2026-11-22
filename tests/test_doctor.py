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
                 fetch_pages: bool = True, omni: str = "LMX-Omni-5.5B-Lite",
                 **model_overrides):
    model_values = {"omni": omni}
    model_values.update(model_overrides)
    return SimpleNamespace(
        lemonade=SimpleNamespace(host=host, port=port, api_key=api_key),
        web=SimpleNamespace(fetch_pages=fetch_pages),
        models=SimpleNamespace(**model_values),
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
    assert result.output.count("[PASS]") == 8
    assert "[PASS] Hardware / backend: Backend rocm, devices Radeon" in result.output
    # The two new informational checks are WARN for this older-style mock
    # server (no capability labels, no backend list) and never fail the run.
    assert "[WARN] Capabilities: No capability metadata reported by server" in result.output
    assert "[WARN] Backends: No backend list reported" in result.output
    assert "Summary: 8 passed, 2 warning(s), 0 failed" in result.output
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
    assert payload["summary"] == {"PASS": 5, "WARN": 5, "FAIL": 0}
    assert [c["name"] for c in payload["checks"]] == [
        "python", "imports", "config", "data_dir", "lemonade_health", "models",
        "configured_models", "capabilities", "backends", "system_info",
    ]
    assert payload["capabilities"] == {
        "vision": 0, "image": 0, "tts": 0,
        "transcription": 0, "tool_calling": 0, "omni": 0,
    }
    assert payload["backends"] == []
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
    assert "WARN configured_models: 1 of 1 configured model(s) not visible" in result.stderr
    assert "lemonade pull" in result.stderr
    assert "WARN capabilities: No capability metadata reported by server" in result.stderr
    assert "WARN backends: No backend list reported" in result.stderr
    assert "WARN models:" not in result.stdout
    assert "WARN configured_models:" not in result.stdout
    assert "WARN capabilities:" not in result.stdout


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


def test_doctor_capabilities_and_backends_present(cli, monkeypatch) -> None:
    """Capability labels and a backend list are reported in text and JSON."""
    models = [
        {"id": "vision-model", "labels": ["vision", "image", "tts"]},
        {"id": "whisper", "capabilities": {"transcription": True, "tool-calling": True}},
        {"id": "LMX-Omni-5.5B-Lite", "capabilities": ["omni", "vision"]},
    ]
    info = {
        "backend": "rocm",
        "data": {"backends": ["rocm", "vulkan", "flm", "cpu"]},
    }
    monkeypatch.setattr(
        cli_main, "_doctor_client",
        lambda config: _FakeClient(
            health={"status": "ok", "code": 200}, models=models, info=info,
        ),
    )

    result = cli.invoke(cli_main.main, ["doctor", "--json"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [c["name"] for c in payload["checks"]] == [
        "python", "imports", "config", "data_dir", "lemonade_health", "models",
        "configured_models", "capabilities", "backends", "system_info",
    ]
    expected_counts = {
        "vision": 2, "image": 1, "tts": 1,
        "transcription": 1, "tool_calling": 1, "omni": 1,
    }
    cap = payload["checks"][7]
    assert cap["name"] == "capabilities" and cap["status"] == "PASS"
    assert cap["capabilities"] == expected_counts
    assert cap["metadata_available"] is True
    assert cap["models_inspected"] == 3
    assert "vision=2, image=1, tts=1, omni=1" in cap["detail"]
    assert payload["capabilities"] == expected_counts

    back = payload["checks"][8]
    assert back["name"] == "backends" and back["status"] == "PASS"
    assert back["backends"] == ["rocm", "vulkan", "flm", "cpu"]
    assert payload["backends"] == back["backends"]
    assert payload["summary"] == {"PASS": 10, "WARN": 0, "FAIL": 0}

    # The human-readable output exposes the same information.
    text = cli.invoke(cli_main.main, ["doctor"])
    assert text.exit_code == 0, text.output
    assert "[PASS] Capabilities: vision=2, image=1, tts=1, omni=1" in text.output
    assert "transcription=1, tool-calling=1" in text.output
    assert "[PASS] Backends: Available backends: rocm, vulkan, flm, cpu" in text.output


def test_doctor_capabilities_metadata_absent_is_a_warning(cli, monkeypatch) -> None:
    """Older/mock servers without capability metadata WARN but still pass."""
    monkeypatch.setattr(
        cli_main, "_doctor_client",
        lambda config: _FakeClient(
            health={"status": "ok", "code": 200},
            models=[{"id": "plain-a"}, {"id": "plain-b"}],
            info={"backend": "rocm"},
        ),
    )
    result = cli.invoke(cli_main.main, ["doctor", "--json"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    cap = next(c for c in payload["checks"] if c["name"] == "capabilities")
    assert cap["status"] == "WARN"
    assert cap["detail"] == "No capability metadata reported by server"
    assert cap["metadata_available"] is False
    assert cap["models_inspected"] == 2
    assert cap["capabilities"]["vision"] == 0
    assert payload["status"] == "ok"
    assert payload["summary"]["FAIL"] == 0
    assert "WARN capabilities: No capability metadata reported by server" in result.stderr

    text = cli.invoke(cli_main.main, ["doctor"])
    assert text.exit_code == 0
    assert "[WARN] Capabilities: No capability metadata reported by server" in text.output
    assert "Traceback" not in text.output


def test_doctor_backends_from_nested_data(cli, monkeypatch) -> None:
    """``data.backends`` also accepts dict entries and deduplicates names."""
    monkeypatch.setattr(
        cli_main, "_doctor_client",
        lambda config: _FakeClient(
            health={"status": "ok", "code": 200},
            models=[{"id": "plain"}],
            info={"data": {"backends": [{"name": "flm"}, {"name": "cpu"}, {"backend": "rocm"}]}},
        ),
    )
    result = cli.invoke(cli_main.main, ["doctor", "--json"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    back = next(c for c in payload["checks"] if c["name"] == "backends")
    assert back["status"] == "PASS"
    assert back["backends"] == ["flm", "cpu", "rocm"]
    assert "[PASS] Backends: Available backends: flm, cpu, rocm" in cli.invoke(
        cli_main.main, ["doctor"],
    ).output


def test_doctor_capabilities_requests_show_all_when_supported(cli, monkeypatch) -> None:
    """A client that can ask for show_all gets the richer model metadata."""

    class _ShowAllClient(_FakeClient):
        def __init__(self) -> None:
            super().__init__(health={"status": "ok", "code": 200}, models=[])
            self.show_all_calls: list[bool] = []

        async def get_models(self, show_all: bool = False):
            self.show_all_calls.append(show_all)
            return [{"id": "vision-model", "labels": ["vision", "tts"]}]

    holder: dict[str, _ShowAllClient] = {}

    def _factory(config):
        holder["client"] = _ShowAllClient()
        return holder["client"]

    monkeypatch.setattr(cli_main, "_doctor_client", _factory)
    result = cli.invoke(cli_main.main, ["doctor", "--json"])
    assert result.exit_code == 0, result.stderr
    assert holder["client"].show_all_calls == [False, True]
    payload = json.loads(result.stdout)
    cap = payload["checks"][7]
    assert cap["capabilities"]["vision"] == 1
    assert cap["capabilities"]["tts"] == 1


def test_doctor_configured_models_all_present(cli, monkeypatch) -> None:
    """Every configured model visible in /v1/models yields a PASS row."""
    monkeypatch.setattr(cli_main, "_get_config", lambda: _fake_config(
        conductor="user.inkling-mini",
        worker="unsloth/Inkling-2-Mini-GGUF",
        embeddings="nomic-embed-text-v1-GGUF",
        image="Flux-2-Klein-4B",
        tts="kokoro-v1",
        omni="LMX-Omni-5.5B-Lite",
    ))
    monkeypatch.setattr(
        cli_main, "_doctor_client",
        lambda config: _FakeClient(
            health={"status": "ok", "code": 200},
            models=[
                {"id": "user.inkling-mini"},
                {"id": "unsloth/Inkling-2-Mini-GGUF:UD-Q4_K_XL"},
                {"id": "nomic-embed-text-v1-GGUF"},
                {"id": "Flux-2-Klein-4B"},
                {"id": "kokoro-v1"},
                {"id": "LMX-Omni-5.5B-Lite"},
            ],
            info={"backend": "rocm"},
        ),
    )
    result = cli.invoke(cli_main.main, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "[PASS] Configured models: All 6 configured model(s) visible in Lemonade" in result.output
    assert "lemonade pull" not in result.output

    payload = json.loads(cli.invoke(cli_main.main, ["doctor", "--json"]).stdout)
    check = next(c for c in payload["checks"] if c["name"] == "configured_models")
    assert check["status"] == "PASS"
    assert check["missing"] == []
    assert set(check["configured"]) == {"conductor", "worker", "embeddings", "image", "tts", "omni"}
    assert check["visible_count"] == 6
    assert payload["status"] == "ok"


def test_doctor_configured_models_missing_warns(cli, monkeypatch) -> None:
    """Missing configured models WARN (never FAIL) with copy-pasteable fixes."""
    monkeypatch.setattr(cli_main, "_get_config", lambda: _fake_config(
        conductor="unsloth/Inkling-2-Mini-GGUF",
        worker="inkling-mini",
        omni="",
    ))
    monkeypatch.setattr(
        cli_main, "_doctor_client",
        lambda config: _FakeClient(
            health={"status": "ok", "code": 200},
            models=[{"id": "Qwen3.6-35B-A3B-GGUF"}],
            info={"backend": "rocm"},
        ),
    )
    result = cli.invoke(cli_main.main, ["doctor"])
    assert result.exit_code == 0, result.output  # informational: never a failure
    assert "[WARN] Configured models:" in result.output
    assert "conductor=unsloth/Inkling-2-Mini-GGUF" in result.output
    assert "worker=inkling-mini" in result.output
    assert "lemonade pull unsloth/Inkling-2-Mini-GGUF" in result.output
    assert "lemonade pull user.inkling-mini ..." in result.output
    assert "registered in Lemonade but not downloaded" in result.output
    assert "docs/MODELS.md" in result.output
    assert "Traceback" not in result.output

    payload = json.loads(cli.invoke(cli_main.main, ["doctor", "--json"]).stdout)
    check = next(c for c in payload["checks"] if c["name"] == "configured_models")
    assert check["status"] == "WARN"
    assert payload["status"] == "ok"
    assert payload["summary"]["FAIL"] == 0
    assert [item["role"] for item in check["missing"]] == ["conductor", "worker"]
    assert check["missing"][0]["pull_command"].startswith("lemonade pull ")
    assert check["show_all_available"] is False
    # Empty roles are skipped.
    assert "omni" not in check["configured"]


def test_doctor_configured_models_uses_show_all_to_classify(cli, monkeypatch) -> None:
    """?show_all=true data separates registered-but-not-downloaded models."""

    class _ShowAllClient(_FakeClient):
        def __init__(self) -> None:
            super().__init__(health={"status": "ok", "code": 200})
            self.show_all_calls: list[bool] = []

        async def get_models(self, show_all: bool = False):
            self.show_all_calls.append(show_all)
            if show_all:
                return [{"id": "unsloth/Inkling-2-Mini-GGUF:UD-Q4_K_XL"}]
            return [{"id": "Qwen3.6-35B-A3B-GGUF"}]

    holder: dict[str, _ShowAllClient] = {}

    def _factory(config):
        holder["client"] = _ShowAllClient()
        return holder["client"]

    monkeypatch.setattr(cli_main, "_get_config", lambda: _fake_config(
        conductor="unsloth/Inkling-2-Mini-GGUF",
        worker="Flux-2-Klein-4B",
        omni="",
    ))
    monkeypatch.setattr(cli_main, "_doctor_client", _factory)
    result = cli.invoke(cli_main.main, ["doctor", "--json"])
    assert result.exit_code == 0, result.stderr
    assert holder["client"].show_all_calls == [False, True]
    payload = json.loads(result.stdout)
    check = next(c for c in payload["checks"] if c["name"] == "configured_models")
    assert check["status"] == "WARN"
    assert check["show_all_available"] is True
    states = {item["role"]: item["state"] for item in check["missing"]}
    assert states == {
        "conductor": "registered but not downloaded",
        "worker": "not registered",
    }
    assert "registered but not downloaded" in check["detail"]
