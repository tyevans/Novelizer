from __future__ import annotations
import asyncio
import click
from rich.console import Console
from rich.table import Table
from novelizer.config import Settings
from novelizer.runtime import Runtime
from novelizer.canon.events import EventType
from novelizer.store.models import DirectorSignal, SignalKind

console = Console()


async def _with_runtime(settings, fn):
    rt = Runtime(settings)
    # CLI commands that only touch the store don't need the LLM runner.
    await rt.events.init()
    await rt.projector.init()
    await rt.read.init()
    await rt.projector.catch_up()
    try:
        return await fn(rt)
    finally:
        await rt.close()


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    ctx.ensure_object(dict)
    ctx.obj["settings"] = Settings()
    if ctx.invoked_subcommand is None:
        _launch_tui(ctx.obj["settings"])


def _launch_tui(settings: Settings) -> None:
    from novelizer.tui.app import NovelizerApp

    async def _boot():
        rt = Runtime(settings)
        await rt.start()
        app = NovelizerApp(rt)
        try:
            await app.run_async()
        finally:
            await rt.close()

    asyncio.run(_boot())


@cli.command()
@click.argument("text")
@click.pass_context
def seed(ctx, text: str):
    """Inject a narrative seed as a director_signal.created event."""
    async def _run(rt: Runtime):
        sig = DirectorSignal(kind=SignalKind.seed, body=text)
        await rt.events.append(EventType.DIRECTOR_SIGNAL_CREATED, sig.id, sig)
        console.print(f"[green]Seed injected:[/green] {text}")
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.pass_context
def chapters(ctx):
    """List chapters by editorial status."""
    async def _run(rt: Runtime):
        chs = await rt.read.list_chapters()
        if not chs:
            console.print("No chapters yet.")
            return
        table = Table(title="Chapters")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Title")
        table.add_column("Status")
        for c in chs:
            table.add_row(c.id[:8], c.title, c.editorial_status.value)
        console.print(table)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.argument("chapter_id")
@click.pass_context
def read(ctx, chapter_id: str):
    """Print a chapter's prose."""
    async def _run(rt: Runtime):
        ch = await rt.read.get_chapter(chapter_id)
        if not ch:
            console.print(f"[red]Chapter {chapter_id} not found.[/red]")
            return
        console.rule(ch.title)
        console.print(ch.prose)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


@cli.command()
@click.pass_context
def retcons(ctx):
    """List open retcon requests."""
    async def _run(rt: Runtime):
        reqs = await rt.read.list_retcon_requests(status="open")
        if not reqs:
            console.print("No open retcon requests.")
            return
        table = Table(title="Open Retcon Requests")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Description")
        table.add_column("Proposed Resolution")
        for r in reqs:
            table.add_row(r.id[:8], r.description, r.proposed_resolution)
        console.print(table)
    asyncio.run(_with_runtime(ctx.obj["settings"], _run))


def main():
    cli()
