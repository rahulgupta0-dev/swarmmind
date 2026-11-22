"""CliRunner smoke tests for the project/source paths exercised by smoke_test.sh.

Every test redirects the CLI's DATA_DIR to pytest's tmp_path so no user data is
touched and no test shares state with another.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from click.testing import CliRunner

import swarmmind.cli.main as cli_main
from swarmmind.data.database import Database


@pytest.fixture
def cli(tmp_path, monkeypatch) -> CliRunner:
    """CLI runner with an isolated database/vector-store directory."""
    monkeypatch.setattr(cli_main, "DATA_DIR", tmp_path)
    return CliRunner()


def _sources(tmp_path) -> list:
    db = Database(str(tmp_path / "swarmmind.db"))

    async def _read() -> list:
        await db.init_db()
        rows = await db.list_sources("ground-proj")
        await db.close()
        return rows

    return asyncio.run(_read())


def test_project_create_with_id(cli, tmp_path) -> None:
    """`project create --id` creates a project with the caller's ID."""
    result = cli.invoke(cli_main.main, [
        "project", "create", "Smoke Test Project", "--id", "smoke-proj",
    ])
    assert result.exit_code == 0, result.output
    assert "Created project" in result.output
    assert "smoke-proj" in result.output

    db = Database(str(tmp_path / "swarmmind.db"))

    async def _read():
        await db.init_db()
        project = await db.get_project("smoke-proj")
        await db.close()
        return project

    project = asyncio.run(_read())
    assert project is not None
    assert project.name == "Smoke Test Project"


def test_project_create_rejects_invalid_id(cli) -> None:
    """Malformed explicit IDs fail with a click error, not a traceback."""
    result = cli.invoke(cli_main.main, [
        "project", "create", "Bad", "--id", "bad id!",
    ])
    assert result.exit_code != 0
    assert "Invalid value" in result.output
    assert "Traceback" not in result.output


def test_source_add_missing_project_is_friendly(cli) -> None:
    """Adding to a missing project reports an error without a raw traceback."""
    result = cli.invoke(cli_main.main, [
        "source", "add", "missing-proj", "text", "some text", "--name", "Fact",
    ])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "does not exist" in result.output.lower()


def test_source_add_text_to_existing_project(cli, tmp_path) -> None:
    """A text source is stored and ingested synchronously for RAG search."""
    result = cli.invoke(cli_main.main, [
        "project", "create", "Grounded", "--id", "ground-proj",
    ])
    assert result.exit_code == 0, result.output

    fact = "The capital of France is Paris."
    result = cli.invoke(cli_main.main, [
        "source", "add", "ground-proj", "text", fact, "--name", "Fact",
    ])
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert "ready" in result.output

    sources = _sources(tmp_path)
    assert len(sources) == 1
    assert sources[0].status == "ready"
    assert sources[0].chunk_count >= 1
    assert sources[0].char_count >= len(fact)

    from swarmmind.rag.chroma_store import ChromaStore

    store = ChromaStore(str(tmp_path / "chroma"))
    hits = store.search("ground-proj", "capital of France", 3)
    assert any(fact in hit["text"] for hit in hits)


def test_help_exposes_new_options(cli) -> None:
    """Help text documents the v0.4 CLI options and commands."""
    result = cli.invoke(cli_main.main, ["--help"])
    assert result.exit_code == 0, result.output
    for command in ("ask", "project", "eval", "config", "benchmark", "web"):
        assert command in result.output

    result = cli.invoke(cli_main.main, ["ask", "--help"])
    assert result.exit_code == 0, result.output
    assert "--json" in result.output
    assert "--project-id" in result.output

    result = cli.invoke(cli_main.main, ["project", "create", "--help"])
    assert result.exit_code == 0, result.output
    assert "--id" in result.output and "-i" in result.output

    result = cli.invoke(cli_main.main, ["eval", "--help"])
    assert result.exit_code == 0, result.output
    for option in ("--report", "--output", "--judge"):
        assert option in result.output

    result = cli.invoke(cli_main.main, ["config", "--help"])
    assert result.exit_code == 0, result.output
    assert "show" in result.output and "set" in result.output


def test_ask_json_stdout_is_pure_json(cli, tmp_path, monkeypatch) -> None:
    """`ask --json` puts only the report on stdout; warnings go to stderr."""
    db = Database(str(tmp_path / "swarmmind.db"))

    async def _init() -> None:
        await db.init_db()
        await db.close()

    asyncio.run(_init())

    class FakeOrchestrator:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def run(self, **kwargs) -> dict:
            return {"title": "Fake Report", "sections": []}

    monkeypatch.setattr(cli_main, "Orchestrator", FakeOrchestrator)

    result = cli.invoke(cli_main.main, [
        "ask", "hello", "--json", "--no-web", "--project-id", "missing-proj",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"title": "Fake Report", "sections": []}
    assert "missing-proj" in result.stderr
    assert "missing-proj" not in result.stdout

