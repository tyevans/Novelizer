from __future__ import annotations
import asyncio
import signal as _signal
from typing import Optional
import click
from rich.console import Console
from rich.table import Table
from novelizer.config import Settings
from novelizer.store.models import (
    DirectorSignal, EditorialStatus, RetconStatus, SignalKind,
)
from novelizer.store.queries import Store

console = Console()


def _get_store(settings: Settings) -> Store:
    return Store(
        db_path=settings.db_path,
        chroma_path=settings.chroma_path,
        embed_model=settings.embed_model,
    )


async def _with_store(settings: Settings, fn):
    store = _get_store(settings)
    await store.init()
    try:
        return await fn(store)
    finally:
        await store.close()


@click.group()
@click.pass_context
def cli(ctx):
    ctx.ensure_object(dict)
    ctx.obj["settings"] = Settings()


@cli.command()
@click.argument("text")
@click.pass_context
def seed(ctx, text: str):
    """Inject a narrative seed into the world."""
    settings = ctx.obj["settings"]

    async def _run_inner(store):
        sig = DirectorSignal(kind=SignalKind.seed, body=text)
        await store.save_director_signal(sig)
        console.print(f"[green]Seed injected:[/green] {text}")

    asyncio.run(_with_store(settings, _run_inner))


@cli.command()
@click.argument("entity")
@click.pass_context
def focus(ctx, entity: str):
    """Set narrative focus on an entity or topic."""
    settings = ctx.obj["settings"]

    async def _run_inner(store):
        sig = DirectorSignal(kind=SignalKind.focus, body=entity)
        await store.save_director_signal(sig)
        console.print(f"[green]Focus set:[/green] {entity}")

    asyncio.run(_with_store(settings, _run_inner))


@cli.command()
@click.argument("agent_name")
@click.pass_context
def pause(ctx, agent_name: str):
    """Pause a named agent (takes effect on next scheduler tick when run is active)."""
    console.print(f"[yellow]Note:[/yellow] pause/resume only takes effect in 'novelizer run'.")


@cli.command()
@click.argument("agent_name")
@click.pass_context
def resume(ctx, agent_name: str):
    """Resume a paused agent."""
    console.print(f"[yellow]Note:[/yellow] pause/resume only takes effect in 'novelizer run'.")


@cli.command()
@click.pass_context
def retcons(ctx):
    """List open retcon requests."""
    settings = ctx.obj["settings"]

    async def _run_inner(store):
        reqs = await store.list_retcon_requests(status=RetconStatus.open)
        if not reqs:
            console.print("No open retcon requests.")
            return
        table = Table(title="Open Retcon Requests")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Description")
        table.add_column("Proposed Resolution")
        for req in reqs:
            table.add_row(req.id[:8], req.description, req.proposed_resolution)
        console.print(table)

    asyncio.run(_with_store(settings, _run_inner))


@cli.command("retcon-approve")
@click.argument("retcon_id")
@click.pass_context
def retcon_approve(ctx, retcon_id: str):
    """Mark a retcon request as resolved (director-approved)."""
    settings = ctx.obj["settings"]

    async def _run_inner(store):
        await store.resolve_retcon(retcon_id, resolved_by="director")
        console.print(f"[green]Retcon {retcon_id} approved.[/green]")

    asyncio.run(_with_store(settings, _run_inner))


@cli.command("retcon-reject")
@click.argument("retcon_id")
@click.pass_context
def retcon_reject(ctx, retcon_id: str):
    """Reject a retcon request."""
    settings = ctx.obj["settings"]

    async def _run_inner(store):
        await store.reject_retcon(retcon_id)
        console.print(f"[red]Retcon {retcon_id} rejected.[/red]")

    asyncio.run(_with_store(settings, _run_inner))


@cli.command()
@click.pass_context
def chapters(ctx):
    """List chapters by editorial status."""
    settings = ctx.obj["settings"]

    async def _run_inner(store):
        all_chapters = await store.list_chapters()
        if not all_chapters:
            console.print("No chapters yet.")
            return
        table = Table(title="Chapters")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Title")
        table.add_column("Status")
        for ch in all_chapters:
            table.add_row(ch.id[:8], ch.title, ch.editorial_status.value)
        console.print(table)

    asyncio.run(_with_store(settings, _run_inner))


@cli.command()
@click.argument("chapter_id")
@click.pass_context
def read(ctx, chapter_id: str):
    """Print chapter prose."""
    settings = ctx.obj["settings"]

    async def _run_inner(store):
        ch = await store.get_chapter(chapter_id)
        if not ch:
            console.print(f"[red]Chapter {chapter_id} not found.[/red]")
            return
        console.rule(ch.title)
        console.print(ch.prose)

    asyncio.run(_with_store(settings, _run_inner))


@cli.command()
@click.argument("chapter_id")
@click.pass_context
def finalize(ctx, chapter_id: str):
    """Promote a chapter to final status."""
    settings = ctx.obj["settings"]

    async def _run_inner(store):
        ch = await store.get_chapter(chapter_id)
        if not ch:
            console.print(f"[red]Chapter {chapter_id} not found.[/red]")
            return
        ch.editorial_status = EditorialStatus.final
        await store.save_chapter(ch)
        console.print(f"[green]Chapter '{ch.title}' finalized.[/green]")

    asyncio.run(_with_store(settings, _run_inner))


@cli.command()
@click.option("--tick-sleep", default=2.0, help="Seconds between scheduler ticks.")
@click.pass_context
def run(ctx, tick_sleep: float):
    """Run the agent system continuously. Ctrl+C to stop."""
    settings = ctx.obj["settings"]

    async def _run_inner():
        from novelizer.agents.world_architect import WorldArchitect
        from novelizer.agents.character_keeper import CharacterKeeper
        from novelizer.agents.author import Author
        from novelizer.agents.editor import Editor
        from novelizer.agents.continuity_checker import ContinuityChecker
        from novelizer.agents.retconner import Retconner
        from novelizer.scheduler import Scheduler

        store = _get_store(settings)
        await store.init()

        agents = [
            WorldArchitect(store=store, min_interval=settings.default_interval, llm_model=settings.llm_model),
            CharacterKeeper(store=store, min_interval=settings.default_interval, llm_model=settings.llm_model),
            Author(store=store, min_interval=settings.author_interval, llm_model=settings.llm_model),
            Editor(store=store, min_interval=settings.default_interval, llm_model=settings.llm_model),
            ContinuityChecker(store=store, min_interval=settings.continuity_interval, llm_model=settings.llm_model),
            Retconner(store=store, min_interval=settings.default_interval, llm_model=settings.llm_model),
        ]
        scheduler = Scheduler(agents=agents, store=store, tick_sleep=tick_sleep)

        loop = asyncio.get_running_loop()
        loop.add_signal_handler(_signal.SIGINT, scheduler.stop)
        loop.add_signal_handler(_signal.SIGTERM, scheduler.stop)

        console.print("[bold green]Novelizer running.[/bold green] Ctrl+C to stop.")
        try:
            await scheduler.run()
        finally:
            await store.close()
            console.print("Shutdown complete.")

    asyncio.run(_run_inner())


def main():
    cli()
