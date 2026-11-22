"""SwarmMind CLI — click-based command-line interface."""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import os
import platform
import re
import sys
import tempfile
from contextlib import contextmanager
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

# Project IDs are free-form strings in the data layer (the default is a UUID).
# The CLI accepts a caller-supplied ID but keeps it URL/shell friendly.
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


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
    return LemonadeClient(
        config.get_lemonade_base_url(),
        api_key=config.lemonade.api_key or None,
    )


def _diagnostics(stderr: bool = False) -> Console:
    """Return a console for diagnostics.

    Machine-readable modes (``ask --json``) pass ``stderr=True`` so stdout
    stays pure JSON; every warning and error then lands on stderr instead.
    """
    return Console(stderr=stderr)


# -----------------------------------------------------------------------
# Main group
# -----------------------------------------------------------------------

@click.group(
    epilog=(
        "\b\n"
        "Quick start:\n"
        '  swarmmind ask "What is AMD Ryzen AI?"\n'
        "  swarmmind web\n"
        "  swarmmind eval --report"
    ),
)
@click.version_option(version=__version__, prog_name="swarmmind")
def main() -> None:
    """Lemonade SwarmMind — multi-agent research orchestration."""


# -----------------------------------------------------------------------
# ask — quick research query
# -----------------------------------------------------------------------

@main.command(
    epilog=(
        "\b\n"
        "Examples:\n"
        '  swarmmind ask "What is AMD Ryzen AI?"\n'
        '  swarmmind ask "Compare RAG vs fine-tuning" --no-web --sequential\n'
        '  swarmmind ask "Summarise my sources" --project-id my-proj --json'
    ),
)
@click.argument("query")
@click.option("--project-id", "-p", default=None, help="Scope the query to a project's sources by ID.")
@click.option("--no-web", is_flag=True, help="Disable web search for this query.")
@click.option("--sequential", is_flag=True, help="Run workers sequentially (safer on low-RAM systems).")
@click.option("--json", "as_json", is_flag=True, help="Print the report as pure JSON on stdout (diagnostics go to stderr).")
def ask(query: str, project_id: Optional[str], no_web: bool, sequential: bool, as_json: bool) -> None:
    """Run a research query and display the report.

    With --json, only the report is written to stdout; warnings and errors go to
    stderr so the output can be piped into other tools.
    """
    diag = _diagnostics(stderr=as_json)

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
                # model_dump_json() serialises datetimes to strings; the
                # conductor embeds this dict in a JSON prompt.
                project_context = json.loads(project.model_dump_json())
            else:
                diag.print(
                    f"[yellow]Warning: project '{project_id}' does not exist; "
                    "continuing without project context.[/yellow]"
                )
            await db.close()

        orchestrator = Orchestrator(config, client, chroma_store=chroma)

        try:
            if as_json:
                # Machine-readable mode: keep stdout free of live status output.
                report = await orchestrator.run(
                    query=query,
                    project_context=project_context,
                    web_search_enabled=not no_web,
                )
            else:
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
            diag.print("[red]Error: Cannot reach Lemonade server.[/red]")
            diag.print(f"[dim]{exc}[/dim]")
            diag.print("\n[yellow]Make sure the Lemonade server is running:[/yellow]")
            diag.print("[dim]  lemonade-server start[/dim]")
            raise SystemExit(1)
        except Exception as exc:
            diag.print(f"[red]Unexpected error: {exc}[/red]")
            raise SystemExit(1)

        if as_json:
            click.echo(json.dumps(report, default=str))
            return

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


@project.command(
    epilog=(
        "\b\n"
        "Examples:\n"
        '  swarmmind project create "AMD Research"\n'
        '  swarmmind project create "AMD Research" --id amd-research '
        '-d "Local notes"'
    ),
)
@click.argument("name")
@click.option("--description", "-d", default="", help="A short project description.")
@click.option("--no-web", is_flag=True, help="Disable web search for this project.")
@click.option(
    "--id", "-i", "project_id", default=None,
    help="Custom project ID (letters, digits, '.', '_' and '-'; default: auto-generated UUID).",
)
def create(name: str, description: str, no_web: bool, project_id: Optional[str]) -> None:
    """Create a new project with an auto-generated ID, or supply --id."""
    if project_id is not None and not _PROJECT_ID_RE.fullmatch(project_id):
        raise click.BadParameter(
            "use only letters, digits, '.', '_' and '-' (max 128 chars, "
            "starting with a letter or digit)",
            param_hint="--id",
        )

    async def _run() -> None:
        db = _get_db()
        try:
            await db.init_db()
            if project_id and await db.get_project(project_id) is not None:
                console.print(
                    f"[red]Error: A project with ID '{project_id}' already exists. "
                    "Choose a different --id.[/red]"
                )
                raise SystemExit(1)
            proj = Project(
                id=project_id or "",
                name=name,
                description=description,
                web_search_enabled=not no_web,
            )
            proj = await db.create_project(proj)
        finally:
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
            console.print(
                f"[red]Error: Project '{project_id}' does not exist. "
                "Use 'swarmmind project list' to see available IDs.[/red]"
            )
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
    """Add a source to a project and ingest it so RAG can find it immediately."""
    from swarmmind.data.models import Source
    from swarmmind.rag.pipeline import Pipeline

    async def _run() -> None:
        db = _get_db()
        try:
            await db.init_db()
            if await db.get_project(project_id) is None:
                console.print(
                    f"[red]Error: Project '{project_id}' does not exist. "
                    "Create it first with `swarmmind project create NAME "
                    f"--id {project_id}`.[/red]"
                )
                raise SystemExit(1)
            src = Source(
                project_id=project_id,
                source_type=source_type,
                source_uri=source_uri,
                display_name=name or source_uri.rsplit("/", 1)[-1],
            )
            src = await db.create_source(src)
        finally:
            await db.close()

        # Ingest synchronously. Raw `text` sources are passed as bytes (the
        # pipeline's str path is interpreted as a file path).
        try:
            ingest_input: str | bytes = (
                source_uri.encode("utf-8") if source_type == "text" else source_uri
            )
            result = await Pipeline(_get_config(), _get_chroma()).process_source(
                source_type, ingest_input, src.id, project_id,
            )
        except Exception as exc:  # noqa: BLE001 - report errors, never a traceback
            result = {"status": "error", "error": str(exc), "char_count": 0, "chunk_count": 0}

        if result.get("status") != "ready":
            error = result.get("error") or "unknown ingestion error"
            db = _get_db()
            try:
                await db.init_db()
                await db.update_source_status(
                    src.id, "error", error_message=str(error),
                )
            finally:
                await db.close()
            console.print(f"[red]Error: Could not ingest source: {error}[/red]")
            raise SystemExit(1)

        chunks = int(result.get("chunk_count", 0))
        chars = int(result.get("char_count", 0))
        db = _get_db()
        try:
            await db.init_db()
            await db.update_source_status(
                src.id, "ready", char_count=chars, chunk_count=chunks,
            )
        finally:
            await db.close()
        console.print(
            f"[green]Added source[/green] [bold]{src.display_name}[/bold] (id: {src.id}) "
            f"— status: ready, {chunks} chunk(s), {chars} chars"
        )

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


def _load_report(conversation_id: str) -> dict:
    """Load a stored report dict for a conversation id (raises if missing)."""
    db = _get_db()
    try:
        asyncio.run(db.init_db())
        conv = asyncio.run(db.get_conversation(conversation_id))
        if conv is None:
            console.print(f"[red]No conversation found with id '{conversation_id}'. "
                          "Use `swarmmind report list <project-id>`.[/red]")
            raise SystemExit(1)
        if not conv.report_json:
            console.print("[yellow]This conversation has no stored report.[/yellow]")
            raise SystemExit(1)
        try:
            return json.loads(conv.report_json)
        except json.JSONDecodeError:
            console.print("[yellow]Stored report JSON is corrupt.[/yellow]")
            raise SystemExit(1)
    finally:
        asyncio.run(db.close())


def _grounding_line(report: dict) -> str:
    """One-line grounding summary (handles legacy reports with no grounding)."""
    grounding = report.get("grounding")
    if not isinstance(grounding, dict):
        claims = report.get("claims") or []
        supported = sum(1 for c in claims if isinstance(c, dict) and c.get("status") == "supported")
        grounding = {"total_claims": len(claims), "supported": supported}
    total = grounding.get("total_claims", 0) or 0
    if not total:
        return "No claims recorded — verify unsupported statements before use."
    return (
        f"{grounding.get('supported', 0)}/{total} claims supported · "
        f"{grounding.get('unverified', 0)} unverified · "
        f"{grounding.get('contradicted', 0)} contradicted"
    )


def _manifest_lines(report: dict) -> list[str]:
    """Markdown run-manifest lines for export/console."""
    lines = [
        f"- Stop reason: `{report.get('stop_reason', 'n/a')}`",
        f"- Rounds used: `{report.get('rounds_used', 1)}`",
        f"- Timestamp: {report.get('timestamp', 'n/a')}",
    ]
    if report.get("query"):
        lines.append(f"- Query: {report['query']}")
    return lines


def _sources_markdown(report: dict) -> list[str]:
    """Markdown Sources appendix with title/kind/url/snippet."""
    sources = report.get("sources") or []
    if not isinstance(sources, list) or not sources:
        return ["## Sources", "", "_No sources were retrieved for this query._", ""]
    lines = ["## Sources", ""]
    for raw in sources:
        src = raw if isinstance(raw, dict) else {"id": "S?", "title": str(raw), "url": str(raw), "kind": "web"}
        sid = src.get("id", "S?")
        lines.append(
            f"- **[{sid}] {src.get('title', 'Untitled source')}** — "
            f"`{src.get('kind', 'model')}`"
            + (f" — {src.get('url')}" if src.get("url") else "")
        )
        snippet = " ".join(str(src.get("snippet", "")).split())
        if snippet:
            lines.append(f"  > {snippet}")
    lines.append("")
    return lines


def _render_report(report: dict) -> None:
    """Print a stored report to the console (rich)."""
    console.print(Panel.fit(f"[bold]{report.get('title', 'Research Report')}", border_style="blue"))
    if report.get("executive_summary"):
        console.print("\n[bold cyan]Executive Summary[/bold cyan]")
        console.print(Markdown(report["executive_summary"]))
    for section in report.get("sections", []):
        console.print(f"\n[bold cyan]{section.get('heading', 'Section')}[/bold cyan]")
        console.print(Markdown(section.get("content", "")))
        if section.get("sources"):
            console.print("[dim]Sources:[/dim] " + ", ".join(section["sources"]))
    if report.get("conclusion"):
        console.print("\n[bold cyan]Conclusion[/bold cyan]")
        console.print(Markdown(report["conclusion"]))
    if report.get("contradictions"):
        console.print("\n[bold yellow]Contradictions[/bold yellow]")
        for c in report["contradictions"]:
            console.print(f"- {c}")
    if report.get("follow_up_questions"):
        console.print("\n[bold magenta]Follow-up Questions[/bold magenta]")
        for q in report["follow_up_questions"]:
            console.print(f"- {q}")

    # WP3 — grounding, Sources appendix and run manifest.
    console.print("\n[bold cyan]Grounding[/bold cyan]")
    console.print(_grounding_line(report))

    sources = report.get("sources") or []
    if isinstance(sources, list) and sources:
        console.print("\n[bold cyan]Sources[/bold cyan]")
        table = Table("ID", "Kind", "Title", "URL / path", "Snippet")
        for raw in sources:
            src = raw if isinstance(raw, dict) else {
                "id": "S?", "kind": "web", "title": str(raw), "url": str(raw),
            }
            table.add_row(
                str(src.get("id", "S?")),
                str(src.get("kind", "model")),
                str(src.get("title", ""))[:40],
                str(src.get("url", ""))[:50],
                " ".join(str(src.get("snippet", "")).split())[:60],
            )
        console.print(table)

    console.print("\n[bold cyan]Run Manifest[/bold cyan]")
    for line in _manifest_lines(report):
        console.print(line)


@report.command(name="show")
@click.argument("conversation-id")
def show_report(conversation_id: str) -> None:
    """Show a stored conversation/report detail."""
    report_dict = _load_report(conversation_id)
    _render_report(report_dict)


@report.command()
@click.argument("conversation-id")
@click.argument("output-path", type=click.Path(dir_okay=False))
def export(conversation_id: str, output_path: str) -> None:
    """Export a stored report to a Markdown file."""
    report_dict = _load_report(conversation_id)

    lines = [
        f"# {report_dict.get('title', 'Research Report')}",
        "",
    ]
    if report_dict.get("executive_summary"):
        lines += ["## Executive Summary", "", report_dict["executive_summary"], ""]
    for section in report_dict.get("sections", []):
        lines += [f"## {section.get('heading', 'Section')}", "", section.get("content", ""), ""]
        if section.get("sources"):
            lines += ["**Sources:** " + ", ".join(section["sources"]), ""]
    if report_dict.get("conclusion"):
        lines += ["## Conclusion", "", report_dict["conclusion"], ""]
    if report_dict.get("contradictions"):
        lines += ["## Contradictions", ""]
        lines += [f"- {c}" for c in report_dict["contradictions"]]
        lines.append("")
    if report_dict.get("follow_up_questions"):
        lines += ["## Follow-up Questions", ""]
        lines += [f"- {q}" for q in report_dict["follow_up_questions"]]
        lines.append("")

    lines += ["## Grounding", "", _grounding_line(report_dict), ""]
    lines += _sources_markdown(report_dict)
    lines += ["## Run Manifest", ""] + _manifest_lines(report_dict) + [""]

    md = "\n".join(lines).strip() + "\n"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md)
    console.print(f"[green]Report exported to {output_path}[/green]")


# -----------------------------------------------------------------------
# config  sub-group
# -----------------------------------------------------------------------

@main.group()
def config() -> None:
    """View or modify configuration, including the Lemonade API key."""


@config.command()
def show_config() -> None:
    """Display the current configuration (including lemonade.api_key)."""
    cfg = _get_config()
    console.print(Panel.fit(json.dumps(cfg.model_dump(), indent=2, default=str), title="Configuration"))
    console.print(f"\nLemonade base URL: [bold]{cfg.get_lemonade_base_url()}[/bold]")


@config.command()
@click.argument("key")
@click.argument("value")
def set_config(key: str, value: str) -> None:
    """Set a config value using dot notation (e.g. lemonade.port=13305)."""
    console.print(
        "[yellow]Config edits via the CLI are not available yet. Edit "
        "~/.swarmmind/config.toml directly (for example, lemonade.api_key) "
        "or export LEMONADE_API_KEY.[/yellow]"
    )


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
    """Run an AMD cross-backend benchmark against the configured Lemonade server."""
    asyncio.run(_benchmark_async(prompt, iterations, concurrency, out))


# -----------------------------------------------------------------------
# eval — golden-set quality evaluation (deterministic-primary)
# -----------------------------------------------------------------------


@main.command(
    name="eval",
    epilog=(
        "\b\n"
        "Examples:\n"
        "  swarmmind eval\n"
        "  swarmmind eval --report --judge\n"
        "  swarmmind eval --output eval-report.md"
    ),
)
@click.option(
    "--report", "show_report", is_flag=True,
    help="Print the Markdown evaluation report to stdout.",
)
@click.option(
    "--output", type=click.Path(path_type=Path), default=None,
    help="Write the Markdown evaluation report to PATH.",
)
@click.option(
    "--judge", is_flag=True,
    help="Enable the secondary LLM judge (a different, blinded model).",
)
def eval_cmd(show_report: bool, output: Path | None, judge: bool) -> None:
    """Evaluate answer quality against the local golden set.

    Primary metrics are deterministic and fully offline. With --judge, an
    optional secondary LLM judge (a different model, blinded, order-randomized)
    adds approximate faithfulness and answer-relevance scores.
    """
    from swarmmind.eval import render_report, run_eval

    result = run_eval(enable_judge=judge)
    markdown = render_report(result)

    if output is not None:
        output.write_text(markdown, encoding="utf-8")
        console.print(f"[green]Evaluation report saved to {output}[/green]")

    if show_report or output is None:
        # Plain echo preserves the Markdown table (Rich would wrap cells).
        click.echo(markdown)


# -----------------------------------------------------------------------
# doctor — newcomer-friendly installation / setup diagnostics
# -----------------------------------------------------------------------

_DOCTOR_IMPORTS = (
    "pydantic", "httpx", "chromadb", "streamlit", "duckduckgo_search",
    "aiosqlite", "click", "rich",
)
_DOCTOR_LABELS = {
    "python": "Python version",
    "imports": "Critical imports",
    "config": "Configuration",
    "data_dir": "Data directory",
    "lemonade_health": "Lemonade server",
    "models": "Models / Omni",
    "capabilities": "Capabilities",
    "backends": "Backends",
    "system_info": "Hardware / backend",
}
_DOCTOR_NEXT_STEPS = (
    "swarmmind doctor",
    'swarmmind ask "What is AMD Ryzen AI?"',
    "swarmmind web",
    "bash tests/smoke_test.sh",
)
_DOCTOR_TIMEOUT = 2.0
_DOCTOR_HINT = (
    "Start Lemonade with `lemonade-server start`. If you have no AMD hardware, "
    "run the mock server instead: `python tests/mock_lemonade_server.py`."
)


def _doctor_check(name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    """Build one doctor result row."""
    row: dict[str, Any] = {
        "name": name,
        "label": _DOCTOR_LABELS.get(name, name),
        "status": status,
        "detail": detail,
    }
    row.update(extra)
    return row


def _mask_api_key(key: str) -> str:
    """Return a redacted representation of *key* — never the key itself."""
    if not key:
        return "(not set)"
    if len(key) <= 4:
        return "****"
    return f"****{key[-2:]}"


def _is_omni_model(name: str) -> bool:
    """Mirror ``LemonadeClient.is_omni_model`` without needing a client instance."""
    lowered = name.lower()
    return "omni" in lowered or lowered.startswith("lmx-omni-")


def _doctor_client(config: Config) -> LemonadeClient:
    """Build a short-timeout client for doctor probes (offline-safe)."""
    return LemonadeClient(
        config.get_lemonade_base_url(),
        api_key=config.lemonade.api_key or None,
        timeout=_DOCTOR_TIMEOUT,
    )


@contextmanager
def _quiet_client_logs():
    """Hide the client's raw request-error logs; doctor reports them itself."""
    logger = logging.getLogger("swarmmind.lemonade.client")
    previous = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(previous)


def _check_python() -> dict[str, Any]:
    version = platform.python_version()
    ok = sys.version_info >= (3, 11)
    return _doctor_check(
        "python", "PASS" if ok else "FAIL",
        f"Python {version} (3.11+ required)",
        version=version,
    )


def _import_for_doctor(module_name: str) -> Any:
    """Import a dependency (seam kept small so doctor tests can fake failures)."""
    return importlib.import_module(module_name)


def _check_imports() -> dict[str, Any]:
    missing: list[str] = []
    for module_name in _DOCTOR_IMPORTS:
        try:
            _import_for_doctor(module_name)
        except Exception as exc:  # noqa: BLE001 - report any import failure
            missing.append(f"{module_name} ({type(exc).__name__})")
    if missing:
        return _doctor_check(
            "imports", "FAIL", "Missing packages: " + ", ".join(missing),
            hint='pip install -e ".[dev]"', missing=missing,
        )
    return _doctor_check("imports", "PASS", f"{len(_DOCTOR_IMPORTS)} critical packages available")


def _check_config() -> tuple[dict[str, Any], Config | None]:
    config_path = Path.home() / ".swarmmind" / "config.toml"
    try:
        cfg = _get_config()
    except Exception as exc:  # noqa: BLE001 - config errors must not escape
        return _doctor_check(
            "config", "FAIL", f"Could not load config: {exc}",
            hint="Create ~/.swarmmind/config.toml or fix the TOML syntax.",
            path=str(config_path),
        ), None

    lemonade = cfg.lemonade
    web = cfg.web
    models = cfg.models
    api_key = getattr(lemonade, "api_key", "") or ""
    masked = _mask_api_key(api_key)
    omni = getattr(models, "omni", "") or ""
    exists = config_path.is_file()
    detail = (
        f"{'Loaded ' + str(config_path) if exists else 'Defaults (no config.toml found)'} — "
        f"Lemonade {lemonade.host}:{lemonade.port}, api_key {masked}, "
        f"web.fetch_pages={web.fetch_pages}, models.omni={omni or '(not set)'}"
    )
    return _doctor_check(
        "config", "PASS", detail,
        path=str(config_path), file_exists=exists,
        host=lemonade.host, port=lemonade.port,
        api_key_present=bool(api_key), api_key_masked=masked,
        web_fetch_pages=bool(web.fetch_pages), models_omni=omni,
    ), cfg


def _check_data_dir() -> dict[str, Any]:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fd, probe_name = tempfile.mkstemp(prefix=".doctor-", dir=str(DATA_DIR))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as probe:
                probe.write("ok")
        finally:
            Path(probe_name).unlink(missing_ok=True)
        return _doctor_check("data_dir", "PASS", f"Writable: {DATA_DIR}", path=str(DATA_DIR))
    except Exception as exc:  # noqa: BLE001 - permission errors must not escape
        return _doctor_check(
            "data_dir", "FAIL", f"Cannot write to {DATA_DIR}: {exc}",
            hint=f"Create {DATA_DIR} and make sure the current user can write to it.",
            path=str(DATA_DIR),
        )


def _parse_system_info(info: Any) -> dict[str, Any]:
    """Extract a friendly backend/hardware summary from /v1/system-info."""
    if not isinstance(info, dict) or not info:
        return {"available": False, "backend": "unknown", "hardware": "unknown", "mock": False}
    mock = "mock" in json.dumps(info, default=str).lower()
    names: list[str] = []
    backends: list[str] = []
    devices = info.get("devices")
    if isinstance(devices, list):
        for device in devices:
            if not isinstance(device, dict):
                continue
            names.append(str(device.get("name") or device.get("type") or "unknown"))
            if device.get("backend"):
                backends.append(str(device["backend"]))
    hardware = ", ".join(names) if names else str(info.get("cpu") or info.get("os") or "unknown")
    backend = str(info.get("backend") or (backends[0] if backends else "unknown"))
    return {"available": True, "backend": backend, "hardware": hardware, "mock": mock}


def _check_models(models: Any) -> dict[str, Any]:
    if isinstance(models, dict):
        models = models.get("data", [])
    if not isinstance(models, list):
        return _doctor_check("models", "WARN", "Could not parse the /v1/models response")
    names = [
        str(m.get("id") or m.get("name") or "")
        for m in models if isinstance(m, dict)
    ]
    omni = [name for name in names if _is_omni_model(name)]
    if omni:
        return _doctor_check(
            "models", "PASS",
            f"{len(models)} model(s) visible; Omni: {', '.join(omni[:3])}",
            count=len(models), omni_visible=True, omni_models=omni,
        )
    return _doctor_check(
        "models", "WARN",
        f"{len(models)} model(s) visible, but no Omni model was found",
        hint="Set models.omni in ~/.swarmmind/config.toml to an Omni collection name.",
        count=len(models), omni_visible=False, omni_models=[],
    )


# Capability reporting (Lemonade v2026.48+ /api/show metadata).  Tokens are
# normalised to lower-case hyphenated form and mapped to one compact bucket.
_DOCTOR_CAPABILITY_ALIASES = {
    "vision": "vision", "visual": "vision", "image-input": "vision",
    "image_input": "vision", "multimodal": "vision",
    "image": "image", "image-generation": "image", "text-to-image": "image",
    "diffusion": "image", "text2image": "image",
    "tts": "tts", "audio": "tts", "speech": "tts", "voice": "tts",
    "text-to-speech": "tts", "speech-generation": "tts",
    "transcription": "transcription", "transcribe": "transcription",
    "speech-to-text": "transcription", "stt": "transcription",
    "asr": "transcription", "audio-transcription": "transcription",
    "tool-calling": "tool_calling", "function-calling": "tool_calling",
    "tools": "tool_calling", "tool_use": "tool_calling",
    "omni": "omni", "omni-collection": "omni", "omnimodal": "omni",
}
_DOCTOR_CAPABILITY_MATCHERS = (
    ("transcription", ("transcri", "speech-to-text", "speech recognition", "stt", "asr")),
    ("tool_calling", ("tool-call", "function-call", "tools", "tool_use", "function")),
    ("vision", ("vision", "visual", "image-input")),
    ("image", ("image", "diffusion", "text2image")),
    ("tts", ("tts", "text-to-speech", "speech", "audio", "voice")),
    ("omni", ("omni",)),
)
_DOCTOR_CAPABILITY_FIELDS = ("capabilities", "labels", "metadata")


def _doctor_flatten_capabilities(value: Any) -> list[str]:
    """Flatten a labels/capabilities value into individual token strings."""
    out: list[str] = []
    if isinstance(value, str):
        out.extend(part for part in re.split(r"[,\s|]+", value) if part)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            out.extend(_doctor_flatten_capabilities(item))
    elif isinstance(value, dict):
        for key, val in value.items():
            if isinstance(val, bool):
                if val:
                    out.append(str(key))
                continue
            out.append(str(key))
            out.extend(_doctor_flatten_capabilities(val))
    return out


def _doctor_capability_bucket(token: str) -> str | None:
    """Map a capability token to a compact reporting bucket."""
    normalised = re.sub(r"[_\s]+", "-", str(token).strip().lower()).strip("-")
    if not normalised:
        return None
    if normalised in _DOCTOR_CAPABILITY_ALIASES:
        return _DOCTOR_CAPABILITY_ALIASES[normalised]
    for bucket, needles in _DOCTOR_CAPABILITY_MATCHERS:
        if any(needle in normalised for needle in needles):
            return bucket
    return None


def _check_capabilities(models: Any) -> dict[str, Any]:
    """Report which Omni-style capabilities the server advertises."""
    if isinstance(models, dict):
        models = models.get("data", [])
    if not isinstance(models, list):
        models = []

    counts = {
        "vision": 0, "image": 0, "tts": 0,
        "transcription": 0, "tool_calling": 0, "omni": 0,
    }
    has_metadata = False
    inspected = 0
    omni_models: list[str] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        inspected += 1
        name = str(model.get("id") or model.get("name") or "")
        if name and _is_omni_model(name):
            omni_models.append(name)
        tokens = [
            token
            for field in _DOCTOR_CAPABILITY_FIELDS
            for token in _doctor_flatten_capabilities(model.get(field))
        ]
        buckets: set[str] = set()
        for token in tokens:
            bucket = _doctor_capability_bucket(token)
            if bucket:
                buckets.add(bucket)
        if buckets:
            has_metadata = True
        for bucket in buckets:
            counts[bucket] += 1
    if omni_models:
        counts["omni"] = max(counts["omni"], len(omni_models))

    extra = {
        "capabilities": counts,
        "metadata_available": has_metadata,
        "models_inspected": inspected,
        "omni_models": omni_models,
    }
    if not has_metadata:
        # Informational only: an older/mock server simply lacks the metadata.
        return _doctor_check(
            "capabilities", "WARN", "No capability metadata reported by server", **extra,
        )
    summary = (
        f"vision={counts['vision']}, image={counts['image']}, "
        f"tts={counts['tts']}, omni={counts['omni']} "
        f"(transcription={counts['transcription']}, tool-calling={counts['tool_calling']})"
    )
    return _doctor_check("capabilities", "PASS", summary, **extra)


def _client_supports_show_all(client: Any) -> bool:
    """True when the client's get_models accepts a ``show_all`` argument."""
    getter = getattr(client, "get_models", None)
    if getter is None:
        return False
    try:
        params = inspect.signature(getter).parameters
    except (TypeError, ValueError):
        return False
    return "show_all" in params or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values()
    )


def _doctor_backend_names(info: Any) -> list[str]:
    """Extract backend names from ``data.backends`` / ``backends``."""
    if not isinstance(info, dict):
        return []
    raw = info.get("backends")
    if raw is None:
        data = info.get("data")
        if isinstance(data, dict):
            raw = data.get("backends")
        elif isinstance(data, list):
            raw = data
    if raw is None:
        return []

    names: list[str] = []
    if isinstance(raw, dict):
        for key, val in raw.items():
            if isinstance(val, bool) and not val:
                continue
            names.append(str(key))
    elif isinstance(raw, str):
        names.extend(part for part in re.split(r"[,\s]+", raw) if part)
    elif isinstance(raw, (list, tuple, set)):
        for item in raw:
            if isinstance(item, dict):
                name = (
                    item.get("name") or item.get("backend") or
                    item.get("id") or item.get("value")
                )
                if name:
                    names.append(str(name))
            elif item:
                names.append(str(item))

    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            unique.append(name)
    return unique


def _check_backends(info: Any) -> dict[str, Any]:
    """Report the backends advertised by /v1/system-info (informational)."""
    names = _doctor_backend_names(info)
    if not names:
        return _doctor_check(
            "backends", "WARN",
            "No backend list reported by /v1/system-info (mock or unsupported server)",
            backends=[],
        )
    return _doctor_check(
        "backends", "PASS", "Available backends: " + ", ".join(names),
        backends=names,
    )


def _check_system_info(info: Any) -> dict[str, Any]:
    summary = _parse_system_info(info)
    if not summary["available"]:
        return _doctor_check(
            "system_info", "WARN",
            "Hardware/backend unknown — /v1/system-info is unavailable (mock or unsupported server)",
            **summary,
        )
    if summary["mock"] or summary["backend"] == "unknown":
        return _doctor_check(
            "system_info", "WARN",
            f"Mock/unknown hardware — backend {summary['backend']}, devices {summary['hardware']} "
            "(system-info is not representative of real hardware)",
            **summary,
        )
    return _doctor_check(
        "system_info", "PASS",
        f"Backend {summary['backend']}, devices {summary['hardware']}",
        **summary,
    )


@main.command(
    epilog=(
        "\b\n"
        "Examples:\n"
        "  swarmmind doctor\n"
        "  swarmmind doctor --json"
    ),
)
@click.option(
    "--json", "as_json", is_flag=True,
    help="Print pure JSON on stdout (diagnostics go to stderr).",
)
def doctor(as_json: bool) -> None:
    """Check your installation and give actionable next steps.

    Verifies Python, dependencies, config, data directory and the Lemonade
    server (health, models, hardware) without making long network waits.
    """
    diag = _diagnostics(stderr=True)
    capabilities_summary: dict[str, int] = {}
    backends_list: list[str] = []

    checks: list[dict[str, Any]] = [
        _check_python(),
        _check_imports(),
    ]
    config_check, config = _check_config()
    checks.append(config_check)
    checks.append(_check_data_dir())

    if config is None:
        checks.append(_doctor_check(
            "lemonade_health", "FAIL", "Skipped — configuration could not be loaded",
            hint=_DOCTOR_HINT,
        ))
        checks.append(_doctor_check("models", "WARN", "Skipped — Lemonade server unavailable"))
        checks.append(_doctor_check("system_info", "WARN", "Skipped — Lemonade server unavailable"))
    else:
        client = _doctor_client(config)
        try:
            with _quiet_client_logs():
                health = asyncio.run(client.health_check())
        except Exception as exc:  # noqa: BLE001 - never leak a traceback
            health = {"status": "error", "detail": str(exc)}
        reachable = isinstance(health, dict) and health.get("status") == "ok"
        if reachable:
            checks.append(_doctor_check(
                "lemonade_health", "PASS",
                f"Lemonade reachable at {config.get_lemonade_base_url()}",
            ))
        else:
            detail = (health.get("detail") or "unreachable") if isinstance(health, dict) else str(health)
            hint = _DOCTOR_HINT
            if isinstance(health, dict) and health.get("status") == "forbidden":
                hint = (
                    "Lemonade rejected authentication. Set LEMONADE_API_KEY "
                    "(or lemonade.api_key in ~/.swarmmind/config.toml). " + hint
                )
            checks.append(_doctor_check(
                "lemonade_health", "FAIL",
                f"Cannot reach Lemonade at {config.get_lemonade_base_url()}: {detail}",
                hint=hint,
            ))

        if not reachable:
            checks.append(_doctor_check("models", "WARN", "Skipped — Lemonade server unavailable"))
            checks.append(_doctor_check("system_info", "WARN", "Skipped — Lemonade server unavailable"))
        else:
            try:
                with _quiet_client_logs():
                    models = asyncio.run(client.get_models())
                checks.append(_check_models(models))
            except Exception as exc:  # noqa: BLE001
                checks.append(_doctor_check("models", "WARN", f"Could not list models: {exc}"))
                models = []

            # Informational: richer /v1/models metadata when the client can
            # ask for show_all; otherwise reuse the list already fetched above.
            capability_models = models
            if _client_supports_show_all(client):
                try:
                    with _quiet_client_logs():
                        capability_models = asyncio.run(client.get_models(show_all=True))
                except Exception:  # noqa: BLE001 - never print raw exceptions here
                    capability_models = models
            capability_check = _check_capabilities(capability_models)
            checks.append(capability_check)
            capabilities_summary = capability_check.get("capabilities", {})

            try:
                with _quiet_client_logs():
                    info = asyncio.run(client.system_info())
            except Exception:  # noqa: BLE001
                info = {}
            backends_check = _check_backends(info)
            checks.append(backends_check)
            backends_list = backends_check.get("backends", [])
            checks.append(_check_system_info(info))

    counts = {
        status: sum(1 for check in checks if check["status"] == status)
        for status in ("PASS", "WARN", "FAIL")
    }
    summary = f"{counts['PASS']} passed, {counts['WARN']} warning(s), {counts['FAIL']} failed"
    payload = {
        "status": "fail" if counts["FAIL"] else "ok",
        "checks": checks,
        "summary": counts,
        "next_steps": list(_DOCTOR_NEXT_STEPS),
        "capabilities": capabilities_summary,
        "backends": backends_list,
    }

    if as_json:
        # Only the JSON document goes to stdout; every diagnostic goes to stderr.
        for check in checks:
            if check["status"] != "PASS":
                diag.print(f"{check['status']} {check['name']}: {check['detail']}")
                if check.get("hint"):
                    diag.print(f"  Hint: {check['hint']}")
        click.echo(json.dumps(payload, indent=2))
    else:
        click.echo("SwarmMind doctor")
        click.echo("================")
        for check in checks:
            click.echo(f"[{check['status']}] {check['label']}: {check['detail']}")
            if check.get("hint"):
                click.echo(f"       -> {check['hint']}")
        click.echo()
        click.echo(f"Summary: {summary}")
        click.echo("Next steps:")
        for step in _DOCTOR_NEXT_STEPS:
            click.echo(f"  {step}")

    if counts["FAIL"]:
        raise SystemExit(1)
