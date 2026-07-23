# research_domain/cli.py
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from agent_kit import Scheduler

from research_domain.corpus import CorpusReader
from research_domain.roles import build_live_agents
from research_domain.runners import ModelSettings, build_role_runner
from research_domain.runtime import ResearchRuntime
from research_domain.tools import make_claim_tools, make_corpus_tools

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


def build_run_components(
    *,
    dsn: str,
    stream: str,
    corpus_dir: str,
    settings: ModelSettings | None,
    interval: int,
    max_concurrent: int,
    tick_sleep: float = 1.0,
    agents=None,
):
    """Construct the runtime + scheduler for a live run. Tests inject
    pre-built `agents` (bound to their own runtime) and pass settings=None;
    the CLI passes settings and lets the real trio be built here."""
    runtime = ResearchRuntime(dsn, stream=stream)
    if agents is None:
        if settings is None:
            raise click.ClickException("model settings required when agents are not injected")
        corpus = CorpusReader(Path(corpus_dir))
        shared_tools = make_corpus_tools(corpus) + make_claim_tools(runtime)
        agents = build_live_agents(
            runtime, corpus, lambda role: build_role_runner(role, settings, shared_tools)
        )
        for agent in agents:
            agent.interval = interval
    scheduler = Scheduler(
        agents, tick_sleep=tick_sleep, max_concurrent_agents=max_concurrent
    )
    return runtime, scheduler


@main.command()
@click.option("--corpus", "corpus_dir", required=True,
              type=click.Path(exists=True, file_okay=False),
              help="Directory of .md/.txt documents to research")
@click.option("--dsn", default=None, help="Postgres DSN (defaults to DATABASE_URL env var)")
@click.option("--stream", default="research-stream", help="Event stream id")
@click.option("--model", envvar="RESEARCH_MODEL", required=True,
              help="Model name (env RESEARCH_MODEL)")
@click.option("--base-url", envvar="LLM_BASE_URL", required=True,
              help="OpenAI-compatible endpoint base URL (env LLM_BASE_URL)")
@click.option("--api-key", envvar="LLM_API_KEY", default="unused",
              help="API key (env LLM_API_KEY)")
@click.option("--interval", default=60, show_default=True,
              help="Per-agent minimum seconds between runs")
@click.option("--max-concurrent", default=2, show_default=True,
              help="Dispatch pool size")
@click.option("--max-ticks", default=None, type=int,
              help="Tick N times then exit (default: run until interrupted)")
def run(corpus_dir, dsn, stream, model, base_url, api_key,
        interval, max_concurrent, max_ticks) -> None:
    """Run the extractor/verifier/retractor trio over a document corpus."""
    resolved_dsn = _resolve_dsn(dsn)
    settings = ModelSettings(model=model, base_url=base_url, api_key=api_key)

    async def _run() -> None:
        runtime, scheduler = build_run_components(
            dsn=resolved_dsn, stream=stream, corpus_dir=corpus_dir,
            settings=settings, interval=interval, max_concurrent=max_concurrent,
        )
        await runtime.connect()
        try:
            await runtime.catch_up()
            console.print(f"[green]research run[/green] corpus={corpus_dir} stream={stream}")
            if max_ticks is None:
                await scheduler.run()
            else:
                for _ in range(max_ticks):
                    await scheduler.tick()
                    await asyncio.sleep(scheduler._tick_sleep)
                await scheduler.drain_in_flight()
        finally:
            await runtime.close()

    asyncio.run(_run())
