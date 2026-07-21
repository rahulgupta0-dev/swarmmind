"""SwarmMind CLI — click-based command-line interface."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from swarmmind import __version__
from swarmmind.config import Config
from swarmmind.core.orchestrator import Orchestrator
from swarmmind.data.database import Database
from swarmmind.data.models import Project
from swarmmind.lemonade.client import LemonadeClient
from swarmmind.benchmark import _benchmark_async
from swarmmind.rag.chroma_store import ChromaStore

console = Console()
DATA_DIR = Path.home() / ".swarmmind"


def _get_config() -> Config:
    """Load and return the application config."""
    return Config()  # type: ignore[call-arg]


def _get_db() -> Database:
    """Return a Database instance."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return Database(str(DATA_DIR / "swarmmind.db"))


def _get_chroma() -> ChromaStore:
    """Return a ChromaStore instance."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return ChromaStore(str(DATA_DIR / "chroma"))


def _get_client(config: Config) -> LemonadeClient:
    """Return a LemonadeClient from config."""
    return LemonadeClient(config.get_lemonade_base_url())


# -----------------------------------------------------------------------
# Main group
# -----------------------------------------------------------------------

@click.group()
@click.version_option(version=__version__, prog_name="swarmmind")
def main() -> None:
    """Lemonade SwarmMind — multi-agent research orchestration."""


# -----------------------------------------------------------------------
# ask — quick research query
# -----------------------------------------------------------------------

@main.command()
@click.argument("query")
@click.option("--project-id", "-p", default=None, help="Project ID for context.")
@click.option("--no-web", is_flag=True, help="Disable web search.")
@click.option("--sequential", is_flag=True, help="Run workers sequentially (safer on low-RAM systems).")
def ask(query: str, project_id: Optional[str], no_web: bool, sequential: bool) -> None:
    """Run a research query and display the report."""
    async def _run() -> None:
        from swarmmind.config import ExecutionConfig

        config = _get_config()
        if sequential:
            config = Config(
                lemonade=config.lemonade,
                rag=config.rag,
                models=config.models,
                execution=ExecutionConfig(mode="sequential", max_concurrent=1),
                ui=config.ui,
            )
        client = _get_client(config)
        chroma = _get_chroma()
        db = _get_db()

        project_context: dict[str, Any] = {}
        if project_id:
            await db.connect()
            project = await db.get_project(project_id)
            if project:
                project_context = project.model_dump()
            await db.close()

        orchestrator = Orchestrator(config, client, chroma_store=chroma)

        try:
            with console.status("[bold green]Researching…") as status:

                def progress(status_str: str, detail: dict[str, Any]) -> None:
                    msg = detail.get("message", status_str)
                    status.update(f"[bold green]{msg}")

                report = await orchestrator.run(
                    query=query,
                    project_context=project_context,
                    web_search_enabled=not no_web,
                    progress_callback=progress,
                )
        except (ConnectionError, OSError) as exc:
            console.print(f"[red]Error: Cannot reach Lemonade server.[/red]")
            console.print(f"[dim]{exc}[/dim]")
            console.print("\n[yellow]Make sure the Lemonade server is running:[/yellow]")
            console.print("[dim]  lemonade-server start[/dim]")
            raise SystemExit(1)
        except Exception as exc:
            console.print(f"[red]Unexpected error: {exc}[/red]")
            raise SystemExit(1)

        # Display the report
        console.print()
        console.print(Panel.fit(f"[bold]{report.get('title', 'Research Report')}", border_style="blue"))
        console.print()

        if report.get("executive_summary"):
            console.print("[bold]Executive Summary[/bold]")
            console.print(Markdown(report["executive_summary"]))
            console.print()

        for section in report.get("sections", []):
            console.print(f"[bold]{section.get('heading', 'Section')}[/bold]")
            console.print(Markdown(section.get("content", "")))
            console.print()

        if report.get("conclusion"):
            console.print("[bold]Conclusion[/bold]")
            console.print(Markdown(report["conclusion"]))
            console.print()

        if report.get("contradictions"):
            console.print("[bold]Contradictions[/bold]")
            for c in report["contradictions"]:
                console.print(f"  • {c}")
            console.print()

        if report.get("follow_up_questions"):
            console.print("[bold]Follow-up Questions[/bold]")
            for q in report["follow_up_questions"]:
                console.print(f"  • {q}")

    asyncio.run(_run())


# -----------------------------------------------------------------------
# project  sub-group
# -----------------------------------------------------------------------

@main.group()
def project() -> None:
    """Manage research projects."""


@project.command()
@click.argument("name")
@click.option("--description", "-d", default="", help="Project description.")
@click.option("--no-web", is_flag=True, help="Disable web search.")
def create(name: str, description: str, no_web: bool) -> None:
    """Create a new project."""
    async def _run() -> None:
        db = _get_db()
        await db.init_db()
        proj = Project(name=name, description=description, web_search_enabled=not no_web)
        proj = await db.create_project(proj)
        await db.close()
        console.print(f"[green]Created project[/green] [bold]{proj.name}[/bold] (id: {proj.id})")

    asyncio.run(_run())


@project.command(name="list")
def list_projects() -> None:
    """List all projects."""
    async def _run() -> None:
        db = _get_db()
        await db.init_db()
        projects = await db.list_projects()
        await db.close()

        if not projects:
            console.print("No projects found. Create one with [bold]swarmmind project create[/bold].")
            return

        table = Table("ID", "Name", "Description", "Sources", "Web Search")
        for p in projects:
            table.add_row(p.id[:8], p.name, p.description[:40], "✓" if p.web_search_enabled else "✗")
        console.print(table)

    asyncio.run(_run())


@project.command()
@click.argument("project-id")
def show(project_id: str) -> None:
    """Show project details."""
    async def _run() -> None:
        db = _get_db()
        await db.init_db()
        proj = await db.get_project(project_id)
        await db.close()

        if not proj:
            console.print("[red]Project not found.[/red]")
            return

        console.print(f"[bold]Name:[/bold] {proj.name}")
        console.print(f"[bold]ID:[/bold] {proj.id}")
        console.print(f"[bold]Description:[/bold] {proj.description or '(none)'}")
        console.print(f"[bold]Web Search:[/bold] {'Enabled' if proj.web_search_enabled else 'Disabled'}")
        console.print(f"[bold]Created:[/bold] {proj.created_at.isoformat()}")
        console.print(f"[bold]Updated:[/bold] {proj.updated_at.isoformat()}")

    asyncio.run(_run())


# -----------------------------------------------------------------------
# source  sub-group
# -----------------------------------------------------------------------

@main.group()
def source() -> None:
    """Manage project sources."""


@source.command()
@click.argument("project-id")
@click.argument("source-type")
@click.argument("source-uri")
@click.option("--name", "-n", default="", help="Display name.")
def add(project_id: str, source_type: str, source_uri: str, name: str) -> None:
    """Add a source to a project."""
    from swarmmind.data.models import Source

    async def _run() -> None:
        db = _get_db()
        await db.init_db()
        src = Source(
            project_id=project_id,
            source_type=source_type,
            source_uri=source_uri,
            display_name=name or source_uri.rsplit("/", 1)[-1],
        )
        src = await db.create_source(src)
        await db.close()
        console.print(f"[green]Added source[/green] [bold]{src.display_name}[/bold] (id: {src.id})")

    asyncio.run(_run())


@source.command(name="list")
@click.argument("project-id")
def list_sources(project_id: str) -> None:
    """List sources for a project."""
    async def _run() -> None:
        db = _get_db()
        await db.init_db()
        sources = await db.list_sources(project_id)
        await db.close()

        if not sources:
            console.print("No sources found for this project.")
            return

        table = Table("ID", "Type", "Name", "Status", "Chars", "Chunks")
        for s in sources:
            table.add_row(s.id[:8], s.source_type, s.display_name[:30], s.status,
                          str(s.char_count), str(s.chunk_count))
        console.print(table)

    asyncio.run(_run())


@source.command()
@click.argument("source-id")
def remove(source_id: str) -> None:
    """Remove a source."""
    async def _run() -> None:
        db = _get_db()
        await db.init_db()
        await db.delete_source(source_id)
        await db.close()
        console.print(f"[green]Removed source[/green] {source_id}")

    asyncio.run(_run())


# -----------------------------------------------------------------------
# report  sub-group
# -----------------------------------------------------------------------

@main.group()
def report() -> None:
    """Manage research reports."""


@report.command(name="list")
@click.argument("project-id")
def list_reports(project_id: str) -> None:
    """List conversations (reports) for a project."""
    async def _run() -> None:
        db = _get_db()
        await db.init_db()
        conversations = await db.list_conversations(project_id)
        await db.close()

        if not conversations:
            console.print("No conversations found for this project.")
            return

        table = Table("ID", "Query", "Web", "Workers", "Date")
        for c in conversations:
            table.add_row(
                c.id[:8],
                c.query[:50],
                "✓" if c.web_search_used else "✗",
                str(c.worker_count),
                c.created_at.isoformat(),
            )
        console.print(table)

    asyncio.run(_run())


@report.command()
@click.argument("conversation-id")
def show_report(conversation_id: str) -> None:
    """Show a conversation/report detail."""
    # For Phase 1 this is a placeholder — full report storage in Phase 2
    console.print("[yellow]Report detail view coming in Phase 2.[/yellow]")


@report.command()
@click.argument("conversation-id")
@click.argument("output-path")
def export(conversation_id: str, output_path: str) -> None:
    """Export a report to a file."""
    console.print("[yellow]Report export coming in Phase 2.[/yellow]")


# -----------------------------------------------------------------------
# config  sub-group
# -----------------------------------------------------------------------

@main.group()
def config() -> None:
    """View or modify configuration."""


@config.command()
def show_config() -> None:
    """Display the current configuration."""
    cfg = _get_config()
    console.print(Panel.fit(json.dumps(cfg.model_dump(), indent=2, default=str), title="Configuration"))
    console.print(f"\nLemonade base URL: [bold]{cfg.get_lemonade_base_url()}[/bold]")


@config.command()
@click.argument("key")
@click.argument("value")
def set_config(key: str, value: str) -> None:
    """Set a config value (dot-notation, e.g. lemonade.port=13305)."""
    console.print("[yellow]Config set via CLI coming in Phase 2. Edit ~/.swarmmind/config.toml directly.[/yellow]")


# -----------------------------------------------------------------------
# web — launch Streamlit UI
# -----------------------------------------------------------------------

@main.command()
def web() -> None:
    """Launch the Streamlit web UI."""
    import sys
    from streamlit.web import cli as stcli

    ui_path = Path(__file__).resolve().parent.parent / "ui" / "app.py"
    sys.argv = ["streamlit", "run", str(ui_path)]
    stcli.main()


# -----------------------------------------------------------------------
# benchmark — cross-backend performance benchmark
# -----------------------------------------------------------------------


@main.command()
@click.option("--prompt", default="Explain the AMD Ryzen AI NPU in 2 sentences.", help="Benchmark prompt.")
@click.option("--iterations", default=3, type=int, help="Number of measurement iterations.")
@click.option("--concurrency", default=4, type=int, help="Concurrent streams in the parallel swarm test.")
@click.option("--out", type=click.Path(path_type=Path), default=None, help="Output file path for Markdown report.")
def benchmark(prompt: str, iterations: int, concurrency: int, out: Path | None) -> None:
    """Run an AMD-cross-backend benchmark against the configured Lemonade server."""
    asyncio.run(_benchmark_async(prompt, iterations, concurrency, out))
