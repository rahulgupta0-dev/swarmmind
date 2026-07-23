"""Comprehensive end-to-end integration tests for SwarmMind.

Covers imports, CLI parsing, database CRUD, ChromaStore CRUD,
orchestrator/conductor/synthesis error handling, LemonadeClient mocks
and config loading.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio


# ===================================================================
# Test 1 — All Module Imports
# ===================================================================

class TestAllModuleImports:
    """Verify every module in the project can be imported without errors."""

    def test_core_imports(self) -> None:
        """Core modules that have no Streamlit dependency."""
        from swarmmind import __version__
        assert __version__ == "0.2.0"

        from swarmmind.config import Config
        assert Config is not None

        from swarmmind.lemonade.client import LemonadeClient
        assert LemonadeClient is not None

        from swarmmind.data.models import Project, Source, Conversation, Note
        assert all([Project, Source, Conversation, Note])

        from swarmmind.data.database import Database
        assert Database is not None

        from swarmmind.rag.chroma_store import ChromaStore
        assert ChromaStore is not None

        from swarmmind.rag.pipeline import Pipeline
        assert Pipeline is not None

        from swarmmind.core.conductor import Conductor
        assert Conductor is not None

        from swarmmind.core.workers import Worker, search_web
        assert Worker is not None

        from swarmmind.core.synthesis import Synthesis
        assert Synthesis is not None

        from swarmmind.core.orchestrator import Orchestrator
        assert Orchestrator is not None

        from swarmmind.cli.main import main
        assert main is not None

    def test_ui_component_imports(self) -> None:
        """Icon components; streamlit is imported only inside function bodies."""
        from swarmmind.ui.components.icons import render_icon, load_phosphor_css, icon_html
        assert callable(render_icon)
        assert callable(load_phosphor_css)
        assert callable(icon_html)

    def test_ui_panel_imports(self) -> None:
        """Panel modules are importable; streamlit is imported inside functions."""
        from swarmmind.ui.panels.chat import render_chat_panel
        from swarmmind.ui.panels.studio import render_studio_panel
        from swarmmind.ui.panels.sources import render_sources_panel
        assert callable(render_chat_panel)
        assert callable(render_studio_panel)
        assert callable(render_sources_panel)

    def test_ui_app_imports_work(self) -> None:
        """swarmmind.ui.app imports cleanly (skips if streamlit not installed)."""
        pytest.importorskip("streamlit", reason="streamlit not installed")
        import swarmmind.ui.app  # noqa: F401
# ===================================================================
# Test 2 — CLI Command Parsing
# ===================================================================

class TestCliCommands:
    """Verify CLI commands parse correctly using Click's CliRunner.

    Most commands will fail at runtime (no Lemonade server), but they
    should at least parse — no import errors or Click syntax errors.
    """

    def _invoke(self, args: list[str]) -> Any:
        from click.testing import CliRunner
        from swarmmind.cli.main import main

        runner = CliRunner()
        return runner.invoke(main, args)

    # --help should always succeed -------------------------------------------------
    def test_help(self) -> None:
        result = self._invoke(["--help"])
        assert result.exit_code == 0
        assert "Usage:" in result.output
        assert "ask" in result.output

    # Commands that parse correctly; runtime failures are expected -----------------
    def test_ask_parses(self) -> None:
        """ask command: parses a single query argument."""
        result = self._invoke(["ask", "test query"])
        # exit 2 would indicate missing-argument error; anything else means it parsed
        assert result.exit_code != 2, f"Parse error: {result.output}"

    def test_project_create_parses(self) -> None:
        """project create: parses a name argument."""
        result = self._invoke(["project", "create", "TestProject"])
        assert result.exit_code != 2, f"Parse error: {result.output}"

    def test_project_list_parses(self) -> None:
        """project list: no arguments required."""
        result = self._invoke(["project", "list"])
        assert result.exit_code != 2, f"Parse error: {result.output}"

    def test_source_add_parses(self) -> None:
        """source add: parses project-id, source-type, source-uri."""
        result = self._invoke([
            "source", "add",
            "00000000-0000-0000-0000-000000000000",
            "web",
            "https://example.com",
        ])
        assert result.exit_code != 2, f"Parse error: {result.output}"

    def test_source_list_parses(self) -> None:
        """source list: parses a project-id argument."""
        result = self._invoke([
            "source", "list", "00000000-0000-0000-0000-000000000000",
        ])
        assert result.exit_code != 2, f"Parse error: {result.output}"

    def test_report_list_parses(self) -> None:
        """report list: parses a project-id argument."""
        result = self._invoke([
            "report", "list", "00000000-0000-0000-0000-000000000000",
        ])
        assert result.exit_code != 2, f"Parse error: {result.output}"

    def test_config_show_parses(self) -> None:
        """config show: no arguments."""
        result = self._invoke(["config", "show-config"])
        assert result.exit_code != 2, f"Parse error: {result.output}"

    # Missing-argument errors should produce exit code 2 --------------------------
    def test_missing_args_return_code_2(self) -> None:
        """Commands invoked without required arguments should exit 2."""
        result = self._invoke(["project", "create"])
        assert result.exit_code == 2
        assert "Error:" in result.output

        result2 = self._invoke(["source", "add"])
        assert result2.exit_code == 2
        assert "Error:" in result2.output

        result3 = self._invoke(["ask"])
        assert result3.exit_code == 2
        assert "Error:" in result3.output


# ===================================================================
# Test 3 — Database CRUD
# ===================================================================

class TestDatabaseCRUD:
    """Full CRUD for projects, sources, conversations and notes via aiosqlite."""

    @pytest_asyncio.fixture
    async def db(self) -> Any:
        """Create a temporary SQLite database, yield a Database instance."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = tmp.name

        from swarmmind.data.database import Database

        db = Database(db_path)
        await db.connect()
        await db.init_db()
        yield db
        await db.close()
        os.unlink(db_path)

    # -- Projects ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_and_get_project(self, db: Any) -> None:
        from swarmmind.data.models import Project

        proj = Project(name="Integration Test", description="End-to-end test")
        created = await db.create_project(proj)
        assert created.id
        assert created.name == "Integration Test"
        assert created.description == "End-to-end test"

        fetched = await db.get_project(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.name == "Integration Test"

    @pytest.mark.asyncio
    async def test_list_projects(self, db: Any) -> None:
        from swarmmind.data.models import Project

        await db.create_project(Project(name="Alpha"))
        await db.create_project(Project(name="Beta"))
        projects = await db.list_projects()
        assert len(projects) >= 2
        names = [p.name for p in projects]
        assert "Alpha" in names
        assert "Beta" in names

    @pytest.mark.asyncio
    async def test_update_project(self, db: Any) -> None:
        from swarmmind.data.models import Project

        proj = await db.create_project(Project(name="Before"))
        proj.name = "After"
        await db.update_project(proj)
        fetched = await db.get_project(proj.id)
        assert fetched is not None
        assert fetched.name == "After"

    @pytest.mark.asyncio
    async def test_delete_project(self, db: Any) -> None:
        from swarmmind.data.models import Project

        proj = await db.create_project(Project(name="ToDelete"))
        await db.delete_project(proj.id)
        fetched = await db.get_project(proj.id)
        assert fetched is None

    # -- Sources -------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_and_get_source(self, db: Any) -> None:
        from swarmmind.data.models import Project, Source

        proj = await db.create_project(Project(name="SrcTest"))
        src = Source(
            project_id=proj.id,
            source_type="web",
            source_uri="https://example.com",
            display_name="Example",
        )
        created = await db.create_source(src)
        assert created.id
        assert created.source_type == "web"

        fetched = await db.get_source(created.id)
        assert fetched is not None
        assert fetched.source_uri == "https://example.com"

    @pytest.mark.asyncio
    async def test_list_sources(self, db: Any) -> None:
        from swarmmind.data.models import Project, Source

        proj = await db.create_project(Project(name="SrcList"))
        await db.create_source(Source(project_id=proj.id, source_type="pdf", source_uri="/a.pdf"))
        await db.create_source(Source(project_id=proj.id, source_type="web", source_uri="https://b"))
        sources = await db.list_sources(proj.id)
        assert len(sources) == 2

    @pytest.mark.asyncio
    async def test_update_source(self, db: Any) -> None:
        from swarmmind.data.models import Project, Source

        proj = await db.create_project(Project(name="SrcUpd"))
        src = await db.create_source(
            Source(project_id=proj.id, source_type="text", source_uri="original"),
        )
        src.status = "ready"
        src.char_count = 100
        await db.update_source(src)
        fetched = await db.get_source(src.id)
        assert fetched is not None
        assert fetched.status == "ready"
        assert fetched.char_count == 100

    @pytest.mark.asyncio
    async def test_delete_source(self, db: Any) -> None:
        from swarmmind.data.models import Project, Source

        proj = await db.create_project(Project(name="SrcDel"))
        src = await db.create_source(
            Source(project_id=proj.id, source_type="text", source_uri="x"),
        )
        await db.delete_source(src.id)
        fetched = await db.get_source(src.id)
        assert fetched is None

    # -- Conversations -------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_and_list_conversations(self, db: Any) -> None:
        from swarmmind.data.models import Conversation, Project

        proj = await db.create_project(Project(name="ConvTest"))
        conv = Conversation(
            project_id=proj.id,
            query="What is RISC-V?",
            web_search_used=True,
            worker_count=3,
        )
        created = await db.create_conversation(conv)
        assert created.id
        assert created.query == "What is RISC-V?"

        convs = await db.list_conversations(proj.id)
        assert len(convs) == 1
        assert convs[0].query == "What is RISC-V?"

    @pytest.mark.asyncio
    async def test_delete_conversation(self, db: Any) -> None:
        from swarmmind.data.models import Conversation, Project

        proj = await db.create_project(Project(name="ConvDel"))
        conv = await db.create_conversation(
            Conversation(project_id=proj.id, query="test"),
        )
        await db.delete_conversation(conv.id)
        convs = await db.list_conversations(proj.id)
        assert len(convs) == 0

    # -- Notes ---------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_and_list_notes(self, db: Any) -> None:
        from swarmmind.data.models import Note, Project

        proj = await db.create_project(Project(name="NoteTest"))
        note = Note(
            project_id=proj.id,
            title="My Note",
            content="# Hello\nWorld",
        )
        created = await db.create_note(note)
        assert created.id
        assert created.title == "My Note"

        notes = await db.list_notes(proj.id)
        assert len(notes) == 1
        assert notes[0].content == "# Hello\nWorld"

    @pytest.mark.asyncio
    async def test_update_note(self, db: Any) -> None:
        from swarmmind.data.models import Note, Project

        proj = await db.create_project(Project(name="NoteUpd"))
        note = await db.create_note(
            Note(project_id=proj.id, title="Before", content="old"),
        )
        note.title = "After"
        note.content = "new"
        await db.update_note(note)
        fetched = (await db.list_notes(proj.id))[0]
        assert fetched.title == "After"
        assert fetched.content == "new"

    @pytest.mark.asyncio
    async def test_delete_note(self, db: Any) -> None:
        from swarmmind.data.models import Note, Project

        proj = await db.create_project(Project(name="NoteDel"))
        note = await db.create_note(
            Note(project_id=proj.id, title="Del", content="x"),
        )
        await db.delete_note(note.id)
        notes = await db.list_notes(proj.id)
        assert len(notes) == 0

    # -- Cascade delete ------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_cascade_delete(self, db: Any) -> None:
        """Deleting a project removes its sources, conversations, and notes."""
        from swarmmind.data.models import Conversation, Note, Project, Source

        proj = await db.create_project(Project(name="Cascade"))
        src = await db.create_source(
            Source(project_id=proj.id, source_type="text", source_uri="x"),
        )
        conv = await db.create_conversation(
            Conversation(project_id=proj.id, query="q"),
        )
        note = await db.create_note(
            Note(project_id=proj.id, title="t", content="c"),
        )

        # Delete the project
        await db.delete_project(proj.id)

        # All related entities should be gone
        assert await db.get_project(proj.id) is None
        assert len(await db.list_sources(proj.id)) == 0
        assert len(await db.list_conversations(proj.id)) == 0
        assert len(await db.list_notes(proj.id)) == 0


# ===================================================================
# Test 4 — ChromaStore CRUD
# ===================================================================

class TestChromaStoreCRUD:
    """Vector store operations with a temporary persistence directory."""

    @pytest.fixture
    def chroma_store(self) -> Any:
        from swarmmind.rag.chroma_store import ChromaStore

        tmp_dir = tempfile.mkdtemp(prefix="chroma_test_")
        store = ChromaStore(tmp_dir)
        yield store
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_create_collection(self, chroma_store: Any) -> None:
        chroma_store.create_project_collection("test-root")
        # Re-creating the same collection should be idempotent
        chroma_store.create_project_collection("test-root")

    def test_add_and_search(self, chroma_store: Any) -> None:
        pid = "search-test"
        chroma_store.create_project_collection(pid)

        chunks = [
            {
                "text": "RISC-V is an open ISA based on reduced instruction set computing.",
                "metadata": {"source": "doc1"},
            },
            {
                "text": "ARM SVE is a scalable vector extension for ARM architecture.",
                "metadata": {"source": "doc2"},
            },
            {
                "text": "AMD ROCm is a software stack for GPU computing.",
                "metadata": {"source": "doc3"},
            },
        ]

        count = chroma_store.add_chunks(pid, chunks)
        assert count == 3

        results = chroma_store.search(pid, "RISC-V architecture", top_k=2)
        assert len(results) <= 2
        for r in results:
            assert "text" in r
            assert "metadata" in r
            assert "distance" in r

    def test_search_empty_collection(self, chroma_store: Any) -> None:
        """Searching an empty collection returns an empty list."""
        chroma_store.create_project_collection("empty")
        results = chroma_store.search("empty", "anything")
        assert results == []

    def test_add_without_explicit_create(self, chroma_store: Any) -> None:
        """add_chunks auto-creates the collection if it doesn't exist."""
        chunks = [{"text": "Hello world", "metadata": {"src": "test"}}]
        count = chroma_store.add_chunks("auto-create", chunks)
        assert count == 1

        results = chroma_store.search("auto-create", "hello")
        assert len(results) >= 1

    def test_delete_collection(self, chroma_store: Any) -> None:
        chroma_store.create_project_collection("to-delete")
        chroma_store.delete_collection("to-delete")
        # Re-delete should be silent (not raise)
        chroma_store.delete_collection("to-delete")


# ===================================================================
# Test 5 — Orchestrator Mock
# ===================================================================

class TestOrchestrator:
    """Orchestrator instantiation and run error handling."""

    def test_instantiation(self) -> None:
        """Orchestrator can be created with a Config and a LemonadeClient."""
        from swarmmind.config import Config
        from swarmmind.core.orchestrator import Orchestrator
        from swarmmind.lemonade.client import LemonadeClient

        config = Config()  # type: ignore[call-arg]
        client = LemonadeClient("http://localhost:13305")
        orch = Orchestrator(config, client)
        assert orch is not None

    def test_instantiation_with_chroma(self) -> None:
        """Orchestrator can be created with an optional ChromaStore."""
        from swarmmind.config import Config
        from swarmmind.core.orchestrator import Orchestrator
        from swarmmind.lemonade.client import LemonadeClient
        from swarmmind.rag.chroma_store import ChromaStore

        tmp_dir = tempfile.mkdtemp(prefix="orch_chroma_")
        chroma = ChromaStore(tmp_dir)

        config = Config()  # type: ignore[call-arg]
        client = LemonadeClient("http://localhost:13305")
        orch = Orchestrator(config, client, chroma_store=chroma)
        assert orch is not None

        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_run_raises_connection_error(self) -> None:
        """Orchestrator.run raises ConnectionError when Lemonade is unreachable."""
        import asyncio

        from swarmmind.config import Config
        from swarmmind.core.orchestrator import Orchestrator
        from swarmmind.lemonade.client import LemonadeClient

        config = Config()  # type: ignore[call-arg]
        # Point at a port that is very unlikely to be serving Lemonade
        client = LemonadeClient("http://127.0.0.1:1", timeout=0.1)
        orch = Orchestrator(config, client)

        with pytest.raises((ConnectionError, OSError, asyncio.TimeoutError)):
            await asyncio.wait_for(orch.run(query="test query"), timeout=5.0)


# ===================================================================
# Test 6 — LemonadeClient Mock
# ===================================================================

class TestLemonadeClientMock:
    """LemonadeClient instantiation and graceful error handling."""

    def test_instantiation(self) -> None:
        from swarmmind.lemonade.client import LemonadeClient

        client = LemonadeClient("http://localhost:13305")
        assert client.base_url == "http://localhost:13305"
        assert client.api_key == "not-needed"

    def test_custom_url_and_key(self) -> None:
        from swarmmind.lemonade.client import LemonadeClient

        client = LemonadeClient("http://192.168.1.50:13305", api_key="test-key")
        assert client.base_url == "http://192.168.1.50:13305"
        assert client.api_key == "test-key"

    @pytest.mark.asyncio
    async def test_health_check_returns_error_gracefully(self) -> None:
        """health_check should not raise; it returns a dict with status 'error'."""
        from swarmmind.lemonade.client import LemonadeClient

        client = LemonadeClient("http://127.0.0.1:1", timeout=0.1)
        result = await client.health_check()
        assert result["status"] == "error"
        assert "detail" in result

    @pytest.mark.asyncio
    async def test_chat_completion_raises_on_unreachable(self) -> None:
        """chat_completion should raise when the server is unreachable."""
        from swarmmind.lemonade.client import LemonadeClient

        client = LemonadeClient("http://127.0.0.1:1", timeout=0.1)
        with pytest.raises(Exception):
            await client.chat_completion(
                model="test-model",
                messages=[{"role": "user", "content": "hello"}],
            )

    @pytest.mark.asyncio
    async def test_embeddings_raises_on_unreachable(self) -> None:
        from swarmmind.lemonade.client import LemonadeClient

        client = LemonadeClient("http://127.0.0.1:1", timeout=0.1)
        with pytest.raises(Exception):
            await client.embeddings(model="test", input_texts="hello")

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        """close() should not raise on a functioning client."""
        from swarmmind.lemonade.client import LemonadeClient

        client = LemonadeClient("http://127.0.0.1:1", timeout=0.1)
        await client.close()

    @pytest.mark.asyncio
    async def test_async_context_manager(self) -> None:
        """async with LemonadeClient(...) should work."""
        from swarmmind.lemonade.client import LemonadeClient

        async with LemonadeClient("http://127.0.0.1:1", timeout=0.1) as client:
            assert client.base_url == "http://127.0.0.1:1"


# ===================================================================
# Test 7 — Conductor + Synthesis Mock
# ===================================================================

class TestConductorSynthesisMock:
    """Error handling and fallback behaviour when no Lemonade server is reachable."""

    @pytest.fixture
    def config(self) -> Any:
        from swarmmind.config import Config
        return Config()  # type: ignore[call-arg]

    @pytest.fixture
    def client(self) -> Any:
        from swarmmind.lemonade.client import LemonadeClient
        return LemonadeClient("http://127.0.0.1:1", timeout=0.1)

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_conductor_fallback(self, config: Any, client: Any) -> None:
        """Conductor.decompose_query returns fallback workers on LLM failure."""
        import asyncio

        from swarmmind.core.conductor import Conductor

        conductor = Conductor(client, config.models.conductor)
        tasks = await asyncio.wait_for(
            conductor.decompose_query("What is happening in AI research?"),
            timeout=5.0,
        )

        # Should fall back to 3 default workers
        assert len(tasks) == 3
        types = {t["worker_type"] for t in tasks}
        assert "rag" in types
        assert "web" in types
        assert "analysis" in types
        for t in tasks:
            assert "task" in t
            assert "context" in t
            assert "reasoning" in t

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_conductor_with_project_context(self, config: Any, client: Any) -> None:
        """Conductor accepts optional project context."""
        import asyncio

        from swarmmind.core.conductor import Conductor

        conductor = Conductor(client, config.models.conductor)
        ctx = {"id": "proj-1", "name": "Test Project"}
        tasks = await asyncio.wait_for(
            conductor.decompose_query("test", project_context=ctx),
            timeout=5.0,
        )
        assert len(tasks) == 3  # fallback

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_synthesis_fallback(self, config: Any, client: Any) -> None:
        """Synthesis.synthesize returns a fallback report on LLM failure."""
        import asyncio

        from swarmmind.core.synthesis import Synthesis

        synthesis = Synthesis(client, config.models.conductor)

        worker_outputs = [
            {
                "worker_type": "web",
                "findings": "Found interesting results.",
                "key_points": ["Point A", "Point B"],
                "sources_cited": ["https://example.com"],
                "confidence": "medium",
                "gaps": [],
            },
        ]

        report = await asyncio.wait_for(
            synthesis.synthesize(worker_outputs, "test query"),
            timeout=5.0,
        )
        # Fallback report must have all expected keys
        assert "title" in report
        assert "executive_summary" in report
        assert "sections" in report
        assert "conclusion" in report
        assert "contradictions" in report
        assert "follow_up_questions" in report
        assert len(report["sections"]) == 1
        assert report["sections"][0]["heading"] == "Worker 1 Findings"

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_synthesis_empty_worker_outputs(self, config: Any, client: Any) -> None:
        """Synthesis handles empty worker outputs gracefully."""
        import asyncio

        from swarmmind.core.synthesis import Synthesis

        synthesis = Synthesis(client, config.models.conductor)
        report = await asyncio.wait_for(
            synthesis.synthesize([], "empty test"),
            timeout=5.0,
        )
        # Fallback with no workers
        assert "title" in report
        assert "sections" in report


# ===================================================================
# Test 8 — Config
# ===================================================================

class TestConfig:
    """Configuration loading and URL generation."""

    def test_default_config(self) -> None:
        """Config loads with correct defaults (user config.toml is isolated by conftest)."""
        from swarmmind.config import Config

        config = Config()  # type: ignore[call-arg]
        assert config.lemonade.host == "localhost"
        assert config.lemonade.port == 13305
        assert config.rag.chunk_size == 512
        assert config.rag.chunk_overlap == 64
        assert config.rag.top_k == 5
        assert config.models.conductor == "Qwen3.6-35B-A3B-GGUF"
        assert config.models.worker == "Gemma-4-12B-it"
        assert config.models.embeddings == "nomic-embed-text-v1-GGUF"
        assert config.models.image == "Flux-2-Klein-4B"
        assert config.models.tts == "kokoro-v1"
        assert config.ui.theme == "light"
        assert config.ui.panel_layout == "balanced"

    def test_custom_lemonade_host(self) -> None:
        from swarmmind.config import Config

        config = Config(  # type: ignore[call-arg]
            lemonade={"host": "192.168.1.100", "port": 8080},
        )
        assert config.lemonade.host == "192.168.1.100"
        assert config.lemonade.port == 8080

    def test_get_lemonade_base_url(self) -> None:
        """Base URL is correct with default config (isolated by conftest)."""
        from swarmmind.config import Config

        config = Config()  # type: ignore[call-arg]
        assert config.get_lemonade_base_url() == "http://localhost:13305"

    def test_custom_base_url(self) -> None:
        from swarmmind.config import Config

        config = Config(  # type: ignore[call-arg]
            lemonade={"host": "10.0.0.50", "port": 9999},
        )
        assert config.get_lemonade_base_url() == "http://10.0.0.50:9999"

    def test_custom_rag_settings(self) -> None:
        from swarmmind.config import Config

        config = Config(  # type: ignore[call-arg]
            rag={"chunk_size": 256, "chunk_overlap": 32, "top_k": 10},
        )
        assert config.rag.chunk_size == 256
        assert config.rag.chunk_overlap == 32
        assert config.rag.top_k == 10

    def test_custom_model_names(self) -> None:
        from swarmmind.config import Config

        config = Config(  # type: ignore[call-arg]
            models={"conductor": "my-model", "embeddings": "my-embed"},
        )
        assert config.models.conductor == "my-model"
        assert config.models.embeddings == "my-embed"
        assert config.models.worker == "Gemma-4-12B-it"

    def test_custom_ui_theme(self) -> None:
        from swarmmind.config import Config

        config = Config(  # type: ignore[call-arg]
            ui={"theme": "dark"},
        )
        assert config.ui.theme == "dark"
        assert config.ui.panel_layout == "balanced"
