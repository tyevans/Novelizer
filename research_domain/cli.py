# research_domain/cli.py
from __future__ import annotations
import asyncio
import json
import os

import click
from rich.console import Console
from rich.table import Table

from research_domain.runtime import ResearchRuntime

console = Console()


def _resolve_dsn(dsn: str | None) -> str:
    if dsn:
        return dsn
    env_dsn = os.environ.get("DATABASE_URL")
    if not env_dsn:
        raise click.ClickException("No --dsn given and DATABASE_URL is not set.")
    return env_dsn


@click.group()
def main() -> None:
    """research_domain: append events to the research stream and inspect projections."""


@main.command()
@click.argument("event_type")
@click.argument("payload_json")
@click.option("--dsn", default=None, help="Postgres DSN (defaults to DATABASE_URL env var)")
@click.option("--stream", default="research-stream", help="Event stream id")
def append(event_type: str, payload_json: str, dsn: str | None, stream: str) -> None:
    """Append EVENT_TYPE with PAYLOAD_JSON to the research stream."""
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON payload: {exc}")

    resolved_dsn = _resolve_dsn(dsn)

    async def _run() -> None:
        runtime = ResearchRuntime(resolved_dsn, stream=stream)
        await runtime.connect()
        try:
            await runtime.append_event(event_type, payload)
        finally:
            await runtime.close()

    asyncio.run(_run())
    console.print(f"[green]Appended[/green] {event_type}")


@main.command()
@click.argument("projection_name")
@click.option("--dsn", default=None, help="Postgres DSN (defaults to DATABASE_URL env var)")
@click.option("--stream", default="research-stream", help="Event stream id")
def show(projection_name: str, dsn: str | None, stream: str) -> None:
    """Show the current value of PROJECTION_NAME."""
    resolved_dsn = _resolve_dsn(dsn)

    async def _run() -> dict:
        runtime = ResearchRuntime(resolved_dsn, stream=stream)
        await runtime.connect()
        try:
            await runtime.catch_up()
            return runtime.get_projection(projection_name)
        finally:
            await runtime.close()

    result = asyncio.run(_run())
    table = Table(title=projection_name)
    table.add_column("Key")
    table.add_column("Value")
    for key, value in result.items():
        table.add_row(str(key), str(value))
    console.print(table)
